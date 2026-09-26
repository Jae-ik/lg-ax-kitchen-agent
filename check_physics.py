# -*- coding: utf-8 -*-
"""기기 시뮬레이터의 물리가 스스로 모순되지 않는지 확인한다.

`stress_test.py` 는 57건을 돌리지만 보는 것은 "예외가 안 났다" 이고,
`check_consistency` 2번은 "스텝 크기를 바꿔도 결과가 같다" 만 본다.
**질량이 맞는지, 온도가 물리적인지는 아무도 본 적이 없었다.**

여기서는 조작(가열·투입·물 붓기·거품 걷기·뚜껑·젓기)을 **무작위로 섞어**
돌리면서 매 스텝 불변량을 확인한다. 특정 시나리오가 아니라 아무 순서나
돌려 보는 것이 요점이다 — 사람이 짠 시나리오는 사람이 생각한 경우만 덮는다.

확인하는 불변량
  질량 수지   졸임 분모 + 부은 물 − 지금 = 증발량 ≥ 0
              (분모는 걷어내면 함께 줄고 재료를 넣으면 함께 는다)
  상 분해     자유 수분 + 흡수된 물 ≤ 전체 질량
  온도        0 ≤ 온도 ≤ 끓는점,  최고 온도 ≥ 현재 온도
  단조        누적량(부은 물·걷은 양·경과 시간)은 줄지 않는다
  비가역      눌어붙음·익힘은 되돌아가지 않는다
  에너지      불을 끄면 온도가 오르지 않는다
"""
from __future__ import annotations
import random
import sys

import dryer as D
import kitchen as K

BOIL_C = 100.0
EPS = 0.05          # 반올림이 만드는 오차 허용폭 (g, ℃)

QUANT = []


def check(title):
    def deco(fn):
        QUANT.append((title, fn))
        return fn
    return deco


def _snapshot(s):
    return {k: s.get(k) for k in
            ("mass_g", "initial_mass_g", "temp_c", "peak_temp_c", "soil_score",
             "added_g", "watered_g", "skimmed_g", "absorbed_g",
             "free_liquid_g", "doneness", "elapsed_min")}


def _violations(prev, cur, note):
    """한 스텝 전후로 깨진 불변량을 모아 돌려준다."""
    bad = []

    # ── 질량 수지 ──
    # `initial_mass_g` 는 "처음 넣은 양" 이 아니라 **졸임 비율의 분모**다.
    # 걷어내면(skim) mass 와 함께 줄고, 도중에 재료를 넣으면 함께 는다.
    # 그래서 skimmed 를 또 빼면 이중 차감이 된다 — 처음에 그렇게 써서
    # 멀쩡한 코드를 버그로 읽을 뻔했다.
    put_in = cur["initial_mass_g"] + cur["watered_g"]
    evaporated = put_in - cur["mass_g"]
    if evaporated < -EPS:
        bad.append(f"질량이 어디선가 생겼다 — 분모 {cur['initial_mass_g']:.1f}g "
                   f"+ 부은 물 {cur['watered_g']:.1f}g, 지금 "
                   f"{cur['mass_g']:.1f}g → 증발량 {evaporated:.2f}g (< 0)")

    # ── 상 분해 : 자유 수분 + 흡수된 물이 전체를 넘을 수 없다 ──
    if cur["free_liquid_g"] + cur["absorbed_g"] > cur["mass_g"] + EPS:
        bad.append(f"자유 수분 {cur['free_liquid_g']:.1f}g + 흡수 "
                   f"{cur['absorbed_g']:.1f}g > 전체 {cur['mass_g']:.1f}g")
    if cur["free_liquid_g"] < -EPS:
        bad.append(f"자유 수분이 음수 {cur['free_liquid_g']:.2f}g")

    # ── 온도 ──
    if not (-EPS <= cur["temp_c"] <= BOIL_C + EPS):
        bad.append(f"온도가 범위를 벗어났다 {cur['temp_c']:.2f}℃")
    if cur["peak_temp_c"] < cur["temp_c"] - EPS:
        bad.append(f"최고 온도 {cur['peak_temp_c']:.2f} < 현재 "
                   f"{cur['temp_c']:.2f}")

    # ── 단조 (누적량은 줄지 않는다) ──
    for k in ("watered_g", "skimmed_g", "added_g", "elapsed_min",
              "peak_temp_c"):
        if cur[k] < prev[k] - EPS:
            bad.append(f"{k} 가 줄었다 {prev[k]} → {cur[k]}")

    # ── 비가역 (탄 것과 익은 것은 되돌아가지 않는다) ──
    if cur["soil_score"] < prev["soil_score"] - 1e-6:
        bad.append(f"눌어붙음이 줄었다 {prev['soil_score']} → {cur['soil_score']}")
    if cur["doneness"] < prev["doneness"] - 1e-6:
        bad.append(f"익힘이 되돌아갔다 {prev['doneness']} → {cur['doneness']}")

    return [f"{note}: {b}" for b in bad]



# ══════════════════════════════════════════════════════════════════════
# 정량 검산 — 모델이 자기가 적어 둔 식을 실제로 지키는가
#
# 위의 불변량은 "말이 안 되는 일이 없다" 를 본다. 여기서는 한 걸음 더
# 들어가 **숫자가 식과 맞는지** 본다. 상수에 근거가 적혀 있으므로
# (620g 100→90도 3분 / 620g 화력3 으로 20→100도 5.5분) 그것도 함께 잰다.
# ══════════════════════════════════════════════════════════════════════
C = K.Cooker


def _fresh(mass=620.0, power=0, temp=None, lid=False):
    K.reset([], seed=1)
    c = K.COOKER
    c.deterministic = True
    c.start(mass, 0, power=power)
    if temp is not None:
        c.peak_temp_c = temp
        c._set_temp(temp)
    c.set_lid(lid)
    return c


@check("한 스텝의 에너지 수지가 맞는가 (투입 = 온도 + 증발 + 방열)")
def p1():
    """`tick` 이 적어 둔 식을 그대로 검산한다.

        p_net·Δt = ΔT·열용량 + 증발량·잠열

    실제로 여기서 구멍을 하나 찾았다. 표면 증발(SURF_EVAP)이 질량만
    줄이고 **잠열을 가져가지 않아**, 95도에서 1분에 2439 J 가 공짜로
    생기고 있었다(같은 조건 방열 10117 J 의 24%).
    """
    rows = []
    for mass, power, temp in ((1000, 0, 95.0), (620, 3, 60.0),
                              (2000, 5, 30.0), (400, 1, 80.0)):
        c = _fresh(mass, power, temp)
        m0, t0 = c.mass_g, c.temp_c
        hc = (m0 + C.POT_EQ_G) * C.C_WATER
        c.tick(1.0)
        dm, dT = m0 - c.mass_g, c.temp_c - t0
        put_in = c.power * C.WATT_PER_POWER * 60.0
        t_mid = (t0 + c.temp_c) / 2
        loss = C.LOSS_W_PER_K * (t_mid - C.AMBIENT_C) * 60.0
        used = dT * hc + dm * C.LATENT_J_PER_G + loss
        err = abs(put_in - used) / max(1.0, abs(put_in) + abs(loss))
        rows.append((mass, power, temp, err))
    worst = max(r[3] for r in rows)
    ok = worst < 0.03          # 중점법 근사라 몇 % 는 남는다
    detail = " · ".join(f"{m}g/화력{p}/{t:.0f}도 오차 {e:.2%}"
                        for m, p, t, e in rows)
    return ok, detail


@check("상수가 적어 둔 근거와 맞는가")
def p2():
    c = _fresh(620, 0, 100.0)
    t = 0.0
    while c.temp_c > 90 and t < 30:
        c.tick(0.1)
        t += 0.1
    cool = t
    c = _fresh(620, 3)
    t = 0.0
    while c.temp_c < 99.99 and t < 30:
        c.tick(0.1)
        t += 0.1
    heat = t
    ok = 2.4 <= cool <= 3.9 and 4.8 <= heat <= 6.4
    return ok, (f"620g 100→90도 {cool:.1f}분 (근거 약 3분) · "
                f"620g 화력3 20→100도 {heat:.1f}분 (근거 약 5.5분)")


@check("끓는점에서 온도가 고정되고 잉여 열이 증발로 간다")
def p3():
    c = _fresh(800, 5)
    while c.temp_c < 99.99:
        c.tick(0.25)
    temps, evaps = [], []
    for _ in range(6):
        m0 = c.mass_g
        c.tick(1.0)
        temps.append(c.temp_c)
        evaps.append(m0 - c.mass_g)
    drift = max(abs(t - C.BOIL_C) for t in temps)
    # 끓는 중 증발량 = p_net / 잠열  (표면 증발 몫이 더해진다)
    p_net = 5 * C.WATT_PER_POWER - C.LOSS_W_PER_K * (C.BOIL_C - C.AMBIENT_C)
    want = p_net * 60.0 / C.LATENT_J_PER_G
    got = sum(evaps) / len(evaps)
    ok = drift < 0.01 and abs(got - want) / want < 0.20
    return ok, (f"온도 흔들림 {drift:.4f}도 · 증발 {got:.2f}g/분 "
                f"(끓는 몫 계산값 {want:.2f} + 표면 증발)")


@check("양이 늘면 데우는 시간이 열용량에 비례해 늘어난다")
def p4():
    """예전에는 310g 과 2000g 이 같은 3.75분에 끓었다(1차 지연 모델).
    열량 수지로 바꾼 뒤 비례하는지 다시 확인한다."""
    rows = []
    for mass in (400, 800, 1600):
        c = _fresh(mass, 3)
        t = 0.0
        while c.temp_c < 99.99 and t < 90:
            c.tick(0.1)
            t += 0.1
        # 예상: (m+냄비)·비열·ΔT / p_net   (방열은 평균 온도로 근사)
        hc = (mass + C.POT_EQ_G) * C.C_WATER
        p_net = 3 * C.WATT_PER_POWER - C.LOSS_W_PER_K * (60 - C.AMBIENT_C)
        want = hc * 80 / p_net / 60.0
        rows.append((mass, t, want))
    errs = [abs(t - w) / w for _, t, w in rows]
    ok = max(errs) < 0.15
    return ok, " · ".join(f"{m}g {t:.1f}분(계산 {w:.1f})" for m, t, w in rows)


@check("화력을 올리면 순투입에 비례해 빨라진다")
def p5():
    rows = []
    for power in (2, 3, 5):
        c = _fresh(800, power)
        t = 0.0
        while c.temp_c < 99.99 and t < 90:
            c.tick(0.1)
            t += 0.1
        rows.append((power, t))
    # 순투입 x 시간 이 거의 일정해야 한다 (방열은 같은 온도 구간이므로)
    prod = [(p * C.WATT_PER_POWER
             - C.LOSS_W_PER_K * (60 - C.AMBIENT_C)) * t for p, t in rows]
    spread = (max(prod) - min(prod)) / min(prod)
    ok = spread < 0.12
    return ok, (" · ".join(f"화력{p} {t:.1f}분" for p, t in rows)
                + f" → 순투입x시간 편차 {spread:.1%}")


@check("뚜껑이 증발과 방열을 적어 둔 비율만큼 바꾼다")
def p6():
    got = {}
    for lid in (False, True):
        c = _fresh(800, 4)
        while c.temp_c < 99.99:
            c.tick(0.25)
        c.set_lid(lid)
        m0 = c.mass_g
        for _ in range(4):
            c.tick(1.0)
        got[lid] = (m0 - c.mass_g) / 4
    ratio = got[True] / got[False]
    # 끓는 중이라 evap 전체에 LID_EVAP 이 곱해진다
    ok = abs(ratio - C.LID_EVAP) < 0.05
    return ok, (f"뚜껑 열고 {got[False]:.2f}g/분 · 덮고 {got[True]:.2f}g/분 "
                f"→ 비율 {ratio:.3f} (LID_EVAP {C.LID_EVAP})")


@check("불을 끄면 지수적으로 식는다 (시상수가 열용량에 비례)")
def p7():
    rows = []
    for mass in (400, 1200):
        c = _fresh(mass, 0, 95.0)
        t = 0.0
        # 95 → 20 의 63% 지점(=20+0.37*75=47.8도)까지 = 시상수
        while c.temp_c > 47.8 and t < 200:
            c.tick(0.5)
            t += 0.5
        want = (mass + C.POT_EQ_G) * C.C_WATER / C.LOSS_W_PER_K / 60.0
        rows.append((mass, t, want))
    # 증발 잠열도 식히므로 실제는 계산보다 빠르다 — 방향만 본다
    ok = all(0.45 * w <= t <= 1.05 * w for _, t, w in rows)
    return ok, " · ".join(f"{m}g 시상수 {t:.0f}분(방열만 볼 때 {w:.0f})"
                          for m, t, w in rows)


def _random_run(seed: int, steps: int = 40):
    """무작위 조작 시퀀스를 돌리며 매 스텝 불변량을 본다."""
    rng = random.Random(seed)
    K.reset([{"name": "배추", "qty_g": 900, "stored_days": 3,
              "shelf_life_days": 7},
             {"name": "두부", "qty_g": 600, "stored_days": 2,
              "shelf_life_days": 5}], seed=seed)
    c = K.COOKER
    c.start(rng.choice([300, 600, 1200]), rng.choice([0, 50, 150]),
            power=rng.randint(1, 5))
    prev = _snapshot(c.state())
    bad = []
    for i in range(steps):
        act = rng.choice(["tick", "tick", "tick", "power", "lid", "stir",
                          "water", "skim", "add"])
        try:
            if act == "tick":
                c.tick(rng.choice([0.1, 0.25, 0.5, 1.0]))
            elif act == "power":
                c.set_power(rng.randint(0, 5))
            elif act == "lid":
                c.set_lid(rng.random() < 0.5)
            elif act == "stir":
                c.stir()
            elif act == "water":
                c.add_water(rng.choice([20, 80, 200]),
                            temp_c=rng.choice([5.0, 18.0, 60.0]))
            elif act == "skim":
                c.skim(rng.choice([2, 10, 40]))
            elif act == "add":
                c.add_ingredient("두부", rng.choice([50, 150]),
                                 temp_c=8.0)
        except Exception as e:
            bad.append(f"시드 {seed} {i}번째 {act} — 예외 "
                       f"{type(e).__name__}: {e}")
            break
        cur = _snapshot(c.state())
        bad += _violations(prev, cur, f"시드 {seed} {i}번째 {act}")
        prev = cur
        if bad:
            break
    return bad


def _heat_direction():
    """불을 끄면 온도가 오르지 않는다 — 에너지 방향 확인."""
    K.reset([], seed=3)
    c = K.COOKER
    c.start(800, 0, power=5)
    for _ in range(8):
        c.tick(1.0)
    hot = c.state()["temp_c"]
    c.set_power(0)
    bad = []
    prev = hot
    for i in range(10):
        c.tick(1.0)
        now = c.state()["temp_c"]
        if now > prev + 1e-6:
            bad.append(f"불을 껐는데 {i+1}분에 온도가 올랐다 "
                       f"{prev:.2f} → {now:.2f}")
            break
        prev = now
    return bad, f"화력 5 로 8분 → {hot:.1f}℃, 끄고 10분 → {prev:.1f}℃"


def _dryer_invariants(seed: int, steps: int = 40):
    rng = random.Random(seed)
    d = D.Dryer()
    d.start(moisture=rng.choice([0.12, 0.18, 0.35]), power=rng.randint(1, 5))
    bad = []
    prev_m, prev_t = d.moisture, d.elapsed_min
    for i in range(steps):
        if rng.random() < 0.3:
            d.set_power(rng.randint(0, 5))
        d.tick(rng.choice([0.1, 0.25, 0.5, 1.0]))
        s = d.state()
        if not (0.0 <= s["moisture"] <= 1.0 + 1e-9):
            bad.append(f"시드 {seed} {i}번째 — 함수율이 범위 밖 {s['moisture']}")
        if s["moisture"] > prev_m + 1e-9:
            bad.append(f"시드 {seed} {i}번째 — 빨래가 다시 젖었다 "
                       f"{prev_m} → {s['moisture']}")
        if s["elapsed_min"] < prev_t - 1e-9:
            bad.append(f"시드 {seed} {i}번째 — 시간이 거꾸로 갔다")
        if not (15.0 <= s["drum_temp_c"] <= 85.0):
            bad.append(f"시드 {seed} {i}번째 — 드럼 온도가 이상하다 "
                       f"{s['drum_temp_c']}")
        prev_m, prev_t = s["moisture"], s["elapsed_min"]
        if bad:
            break
    return bad


def main() -> int:
    print("물리 불변량 검사 — 시뮬레이터가 스스로 모순되지 않는가")
    print("  조작을 무작위로 섞어 돌리며 매 스텝 확인한다.")
    print()
    bad_total = 0

    N = 60
    found = []
    for seed in range(N):
        found += _random_run(seed)
    if found:
        print(f"  !!  조리기 무작위 {N}회 — {len(found)}건 위반")
        for f in found[:5]:
            print(f"        {f}")
        bad_total += 1
    else:
        print(f"  OK  조리기 무작위 {N}회 · 각 40조작 — 질량·상·온도·단조·비가역 "
              f"모두 지켜짐")

    hb, note = _heat_direction()
    if hb:
        print(f"  !!  에너지 방향 — {hb[0]}")
        bad_total += 1
    else:
        print(f"  OK  에너지 방향 — {note}")

    dfound = []
    for seed in range(N):
        dfound += _dryer_invariants(seed)
    if dfound:
        print(f"  !!  건조기 무작위 {N}회 — {len(dfound)}건 위반")
        for f in dfound[:5]:
            print(f"        {f}")
        bad_total += 1
    else:
        print(f"  OK  건조기 무작위 {N}회 — 함수율 범위·단조 감소·시간·드럼 온도 "
              f"모두 지켜짐")

    print()
    print("정량 검산 — 숫자가 식과 맞는가")
    for title, fn in QUANT:
        try:
            ok, note = fn()
        except Exception as e:
            print(f"  !!  {title}")
            print(f"        예외 {type(e).__name__}: {e}")
            bad_total += 1
            continue
        print(f"  {'OK ' if ok else '!! '} {title}")
        print(f"        {note}")
        if not ok:
            bad_total += 1

    print()
    total = 3 + len(QUANT)
    print(f"판정: {total - bad_total}/{total} 항목 통과")
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main())
