# -*- coding: utf-8 -*-
"""주문 방식과 퇴근 정보가 바뀌면 4상황의 결과가 어떻게 바뀌는가.

왜 필요한가:
  주문 방식(order_mode)은 **고객의 선호**다 — 되는 것은 알아서(auto),
  매번 묻고(ask), 직접 사 간다(self). 같은 가구라도 무엇을 고르느냐에 따라
  개입 횟수와 식사까지 시간이 달라진다. 그 차이를 값으로 보여 줘야
  "선택권을 준다" 가 말이 된다.

  퇴근 정보(시각·이동 시간)가 있는 판과 없는 판을 같은 표에 둔다 — 같은
  가구끼리의 비교다. p1(야근)은 퇴근 시각을 모르는 가구라, "앎" 줄은
  **퇴근할 때 알렸다면**(persona 의 leave_if_notified) 이다.

    python mode_sensitivity.py            → mode_sensitivity.json
"""
from __future__ import annotations
import contextlib
import io
import json
import sys

import personas
import run_design
from orchestrator import Trace

MODES = ("auto", "ask", "self")
LEAVE_KEYS = ("leave_office", "commute_min", "leave_source")


def run_one(pid: str, mode: str, leave: bool) -> dict:
    base = personas.get(pid)
    p = dict(base, order_mode=mode)
    if not leave:
        for k in LEAVE_KEYS:
            p.pop(k, None)
    elif "leave_office" not in p and p.get("leave_if_notified"):
        p.update(p["leave_if_notified"])
    tmp = f"_ms_{pid}"
    personas.PERSONAS[tmp] = dict(p, id=tmp)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            r = run_design.design_for(tmp, Trace(), seed=7)
    finally:
        del personas.PERSONAS[tmp]
    m, v = r["verify"]["metrics"], r["verify"]
    return {"persona": pid, "mode": mode, "leave": leave,
            "notified": leave and "leave_office" not in base,
            "menu": m.get("메뉴"), "meal_min": m.get("식사까지(분)"),
            "budget_min": v.get("budget_min"), "touches": v["user_touches"],
            "self_buy": m.get("직접 살 것"), "asked": m.get("확인 요청"),
            "procure": m.get("조달 대기"), "replanned": v.get("replanned"),
            "verified": v["verified"],
            "why_not": (m.get("메뉴 없음") or m.get("시간 예산")
                        if not v["verified"] else None)}


def main() -> int:
    rows = [run_one(pid, mode, leave)
            for pid in personas.ids() for leave in (True, False) for mode in MODES]
    with open("mode_sensitivity.json", "w", encoding="utf-8") as f:
        json.dump({"modes": MODES, "results": rows}, f, ensure_ascii=False, indent=1)
    print("주문 방식 × 퇴근 정보별 결과 (식사까지/예산 · 개입)")
    for r in rows:
        meal = (f"{r['meal_min']}/{r['budget_min']}분" if r["meal_min"] is not None
                else "메뉴 못 정함")
        print(f"  {r['persona']:12} {'퇴근 앎' if r['leave'] else '모름':5} "
              f"{r['mode']:5} {'성립' if r['verified'] else '미달성':4} "
              f"{r['menu'] or '-':8} {meal:14} 개입 {r['touches']}"
              f"{' 재계획' if r['replanned'] else ''}"
              f"{'  직접 ' + ','.join(r['self_buy']) if r['self_buy'] else ''}")
    print("\n  mode_sensitivity.json 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
