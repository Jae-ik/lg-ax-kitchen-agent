# -*- coding: utf-8 -*-
"""실행 시연 — 두 시나리오.

시나리오 1  주방   : inventory → menu → procure → converge(조리기) → aftercare(식기세척기)
시나리오 2  세탁실 : converge(건조기) → aftercare(세탁기)

두 시나리오는 converge 와 aftercare 를 **같은 코드 그대로** 쓴다.
기기와 주입 함수만 다르다. 이것이 '재사용 방안'의 실물 증거다.
"""
from __future__ import annotations
import kitchen as K
import dryer as D
from skills import REGISTRY
from orchestrator import Trace, banner, run_skill, W

CATALOG = {"두부": 2800, "대파": 1900, "표고버섯": 4500, "한우등심": 32000}
KNOWN = ["두부", "대파"]          # 이전에 산 적 있는 품목
AVOID = ["표고버섯"]              # 가구원 기피·알레르기


def scenario_kitchen(trace):
    banner("시나리오 1 · 주방 — 사용자 지시 없이 시작한다 (Zero-Touch)")

    # ── GOAL : 사용자가 아무것도 하지 않았다 ──
    inv = run_skill(REGISTRY, trace, "inventory",
                    items=K.fridge_list_items(), urgency_ratio=0.6)
    urgent_names = [i["name"] for i in inv.output["urgent"]]
    days_left = min((i["days_left"] for i in inv.output["urgent"]), default=99)
    trace.stage("GOAL", f"소진 임박 재료를 {days_left}일 안에 사용한다 — "
                        f"{', '.join(urgent_names)}",
                {"trigger": "재고 상태 변화", "user_request": None})

    # ── PLAN ──
    trace.stage("PLAN", "메뉴 결정 → 부족분 조달 → 조리 → 세척",
                {"skills": ["menu", "procure", "converge", "aftercare"]})

    # ── 메뉴 ──
    menu = run_skill(REGISTRY, trace, "menu",
                     records=list(K.RECORDS.values()),
                     stock=K.fridge_list_items(), prefer_items=urgent_names)
    best = menu.output["best"]
    rec = K.record_get(best["record_id"])

    # ── 조달 ──
    proc = run_skill(REGISTRY, trace, "procure",
                     missing=best["missing"], catalog=CATALOG,
                     known_items=KNOWN, avoid=AVOID, auto_limit_krw=15000)
    if proc.output["need_confirm"]:
        trace.stage("OUTPUT", "사용자 확인이 필요한 항목이 있어 멈춘다",
                    proc.output["need_confirm"])
    ordered = {a["name"] for a in proc.output["auto_ordered"]}
    for name in ordered:                      # 주문한 품목을 재고에 반영
        need = next((i["qty_g"] for i in rec["ingredients"] if i["name"] == name), 150)
        K.fridge_add(name, need)

    # ── 준비 : 계량 ──
    total, extra = 0.0, 0.0
    for ing in rec["ingredients"]:
        if ing["name"] in ordered or K.fridge_check(ing["name"]):
            w = K.prep_weigh(ing["name"], ing["qty_g"])
            if w.get("ok"):
                total += w["actual_g"]; extra += w["expected_extra_water_g"]
    total += rec["initial_mass_g"] - sum(i["qty_g"] for i in rec["ingredients"])
    trace.stage("EXECUTE", f"계량 완료 — 총 {round(total)}g (기록 {rec['initial_mass_g']}g), "
                           f"추가 수분 {round(extra,1)}g 예상")

    # ── 조리 : converge 스킬 ──
    K.COOKER.start(total, extra, power=3)
    conv = run_skill(
        REGISTRY, trace, "converge",
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio",
        target=rec["target_mass_ratio"], direction="down",
        ready_key="temp_c", ready_at=92.0, max_steps=30)
    K.COOKER.stop()
    saved_min = rec["cook_minutes_observed"] - conv.output["steps"]

    # ── 세척 : aftercare 스킬 ──
    after = run_skill(REGISTRY, trace, "aftercare",
                      soil_score=rec["soil_score"], profile="dishwasher")

    trace.stage("OUTPUT",
                f"{rec['menu']}({rec['saved_by']} 기록) 완료 — "
                f"{conv.output['steps']}분에 목표 {conv.output['target']} 도달",
                {"고정시간_대비_가열_절감_분": saved_min,
                 "세척코스": after.output["course"],
                 "기본코스_대비_기대물_절감_L": after.output["saved_l"],
                 "사용자_터치_횟수": 1 if proc.output["need_confirm"] else 0})
    return conv, after


def scenario_laundry(trace):
    banner("시나리오 2 · 세탁실 — 같은 스킬, 다른 기기")

    trace.stage("GOAL", "빨래 함수율을 8%까지 낮춘다",
                {"trigger": "세탁 종료 이벤트", "user_request": None})
    trace.stage("PLAN", "건조 → 세탁조 관리", {"skills": ["converge", "aftercare"]})

    D.DRYER.start(moisture=0.18, power=2)
    conv = run_skill(
        REGISTRY, trace, "converge",
        observe=lambda: D.DRYER.state(), actuate=D.DRYER.set_power,
        step=D.DRYER.tick, metric="moisture", target=0.08, direction="down",
        ready_key="drum_temp_c", ready_at=45.0, max_steps=40)
    D.DRYER.stop()

    after = run_skill(REGISTRY, trace, "aftercare", soil_score=0.36, profile="washer")

    trace.stage("OUTPUT",
                f"건조 완료 — {conv.output['steps']}분에 함수율 {conv.output['final']} 도달",
                {"세탁조코스": after.output["course"],
                 "기본코스_대비_기대물_절감_L": after.output["saved_l"]})
    return conv, after


def main():
    trace = Trace()
    print("=" * W)
    print("등록된 스킬")
    print("=" * W)
    for s in REGISTRY.list():
        print(f"  {s['name']:10} {s['description'][:46]}…")
        if s["reusable_for"]:
            print(f"{'':13}재사용: {', '.join(s['reusable_for'])}")

    c1, a1 = scenario_kitchen(trace)
    c2, a2 = scenario_laundry(trace)

    banner("재사용 증거")
    print(f"  converge  조리기 {c1.output['steps']}단계로 질량비 {c1.output['final']} 도달")
    print(f"            건조기 {c2.output['steps']}단계로 함수율 {c2.output['final']} 도달")
    print(f"            → 스킬 코드 동일. 관측·액추에이터 함수만 교체")
    print(f"  aftercare 식기세척기 '{a1.output['course']}' / 세탁기 '{a2.output['course']}'")
    print(f"            → 스킬 코드 동일. 코스 프로파일만 교체")

    trace.dump("trace.json")
    print("\n실행 로그를 trace.json 에 저장했습니다.")


if __name__ == "__main__":
    main()
