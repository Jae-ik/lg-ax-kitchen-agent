# -*- coding: utf-8 -*-
"""UX 시나리오 설계 에이전트.

직무: UX/UI 시나리오 설계자
입력: 고객 상황 (지시가 아니다)
출력: UX 시나리오 + 실행 결과

    Goal → Plan → Tool·Skill → Execute → Evaluate → Output

설계 4단계의 순서도 코드에 적지 않는다. 플래너가 전제조건에서 계산한다.
서로 다른 고객 상황 3건을 넣으면 서로 다른 시나리오 3건이 나온다 —
같은 업무를 새로운 입력에 반복 수행한다는 뜻이다.
"""
from __future__ import annotations
import json
import sys

import kitchen as K
import personas
from kitchen_domain import (build_tasks, make_executor, pantry_stock,
                            pantry_refill)
from planner import Task, plan as make_plan
from skills import REGISTRY
from orchestrator import Trace, banner, W

# 단계별 예상 소요 — 상황 판단(시간이 모자라는가)의 기준이 된다
# '조달' 은 더 이상 내가 정한 값이 아니다. 상점 어댑터가 알려주는
# **가장 빠른 배송 시간**을 쓴다. 상점이 바뀌면 이 판단도 함께 바뀐다.
import store as _store
STAGE_COSTS = {"보관 확인": 2, "메뉴 결정": 3,
               "조달": _store.min_delivery_min(), "준비": 4,
               "조리": 12, "세척 시작": 2}


def pad(s: str, width: int) -> str:
    """한글은 두 칸을 차지한다. 표가 어긋나지 않게 실제 폭으로 맞춘다."""
    w = sum(2 if ord(ch) > 0x2000 else 1 for ch in s)
    return s + " " * max(1, width - w)


def build_design_tasks(ctx_seed: dict) -> list:
    """설계 직무 자체를 작업으로 선언한다. 순서는 플래너가 정한다."""
    return [
        Task(skill="situation_read", provides=("friction", "constraints"),
             bind=lambda c: {"persona": c["persona"], "stage_costs": STAGE_COSTS},
             absorb=lambda c, o: c.update(friction=o["friction"],
                                          constraints=o["constraints"]),
             note="상황을 읽어야 무엇을 없앨지 정해진다"),
        Task(skill="scenario_draft", requires=("friction", "constraints"),
             provides=("scenario",),
             bind=lambda c: {"persona": c["persona"], "friction": c["friction"],
                             "constraints": c["constraints"]},
             absorb=lambda c, o: c.update(scenario=o["scenario"]),
             note="수고가 사라진 하루를 먼저 그려야 설계 기준이 생긴다"),
        Task(skill="flow_design", requires=("scenario", "constraints"),
             provides=("flow",),
             bind=lambda c: {"constraints": c["constraints"],
                             "tasks": build_tasks(c["constraints"]),
                             "planner": make_plan},
             absorb=lambda c, o: c.update(flow=o["flow"], exec_plan=o["plan"]),
             note="시나리오를 실현할 가전 작업 순서를 계산한다"),
        Task(skill="experience_verify", requires=("flow", "scenario"),
             provides=("verified",),
             bind=lambda c: {"scenario": c["scenario"], "plan": c["exec_plan"],
                             "execute": c["executor"],
                             "touch_baseline": len(c["exec_plan"].steps)},
             absorb=lambda c, o: c.update(verify=o),
             note="그림으로 끝내지 않고 실제로 돌려 확인한다"),
    ]


def design_for(pid: str, trace: Trace, seed: int = 7) -> dict:
    p = personas.get(pid)
    banner(f"고객 상황 · {p['label']}  ({pid})")

    # 가구가 바뀌면 재고도 바뀐다. 앞 실행의 상태가 남지 않게 초기화한다.
    # 상비품(소금·후춧가루 등)은 가구와 무관하게 늘 있다고 본다.
    # seed 를 바꿔 같은 상황을 여러 번 돌리면 흔들림의 크기를 잴 수 있다.
    low = p.get("pantry_low")
    K.reset((p.get("fridge") or []) + pantry_stock(low), seed=seed)

    trace.stage("GOAL", f"{p['label']}의 수고를 줄이는 UX 시나리오를 만들고 "
                        f"실행으로 검증한다",
                {"입력": "고객 상황", "사용자 지시": None,
                 "보고된 불편": len(p["friction_reported"])})

    refill = pantry_refill(low)
    ctx = {"persona": p,
           "executor": make_executor(REGISTRY, seed_ctx={"pantry_refill": refill})}
    tasks = build_design_tasks(ctx)
    dp = make_plan({"verified"}, tasks)

    trace.stage("PLAN", "전제조건에서 계산한 설계 단계",
                {"순서": [t.skill for t in dp.steps]})
    trace.detail(dp.reasoning)

    for i, t in enumerate(dp.steps, 1):
        skill = REGISTRY.get(t.skill)
        trace.stage("SKILL", f"[{i}/{len(dp.steps)}] {skill.name} — {t.note}")
        res = skill.run(**t.kwargs(ctx))
        t.absorb(ctx, res.output)
        shown = {k: v for k, v in res.output.items()
                 if k not in ("plan", "execution", "scenario")}
        trace.stage("EXECUTE", f"{skill.name} 실행", shown or None)
        trace.stage("EVALUATE", "근거")
        trace.detail(res.evidence[:14])
        if len(res.evidence) > 14:
            print(f"{'':12}… ({len(res.evidence) - 14}줄 생략)")

    v = ctx["verify"]
    sc = ctx["scenario"]
    trace.stage("OUTPUT", f"{p['label']} — UX 시나리오 {len(sc['beats'])}장면, "
                          f"사용자 개입 {v['user_touches']}회 "
                          f"(설계 전 {v['touch_baseline']}회)",
                {"가전 순서": ctx["flow"]["steps"], **v["metrics"]})

    print()
    print("  ── 시나리오 ──")
    for b in sc["beats"]:
        print(f"   {b['at']}  사용자: {b['user']}")
        print(f"          가전: {b['system']}")
        print(f"          사라진 수고: {b['removes']}")
    return {"persona": pid, "label": p["label"], "scenario": sc,
            "flow": ctx["flow"], "verify": v}


def main():
    ids = sys.argv[1:] or personas.ids()
    trace = Trace()

    print("=" * W)
    print("등록된 스킬 — 설계 층과 실행 층")
    print("=" * W)
    for s in REGISTRY.list():
        tag = "설계" if s["name"] in ("situation_read", "scenario_draft",
                                    "flow_design", "experience_verify") else "실행"
        print(f"  [{tag}] {s['name']:18} 필요 {list(s['requires']) or '-'}"
              f" → 생성 {list(s['provides']) or '-'}")

    results = [design_for(pid, trace) for pid in ids]

    banner("상황이 다르면 시나리오가 다르다")
    print("  " + pad("고객 상황", 28) + pad("가전 작업 순서", 52)
          + pad("개입", 8) + pad("장면", 8))
    print("  " + "-" * 94)
    for r in results:
        v = r["verify"]
        steps = " → ".join(r["flow"]["steps"])
        touches = f"{v['user_touches']}회"
        scenes = f"{v['beats_met']}/{v['beats_total']}"
        print("  " + pad(r["label"], 28) + pad(steps, 52)
              + pad(touches, 8) + pad(scenes, 8))
    print()
    print("  같은 코드가 상황만 바꿔 서로 다른 계획과 시나리오를 만들었다.")
    print("  계획은 적어둔 것이 아니라 전제조건에서 계산된 것이다.")

    with open("scenarios.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)
    trace.dump("trace_design.json")
    print("\n  scenarios.json · trace_design.json 저장")


if __name__ == "__main__":
    main()
