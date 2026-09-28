# -*- coding: utf-8 -*-
"""어느 상수가 결과를 지배하는가 — 민감도.

왜 이것이 중요한가:
  `kitchen.py` 의 상수는 출처가 세 가지다.
      측정에서 역산   C_WATER · LATENT · POT_EQ_G · WATT_PER_POWER ·
                      LOSS_W_PER_K · LID_LOSS · LID_EVAP · COOK_BASE_C ·
                      ABSORB_BASE_C
      실제 조리 관찰  REST_MIN
      시뮬레이터 가정 SURF_EVAP · ABSORB_RATE · STIR_RELIEF ·
                      LID_OVERFLOW · SKIM_SOLID

  제안은 "측정에서 역산한 값으로 돈다" 고 말한다. 그런데 **결과를
  지배하는 것이 가정값이면 그 말은 약해진다.** 그래서 상수를 하나씩
  흔들어 결과가 얼마나 움직이는지 잰다.

공정하게 재는 법:
  뚜껑을 안 덮는 시나리오에서 LID_EVAP 을 흔들면 당연히 0 이 나온다.
  그러면 "뚜껑 상수는 영향이 없다" 는 틀린 결론이 된다. 그래서
  **각 상수가 실제로 쓰이는 시나리오**를 따로 두고 거기서 잰다.

주의 (실제로 여기서 한 번 속았다):
  `K.reset()` 은 모듈 전역 COOKER 를 재사용한다. 예전에는 상수를
  되돌리지 않아, 앞 항목에서 바꾼 값이 다음 항목에 남았다. 그래서
  뚜껑을 쓰지도 않는 실행에서 LID_EVAP 이 결과를 바꾸는 것처럼 보였다.
  지금은 reset 이 상수도 되돌리며, 이 검사가 그것을 함께 확인한다.
"""
from __future__ import annotations
import sys

import kitchen as K

C = K.Cooker

GUESSED = {"SURF_EVAP", "ABSORB_RATE", "STIR_RELIEF", "LID_OVERFLOW",
           "SKIM_SOLID", "REST_MIN"}

BUMP = 1.20          # 상수를 20% 올려 본다

# **이미 알고 있는 약한 자리.** 숨기지 않고 적어 두고, 여기 없는 항목이
# 새로 커지면 실패로 잡는다. (매번 빨간불이면 검사를 안 보게 된다)
KNOWN_RISKY = {
    # 예전에는 STIR_RELIEF 가 여기 있었다. 0.55→0.66 이면 눌어붙음이
    # 0.5404→0.6066 이 되어 **세척 코스가 표준에서 강력으로 뒤집혔다**
    # (물 11.0L→16.0L). 실측을 구하려 했지만 가정 조리 조건에 맞는
    # 정량 데이터를 공개 문헌에서 찾지 못했다.
    #
    # 그래서 값을 지어내는 대신 **불확실하다는 사실을 값에 실었다.**
    # 교반이 덜어낸 몫에 STIR_RELIEF_UNCERTAINTY(±20%)를 곱해
    # soil_sigma 에 더하고, aftercare 가 그 분포로 기대 비용을 최소화해
    # 코스를 고른다. 이제 0.44·0.55·0.66 어느 값이든 '표준 11.0L' 이다.
    #
    # 남은 사실: **눌어붙음 점수 자체는 여전히 상수에 흔들린다**
    # (0.4631~0.6066). 출하물(코스·물)이 안정될 뿐이다. 점수를 그대로
    # 인용하려면 이 폭을 함께 말해야 한다.
}



# ── 시나리오 : 각 상수가 실제로 쓰이는 판 ───────────────────────────────
def s_plain():
    """뚜껑 없이 졸이기 — 열 모델 상수가 지배하는 판."""
    c = K.COOKER
    c.start(700, 0, power=4, solid_g=150.0)
    t = 0.0
    while c.state()["mass_ratio"] > 0.78 and t < 90:
        c.tick(0.05)
        t += 0.05
    r = c.rest_until_still()
    return {"시간": t, "최종": r["mass_ratio"], "눌어붙음": c.state()["soil_score"]}


def s_lid():
    """뚜껑을 덮고 끓인 뒤 열어 졸이기 — 뚜껑 상수가 쓰이는 판."""
    c = K.COOKER
    c.start(900, 0, power=4, solid_g=150.0)
    c.set_lid(True)
    t = 0.0
    while c.temp_c < 99.9 and t < 60:
        c.tick(0.05)
        t += 0.05
    risk = c.overflow_risk()
    for _ in range(40):
        c.tick(0.1)
        t += 0.1
    return {"끓기까지": t, "질량비": c.state()["mass_ratio"], "넘침위험": risk}


def s_skim():
    """거품을 걷어내며 졸이기 — SKIM_SOLID 가 쓰이는 판."""
    c = K.COOKER
    c.start(800, 0, power=4, solid_g=250.0)
    while c.temp_c < 99.9:
        c.tick(0.1)
    for i in range(20):
        if i % 5 == 0:
            c.skim(15)
        c.tick(0.25)
    s = c.state()
    return {"질량비": s["mass_ratio"], "자유수분비": s["free_ratio"],
            "눌어붙음": s["soil_score"]}


def s_absorb():
    """물을 빨아들이는 재료 — ABSORB_* 가 쓰이는 판."""
    c = K.COOKER
    c.start(1000, 0, power=4, absorb_cap_g=350.0, solid_g=200.0)
    for _ in range(60):
        c.tick(0.25)
    s = c.state()
    return {"흡수량": s["absorbed_g"], "자유수분": s["free_liquid_g"],
            "눌어붙음": s["soil_score"]}


def s_stir():
    """저으며 졸이기 — STIR_RELIEF 가 쓰이는 판.

    **출하물은 눌어붙음 점수가 아니라 세척 코스와 물 사용량이다.**
    점수는 중간 지표다(2026-08-14 "판정은 항상 출하 형식으로"). 그래서
    여기서 aftercare 까지 돌려 실제로 고른 코스를 본다.

    교반 상수가 실측이 아니므로 그 불확실성이 soil_sigma 에 실려 오고,
    코스는 그 분포로 기대 비용을 최소화해 골라진다 — 상수가 얼마쯤
    틀려도 코스가 경계에서 뒤집히지 않는다.
    """
    from skills import REGISTRY
    c = K.COOKER
    c.start(500, 0, power=5, solid_g=200.0)
    while c.temp_c < 99.9:
        c.tick(0.1)
    for i in range(16):
        if i % 2 == 0:
            c.stir()
        c.tick(0.5)
    st = c.state()
    r = REGISTRY.get("aftercare").run(soil_score=st["soil_score"],
                                      soil_sigma=st["soil_sigma"])
    # aftercare 는 강도를 숫자로 내주지 않으므로 코스표 순서로 환산한다.
    # (없는 키를 get 으로 읽어 늘 0 이 되면 그 지표는 아무것도 검증하지
    #  못한다 — 오늘 물만 넣고 눌어붙음을 잰 시험이 그랬다)
    ORDER = {"에코": 0.0, "표준": 1.0, "강력(불림 포함)": 2.0}
    return {"물(L)": r.output["water_l"],
            "코스 강도": ORDER.get(r.output["course"], -1.0),
            "눌어붙음": st["soil_score"]}


def s_rest():
    """불을 끄고 두기 — REST_MIN 과 여열 상수가 쓰이는 판."""
    c = K.COOKER
    c.start(700, 0, power=5, solid_g=150.0)
    while c.state()["mass_ratio"] > 0.82:
        c.tick(0.1)
    off = c.state()["mass_ratio"]
    r = c.rest_until_still()
    return {"끌때": off, "먹을때": r["mass_ratio"],
            "여열차": off - r["mass_ratio"]}


# (시나리오, 쓰이는 상수들, **출하 지표**)
#
# 판정은 반드시 출하 지표로 한다. 중간 지표의 상대 변화로 재면 절대값이
# 작은 항목이 과대평가된다 — 처음에 "여열차"(0.0047)로 쟀더니 REST_MIN 이
# 27.66% 로 1위였지만, 그것이 최종 질량비에 미치는 영향은 0.0013 이다.
# (2026-08-14 "채택·기각 판정은 항상 출하 형식으로" 와 같은 자리)
#
# 출하 지표의 의미
#   질량비  사용자가 먹을 때의 졸임 정도. 허용 오차 0.10 안에 들어야 한다
#   눌어붙음 세척 코스를 고르는 값. 코스 경계는 0.30 / 0.55
SCENES = [
    ("졸이기", s_plain, ["WATT_PER_POWER", "LATENT_J_PER_G", "C_WATER",
                       "POT_EQ_G", "LOSS_W_PER_K", "SURF_EVAP"],
     {"최종": 0.10, "눌어붙음": 0.10}),
    ("뚜껑", s_lid, ["LID_LOSS", "LID_EVAP", "LID_OVERFLOW"],
     {"질량비": 0.10, "넘침위험": 0.20}),
    ("거품 걷기", s_skim, ["SKIM_SOLID"],
     {"질량비": 0.10, "눌어붙음": 0.10}),
    ("흡수 재료", s_absorb, ["ABSORB_RATE", "ABSORB_BASE_C"],
     {"자유수분": 80.0, "눌어붙음": 0.10}),
    # 출하 지표는 물 사용량과 코스 강도다. 물 ±3L 는 한 코스 차이,
    # 강도 ±1 은 코스가 한 단계 바뀌는 폭이다.
    ("교반", s_stir, ["STIR_RELIEF"], {"물(L)": 3.0, "코스 강도": 1.0}),
    ("여열", s_rest, ["REST_MIN", "SURF_EVAP", "LOSS_W_PER_K"],
     {"먹을때": 0.10}),
]


def _run(scene, over=None):
    K.reset([], seed=11)
    c = K.COOKER
    c.deterministic = True
    for k, v in (over or {}).items():
        setattr(c, k, v)
    out = scene()
    K.reset([], seed=11)          # 상수까지 되돌린다
    return out


def _delta(base, got, ship):
    """출하 지표가 **허용 폭 대비** 얼마나 움직였는가 (%).

    허용 폭은 그 지표가 실제로 판정에 쓰이는 여유다(질량비 허용 오차
    0.10, 세척 코스 경계 간격 등). 상대 변화 대신 이것을 쓰면 절대값이
    작은 지표가 과대평가되지 않는다.
    """
    worst, detail = 0.0, ""
    for k, tol in ship.items():
        if k not in base:
            continue
        d = abs(got.get(k, base[k]) - base[k])
        pct = d / tol * 100
        if pct > worst:
            worst, detail = pct, f"{k} {base[k]:.4g}→{got.get(k, base[k]):.4g}"
    return worst, detail


def main() -> int:
    print("민감도 — 어느 상수가 결과를 지배하는가")
    print(f"  상수를 {BUMP:.0%} 로 올려 지표가 얼마나 움직이는지 잰다.")
    print("  각 상수는 **그것이 실제로 쓰이는 시나리오**에서만 잰다.")
    print()

    bad = 0

    # 0) reset 이 상수를 되돌리는가 — 이 검사 자체의 전제
    K.COOKER.LID_EVAP = 99.0
    K.COOKER.LOSS_W_PER_K = 0.01
    K.reset([], seed=1)
    if (K.COOKER.LID_EVAP, K.COOKER.LOSS_W_PER_K) != (C.LID_EVAP,
                                                      C.LOSS_W_PER_K):
        print("  !!  reset() 이 물리 상수를 되돌리지 않는다 — "
              "이 검사의 결과를 믿을 수 없다")
        return 1
    print("  OK  reset() 이 물리 상수를 기본값으로 되돌린다 (검사의 전제)")
    print()

    rows = []
    for title, scene, consts, ship in SCENES:
        base = _run(scene)
        line = " · ".join(f"{k} {v:.4g}" for k, v in base.items())
        print(f"  [{title}] {line}")
        print(f"       출하 지표 허용 폭: "
              + " · ".join(f"{k} ±{v:g}" for k, v in ship.items()))
        for name in consts:
            v0 = getattr(C, name)
            got = _run(scene, {name: v0 * BUMP})
            d, what = _delta(base, got, ship)
            rows.append((d, name, title))
            src = "가정" if name in GUESSED else "역산"
            print(f"       {name:16s}({src}) {v0:>8.4g} → 허용 폭의 "
                  f"{d:6.2f}%  {what}")
        print()

    # 판정 : 결과를 가장 크게 움직이는 상수들이 역산 출처인가
    rows.sort(reverse=True)
    top = rows[:5]
    guessed_in_top = [n for _, n, _ in top if n in GUESSED]
    print("출하 지표를 가장 크게 움직이는 상수 5개 (허용 폭 대비)")
    for d, n, t in top:
        print(f"   {d:6.2f}%  {n:16s} ({'가정' if n in GUESSED else '역산'}, {t})")
    print()

    # 판정: 가정값이 출하 지표를 허용 폭의 절반 넘게 움직이면 위험하다.
    risky = [(d, n) for d, n, _ in rows if n in GUESSED and d > 50.0]
    new_risky = [(d, n) for d, n in risky if n not in KNOWN_RISKY]
    if new_risky:
        print(f"  !!  **새로** 가정값이 출하 지표를 허용 폭의 절반 넘게 "
              f"움직인다: " + " · ".join(f"{n} {d:.1f}%" for d, n in new_risky))
        print("        알려진 한계 목록(KNOWN_RISKY)에 없는 항목이다. "
              "원인을 보고 목록에 넣든지 상수를 실측으로 바꿔야 한다.")
        bad += 1
    else:
        for d, n in risky:
            print(f"  ·   {n} {d:.1f}% — 이미 아는 한계")
            print(f"        {KNOWN_RISKY[n]}")
        worst_g = max((d for d, n, _ in rows if n in GUESSED
                       and n not in KNOWN_RISKY), default=0.0)
        print(f"  OK  그 밖의 가정값이 출하 지표를 움직이는 폭은 최대 "
              f"허용 폭의 {worst_g:.1f}% 다 — 결과를 지배하지 않는다")
        if guessed_in_top:
            print(f"        (상대 변화만 보면 {guessed_in_top} 가 커 보이지만, "
                  f"그 지표들의 절대값이 작다)")

    # 가정값이 실제로 얼마나 움직이는지도 숫자로 남긴다
    g = [(d, n) for d, n, _ in rows if n in GUESSED]
    if g:
        print(f"        가정값들의 최대 영향: "
              + " · ".join(f"{n} {d:.2f}%" for d, n in sorted(g, reverse=True)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
