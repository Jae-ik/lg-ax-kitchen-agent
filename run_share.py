# -*- coding: utf-8 -*-
"""본가에서 만든 기록을 자취방 기기로 옮겨 그대로 만들어 본다.

    엄마가 본가 6인용 냄비로 만든 된장찌개를
    내 자취방 2인용 냄비에서 같은 상태로 재현할 수 있는가?

옮길 때 무엇을 가져가고 무엇을 버리는지가 이 제안의 핵심이다.

    가져간다  목표 질량비    — 비율이라 냄비 크기와 무관하다
    줄인다    재료량         — 용량 비율만큼
    버린다    조리 시간      — 화력이 다르면 시간도 달라야 한다

선행 특허처럼 '화력·타이머 같은 제어 입력' 을 복사하면 기기가 바뀔 때 결과도
바뀐다. 목표를 상태로 두었기 때문에 기기가 알아서 다른 시간을 쓴다.

    python run_share.py
"""
from __future__ import annotations

import kitchen as K
from skills import REGISTRY
from orchestrator import banner, W

CAPACITY = 1 / 3          # 본가 6인용 → 자취방 2인용


def cook(rec: dict, label: str) -> dict:
    """주어진 기록대로 만들어 본다. 재료는 있다고 보고 계량만 한다."""
    K.reset(keep_records=True)
    for ing in rec["ingredients"]:                 # 필요한 재료를 채워 둔다
        K.fridge_add(ing["name"], ing["qty_g"] + 50)
    total = extra = 0.0
    for ing in rec["ingredients"]:
        w = K.prep_weigh(ing["name"], ing["qty_g"])
        if w.get("ok"):
            total += w["actual_g"]; extra += w["expected_extra_water_g"]
    listed = sum(i["qty_g"] for i in rec["ingredients"])
    total += (rec.get("initial_mass_g") or listed) - listed

    K.COOKER.start(total, extra, power=3)
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio",
        target=rec["target_mass_ratio"], direction="down",
        ready_key="temp_c", ready_at=92.0, max_steps=40)
    K.COOKER.stop()
    return {"label": label, "start_g": round(total), "steps": r.output["steps"],
            "final": r.output["final"], "target": rec["target_mass_ratio"],
            "reached": r.output["reached"]}


def main():
    K.reset()
    src = K.RECORDS["rec_001"]

    banner("본가 기록을 자취방으로 옮긴다")
    print(f"  원본  {src['record_id']}  {src['menu']}  ·  {src['saved_by']}  "
          f"·  {src['device']}")
    print(f"        재료 " + ", ".join(f"{i['name']} {i['qty_g']}g"
                                     for i in src["ingredients"]))
    print(f"        초기 {src['initial_mass_g']}g  목표 질량비 "
          f"{src['target_mass_ratio']}  관측 시간 {src['cook_minutes_observed']}분")

    imp = K.record_import(src, to_device="자취방 2인용 조리기",
                          capacity_ratio=CAPACITY)
    print(f"\n  이식  {imp['record_id']}  →  {imp['device']}  (용량 {CAPACITY:.2f}배)")
    print(f"        재료 " + ", ".join(f"{i['name']} {i['qty_g']}g"
                                     for i in imp["ingredients"]))
    print(f"        초기 {imp['initial_mass_g']}g  목표 질량비 "
          f"{imp['target_mass_ratio']}  관측 시간 {imp['cook_minutes_observed']}")
    print(f"        └ 목표 질량비는 그대로, 조리 시간은 버렸다")

    a = cook(src, "본가 6인용")
    b = cook(imp, "자취방 2인용")

    print(f"\n  {'':12} {'초기 질량':>10} {'목표':>8} {'도달 시간':>10} "
          f"{'최종 질량비':>12} {'도달':>6}")
    print("  " + "-" * 64)
    for r in (a, b):
        print(f"  {r['label']:12} {r['start_g']:>9}g {r['target']:>8} "
              f"{r['steps']:>9}분 {r['final']:>12} {'예' if r['reached'] else '아니오':>6}")

    print()
    if a["reached"] and b["reached"]:
        print(f"  두 기기 모두 같은 상태({a['target']})에 도달했다.")
        print(f"  걸린 시간은 {a['steps']}분과 {b['steps']}분으로 다르다 — "
              f"양이 다르니 당연하다.")
        print("  시간을 복사했다면 작은 냄비는 지나쳤을 것이다.")
    print(f"\n  이식 이력: {imp['imported_from']}")


if __name__ == "__main__":
    main()
