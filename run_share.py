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

# 용량 비율은 **적지 않는다.** 기기 제원(kitchen.DEVICES)에서 계산한다.
# 냄비가 바뀌면 이 파일을 고칠 필요 없이 대상 기기 이름만 달라진다.
TARGETS = ["자취방 2인용 조리기", "원룸 1인용 조리기"]


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

    K.COOKER.start(total, extra, power=3, capacity_g=3000)
    tgt = rec["target_mass_ratio"]
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio",
        target=tgt, direction="down",
        ready_key="temp_c", ready_at=99.5, max_steps=400, max_minutes=60,
        # 파이프라인과 같은 보정을 쓴다. 여기만 빼 두면 작은 냄비의 오차가
        # 실제보다 커 보여서, 이식이 안 되는 것처럼 읽힌다.
        residual=lambda st: (K.COOKER.predict_residual_g()
                             / max(1.0, st["initial_mass_g"])))
    # 먹기 직전 상태로 재고, 지나쳤으면 물로 되돌린다 — 파이프라인과 같다.
    rested = K.COOKER.rest_until_still()
    if rested["mass_ratio"] < tgt - 1e-3:
        need = (tgt - rested["mass_ratio"]) * rested["initial_mass_g"]
        if need / max(1.0, rested["mass_g"]) <= 0.06:
            K.COOKER.add_water(need)
            rested = K.COOKER.state()
    K.COOKER.stop()
    return {"label": label, "start_g": round(total), "steps": r.output["steps"],
            "final": rested["mass_ratio"], "target": tgt,
            "reached": abs(tgt - rested["mass_ratio"]) <= 0.10}


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

    rows = [cook(src, "본가 6인용(원본)")]
    for dev in TARGETS:
        imp = K.record_import(src, to_device=dev)       # 비율을 주지 않는다
        print()
        print(f"  이식  {imp['record_id']}  →  {imp['device']}")
        print(f"        근거  {imp['scale_basis']}")
        print(f"        재료 " + ", ".join(f"{i['name']} {i['qty_g']}g"
                                         for i in imp["ingredients"]))
        print(f"        초기 {imp['initial_mass_g']}g  목표 질량비 "
              f"{imp['target_mass_ratio']}  관측 시간 {imp['cook_minutes_observed']}")
        if imp.get("scale_warning"):
            print(f"        ! {imp['scale_warning']}")
        rows.append(cook(imp, dev.replace(" 조리기", "")))

    print()
    print(f"  {'':16} {'초기 질량':>10} {'목표':>8} {'도달 시간':>10} "
          f"{'최종 질량비':>12} {'도달':>6}")
    print("  " + "-" * 70)
    for r in rows:
        print(f"  {r['label']:16} {r['start_g']:>9}g {r['target']:>8} "
              f"{r['steps']:>9}분 {r['final']:>12} "
              f"{'예' if r['reached'] else '아니오':>6}")

    print()
    if all(r["reached"] for r in rows):
        print(f"  기기 {len(rows)}대가 모두 같은 상태({rows[0]['target']})에 도달했다.")
        print("  걸린 시간은 " + " · ".join(f"{r['steps']}분" for r in rows)
              + " 로 다르다 — 양이 다르니 당연하다.")
        print("  시간을 복사했다면 작은 냄비는 지나쳤을 것이다.")
    else:
        for r in rows:
            if not r["reached"]:
                print(f"  ! {r['label']} 는 목표에 도달하지 못했다 "
                      f"(최종 {r['final']}) — 한 주기에 지나치는 양이다")
    print()
    print("  용량 비율은 코드 어디에도 적혀 있지 않다 — 기기 제원에서 계산했다.")


if __name__ == "__main__":
    main()
