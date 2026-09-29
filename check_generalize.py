# -*- coding: utf-8 -*-
"""설계하지 않은 상황에서도 도는가 — 일반화 시험.

왜 이 파일이 따로 있는가:
  `personas.py` 의 상황 4종은 **우리가 만든 것**이다. 그것들이 잘 도는 것은
  "상황이 바뀌어도 매번 새 시나리오를 그려낸다" 의 증거가 되지 못한다.
  우리가 그 4종에 맞춰 코드를 고쳐 왔기 때문이다.

  그래서 여기 상황들은 **페르소나에 넣지 않는다.** 넣는 순간 다음 수정이
  이것들에도 맞춰지고, 증거력이 사라진다. 여기 있는 것은 파이프라인이
  처음 보는 입력이어야 한다.

  실제로 이 시험을 처음 돌렸을 때 세 가지가 드러났다.
    · 임박한 재료가 하나도 없는 가구에서 inventory 가 ok=False 를 내
      파이프라인이 1단계에서 멈췄다(냉장고가 신선하면 정상인 상황인데).
    · 그렇게 멈췄는데도 "가전 7단계 · 개입 0회 · 절감 7회" 로 보고됐다.
    · 멈춰서 도달하지 못한 단계가 "계획에 없다" 로 보고됐다.

판정
  통과 = 예외 없이 끝나고, **결과가 왜 그런지 지표에 남는다.**
  ok=False 자체는 실패가 아니다 — 계란 하나로 만들 수 있는 것은 없다.
  실패인 것은 **이유 없이 조용히 끝나는 것**이다.
"""
from __future__ import annotations
import contextlib
import io
import sys

import personas
import run_design
from orchestrator import Trace

# 설계 때 고려하지 않은 축을 하나씩 건드린다.
#   x1 재료가 거의 없다       (메뉴를 못 고르는 경우)
#   x2 임박 재료가 하나도 없다 (냉장고가 전부 신선 — 앞서 여기서 멈췄다)
#   x3 시간이 턱없이 모자란다  (예산 10분)
#   x4 인원이 기기 용량을 넘는다
#   x5 보관 목록이 비어 있다   (극단값)
UNSEEN = {
    "x1_빈냉장고": dict(
        device="원룸 1인용 조리기", label="재료가 거의 없는 1인 가구",
        household_size=1, arrive_home="20:00", time_budget_min=30,
        next_morning_rush=False, avoid=[], dislike_noise_after="23:00",
        goal_hint="있는 걸로 어떻게든",
        friction_reported=["장을 못 봐서 뭘 할지 모르는 일"],
        fridge=[{"name": "계란", "qty_g": 240, "stored_days": 4,
                 "shelf_life_days": 21}]),
    "x2_전부신선": dict(
        device="본가 6인용 조리기", label="저염이 필요한 6인 가구",
        household_size=6, arrive_home="17:00", time_budget_min=90,
        next_morning_rush=False, avoid=["우유", "땅콩"], max_sodium_mg=200,
        dislike_noise_after="22:00", goal_hint="여섯 명이 먹을 저염 한 끼",
        friction_reported=["여섯 명분 양을 맞추는 일", "성분을 매번 읽는 일"],
        fridge=[{"name": "배추", "qty_g": 900, "stored_days": 2, "shelf_life_days": 7},
                {"name": "두부", "qty_g": 600, "stored_days": 1, "shelf_life_days": 5},
                {"name": "된장", "qty_g": 500, "stored_days": 30, "shelf_life_days": 365}]),
    "x3_초단시간": dict(
        device="자취방 2인용 조리기", label="10분밖에 없는 2인 가구",
        household_size=2, arrive_home="22:30", time_budget_min=10,
        next_morning_rush=True, avoid=[], dislike_noise_after="23:00",
        goal_hint="10분 안에", friction_reported=["늦어서 아무것도 못 하는 일"],
        fridge=[{"name": "배추", "qty_g": 400, "stored_days": 6, "shelf_life_days": 7},
                {"name": "된장", "qty_g": 500, "stored_days": 30, "shelf_life_days": 365},
                {"name": "두부", "qty_g": 300, "stored_days": 2, "shelf_life_days": 5}]),
    "x4_용량초과": dict(
        device="원룸 1인용 조리기", label="1인용 기기로 5인분을 해야 하는 가구",
        household_size=5, arrive_home="18:00", time_budget_min=60,
        next_morning_rush=False, avoid=[], dislike_noise_after="23:00",
        goal_hint="기기는 작은데 먹을 사람은 다섯",
        friction_reported=["양이 안 맞는 일"],
        fridge=[{"name": "배추", "qty_g": 900, "stored_days": 6, "shelf_life_days": 7},
                {"name": "두부", "qty_g": 600, "stored_days": 4, "shelf_life_days": 5},
                {"name": "된장", "qty_g": 500, "stored_days": 30, "shelf_life_days": 365}]),
    "x5_빈목록": dict(
        device="자취방 2인용 조리기", label="냉장고가 비어 있는 가구",
        household_size=2, arrive_home="19:00", time_budget_min=45,
        next_morning_rush=False, avoid=[], dislike_noise_after="23:00",
        goal_hint="아무것도 없다", friction_reported=["살 것을 정하는 일"],
        fridge=[]),
}

# 결과가 갈려도 되지만, **왜 그런지는 반드시 남아야 한다.**
# ok=False 일 때 이 중 하나는 지표에 있어야 한다.
REASON_KEYS = ("메뉴 없음", "시간 예산", "용량 초과", "계량 실패",
               "재고 부족", "확인 요청", "중단")


def main() -> int:
    for pid, p in UNSEEN.items():
        personas.PERSONAS[pid] = {"id": pid, **p}

    print("일반화 시험 — 설계하지 않은 상황 5종")
    print("  ok=False 는 실패가 아니다. 이유 없이 끝나는 것이 실패다.")
    print()

    bad = 0
    for pid in UNSEEN:
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r = run_design.design_for(pid, Trace(), seed=7)
        except Exception as e:
            print(f"  !!  {pid:12s} 예외 {type(e).__name__}: {e}")
            bad += 1
            continue

        v, m = r["verify"], r["verify"]["metrics"]
        head = (f"{pid:12s} ok={str(v['verified']):5s} "
                f"계획{v['steps_planned']}/실행{v['steps_run']} "
                f"장면{v['beats_met']}/{v['beats_total']} "
                f"개입{v['user_touches']} 절감{v['touches_removed']}")

        reasons = [k for k in REASON_KEYS if m.get(k)]
        if not v["verified"] and not reasons:
            print(f"  !!  {head}")
            print("        이유가 지표에 남지 않았다 — 조용히 끝났다")
            bad += 1
            continue

        # 중단된 실행이 절감을 성과로 세면 안 된다
        if v["not_run"] and v["touches_removed"] > 0:
            print(f"  !!  {head}")
            print(f"        {v['halted_at']} 에서 멈췄는데 절감 "
                  f"{v['touches_removed']}회로 집계됐다")
            bad += 1
            continue

        print(f"  OK  {head}")
        if v["not_run"]:
            print(f"        중단: {v['halted_at']} → 미도달 {v['not_run']}")
        for k in reasons[:2]:
            print(f"        {k}: {str(m[k])[:88]}")

    ok_r, lines = check_replan()
    print()
    print(f"  {'OK ' if ok_r else '!! '} 재계획은 필요할 때 한 번만 하고, 해도 안 되면 이유를 남긴다")
    for ln in lines:
        print(f"        {ln}")
    if not ok_r:
        bad += 1

    print()
    print(f"판정: {len(UNSEEN) + 1 - bad}/{len(UNSEEN) + 1} 항목 통과 "
          f"(설계하지 않은 상황 {len(UNSEEN)} + 재계획 1)")
    return 1 if bad else 0


def check_replan():
    """재계획의 성질을 본다.

    상황 판단이 대략 추정으로 "조달을 뺀다" 고 정했는데 재고로 만들 게 없으면,
    전에는 그대로 멈췄다. 이제 조달을 넣어 한 번 다시 계획한다.
      1 재고만으로 되는 가구(p1)는 재계획하지 않는다
      2 x1(계란 하나) 은 재계획하고, 예산을 넘든 못 만들든 이유가 남는다
      3 재계획은 한 번뿐이다 — 계획 단계에 REPLAN 이 두 번 찍히지 않는다
    """
    import personas as P
    lines, ok = [], True
    rows = {}
    for pid in ("p1_야근", "x1_빈냉장고"):
        if pid not in P.PERSONAS:
            P.PERSONAS[pid] = {"id": pid, **UNSEEN[pid]}
        tr = Trace()
        with contextlib.redirect_stdout(io.StringIO()):
            r = run_design.design_for(pid, tr, seed=7)
        n_replan = sum(1 for row in tr.rows if row["stage"] == "REPLAN")
        rows[pid] = (r, n_replan)
    r1, n1 = rows["p1_야근"]
    rx, nx = rows["x1_빈냉장고"]
    mx = rx["verify"]["metrics"]
    if r1["verify"].get("replanned") or n1:
        ok = False; lines.append("p1 이 재고로 되는데 재계획했다")
    if not rx["verify"].get("replanned") or nx != 1:
        ok = False; lines.append(f"x1 재계획 {rx['verify'].get('replanned')} · {nx}회")
    reason = mx.get("시간 예산") or mx.get("메뉴 없음") or mx.get("중단")
    if not rx["verify"]["verified"] and not reason:
        ok = False; lines.append("x1 이 재계획 뒤에도 안 됐는데 이유가 없다")
    lines.append(f"p1 재계획 {n1}회 · x1 재계획 {nx}회 → "
                 f"{mx.get('메뉴') or '-'} · {str(reason or '성립')[:50]}")
    return ok, lines


if __name__ == "__main__":
    sys.exit(main())
