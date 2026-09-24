# -*- coding: utf-8 -*-
"""건조기 시뮬레이터.

조리기와 아무 관계가 없는 별도 기기다.
그런데 converge 스킬은 코드 한 줄 바꾸지 않고 이 기기에도 쓸 수 있다.
'관측값을 목표값으로 수렴시킨다'는 구조가 같기 때문이다.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field

random.seed(11)


@dataclass
class Dryer:
    running: bool = False
    elapsed_min: float = 0.0
    moisture: float = 0.0          # 함수율 (0~1)
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
        self.drum_temp_c += (target_t - self.drum_temp_c) * 0.5
        # 건조 속도는 드럼 온도와 남은 수분에 비례
        dry = 0.0
        if self.drum_temp_c > 40:
            dry = (self.drum_temp_c - 40) / 400 * self.moisture * minutes
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

    def stop(self):
        self.running = False
        return self.state()


DRYER = Dryer()
