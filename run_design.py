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
from kitchen_domain import (KITCHEN_TERMS, build_tasks, domain_goal,
                            kitchen_beats, make_executor, pantry_stock,
                            pantry_refill)
from planner import Task, plan as make_plan
from skills import REGISTRY
from orchestrator import Trace, banner, W

# 단계별 예상 소요 — 상황 판단(시간이 모자라는가)의 기준이 된다.
# **설계 시점의 추정값**이다. 조리 시간은 양과 메뉴에 따라 5.5~25분으로
# 갈리는데, 그 둘은 아직 정해지지 않았다. 실제 소요는 실행 뒤에 재서
# 시간 예산과 대조한다(experience_verify).
# '조달' 은 더 이상 내가 정한 값이 아니다. 상점 어댑터가 알려주는
# **가장 빠른 배송 시간**을 쓴다. 상점이 바뀌면 이 판단도 함께 바뀐다.
import store as _store
STAGE_COSTS = {"보관 확인": 2, "메뉴 결정": 3,
               "조달": _store.min_delivery_min(), "준비": 4,
               "조리": 12, "세척 시작": 2}


# 재계획을 몇 번까지 하는가. 원인 하나를 풀 때마다 한 번이다.
MAX_REPLANS = 3


def pad(s: str, width: int) -> str:
    """한글은 두 칸을 차지한다. 표가 어긋나지 않게 실제 폭으로 맞춘다."""
    w = sum(2 if ord(ch) > 0x2000 else 1 for ch in s)
    return s + " " * max(1, width - w)


def build_design_tasks(ctx_seed: dict) -> list:
    """설계 직무 자체를 작업으로 선언한다. 순서는 플래너가 정한다."""
    return [
        Task(skill="situation_read", provides=("friction", "constraints"),
             bind=lambda c: {"persona": c["persona"], "stage_costs": STAGE_COSTS,
                             "terms": KITCHEN_TERMS,
                             "self_min": {
                                 "on_way": (c["persona"].get("prefs") or {}).get(
                                     "shop_detour_min", _store.SHOP_DETOUR_MIN),
                                 "from_home": (c["persona"].get("prefs") or {}).get(
                                     "shop_trip_min", _store.SHOP_TRIP_MIN)}},
             absorb=lambda c, o: c["seed"].update(
                 time_budget_min=o["constraints"].get("time_budget_min"),
                 # 조달이 계획에서 빠져도 주문 방식은 결과에 남긴다
                 planned_order_mode=o["constraints"].get("order_mode")
             ) or c.update(friction=o["friction"],
                                          constraints=o["constraints"]),
             note="상황을 읽어야 무엇을 없앨지 정해진다"),
        Task(skill="scenario_draft", requires=("friction", "constraints"),
             provides=("scenario",),
             bind=lambda c: {"persona": c["persona"], "friction": c["friction"],
                             "constraints": c["constraints"],
                             # 어떤 장면을 그릴지는 도메인이 안다.
                             # LLM 으로 제안받을 때는 그 함수가 대신 들어온다.
                             "beats_for": c.get("beats_for") or kitchen_beats},
             absorb=lambda c, o: c.update(scenario=o["scenario"]),
             note="수고가 사라진 하루를 먼저 그려야 설계 기준이 생긴다"),
        Task(skill="flow_design", requires=("scenario", "constraints"),
             provides=("flow",),
             bind=lambda c: {"constraints": c["constraints"],
                             "tasks": build_tasks(c["constraints"]),
                             "planner": make_plan,
                             # 무엇이 "끝났다" 인지는 도메인이 안다.
                             "goal": domain_goal(c["constraints"])},
             absorb=lambda c, o: c.update(flow=o["flow"], exec_plan=o["plan"]),
             note="시나리오를 실현할 가전 작업 순서를 계산한다"),
        Task(skill="experience_verify", requires=("flow", "scenario"),
             provides=("verified",),
             bind=lambda c: {"scenario": c["scenario"], "plan": c["exec_plan"],
                             "execute": c["executor"],
                             "touch_baseline": len(c["exec_plan"].steps),
                             "budget_min": c["constraints"].get("time_budget_min")},
             absorb=lambda c, o: c.update(verify=o),
             note="그림으로 끝내지 않고 실제로 돌려 확인한다"),
    ]


def _sourced(p: dict) -> list:
    """재고마다 **어디서 알았나**를 붙인다. 항목에 없으면 가구의 stock_source,
    그것도 없으면 사람이 말해 준 것(told)으로 본다."""
    return [dict(x, source=x.get("source", p.get("stock_source", "told")))
            for x in (p.get("fridge") or [])]


def design_for(pid: str, trace: Trace, seed: int = 7,
               keep_records: bool = False, beats_factory=None,
               approve_purchase=None) -> dict:
    """beats_factory: (friction_of) -> beats_for. LLM 장면 제안을 쓸 때 준다.
    friction_of 는 situation_read 가 **이번에 읽은** 수고를 돌려준다."""
    p = personas.get(pid)
    banner(f"고객 상황 · {p['label']}  ({pid})")

    # 가구가 바뀌면 재고도 바뀐다. 앞 실행의 상태가 남지 않게 초기화한다.
    # 상비품(소금·후춧가루 등)은 가구와 무관하게 늘 있다고 본다.
    # seed 를 바꿔 같은 상황을 여러 번 돌리면 흔들림의 크기를 잴 수 있다.
    low = p.get("pantry_low")
    seed_val = seed        # 아래에서 seed 이름을 초기값 dict 로 다시 쓴다
    K.reset(_sourced(p) + pantry_stock(low), seed=seed_val,
            keep_records=keep_records)
    K.cookware_reset(p.get("cookware_history"))

    trace.stage("GOAL", f"{p['label']}의 수고를 줄이는 UX 시나리오를 만들고 "
                        f"실행으로 검증한다",
                {"입력": "고객 상황", "사용자 지시": None,
                 "보고된 불편": len(p["friction_reported"])})

    refill = pantry_refill(low)
    # 실행 층에 넘길 초기값. 상황 판단(situation_read)이 끝난 뒤에야 알 수
    # 있는 값(시간 예산 등)이 있으므로, 같은 dict 를 참조로 공유해 나중에 채운다.
    seed = {"pantry_refill": refill}
    # 처음 사는 것·상한 초과 등 확인이 필요한 주문의 승인. None 이면 시연 —
    # 동의했다고 가정하고 결과에 그렇게 적는다(실제 경로는 thinq 가 넘긴다).
    if approve_purchase is not None:
        seed["approve_purchase"] = approve_purchase
    ctx = {"persona": p, "seed": seed,
           "executor": make_executor(REGISTRY, seed_ctx=seed)}
    if beats_factory is not None:
        ctx["beats_for"] = beats_factory(lambda _p, _c: ctx.get("friction", []))
    tasks = build_design_tasks(ctx)
    dp = make_plan({"verified"}, tasks)

    trace.stage("PLAN", "전제조건에서 계산한 설계 단계",
                {"순서": [t.skill for t in dp.steps]})
    trace.detail(dp.reasoning)

    def run_steps(steps):
        for i, t in enumerate(steps, 1):
            skill = REGISTRY.get(t.skill)
            trace.stage("SKILL", f"[{i}/{len(steps)}] {skill.name} — {t.note}")
            res = skill.run(**t.kwargs(ctx))
            t.absorb(ctx, res.output)
            shown = {k: v for k, v in res.output.items()
                     if k not in ("plan", "execution", "scenario")}
            trace.stage("EXECUTE", f"{skill.name} 실행", shown or None)
            trace.stage("EVALUATE", "근거")
            trace.detail(res.evidence[:14])
            if len(res.evidence) > 14:
                print(f"{'':12}… ({len(res.evidence) - 14}줄 생략)")

    import copy
    records0 = copy.deepcopy(K.RECORDS)
    run_steps(dp.steps)
    v = ctx["verify"]

    # ── 재계획 ──────────────────────────────────────────────────────────
    # 막히면 되돌아간다. **무엇이 막았는지는 도메인이 진단하고**(diagnose),
    # 여기서는 그중 풀 수 있는 원인을 **하나씩** 풀어 다시 설계한다.
    #   · 같은 원인은 두 번 풀지 않는다      · 최대 MAX_REPLANS 번
    #   · 고객의 사실·안전(알레르기·예산·기기)은 도메인이 애초에 풀 수
    #     없다고 표시한다 — 그것을 풀어 성립시키면 고객을 바꾼 것이다
    # 사실(시간 예산·재고·불편)은 다시 읽지 않는다. 바뀌는 것은 **우리가
    # 추정·선택으로 정한 결정**뿐이고, 순서는 플래너가 다시 계산한다.
    # 예산을 넘는지는 추정이 아니라 실제 실행이 판정한다.
    #
    # (고쳐 온 과정: 처음엔 "조달 생략 + 메뉴에서 멈춤" 한 경우만 한 번
    #  되돌렸다. 지금은 뒤 단계에서 막힌 메뉴·예산을 넘긴 메뉴도 다룬다.)
    from kitchen_domain import diagnose, rank_attempt

    def rerun(constraints):
        K.RECORDS.clear()
        K.RECORDS.update(copy.deepcopy(records0))
        K.reset(_sourced(p) + pantry_stock(low), seed=seed_val,
                keep_records=True)
        K.cookware_reset(p.get("cookware_history"))
        ctx["constraints"] = constraints
        ctx["executor"] = make_executor(REGISTRY, seed_ctx=seed)
        run_steps([t for t in dp.steps if t.skill != "situation_read"])
        return ctx["verify"]

    attempts = [(dict(ctx["constraints"]), v)]
    history, tried = [], set()
    for _ in range(MAX_REPLANS):
        cons = attempts[-1][0]
        causes = diagnose(cons, attempts[-1][1])
        fix = next((c for c in causes if c["relaxable"] and c["key"] not in tried),
                   None)
        if fix is None:
            break
        tried.add(fix["key"])
        trace.stage("REPLAN", fix["why"])
        new_cons = fix["apply"](cons)
        v = rerun(new_cons)
        history.append({"key": fix["key"], "why": fix["why"],
                        "verified": v["verified"], "spent_min": v.get("spent_min")})
        attempts.append((dict(new_cons), v))

    # 시도들 가운데 가장 나은 안을 고른다. 마지막 시도가 아니면 그 안으로
    # **한 번 더 돌려** 주방 상태(재고·저장 기록)를 그 안에 맞춘다(결정적이다).
    best = max(range(len(attempts)),
               key=lambda i: (rank_attempt(attempts[i][1]), -i))
    if best != len(attempts) - 1:
        v = rerun(attempts[best][0])
    else:
        v = attempts[best][1]
    v["replans"] = history
    v["replanned"] = bool(history)
    # 실행 뒤에야 아는 것: 실제 조리·세척 시각, 에이전트가 스스로 한 판단
    from kitchen_domain import outcome_notes, retime_beats
    ctx["scenario"]["retimed"] = retime_beats(ctx["scenario"], v, ctx["constraints"])
    ctx["scenario"]["outcomes"] = outcome_notes(v)
    if history:
        v["metrics"]["재계획"] = " → ".join(h["why"] for h in history)
        if len(attempts) > 1:
            v["metrics"]["고른 안"] = (f"시도 {len(attempts)}개 중 {best + 1}번째"
                                   + (" (성립)" if v["verified"] else
                                      " (성립한 안이 없어 가장 가까운 것)"))
    sc = ctx["scenario"]
    trace.stage("OUTPUT", f"{p['label']} — UX 시나리오 {len(sc['beats'])}장면, "
                          f"사용자 개입 {v['user_touches']}회 "
                          f"(설계 전 {v['touch_baseline']}회)",
                {"가전 순서": ctx["flow"]["steps"], **v["metrics"]})

    print()
    print("  ── 시나리오 ──")
    from skills.design_skills import night_order
    for b in sorted(sc["beats"], key=night_order(sc.get("arrive_home")
                                                  or ctx["persona"].get("arrive_home"))):
        print(f"   {b['at']}  사용자: {b['user']}")
        print(f"          가전: {b['system']}")
        print(f"          사라진 수고: {b['removes']}")
    for o in sc.get("outcomes", []):
        print(f"   (실행 중) 사용자: {o['user']}")
        print(f"          가전: {o['system']}")
    rep = getattr(ctx.get("beats_for"), "report", None)
    return {"persona": pid, "label": p["label"], "scenario": sc,
            "design_report": dict(rep) if rep is not None else None,
            "flow": ctx["flow"], "verify": v,
            "chosen": v["metrics"].get("사용한 기록"),
            "target": v["metrics"].get("목표 질량비"),
            "estimated": v["metrics"].get("목표 출처", "").startswith("조리법")}


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
