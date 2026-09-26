# -*- coding: utf-8 -*-
"""건조기 시뮬레이터.

조리기와 아무 관계가 없는 별도 기기다.
그런데 converge 스킬은 코드 한 줄 바꾸지 않고 이 기기에도 쓸 수 있다.
'관측값을 목표값으로 수렴시킨다'는 구조가 같기 때문이다.
"""
from __future__ import annotations
import copy
import random
from dataclasses import dataclass, field

random.seed(11)


@dataclass
class Dryer:
    running: bool = False
    elapsed_min: float = 0.0
    moisture: float = 0.0          # 함수율 (0~1)
    deterministic: bool = False    # True 면 회차 편차를 넣지 않는다(예측용)
    HEAT_K: float = 0.5            # 드럼이 데워지는 속도 (1분당 비율)
    # 건조가 끝나고 **문을 열어 꺼낼 때까지**. 조리기의 REST_MIN 과 같은
    # 성격의 값이고 마찬가지로 **가정**이다 — 물리가 정해 주지 않는다.
    # 이 모델에서는 드럼이 2분이면 40℃ 아래로 떨어져 건조가 멎으므로
    # 3분으로 두면 충분하다(측정: 79.9℃ → 2분 38.7℃ → 건조 정지).
    REST_MIN: int = 3
    drum_temp_c: float = 22.0
    power: int = 0                 # 0~5
    log: list = field(default_factory=list)

    def start(self, moisture: float, power: int = 2):
        self.running = True
        self.elapsed_min = 0.0
        self.moisture = moisture
        self.drum_temp_c = 22.0
        self.power = power
        self.log = [(0.0, moisture)]

    def tick(self, minutes: float = 1.0):
        if not self.running:
            return
        self.elapsed_min += minutes
        target_t = 25 + self.power * 11          # 최대 80℃ 부근
        # k 는 **1분당** 비율이다. 그대로 쓰면 tick(0.5) 도 tick(1.0) 과 같은
        # 양만큼 온도를 바꿔, 관측을 자주 할수록 결과가 달라진다.
        # 조리기에서 같은 버그를 고쳤는데 건조기는 그대로였다 —
        # "같은 스킬이 다른 기기에서 동작한다" 가 제안의 핵심인데,
        # 기기 쪽 물리가 서로 달라서는 그 주장을 뒷받침할 수 없다.
        k_eff = 1 - (1 - self.HEAT_K) ** minutes
        t_before = self.drum_temp_c
        self.drum_temp_c += (target_t - self.drum_temp_c) * k_eff
        # 건조 속도는 드럼 온도와 남은 수분에 비례.
        # 스텝 동안의 **평균 온도**로 구한다(끝 온도만 쓰면 큰 스텝에서 과소).
        t_mid = (t_before + self.drum_temp_c) / 2
        dry = 0.0
        if t_mid > 40:
            dry = (t_mid - 40) / 400 * self.moisture * minutes
            if not self.deterministic:
                dry *= random.uniform(0.9, 1.1)
        self.moisture = max(0.0, self.moisture - dry)
        self.log.append((round(self.elapsed_min, 1), round(self.moisture, 4)))

    def state(self):
        return {"running": self.running,
                "elapsed_min": round(self.elapsed_min, 1),
                "moisture": round(self.moisture, 4),
                "drum_temp_c": round(self.drum_temp_c, 1),
                "power": self.power}

    def set_power(self, level: int):
        self.power = max(0, min(5, int(level)))
        return self.state()

    def predict_residual(self, horizon_min: int | None = None) -> float:
        """지금 끄면 **앞으로 더 마를 양**.

        조리기의 `predict_residual_g` 와 같은 일을 한다. 드럼에 남은 열로
        건조가 이어지므로, 제어기가 이 값을 모르면 항상 목표를 지나친다
        (측정: 목표 0.08 에 0.0797 로 껐는데 문 열 때 0.0734 — 8.2% 과건조).

        예측은 **실제와 같은 코드**로 한다. 따로 식을 적으면 본체를 고칠 때
        어긋난다. 유령 사본에서 화력을 0 으로 두고 돌리면, 드럼이 식는 것과
        남은 건조가 tick 안에서 함께 계산된다.

        converge 는 이 함수를 `residual` 로 주입받을 뿐 건조기를 모른다 —
        조리기에 쓰던 스킬을 한 줄도 고치지 않고 쓸 수 있는 이유다.
        """
        ghost = copy.copy(self)
        ghost.log = []
        ghost.power = 0
        ghost.deterministic = True
        before = ghost.moisture
        for _ in range(horizon_min or self.REST_MIN):
            prev = ghost.moisture
            ghost.tick(1.0)
            if prev - ghost.moisture < 1e-5:
                break
        return round(before - ghost.moisture, 4)

    def rest_until_still(self, max_min: int | None = None) -> dict:
        """화력을 끄고 **문을 열 때까지** 둔다. 그때 상태를 돌려준다.

        사람이 꺼내 입는 것은 끄는 순간의 옷이 아니라 여열이 끝난 옷이다.
        조리기의 `rest_until_still` 과 같은 이유로 둔다.
        """
        self.set_power(0)
        for _ in range(max_min or self.REST_MIN):
            before = self.moisture
            self.tick(1.0)
            if before - self.moisture < 1e-5:
                break
        return self.state()

    def stop(self):
        self.running = False
        return self.state()


DRYER = Dryer()
