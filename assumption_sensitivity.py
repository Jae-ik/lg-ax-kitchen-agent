# -*- coding: utf-8 -*-
"""근거를 다 찾지 못한 가정값이 결론을 바꾸는가.

왜 필요한가:
  약점으로 남은 가정값을 코드가 "맞출" 수는 없다. 할 수 있는 것은 셋이다 —
  근거 있는 값으로 바꾸기(조용 시간 끝 06:00: 층간소음 규칙의 야간 경계),
  근거가 범위만 있으면 그 범위 양 끝에서 결론이 바뀌는지 재기(식사 시간
  25~39분: 통계청 2019 생활시간조사), 근거가 없으면 가구가 정하게 열고 값에
  따라 무엇이 바뀌는지 재기(들르는 시간·위치 확인 시간). 이 파일이 뒤의 둘을 잰다.

    python assumption_sensitivity.py      → assumption_sensitivity.json
"""
from __future__ import annotations
import contextlib
import io
import json
import sys

import personas
import run_design
import thinq
from orchestrator import Trace

EAT = (25, 30, 39)          # 통계청 2019: 한 끼 25분(평일 아침)~39분(토요일 저녁)
DETOUR = (10, 15, 20, 25)   # 근거 없음 — 퇴근길에 들러 사는 데 더 드는 분
DWELL = (0, 3, 5, 10)       # 근거 없음 — 위치로 퇴근을 판단하기 전 기다리는 분
COMMUTE = (30, 37, 40)      # 위치 판단이 성패를 가르는 이동 시간들


def _design(pid: str, **over) -> dict:
    p = dict(personas.get(pid), **over)
    tmp = f"_as_{pid}"
    personas.PERSONAS[tmp] = dict(p, id=tmp)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return run_design.design_for(tmp, Trace(), seed=7)
    finally:
        del personas.PERSONAS[tmp]


def eat_rows() -> list:
    rows = []
    for pid in personas.ids():
        for e in EAT:
            r = _design(pid, prefs={"eat_min": e})
            m = r["verify"]["metrics"]
            wash = [b["at"] for b in r["scenario"]["beats"]
                    if b["verified_by"] == "aftercare"]
            note = m.get("소음 조치") or ""
            rows.append({"persona": pid, "eat_min": e,
                         "wash_at": wash[0] if wash else None,
                         "quiet": ("저소음 전환" if "저소음으로 전환" in note else
                                   "이미 조용" if "이미 조용" in note else
                                   "조치 불필요" if note else "세척 없음"),
                         "course": m.get("세척 코스")})
    return rows


def detour_rows() -> list:
    rows = []
    for pid in personas.ids():
        for d in DETOUR:
            r = _design(pid, order_mode="self", prefs={"shop_detour_min": d})
            m, v = r["verify"]["metrics"], r["verify"]
            rows.append({"persona": pid, "detour_min": d,
                         "meal_min": m.get("식사까지(분)"),
                         "budget_min": v.get("budget_min"),
                         "verified": v["verified"], "self_buy": m.get("직접 살 것")})
    return rows


def dwell_rows() -> list:
    """두부가 없고 예산 25분인 가구(check_thinq n4 와 같은 상황)."""
    food = "25분 안에 먹어야 해. 냉장고에 배추랑 된장 있어"
    loc = [{"at": "18:40", "kind": "exit", "place": "office"}]
    rows = []
    for c in COMMUTE:
        for d in DWELL:
            o = thinq.run(food, location=loc,
                          profile={"order_mode": "auto", "commute_min": c,
                                   "location_consent": True, "location_dwell_min": d})
            v = o["result"]["verify"]
            rows.append({"commute_min": c, "dwell_min": d, "verified": v["verified"],
                         "meal_min": v["metrics"].get("식사까지(분)")})
    return rows


def main() -> int:
    out = {"eat": eat_rows(), "detour": detour_rows(), "dwell": dwell_rows()}
    with open("assumption_sensitivity.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("식사 시간(분) → 세척 시작 · 소음 판단 · 코스")
    for r in out["eat"]:
        print(f"  {r['persona']:12} {r['eat_min']:3}분  {r['wash_at']}  {r['quiet']:8} {r['course']}")
    print("\n직접 장보기 들르는 시간(분) → 식사까지/예산")
    for r in out["detour"]:
        print(f"  {r['persona']:12} {r['detour_min']:3}분  "
              f"{r['meal_min']}/{r['budget_min']}  {'성립' if r['verified'] else '미달성'}")
    print("\n위치 확인 시간(분) × 이동 시간 → 성립 (두부 없는 25분 가구)")
    for r in out["dwell"]:
        print(f"  이동 {r['commute_min']}분 · 확인 {r['dwell_min']}분 → "
              f"{'성립' if r['verified'] else '실패'} {r['meal_min']}")
    print("\n  assumption_sensitivity.json 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
