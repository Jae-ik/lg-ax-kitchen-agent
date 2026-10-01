# -*- coding: utf-8 -*-
"""주문이 계획대로 안 되면 — 결제 실패·상점 취소·배송 지연.

왜 필요한가:
  선제 주문(퇴근길에 주문해 이동 중에 받는다)이 제안의 핵심인데, 지금까지는
  주문하면 **늘 제시간에 왔다.** 가장 약한 가정이었다(2026-10-01).

  주문하는 세 가구(맞벌이·알레르기·퇴근길)의 첫 주문 품목에 사고를 넣는다.
  에이전트는 사고를 **알게 되는 시점에** 대응한다 — 결제 실패는 주문하는 순간
  (다른 결제수단으로 다시 할지 묻는다), 상점 취소는 주문 10분 뒤(가정 — 즉시배송
  상점이 하나뿐이라 퇴근길에 들러 사 달라고 묻는다), 지연은 도착할 때(이동 시간 안이면
  묻히고, 넘치면 집에서 기다린다). 사람이 거절하면 사지 않는다.

    python delivery_gap.py        → delivery_gap.json
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

CASES = [
    ("결제 실패 · 다시 승인", {"payment_fail": True}, True),
    ("결제 실패 · 거절", {"payment_fail": True}, False),
    ("상점 취소 · 들러 사 줌", {"cancelled": True}, True),
    ("상점 취소 · 거절", {"cancelled": True}, False),
    ("배송 10분 지연", {"late_min": 10}, True),
    ("배송 40분 지연", {"late_min": 40}, True),
]


def run(pid: str, reality: dict | None = None, yes: bool = True) -> dict:
    store.set_order_reality(reality)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            r = run_design.design_for(pid, Trace(), seed=7,
                                      approve_purchase=lambda items: yes)
    finally:
        store.set_order_reality(None)
    m, v = r["verify"]["metrics"], r["verify"]
    return {"verified": v["verified"], "menu": m.get("메뉴"),
            "meal_min": m.get("식사까지(분)"), "budget_min": v.get("budget_min"),
            "touches": v["user_touches"], "issues": m.get("주문 사고") or [],
            "replanned": v.get("replanned"),
            "bought": (m.get("확인 후 승인") or []) + ([] if not m.get("자동 주문(원)") else ["(자동)"])}


def first_order(pid: str) -> str | None:
    """기본 실행에서 처음 주문한(또는 승인받아 산) 품목."""
    import kitchen_domain as KD
    names = []
    orig = KD.K.fridge_add

    def spy(name, qty, *a, **k):
        names.append(name)
        return orig(name, qty, *a, **k)
    KD.K.fridge_add = spy
    try:
        run(pid)
    finally:
        KD.K.fridge_add = orig
    return names[0] if names else None


def label(r: dict, base: dict) -> str:
    if not r["verified"]:
        if r["meal_min"] is None:
            return "저녁 실패"
        return f"예산 초과 {r['meal_min']}/{r['budget_min']}분"
    bits = [f"{r['meal_min']}분"]
    if r["menu"] != base["menu"]:
        bits.append(f"메뉴 변경({r['menu']})")
    if r["touches"] != base["touches"]:
        bits.append(f"개입 {base['touches']}→{r['touches']}")
    return "성립 " + " · ".join(bits)


def main() -> int:
    rows = []
    for pid in ("p2_맞벌이", "p3_알레르기", "p4_퇴근길"):
        item = first_order(pid)
        base = run(pid)
        for name, real, yes in CASES:
            r = run(pid, {item: real}, yes)
            rows.append({"persona": pid, "item": item, "case": name,
                         "label": label(r, base), "issues": r["issues"],
                         "verified": r["verified"], "meal_min": r["meal_min"],
                         "base_meal_min": base["meal_min"], "touches": r["touches"]})
    with open("delivery_gap.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("주문 사고 — 첫 주문 품목에 넣었을 때")
    for r in rows:
        print(f"  {r['persona']:12} {r['item']:4} {r['case']:14} [{r['label']}]")
        for i in r["issues"]:
            print(f"      · {i}")
    print("\n  delivery_gap.json 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
