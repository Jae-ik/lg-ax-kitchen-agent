# -*- coding: utf-8 -*-
"""API 키 없이 기기 계층과 오케스트레이션을 검증한다.

주의: 여기서 '무엇을 할지' 고르는 판단은 하드코딩이다(LLM 아님).
      다만 모든 수치는 kitchen.py 시뮬레이터가 실제로 계산한 값이다.
      agent.py 를 키와 함께 돌리면 같은 도구를 LLM 이 스스로 골라 호출한다.
"""
from __future__ import annotations
import kitchen as K

W = 74


def say(who, msg):
    print(f"\n[{who}] {msg}")


def call(name, result):
    print(f"  → {name}\n    {result}")


def main():
    print("=" * W)
    print("조리 오케스트레이션 데모 — 보관 → 준비 → 조리 → 세척")
    print("=" * W)
    say("사용자", "오늘 저녁에 어머니가 저장해둔 된장찌개 해줘.")

    # ── 1) 보관 : 어떤 기록인지, 재료가 있는지 ──
    print("\n── 1. 보관 " + "─" * (W - 11))
    recs = K.record_list("된장찌개")
    call("record_list('된장찌개')", recs)
    rec_id = next(r["record_id"] for r in recs if r["saved_by"] == "어머니")
    rec = K.record_get(rec_id)
    call(f"record_get('{rec_id}')",
         {k: rec[k] for k in ("menu", "saved_by", "ingredients",
                              "initial_mass_g", "target_mass_ratio", "soil_score")})
    stock = K.fridge_list_items()
    call("fridge_list_items()", stock)

    have = {s["name"]: s for s in stock}
    missing = [i for i in rec["ingredients"] if i["name"] not in have]
    aged = [have[i["name"]] for i in rec["ingredients"]
            if i["name"] in have and have[i["name"]]["stored_days"] >= 4]

    say("에이전트",
        f"어머니가 저장한 기록({rec_id}, 만족도 {rec['satisfaction']})을 찾았습니다. "
        f"다만 냉장고에 없는 재료가 있습니다: {', '.join(m['name'] for m in missing)}.\n"
        f"        배추는 보관 {aged[0]['stored_days']}일차라 저장 당시보다 물이 더 나올 것으로 "
        f"예상됩니다.\n"
        f"        두부 없이 진행할까요, 아니면 다른 기록을 볼까요?")
    say("사용자", "두부 없이 그냥 해줘.")

    # ── 2) 준비 : 계량 ──
    print("\n── 2. 준비 " + "─" * (W - 11))
    total, extra = 0, 0.0
    for ing in rec["ingredients"]:
        if ing["name"] in have:
            w = K.prep_weigh(ing["name"], ing["qty_g"])
            call(f"prep_weigh('{ing['name']}', {ing['qty_g']})", w)
            total += w["actual_g"]
            extra += w["expected_extra_water_g"]
    water = rec["initial_mass_g"] - sum(i["qty_g"] for i in rec["ingredients"])
    total += water
    say("에이전트",
        f"계량 끝났습니다. 두부 {rec['ingredients'][1]['qty_g']}g 이 빠져 총 {total}g 으로 "
        f"시작합니다(기록은 {rec['initial_mass_g']}g).\n"
        f"        보관 기간을 반영해 수분 {extra}g 이 더 나올 것으로 보고 목표를 보정합니다.")

    # ── 3) 조리 : 상태를 보며 재계획 ──
    print("\n── 3. 조리 " + "─" * (W - 11))
    K.COOKER.start(total, extra, power=3)
    call("cooker_start()", K.COOKER.state())
    for step in range(1, 26):
        K.COOKER.tick(1.0)
        p = K.record_progress(rec_id)
        print(f"  → cooker_tick(1) → {K.COOKER.state()}")
        print(f"    record_progress → 진행 {p['progress_pct']}% / 잔여 {p['remaining_g']}g"
              f" / ETA {p['eta_min']}분")
        if p["reached"]:
            K.COOKER.stop()
            d = step - rec["cook_minutes_observed"]
            why = "두부가 빠져 총량이 줄어" if d < 0 else "물이 더 나와"
            faster = "빨리 끝났습니다" if d < 0 else "더 걸렸습니다"
            say("에이전트",
                f"목표 질량비 {p['target_mass_ratio']} 에 도달해 {step}분에 종료했습니다. "
                f"기록의 관측 시간은 {rec['cook_minutes_observed']}분인데 "
                f"{why} {abs(d)}분 {faster}.\n"
                f"        고정 시간으로 {rec['cook_minutes_observed']}분을 끓였다면 "
                f"목표를 지나쳤을 것입니다. 시간이 아니라 상태로 끝냈습니다.")
            break
        st = K.COOKER.state()
        if K.COOKER.power < 5:
            if st["temp_c"] < 99.5 and p["progress_pct"] <= 0.1:
                K.COOKER.set_power(K.COOKER.power + 1)
                print(f"    ↑ 아직 끓지 않아 화력을 {K.COOKER.power} 로 올림")
            elif p["eta_min"] and p["eta_min"] > 6:
                K.COOKER.set_power(K.COOKER.power + 1)
                print(f"    ↑ 잔여 시간이 길어 화력을 {K.COOKER.power} 로 올림")
    else:
        K.COOKER.stop()
        say("에이전트", "목표에 도달하지 못해 가열을 중단하고 확인을 요청합니다.")

    # ── 4) 세척 : 조리 이력을 넘긴다 ──
    print("\n── 4. 세척 " + "─" * (W - 11))
    course = K.dishwasher_recommend_course(rec["soil_score"])
    call(f"dishwasher_recommend_course({rec['soil_score']})", course)
    sched = K.dishwasher_schedule(course["course"], 30)
    call("dishwasher_schedule(...)", sched)
    say("에이전트",
        f"조리 기록의 눌어붙음 점수 {rec['soil_score']} 를 식기세척기에 넘겨 "
        f"'{course['course']}' 코스를 30분 뒤로 예약했습니다.\n"
        f"        이 값은 조리기만 갖고 있던 정보라, 넘기지 않으면 식기세척기는 "
        f"표준 코스를 골랐을 것입니다.")

    print("\n" + "=" * W)
    print("기기 4종이 '측정된 조리 상태 기록' 하나로 이어졌습니다.")
    print("=" * W)


if __name__ == "__main__":
    main()
