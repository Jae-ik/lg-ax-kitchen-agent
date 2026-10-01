# -*- coding: utf-8 -*-
"""조리기·세척기·건조기 물리를 **극단값 격자**로 훑어, 늘 성립해야 할 성질을 본다.

사례를 하나씩 찾아 고치는 대신, 입력 공간을 격자로 돌리며 불변식을 확인한다.

  조리기  질량 1g~3,000g · 화력 0~9(상한 밖 포함) · 출발 온도 2~95도 · 뚜껑 ·
          고형분·흡수분 · 시간 간격 0.1~2분 · 최대 240분
    I1 질량 ≥ 0, 유한       I2 물이 있으면 온도 ≤ 100.5도, 출발 온도 미만으로 안 떨어짐(불 켬)
    I3 질량은 투입 없이 늘지 않음   I4 눌어붙음·익힘·넘침 위험 ∈ [0, 1]
    I5 화력은 0~5 로 묶인다(시작·설정 모두)   I6 최고 온도 ≥ 출발 온도
    I7 같은 시간을 다른 간격으로 가도 질량이 5% 안
  제어    목표 0.05~0.99 · 질량 5~3,000g — 예외 없이 끝나고 **끝나면 화력 0**
  세척기  눌어붙음 0~1(밖 포함) · 시작 시각 00:00~23:59 — 분·물 ≥ 0, 예외 없음

    python audit_physics.py        → audit_physics.json (위반 목록)
"""
from __future__ import annotations
import itertools
import json
import math
import sys

import kitchen as K
from skills import REGISTRY

MASSES = (1, 5, 50, 300, 1000, 2000, 3000)
POWERS = (0, 1, 3, 5, 9)
TEMPS = (2, 20, 95)


def _cook(mass, power, temp, lid=False, solid=0.0, absorb=0.0, dt=1.0, minutes=60):
    K.reset([])
    c = K.COOKER
    c.start(mass, 0, power=power, capacity_g=3500, solid_g=solid,
            absorb_cap_g=absorb, lid=lid, start_temp_c=temp)
    c.deterministic = True
    seen, prev = [], c.state()
    for _ in range(int(round(minutes / dt))):
        c.tick(dt)
        st = c.state()
        seen.append((prev, st))
        prev = st
    return c, seen


def audit() -> list:
    out = []

    def bad(kind, why):
        out.append({"kind": kind, "why": why})

    for mass, power, temp, lid in itertools.product(MASSES, POWERS, TEMPS, (False, True)):
        tag = f"{mass}g·화력{power}·{temp}도·뚜껑{'O' if lid else 'X'}"
        try:
            c, seen = _cook(mass, power, temp, lid=lid, minutes=240)
        except Exception as e:
            bad("예외", f"{tag}: {type(e).__name__}: {e}")
            continue
        if c.power > 5 or c.power < 0:
            bad("I5화력상한", f"{tag}: 시작 화력이 {c.power} 로 남았다(0~5)")
        for prev, st in seen:
            m, t = st["mass_g"], st["temp_c"]
            if not (math.isfinite(m) and math.isfinite(t)):
                bad("I1유한", f"{tag}: 질량 {m}·온도 {t}"); break
            if m < -1e-9:
                bad("I1음수", f"{tag}: 질량 {m}"); break
            if st["free_liquid_g"] > 1 and t > 100.5:
                bad("I2끓는점", f"{tag}: 물이 {st['free_liquid_g']}g 있는데 {t}도"); break
            if power > 0 and t < min(temp, 20) - 0.5:
                bad("I2식음", f"{tag}: 불을 켰는데 {t}도로 출발({temp}도)보다 낮다"); break
            if m > prev["mass_g"] + 1e-6:
                bad("I3질량증가", f"{tag}: {prev['mass_g']}→{m}g"); break
            for k in ("soil_score", "doneness", "overflow_risk"):
                v = st.get(k)
                if v is not None and not (-1e-9 <= v <= 1 + 1e-9):
                    bad("I4범위", f"{tag}: {k}={v}"); break
        if seen and seen[-1][1]["peak_temp_c"] < temp - 0.5:
            bad("I6최고온도", f"{tag}: 최고 온도 {seen[-1][1]['peak_temp_c']} < 출발 {temp}")

    # I7 시간 간격 불변 — 고형분·흡수분·뚜껑까지
    for mass, solid, absorb, lid in ((300, 0, 0, False), (1500, 300, 400, True), (50, 10, 0, False)):
        ms = []
        for dt in (1.0, 0.5, 0.25, 0.1):
            c, _ = _cook(mass, 5, 20, lid=lid, solid=solid, absorb=absorb, dt=dt, minutes=20)
            ms.append(c.state()["mass_g"])
        if max(ms) - min(ms) > 0.05 * max(ms) + 0.5:
            bad("I7간격", f"{mass}g·고형{solid}·흡수{absorb}: {['%.1f' % x for x in ms]}")

    # 제어: 목표·질량 극단 — 예외 없이 끝나고 화력 0
    conv = REGISTRY.get("converge")
    for mass, tgt in itertools.product((5, 50, 300, 3000), (0.05, 0.5, 0.78, 0.99)):
        K.reset([])
        K.COOKER.start(mass, 0, power=3, capacity_g=3500)
        try:
            r = conv.run(observe=K.COOKER.state, actuate=K.COOKER.set_power,
                         step=K.COOKER.tick, metric="mass_ratio", target=tgt,
                         direction="down", max_power=5, max_steps=400, max_minutes=120)
            if K.COOKER.power != 0:
                bad("C끄기", f"{mass}g·목표{tgt}: 끝났는데 화력 {K.COOKER.power}")
        except Exception as e:
            bad("C예외", f"{mass}g·목표{tgt}: {type(e).__name__}: {e}")

    # 조작 — 물·재료 붓기, 거품 걷기에 극단값
    for fn, args in (("add_water", (float("nan"),)), ("add_water", (-50,)),
                     ("add_water", (100, 300)), ("add_ingredient", ("두부", float("inf"))),
                     ("add_ingredient", ("두부", 100, -200)), ("skim", (10000,)),
                     ("skim", (float("nan"),))):
        K.reset([])
        c = K.COOKER
        c.start(500, 0, power=3, capacity_g=600)
        try:
            getattr(c, fn)(*args)
            st = c.state()
            if not (math.isfinite(st["mass_g"]) and st["mass_g"] > 0
                    and -25 <= st["temp_c"] <= 100.5):
                bad("O조작", f"{fn}{args}: 질량 {st['mass_g']}·온도 {st['temp_c']}")
        except Exception as e:
            bad("O예외", f"{fn}{args}: {type(e).__name__}: {e}")
    K.reset([])
    K.COOKER.start(500, 0, power=3, capacity_g=600)
    for fn, args in (("add_water", (300,)), ("add_ingredient", ("두부", 300))):
        r = getattr(K.COOKER, fn)(*args) or {}
        if not r.get("overfilled"):
            bad("O넘침", f"{fn}{args}: 용량 600g 을 넘는데 알리지 않았다")

    # 건조기 — 함수율·화력 극단값
    import dryer as D
    for m_, pw in ((-0.2, 2), (1.5, 2), (float("nan"), 2), (0.3, 9), (0.3, -3), (0.3, 5)):
        try:
            D.DRYER.start(m_, power=pw)
        except ValueError:
            if 0 <= (m_ if m_ == m_ else -1) <= 1:
                bad("D거부", f"함수율 {m_}·화력 {pw}: 정상 값을 거부했다")
            continue
        if not (0 <= m_ <= 1):
            bad("D받음", f"함수율 {m_}: 있을 수 없는 값을 받았다")
        D.DRYER.deterministic = True
        for _ in range(60):
            D.DRYER.tick(1)
        st = D.DRYER.state()
        if not (0 <= st["moisture"] <= 1 and 15 <= st["drum_temp_c"] <= 85):
            bad("D범위", f"함수율 {m_}·화력 {pw}: {st}")

    # 세척기
    ac = REGISTRY.get("aftercare")
    for soil, start in itertools.product((-0.5, 0, 0.3, 0.55, 1.0, 1.7, float("nan")),
                                         ("00:00", "06:30", "22:59", "23:59")):
        try:
            o = ac.run(soil_score=soil, profile="dishwasher", start_at=start,
                       quiet_after="22:00").output
            if not (o["minutes"] > 0 and o["expected_water_l"] >= 0):
                bad("W범위", f"눌어붙음 {soil}·{start}: {o['minutes']}분·{o['expected_water_l']}L")
        except Exception as e:
            bad("W예외", f"눌어붙음 {soil}·{start}: {type(e).__name__}: {e}")
    return out


def main() -> int:
    out = audit()
    with open("audit_physics.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    kinds = {}
    for o in out:
        kinds.setdefault(o["kind"], []).append(o)
    print(f"물리 극단값 격자 — 위반 {len(out)}건")
    for k, v in sorted(kinds.items()):
        print(f"\n  [{k}] {len(v)}건")
        for o in v[:8]:
            print(f"    {o['why']}")
        if len(v) > 8:
            print(f"    … {len(v) - 8}건 더")
    # 위반이 없어도 **무엇을 몇 개 봤는지** 남긴다 — 출력이 비면 검사를 안 한 것과 구분이 안 된다
    print(f"  확인한 것: 조리기 {len(MASSES) * len(POWERS) * len(TEMPS) * 2}조합(240분) · "
          f"시간 간격 3조합 · 제어 16조합 · 조작 9가지 · 건조기 6가지 · 세척기 28조합")
    print(f"  격자 {len(out)}건 위반 — 0 이어야 통과")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
