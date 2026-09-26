# -*- coding: utf-8 -*-
"""요리로서 말이 되는가 — 조리 상식이 코드에서 성립하는지 확인한다.

`check_physics.py` 는 열역학이 자기 식을 지키는지 본다. 그것이 맞아도
**요리로서 틀릴 수 있다.** 예를 들어 에너지 수지가 완벽해도
  · 배추를 넣었는데 국물이 안 늘거나
  · 젓는 것이 눌어붙음을 못 줄이거나
  · 양을 두 배로 했는데 간이 두 배가 안 되거나
하면 그건 요리가 아니다.

여기서는 **조리하는 사람이 아는 사실**을 하나씩 코드에 물어본다.
각 항목은 "이렇게 하면 이렇게 되어야 한다" 는 한 문장으로 적는다.
"""
from __future__ import annotations
import io
import sys

import kitchen as K

CHECKS = []


def check(title):
    def deco(fn):
        CHECKS.append((title, fn))
        return fn
    return deco


def _pot(fridge=None, seed=1):
    K.reset(fridge or [], seed=seed)
    K.COOKER.deterministic = True
    return K.COOKER


def _boil_to(c, ratio, cap_min=120):
    """목표 질량비까지 졸인다. 걸린 시간을 돌려준다."""
    t = 0.0
    while c.state()["mass_ratio"] > ratio and t < cap_min:
        c.tick(0.25)
        t += 0.25
    return t


# ── 양과 시간 ───────────────────────────────────────────────────────────
@check("양을 두 배로 하면 졸이는 시간이 늘지만 두 배까지는 아니다")
def c1():
    """국물이 두 배여도 증발 면적과 화력 상한은 그대로다. 실제 조리에서
    '두 배 하면 두 배 걸린다' 가 아닌 이유이고, 레시피의 고정 시간을
    그대로 쓰면 큰 냄비에서 덜 졸아드는 이유다."""
    times = []
    for mass in (600, 1200):
        c = _pot()
        c.start(mass, 0, power=5)
        times.append(_boil_to(c, 0.78))
    ratio = times[1] / times[0]
    ok = 1.2 < ratio < 2.0
    return ok, (f"600g {times[0]:.2f}분 → 1200g {times[1]:.2f}분 "
                f"(배율 {ratio:.2f}, 1.0도 2.0도 아니어야 한다)")


@check("같은 목표 질량비는 양이 달라도 같은 '졸아든 정도' 를 뜻한다")
def c2():
    """비율로 기록하는 이유다. 절대 질량으로 기록하면 냄비가 바뀔 때
    무의미해진다(run_share.py 가 이것에 기대고 있다)."""
    outs = []
    for mass in (400, 900, 1800):
        c = _pot()
        c.start(mass, 0, power=5)
        _boil_to(c, 0.78)
        s = c.state()
        outs.append((mass, s["mass_ratio"], s["mass_g"] / mass))
    spread = max(r for _, r, _ in outs) - min(r for _, r, _ in outs)
    ok = spread < 0.01
    return ok, " · ".join(f"{m}g → 비율 {r:.4f}" for m, r, _ in outs)


# ── 재료가 하는 일 ──────────────────────────────────────────────────────
@check("수분 많은 재료를 넣으면 국물이 늘어난다")
def c3():
    """배추·애호박은 조리 중 물을 내놓는다. 그래서 '물 적당히' 레시피가
    같은 양으로도 다른 결과를 낸다 — 보관이 길수록 더 나온다."""
    K.reset([{"name": "배추", "qty_g": 400, "stored_days": 1,
              "shelf_life_days": 7},
             {"name": "된장", "qty_g": 400, "stored_days": 30,
              "shelf_life_days": 365}], seed=1)
    fresh = K.prep_weigh("배추", 200)
    K.reset([{"name": "배추", "qty_g": 400, "stored_days": 6,
              "shelf_life_days": 7}], seed=1)
    old = K.prep_weigh("배추", 200)
    K.reset([{"name": "된장", "qty_g": 400, "stored_days": 30,
              "shelf_life_days": 365}], seed=1)
    dry = K.prep_weigh("된장", 200)
    ok = (fresh["expected_extra_water_g"] > 0
          and old["expected_extra_water_g"] > fresh["expected_extra_water_g"]
          and dry["expected_extra_water_g"] == 0)
    return ok, (f"배추 1일 +{fresh['expected_extra_water_g']}g · "
                f"6일 +{old['expected_extra_water_g']}g · "
                f"된장 +{dry['expected_extra_water_g']}g")


@check("물을 빨아들이는 재료가 있으면 국물만 따로 줄어든다")
def c4():
    """찹쌀 같은 재료는 총 질량을 안 바꾸면서 **졸일 수 있는 물**을
    없앤다. 질량비만 보고 제어하면 목표에 영영 닿지 못한다 —
    실제로 0.9336 에서 멈추고 바닥이 탔었다."""
    c = _pot()
    c.start(1000, 0, power=4, absorb_cap_g=300)
    before = c.free_liquid_g()
    for _ in range(12):
        c.tick(0.5)
    s = c.state()
    ok = (s["absorbed_g"] > 100 and s["free_liquid_g"] < before
          and s["mass_g"] <= 1000)
    return ok, (f"자유 수분 {before:.0f}g → {s['free_liquid_g']:.0f}g, "
                f"흡수 {s['absorbed_g']:.0f}g (총 질량 {s['mass_g']:.0f}g)")


@check("도중에 넣은 재료는 넣은 뒤부터 익는다")
def c5():
    """8분에 넣은 고기가 0분부터 익은 것으로 계산되면 덜 익은 것을
    익었다고 보고한다."""
    # 익힘이 1.0 에 포화하면 내려갈 여지가 없다. 오래 익혀야 하는
    # 재료(need_units 를 크게)로 두어 포화 전에 잰다.
    c = _pot()
    c.start(800, 0, power=5, need_units=3000.0)
    for _ in range(6):
        c.tick(1.0)
    before = c.state()["doneness"]
    c.add_ingredient("두부", 300, temp_c=5.0, need_units=3000.0)
    after = c.state()["doneness"]
    ok = 0.0 < after < before < 1.0
    return ok, (f"투입 전 익힘 {before:.3f} → 투입 직후 {after:.3f} "
                f"(덜 익은 것이 들어와 전체가 내려간다)")


@check("찬 것을 넣으면 온도가 떨어지고, 양이 많을수록 더 떨어진다")
def c6():
    drops = []
    for grams in (100, 400):
        c = _pot()
        c.start(800, 0, power=5)
        while c.temp_c < 99.9:
            c.tick(0.25)
        t0 = c.temp_c
        c.add_ingredient("두부", grams, temp_c=5.0)
        drops.append((grams, t0 - c.temp_c))
    ok = drops[0][1] > 0 and drops[1][1] > drops[0][1]
    return ok, " · ".join(f"{g}g 투입 → -{d:.1f}도" for g, d in drops)


# ── 조작이 하는 일 ──────────────────────────────────────────────────────
@check("뚜껑을 덮으면 빨리 끓고, 졸이려면 열어야 한다")
def c7():
    times, evaps = {}, {}
    for lid in (False, True):
        c = _pot()
        c.start(800, 0, power=3)
        c.set_lid(lid)
        t = 0.0
        while c.temp_c < 99.9 and t < 60:
            c.tick(0.25)
            t += 0.25
        times[lid] = t
        m0 = c.mass_g
        for _ in range(4):
            c.tick(1.0)
        evaps[lid] = m0 - c.mass_g
    ok = times[True] < times[False] and evaps[True] < evaps[False] * 0.5
    return ok, (f"끓기까지 열고 {times[False]:.2f}분 / 덮고 {times[True]:.2f}분 · "
                f"4분 증발 {evaps[False]:.1f}g / {evaps[True]:.1f}g")


@check("저으면 눌어붙음이 줄고, 오래 안 저으면 다시 늘어난다")
def c8():
    got = {}
    for stir in (False, True):
        c = _pot()
        c.start(400, 0, power=5)
        while c.temp_c < 99.9:
            c.tick(0.25)
        c.soil = 0.0
        for i in range(8):
            if stir:
                c.stir()
            c.tick(1.0)
        got[stir] = c.state()["soil_score"]
    ok = got[True] < got[False]
    return ok, (f"안 젓고 {got[False]:.4f} · 매분 젓고 {got[True]:.4f} "
                f"({(1 - got[True] / max(1e-9, got[False])):.0%} 감소)")


@check("국물이 마를수록 눌어붙음이 심해진다")
def c9():
    """총 질량이 아니라 **자유 수분**이 기준이다."""
    # 건더기가 있어야 국물 비율이 떨어진다. 순수한 물은 졸여도 자유
    # 수분 비율이 1.0 그대로라 눌어붙지 않는다 — 실제로도 그렇다.
    # 처음에 물만 넣고 재는 바람에 초반·후반이 똑같이 0.04500 인데도
    # 부동소수점 차이로 통과했다. 통과하고도 아무것도 검증 못 한 시험이었다.
    c = _pot()
    c.start(500, 0, power=5, solid_g=200.0)
    while c.temp_c < 99.9:
        c.tick(0.25)
    marks = []
    prev_soil = c.soil
    for _ in range(40):
        c.tick(0.5)
        s = c.state()
        marks.append((s["free_ratio"], c.soil - prev_soil))
        prev_soil = c.soil
        if s["mass_ratio"] < 0.48:
            break
    early = sum(d for _, d in marks[:6]) / 6
    late = sum(d for _, d in marks[-6:]) / 6
    r0, r1 = marks[0][0], marks[-1][0]
    ok = late > early * 1.3 and r1 < r0
    return ok, (f"국물 비율 {r0:.2f}→{r1:.2f} · 분당 눌어붙음 "
                f"{early:.5f}→{late:.5f} ({late / max(1e-9, early):.1f}배)")


@check("거품을 걷으면 국물이 아니라 고형분이 주로 빠진다")
def c10():
    """걷어내는 것은 떠오른 단백질·기름이다. 국물에서 빼면 졸일 수 있는
    물이 그만큼 줄어든 것으로 계산돼 '국물이 바닥났다' 를 일찍 본다."""
    c = _pot()
    c.start(800, 0, power=4, solid_g=200.0)
    while c.temp_c < 99.9:
        c.tick(0.25)
    s0 = c.state()
    solid0, free0 = c.solid_g, c.free_liquid_g()
    r = c.skim(40)
    solid1, free1 = c.solid_g, c.free_liquid_g()
    took_solid = solid0 - solid1
    took_free = free0 - free1
    ok = r and took_solid > took_free
    return ok, (f"40g 걷어내 고형분 -{took_solid:.1f}g · "
                f"자유 수분 -{took_free:.1f}g")


@check("가득 찬 냄비에 뚜껑을 덮고 화력을 올리면 넘칠 위험이 커진다")
def c11():
    got = {}
    for lid in (False, True):
        c = _pot()
        c.start(950, 0, power=5)      # 1인용 1000g 상한 근처
        c.set_lid(lid)
        while c.temp_c < 99.9:
            c.tick(0.25)
        got[lid] = c.overflow_risk()
    ok = got[True] > got[False] > 0
    return ok, f"뚜껑 열고 위험 {got[False]:.3f} · 덮고 {got[True]:.3f}"


# ── 기록과 재현 ─────────────────────────────────────────────────────────
@check("저장한 기록으로 다시 만들면 값이 흘러가지 않는다")
def c12():
    """이번 실측을 다음 목표로 쓰는 구조라, 제어에 편향이 있으면 회차마다
    값이 밀린다. `record_save` 는 목표를 **가정값일 때만** 실측으로 갈아
    끼워 그것을 막는다.

    그 방어가 실제로 일하는지 보려면 **없을 때와 견줘야** 한다. 방어를
    켠 쪽만 재면 "원래 안 흘렀는지 막아서 안 흘렀는지" 를 구분할 수 없다.
    """
    def run_once(target):
        c = K.COOKER
        c.deterministic = True
        c.start(700, 0, power=5)
        _boil_to(c, target)
        r = c.rest_until_still()
        c.stop()
        return r["mass_ratio"]

    # (가) 방어 없음 — 매번 실측을 다음 목표로
    K.reset([], seed=5)
    t, naive = 0.78, []
    for _ in range(6):
        t = run_once(t)
        naive.append(t)

    # (나) 실제 경로 — record_save 가 estimated 일 때만 목표를 바꾼다
    K.reset([], seed=5)
    base = {"menu": "시험", "servings": 1, "initial_mass_g": 700,
            "target_mass_ratio": 0.78, "estimated": True,
            "ingredients": [{"name": "물", "qty_g": 700}]}
    guarded = []
    for i in range(6):
        got = run_once(base["target_mass_ratio"])
        base = K.record_save(base, {"final_ratio": got, "peak_temp_c": 100.0,
                                    "cook_min": 1.0, "soil_score": 0.1})
        guarded.append(base["target_mass_ratio"])

    drift_n = abs(naive[-1] - naive[0])
    drift_g = abs(guarded[-1] - guarded[1])   # 1회차는 가정값→실측이라 제외
    ok = drift_g < 1e-9 < drift_n
    return ok, (f"방어 없으면 {naive[0]:.4f}→{naive[-1]:.4f} (표류 {drift_n:.4f}) · "
                f"실제 경로는 1회차에 {guarded[0]:.4f}로 한 번 정해진 뒤 "
                f"{guarded[-1]:.4f} 고정 (표류 {drift_g:.4f})")


@check("먹기 직전 값과 불 끌 때 값이 다르다는 것을 구분한다")
def c13():
    """사람이 먹는 것은 불 끄는 순간의 음식이 아니다. 저장은 먹기 직전
    값으로 해야 회차마다 밀리지 않는다."""
    c = _pot()
    c.start(700, 0, power=5)
    _boil_to(c, 0.80)
    off = c.state()["mass_ratio"]
    eat = c.rest_until_still()["mass_ratio"]
    ok = eat < off
    return ok, (f"불 끌 때 {off:.4f} → 먹기 직전 {eat:.4f} "
                f"(여열로 {off - eat:.4f} 더 졸았다)")


@check("기기가 바뀌어도 같은 비율에 도달한다")
def c14():
    """본가 6인용에서 만든 것을 자취방 2인용에서 재현하는 근거다.
    화력·시간을 복사하면 기기가 바뀔 때 결과가 달라지지만, 상태를
    목표로 두면 기기가 알아서 다른 시간을 쓴다."""
    outs = []
    for mass, power in ((1800, 5), (600, 3), (300, 2)):
        c = _pot()
        c.start(mass, 0, power=power)
        t = _boil_to(c, 0.78)
        outs.append((mass, power, t, c.state()["mass_ratio"]))
    spread = max(r for *_, r in outs) - min(r for *_, r in outs)
    ok = spread < 0.01 and len({round(t, 1) for *_, t, _ in outs}) > 1
    return ok, " · ".join(f"{m}g/화력{p} {t:.1f}분→{r:.4f}"
                          for m, p, t, r in outs)


# ── 공개 자료 읽기 ──────────────────────────────────────────────────────
@check("재료 이름을 잘못 자르지 않는다")
def c15():
    """사람이 읽으라고 쓴 재료 문장을 구조화한다. 이름을 잘못 자르면
    재고의 '간장' 과 대조되지 않아 **없는 재료로 잡혀 조달로 넘어간다.**

    실제로 접두어 규칙이 한 글자 접두어(간·생)를 공백 없이도 떼어내고
    있었다. 자료 100건에서 간장→장 6회, 생강→강 5회, 생크림→크림 8회.
    """
    import recipe_parse as R
    want = {"간장 10g": "간장", "생강 5g": "생강", "생크림 100g": "생크림",
            "저염간장 20g": "간장", "다진마늘 10g": "마늘",
            "간 마늘 10g": "마늘", "달걀 1개 50g": "달걀"}
    bad = [f"{t}→{R.parse_ingredients(t)[0]['name'] if R.parse_ingredients(t) else '?'}"
           f"(기대 {w})"
           for t, w in want.items()
           if not (R.parse_ingredients(t)
                   and R.parse_ingredients(t)[0]["name"] == w)]
    # 실제 자료에서 한 글자 이름이 실재 재료뿐인지도 본다
    import json
    rows = json.load(io.open("data/recipes.json", encoding="utf-8"))["recipes"]
    names = {i["name"] for r in rows
             for i in R.parse_ingredients(r.get("parts_raw") or "")}
    REAL_ONE = {"무", "꿀", "잣", "파", "배", "쌀", "떡", "밥", "물", "국"}
    odd = sorted(n for n in names if len(n) == 1 and n not in REAL_ONE)
    ok = not bad and not odd
    return ok, (f"표기 {len(want)}종 " + ("전부 맞음" if not bad else str(bad))
                + f" · 자료 {len(rows)}건에서 설명 안 되는 한 글자 이름 "
                + (str(odd) if odd else "없음"))


@check("같은 재료가 두 번 나오면 합친다")
def c16():
    """밑간용 간장 + 조림용 간장처럼 실제 레시피에 흔하다. 뒤엣것을
    버리면 양이 적게 계산된다(자료 100건에서 11건)."""
    import recipe_parse as R
    got = R.parse_ingredients("간장 10g, 간장 5g, 설탕 20g")
    d = {i["name"]: i["qty_g"] for i in got}
    ok = d.get("간장") == 15.0 and d.get("설탕") == 20.0 and len(got) == 2
    return ok, f"{got}"


def main() -> int:
    print("요리 검사 — 조리 상식이 코드에서 성립하는가")
    print()
    bad = 0
    for title, fn in CHECKS:
        try:
            ok, note = fn()
        except Exception as e:
            print(f"  !!  {title}")
            print(f"        예외 {type(e).__name__}: {e}")
            bad += 1
            continue
        print(f"  {'OK ' if ok else '!! '} {title}")
        print(f"        {note}")
        if not ok:
            bad += 1
    print()
    print(f"판정: {len(CHECKS) - bad}/{len(CHECKS)} 항목 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
