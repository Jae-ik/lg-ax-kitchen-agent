# -*- coding: utf-8 -*-
"""전제조건·효과 기반 플래너.

에이전트의 PLAN 단계는 문장이 아니라 **계산**이어야 한다.
여기서는 목표 사실(goal)에서 역방향으로 필요한 작업을 고르고,
전제조건이 먼저 오도록 위상 정렬한다.

호출 순서를 코드에 적어두지 않는다. 그래서
 - 목표가 달라지면 계획이 달라지고 (세척까지 vs 조리까지)
 - 이미 성립한 사실이 있으면 그 작업은 빠지고
 - 스킬을 새로 등록하면 계획에 저절로 들어온다.

도메인 지식은 Skill 이 아니라 Task 에 둔다.
converge 는 조리기에서도 건조기에서도 쓰이므로,
"재고가 갖춰져야 한다" 같은 주방 전용 전제를 스킬에 박으면 안 된다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Task:
    """계획의 단위. 스킬 하나를 이 도메인에서 어떻게 쓰는지에 대한 선언."""
    skill: str                       # 레지스트리에 등록된 스킬 이름
    provides: tuple                  # 이 작업이 끝나면 성립하는 사실
    requires: tuple = ()             # 이 작업 전에 성립해야 하는 사실
    setup: Callable[[dict], None] = None  # 실행 직전 준비 (기기 시동 등)
    bind: Callable[[dict], dict] = None   # 컨텍스트 → 스킬 kwargs
    absorb: Callable[[dict, dict], None] = None  # 스킬 출력 → 컨텍스트 반영
    note: str = ""                   # 왜 이 작업이 필요한지 (로그용)

    def kwargs(self, ctx: dict) -> dict:
        return self.bind(ctx) if self.bind else {}


class PlanError(Exception):
    pass


@dataclass
class Plan:
    steps: list = field(default_factory=list)      # 정렬된 Task
    reasoning: list = field(default_factory=list)  # 어떻게 이 순서가 나왔는지
    unmet: tuple = ()                              # 아무도 못 만드는 사실


def plan(goal, tasks, known=()) -> Plan:
    """목표에서 역방향으로 계획을 계산한다.

    goal  : 달성하려는 사실의 집합
    tasks : 이 도메인에서 쓸 수 있는 Task 목록
    known : 이미 성립해 있는 사실 (그 작업은 건너뛴다)
    """
    goal, known = set(goal), set(known)
    by_fact = {}
    for t in tasks:
        for f in t.provides:
            by_fact.setdefault(f, t)

    selected, reasoning, unmet = {}, [], set()
    frontier = list(goal - known)
    if goal & known:
        for f in sorted(goal & known):
            reasoning.append(f"'{f}' 은 이미 성립 — 작업 불필요")

    # ── 역방향 선택: 필요한 사실을 만드는 작업을 끌어온다 ──
    while frontier:
        fact = frontier.pop()
        if fact in known:
            continue
        producer = by_fact.get(fact)
        if producer is None:
            unmet.add(fact)
            continue
        if producer.skill in selected:
            continue
        selected[producer.skill] = producer
        why = producer.note or f"'{fact}' 을 만들기 위해"
        reasoning.append(f"{producer.skill} 선택 — {why}")
        for r in producer.requires:
            if r not in known:
                frontier.append(r)

    if unmet:
        raise PlanError(f"아무 스킬도 만들 수 없는 사실: {sorted(unmet)}")

    # ── 위상 정렬: 전제가 먼저 오도록 ──
    ordered, done = [], set(known)
    remaining = list(selected.values())
    while remaining:
        ready = [t for t in remaining if set(t.requires) <= done]
        if not ready:
            stuck = [t.skill for t in remaining]
            raise PlanError(f"전제가 순환하거나 충족되지 않는다: {stuck}")
        # 같은 단계에 여러 개가 준비되면 이름 순으로 고정 — 계획을 재현 가능하게
        ready.sort(key=lambda t: t.skill)
        nxt = ready[0]
        ordered.append(nxt)
        done |= set(nxt.provides)
        remaining.remove(nxt)

    reasoning.append("전제 순서로 정렬 → " + " → ".join(t.skill for t in ordered))
    return Plan(steps=ordered, reasoning=reasoning)
