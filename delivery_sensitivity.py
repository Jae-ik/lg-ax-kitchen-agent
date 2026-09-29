# -*- coding: utf-8 -*-
"""즉시배송 시간이 바뀌면 4상황의 결과가 어떻게 바뀌는가.

왜 필요한가:
  즉시배송 시간은 상황 판단(조달을 넣을까)과 선제 주문(퇴근 이동 시간 안에
  오나)을 함께 가른다. 처음엔 근거 없이 20분이었고, 지금은 B마트 평균
  27분(CEO스코어데일리 2025-04)이다. B마트가 내건 "1시간 내" 는 상한이다.
  "20분의 근거가 뭐냐" 에 답하려면 **값을 바꿨을 때 무엇이 바뀌는지**를
  보여 줘야 한다.

    python delivery_sensitivity.py            → delivery_sensitivity.json
"""
from __future__ import annotations
import contextlib
import io
import json
import sys

import personas
import run_design
import store
from orchestrator import Trace

MINUTES = (20, 27, 45, 60)


def measure(minutes: int) -> list:
    quick = store.STORES[0]
    old = quick.delivery_min
    quick.delivery_min = minutes
    run_design.STAGE_COSTS["조달"] = store.min_delivery_min()
    rows = []
    try:
        for pid in personas.ids():
            with contextlib.redirect_stdout(io.StringIO()):
                r = run_design.design_for(pid, Trace(), seed=7)
            m, v = r["verify"]["metrics"], r["verify"]
            rows.append({
                "persona": pid, "menu": m.get("메뉴"),
                "procure": "procure" in r["flow"]["steps"],
                "preorder": "기다림 없음" in str(m.get("조달 대기", "")),
                "meal_min": m.get("식사까지(분)"), "budget_min": v.get("budget_min"),
                "replanned": v.get("replanned"), "verified": v["verified"],
                "why_not": (m.get("메뉴 없음") or m.get("시간 예산")
                            if not v["verified"] else None)})
    finally:
        quick.delivery_min = old
        run_design.STAGE_COSTS["조달"] = store.min_delivery_min()
    return rows


def main() -> int:
    out = {str(mn): measure(mn) for mn in MINUTES}
    with open("delivery_sensitivity.json", "w", encoding="utf-8") as f:
        json.dump({"current_min": store.QUICK_DELIVERY_MIN, "results": out},
                  f, ensure_ascii=False, indent=1)
    print(f"즉시배송 시간별 결과 (현재 {store.QUICK_DELIVERY_MIN}분)")
    for mn, rows in out.items():
        print(f"\n  {mn}분")
        for r in rows:
            meal = (f"{r['meal_min']}/{r['budget_min']}분" if r["meal_min"]
                    else "메뉴 못 정함")
            print(f"    {r['persona']:12} {'성립' if r['verified'] else '미달성':4} "
                  f"{r['menu'] or '-':8} {meal:14} "
                  f"{'재계획 ' if r['replanned'] else ''}"
                  f"{'선제주문' if r['preorder'] else ''}")
    print("\n  delivery_sensitivity.json 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
