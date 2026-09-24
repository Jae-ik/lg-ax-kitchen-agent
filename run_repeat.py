# -*- coding: utf-8 -*-
"""같은 상황을 두 번 돌려 '두 번째가 달라지는지' 확인한다.

제안의 핵심은 "좋아했던 결과를 기록하고 다시 쓴다" 이다.
그 말이 참이려면 **1회차와 2회차가 달라야** 한다.

  1회차  저장된 기록이 없다 → 공개 레시피를 쓴다 → 목표는 조리법 기본값(가정)
  2회차  1회차 실측이 기록으로 남아 있다 → 그 기록을 쓴다 → 목표는 실측값

    python run_repeat.py            기본 p3_알레르기
    python run_repeat.py p4_퇴근길
"""
from __future__ import annotations
import io
import contextlib
import sys

import kitchen as K
import personas
import run_design
from orchestrator import Trace, banner, W


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "p3_알레르기"
    label = personas.get(pid)["label"]
    trace = Trace()

    print("=" * W)
    print(f"같은 상황을 두 번 — {label}")
    print("=" * W)

    r1 = quiet(run_design.design_for, pid, trace, seed=7)
    before = dict(K.RECORDS)                       # 1회차가 남긴 기록
    r2 = quiet(run_design.design_for, pid, trace, seed=8, keep_records=True)

    rows = [("1회차", r1), ("2회차", r2)]
    print(f"\n  {'':6} {'쓴 기록':12} {'목표 질량비':>12} {'출처':>14} "
          f"{'가열':>6} {'최종 질량비':>12}")
    print("  " + "-" * 72)
    for tag, r in rows:
        m = r["verify"]["metrics"]
        src = "가정(조리법 기본값)" if r["estimated"] else "실측 기록"
        print(f"  {tag:6} {str(r['chosen']):12} {r['target']:>12} {src:>14} "
              f"{str(m.get('가열 시간(분)')):>6} {str(m.get('최종 질량비')):>12}")

    print()
    if r1["chosen"] != r2["chosen"]:
        print(f"  2회차는 1회차가 남긴 기록({r2['chosen']})을 골랐다.")
    if r1["estimated"] and not r2["estimated"]:
        print(f"  목표가 **가정값 {r1['target']} 에서 실측 {r2['target']} 로** 바뀌었다.")
        print("  같은 메뉴를 두 번째 만들 때부터는 남의 평균이 아니라")
        print("  내 주방에서 실제로 나온 값을 쫓는다.")
    else:
        print("  두 회차가 같은 기록을 썼다 — 저장된 기록이 이미 있었다는 뜻이다.")

    print(f"\n  기록 수: 시작 3개 → 1회차 뒤 {len(before)}개 → 2회차 뒤 {len(K.RECORDS)}개")


if __name__ == "__main__":
    main()
