# -*- coding: utf-8 -*-
"""도메인에 묶이지 않는 공용 스킬.

converge  : 관측값을 목표값으로 수렴시킨다.  조리기·건조기·제습기·에어컨 공용
aftercare : 오염도로 사후처리 코스를 고른다. 식기세척기·세탁기 공용

두 스킬 모두 '무엇을 관측하고 무엇을 조작할지'를 함수로 주입받는다.
그래서 기기를 바꿔도 스킬 코드는 그대로다.
"""
from __future__ import annotations
from typing import Callable

from .base import Skill, SkillResult


class ConvergeSkill(Skill):
    name = "converge"
    description = ("관측값을 목표값에 도달시킨다. 매 단계 현재 상태를 읽고 목표와의 차이를 "
                   "계산해 액추에이터를 조정하며, 목표에 닿으면 즉시 멈춘다. "
                   "경과 시간이 아니라 상태로 종료를 판정한다.")
    input_schema = {
        "observe": "() -> dict            현재 상태를 읽는 함수",
        "actuate": "(int) -> None         액추에이터 세기를 바꾸는 함수",
        "step": "(float) -> None          시간을 진행시키는 함수",
        "metric": "str                    수렴을 판정할 상태 키",
        "target": "float                  목표값",
        "direction": "'down' | 'up'       목표에 접근하는 방향",
        "power_key": "str                 현재 세기를 담은 상태 키",
        "max_power": "int",
        "max_steps": "int",
        "ready_key": "str | None          준비 상태 키(예: 온도). 미달이면 세기를 올린다",
        "ready_at": "float | None",
        "min_controllable": "float | None  이보다 적으면 관측 주기 안에 지나친다",
        "amount_key": "str                 양을 담은 상태 키",
    }
    reusable_for = ["조리기(질량비)", "건조기(함수율)", "제습기(습도)", "에어컨(체감온도)"]

    def run(self, observe: Callable, actuate: Callable, step: Callable,
            metric: str, target: float, direction: str = "down",
            power_key: str = "power", max_power: int = 5, max_steps: int = 30,
            ready_key: str | None = None, ready_at: float | None = None,
            min_controllable: float | None = None,
            amount_key: str = "initial_mass_g",
            tolerance: float | None = 0.10,
            min_interval: float = 0.1,
            **_) -> SkillResult:

        # 목표를 '넘어선 것' 과 '맞춘 것' 은 다르다.
        # 졸이기는 되돌릴 수 없으므로 목표 아래로 내려가면 루프는 멈춰야 하지만,
        # 그것을 성공이라고 보고하면 안 된다. 0.78 을 노리고 0.55 로 끝난 것은
        # 실패다. tolerance 밖으로 지나치면 ok=False 로 돌려준다.
        def reached(v):
            return v <= target if direction == "down" else v >= target

        def on_target(v):
            return tolerance is None or abs(v - target) <= tolerance

        start = observe()
        trace = [{"t": 0, metric: round(start[metric], 4),
                  power_key: start.get(power_key)}]
        evidence = []

        # 시작하기 전에 이미 목표를 만족하는지 본다.
        # 이 확인이 없으면 이미 도달한 상태에서도 한 단계를 돌려
        # 불필요하게 가열한다 (에너지 낭비이자 과조리 위험).
        # 양이 너무 적으면 한 관측 주기 안에 목표를 지나친다.
        # 제어가 불가능한 구간이므로 먼저 알린다.
        amount = start.get(amount_key)
        too_small = bool(min_controllable and amount is not None
                         and amount < min_controllable)
        if too_small:
            evidence.append(f"{amount_key}={amount} < 제어 가능 최소 "
                            f"{min_controllable} — 한 주기 안에 목표를 지나칠 수 "
                            f"있다. 관측 주기를 줄이거나 양을 늘려야 한다")

        if reached(start[metric]):
            evidence.append(f"시작 시점에 이미 {metric}={start[metric]:.4f} 로 "
                            f"목표 {target} 을 만족 — 가열하지 않는다")
            return SkillResult(True, {"reached": True, "steps": 0,
                                      "final": round(start[metric], 4),
                                      "target": target, "trace": trace,
                                      "already": True}, evidence)

        # 관측 주기는 고정이 아니다. 목표까지 한 주기도 안 남았으면 더 자주 본다.
        # 이것이 '목표를 상태로 두는' 방식의 이점이다 — 제어 입력(시간)을
        # 복사했다면 주기를 줄일 근거 자체가 없다.
        dt = 1.0
        elapsed = 0.0
        for i in range(1, max_steps + 1):
            step(dt)
            elapsed += dt
            s = observe()
            cur = s[metric]
            prev = trace[-1][metric]
            rate = abs(prev - cur)
            gap = abs(cur - target)
            eta = round(gap / rate, 1) if rate > 1e-6 else None
            progress = 0.0
            if abs(start[metric] - target) > 1e-9:
                progress = abs(start[metric] - cur) / abs(start[metric] - target) * 100

            trace.append({"t": round(elapsed, 2), metric: round(cur, 4),
                          power_key: s.get(power_key), "eta": eta})
            evidence.append(f"{elapsed:g}분 {metric}={cur:.4f} "
                            f"진행 {progress:.1f}% ETA {eta}")

            if reached(cur):
                over = round(abs(target - cur), 4)
                ok = on_target(cur)
                if not ok:
                    evidence.append(
                        f"목표 {target} 을 {over} 만큼 지나쳤다 "
                        f"(허용 {tolerance}) — 도달로 세지 않는다. "
                        f"양이 적어 한 주기 안에 넘어간 것으로 본다")
                return SkillResult(ok, {
                    "reached": ok, "observations": i,
                    "steps": round(elapsed, 2),
                    "final": round(cur, 4),
                    "target": target, "trace": trace,
                    "too_small": too_small,
                    "overshot": not ok, "overshoot": over}, evidence)

            # 준비 상태(예: 끓는점)에 못 미치면 세기를 올린다
            if ready_key and ready_at and s.get(ready_key, 0) < ready_at:
                p = min(max_power, (s.get(power_key) or 0) + 1)
                actuate(p)
                evidence.append(f"  ↑ {ready_key}={s.get(ready_key):.1f} < {ready_at} → 세기 {p}")
                continue
            # 끓는데도 너무 느리면 세기를 올린다
            if eta is not None and eta > 6 and (s.get(power_key) or 0) < max_power:
                p = (s.get(power_key) or 0) + 1
                actuate(p)
                evidence.append(f"  ↑ ETA {eta}분으로 김 → 세기 {p}")

            # 다음 주기를 목표까지 남은 시간에 맞춘다. 남은 시간이 한 주기보다
            # 짧으면 그만큼만 진행해야 지나치지 않는다.
            if eta is not None and eta < dt:
                new_dt = max(min_interval, round(eta / 2, 3))
                if new_dt < dt:
                    evidence.append(f"  ↓ 남은 {eta}분 < 주기 {dt}분 "
                                    f"→ 관측 주기를 {new_dt}분으로 줄인다")
                    dt = new_dt

        return SkillResult(False, {"reached": False, "steps": round(elapsed, 2),
                                   "observations": max_steps,
                                   "final": round(observe()[metric], 4),
                                   "target": target, "trace": trace},
                           evidence + ["목표 미도달 — 가열 중단하고 사용자 확인 필요"])


class AftercareSkill(Skill):
    name = "aftercare"
    description = ("직전 작업에서 나온 오염도로 사후처리 코스를 고른다. 오염도는 작업을 "
                   "수행한 기기만 알고 있으므로, 넘겨주지 않으면 사후처리 기기는 기본 "
                   "코스를 쓰고 재세척 위험을 떠안는다.")
    input_schema = {"soil_score": "float 0~1", "profile": "str  코스 프로파일 이름"}
    reusable_for = ["식기세척기(조리 후)", "세탁기(오염 의류)", "로봇청소기(바닥 오염)"]

    # (필요강도, 코스명, 분, 온도, 물L)  — 강도 0=약 1=보통 2=강
    PROFILES = {
        "dishwasher": [(2, "강력(불림 포함)", 145, 70, 16.0),
                       (1, "표준", 95, 60, 11.0),
                       (0, "에코", 60, 50, 7.5)],
        "washer": [(2, "삶음", 150, 90, 65.0),
                   (1, "표준", 80, 40, 48.0),
                   (0, "울/섬세", 45, 30, 35.0)],
    }
    BASELINE_STRENGTH = 1        # 정보가 없으면 늘 '표준' 을 쓴다

    @staticmethod
    def _need(soil: float) -> int:
        return 0 if soil < 0.30 else (1 if soil < 0.55 else 2)

    @staticmethod
    def _rewash_prob(short: int) -> float:
        """필요 강도보다 약한 코스를 돌렸을 때 다시 돌리게 될 확률."""
        return {0: 0.05, 1: 0.45, 2: 0.80}.get(max(0, short), 0.80)

    def run(self, soil_score: float, profile: str = "dishwasher", **_) -> SkillResult:
        table = self.PROFILES.get(profile)
        if table is None:
            return SkillResult(False, {"error": f"알 수 없는 프로파일 {profile}"}, [])
        need = self._need(soil_score)
        by_strength = {t[0]: t for t in table}
        chosen = by_strength[need]
        strong = by_strength[2]

        def expected(course):
            p = self._rewash_prob(need - course[0])
            # 재세척은 가장 강한 코스로 한 번 더 돌린다고 본다
            return round(course[4] + p * strong[4], 1), p

        exp_c, p_c = expected(chosen)
        base = by_strength[self.BASELINE_STRENGTH]
        exp_b, p_b = expected(base)

        ev = [f"오염도 {soil_score} → 필요 강도 {need} → '{chosen[1]}'",
              f"선택: 물 {chosen[4]}L + 재세척확률 {p_c:.0%} → 기대 {exp_c}L",
              f"기본('{base[1]}', 오염도 모를 때): 물 {base[4]}L + 재세척확률 {p_b:.0%} "
              f"→ 기대 {exp_b}L",
              f"차이 {round(exp_b - exp_c, 1):+}L"]
        return SkillResult(True, {
            "course": chosen[1], "minutes": chosen[2], "temp_c": chosen[3],
            "water_l": chosen[4], "rewash_prob": p_c,
            "expected_water_l": exp_c,
            "baseline_course": base[1], "baseline_expected_water_l": exp_b,
            "saved_l": round(exp_b - exp_c, 1)}, ev)
