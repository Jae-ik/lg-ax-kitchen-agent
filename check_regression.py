# -*- coding: utf-8 -*-
"""회귀 검사 — 단계 수만 보지 않는다.

단계 수 7 은 '계획이 7단계' 라는 뜻이지 '7단계가 다 돌았다' 는 뜻이 아니다.
실제로 한 번 이것만 보고 통과시켰다가, 조리가 통째로 빠진 것을 놓쳤다.
그래서 실행 로그의 ok 와 최종 수치까지 함께 본다.

    python check_regression.py
"""
from __future__ import annotations
import json
import sys

EXPECT = {
    "p1_야근":    {"steps": 6, "touches": 0, "beats": "4/4", "cooked": True},
    "p2_맞벌이":  {"steps": 7, "touches": 0, "beats": "4/4", "cooked": True},
    "p3_알레르기": {"steps": 7, "touches": 2, "beats": "5/5", "cooked": True},
    "p4_퇴근길":  {"steps": 7, "touches": 0, "beats": "4/4", "cooked": True},
}
SKIP_OK = {"procure"}          # 확인 요청은 실패가 아니라 설계된 정지


def main():
    rows = json.load(open("scenarios.json", encoding="utf-8"))
    bad = 0
    # 실행이 중간에 죽으면 파일은 **앞 실행 내용 그대로** 남는다.
    # 개수부터 보지 않으면 옛 결과를 새 결과로 착각한다.
    if len(rows) != len(EXPECT):
        print(f"  !! scenarios.json 에 {len(rows)}개뿐 — "
              f"{len(EXPECT)}개 상황이 모두 돌지 않았다(이전 실행이 남았을 수 있다)")
        bad += 1
    for r in rows:
        pid = r["persona"]
        exp = EXPECT.get(pid)
        v = r["verify"]
        m = v["metrics"]
        got = {"steps": len(r["flow"]["steps"]),
               "touches": v["user_touches"],
               "beats": f"{v['beats_met']}/{v['beats_total']}",
               "cooked": "가열 시간(분)" in m}
        fail = [k for k in exp if exp[k] != got[k]] if exp else ["미등록 상황"]

        # 실행 로그의 모든 단계가 성공했는가 (procure 제외)
        broke = [e["skill"] for e in v.get("execution", [])
                 if not e["ok"] and e["skill"] not in SKIP_OK]
        ran = [e["skill"] for e in v.get("execution", [])]
        missing = [s for s in r["flow"]["steps"] if s not in ran]

        mark = "OK " if not (fail or broke or missing) else "!! "
        if fail or broke or missing:
            bad += 1
        print(f"  {mark}{pid:12} 계획 {got['steps']}단계 · 실행 {len(ran)}단계 · "
              f"개입 {got['touches']} · 장면 {got['beats']} · "
              f"가열 {m.get('가열 시간(분)', '-')}분 → {m.get('최종 질량비', '-')}")
        if missing:
            print(f"       계획에 있으나 실행되지 않음: {missing}")
        if broke:
            print(f"       실행 실패: {broke}")
        if fail:
            print(f"       기대와 다름: " +
                  ", ".join(f"{k} {exp[k]}→{got[k]}" for k in fail))
        for key in ("재고 부족", "제어 한계", "저장 전 확인"):
            if key in m:
                print(f"       {key}: {m[key]}")

    print(f"\n  {len(rows)}개 상황 중 {bad}개 이상")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
