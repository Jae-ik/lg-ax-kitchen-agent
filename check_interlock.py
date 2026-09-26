# -*- coding: utf-8 -*-
"""스킬이 실제로 맞물려 도는지 확인한다 — 절단(ablation) 실험.

왜 이 파일이 따로 있는가:
  `check_consistency.py` 7번은 "앞 스킬이 만든 ctx 키를 뒤 스킬이 읽는가"를
  본다. 그건 **선언이 이어진다**는 뜻이지 **실제로 맞물린다**는 뜻이 아니다.
  키를 읽기만 하고 결과에 반영하지 않는 단계는 그 검사를 통과한다.

  그래서 여기서는 반대로 한다. 각 스킬의 **출력을 하나씩 망가뜨리고**
  최종 산출(시나리오·가전 순서·지표)이 **달라지는지**를 본다.
  달라지지 않으면 그 스킬은 파이프라인에 실제로 기여하지 않는 것이다.

판정
  변함  -> 실패. 그 단계는 뒤에 영향을 주지 않는다(죽은 단계).
  달라짐 / 막힘 -> 통과. 뒤 단계가 그 출력에 실제로 의존한다.
"""
from __future__ import annotations
import contextlib
import copy
import io
import json
import sys

import kitchen as K
import personas
from orchestrator import Trace
from skills import REGISTRY

import run_design


def _fingerprint(res: dict) -> dict:
    """최종 산출에서 비교할 부분만 뽑는다."""
    v = res["verify"]
    return {
        "가전 순서": list(res["flow"]["steps"]),
        "장면 수": len(res["scenario"]["beats"]),
        "개입": v["user_touches"],
        "지표": {k: v["metrics"][k] for k in sorted(v["metrics"])},
    }


def _run(pid: str, patch=None) -> dict:
    """한 상황을 끝까지 돌린다. patch 가 있으면 그 스킬 출력을 교란한다.

    patch: (스킬이름, 함수(output)->output)
    """
    originals = {}
    if patch:
        name, fn = patch
        sk = REGISTRY.get(name)
        originals[name] = sk.run

        def wrapped(_sk=sk, _fn=fn, **kw):
            r = _sk.__class__.run(_sk, **kw)
            before = copy.deepcopy(r.output)
            r.output = _fn(r.output)
            _assert_changed(_sk.name, before, r.output)
            return r

        sk.run = wrapped
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = run_design.design_for(pid, Trace(), seed=7)
        return _fingerprint(res)
    finally:
        for n, fn in originals.items():
            REGISTRY.get(n).run = fn


# ── 교란 정의 ──────────────────────────────────────────────────────────
# 각 스킬의 **주요 출력 하나**만 건드린다. 여러 개를 동시에 건드리면
# 어느 것이 효과를 냈는지 알 수 없다(2026-08-12 교훈).

def _drop_pool(o):          # recipe_source: 공개 레시피를 못 받아온 상황
    o = dict(o); o["recipe_pool"] = []; return o

def _no_urgent(o):          # inventory: 임박 재료가 없다고 본 상황
    o = dict(o); o["urgent"] = []; return o

def _other_menu(o):         # menu: 두 번째 후보를 골랐다면
    o = copy.deepcopy(o)
    c = o.get("candidates") or []
    if len(c) > 1:
        o["best"] = c[1]
    return o

def _no_order(o):           # procure: 자동 주문이 하나도 안 된 상황
    o = copy.deepcopy(o); o["auto_ordered"] = []; return o

def _light_measure(o):      # prep: 계량이 20% 적게 된 상황
    o = copy.deepcopy(o)
    o["total_mass_g"] = round(o["total_mass_g"] * 0.8, 1)
    return o

def _stop_early(o):         # converge: 목표에 못 미친 채 끝난 상황
    o = copy.deepcopy(o)
    o["reached"] = False
    o["final"] = round(o["final"] * 1.15, 4)
    return o


def _short_budget(o):       # situation_read: 쓸 수 있는 시간이 5분뿐
    o = copy.deepcopy(o)
    o["constraints"]["time_budget_min"] = 5
    return o

def _drop_beat(o):          # scenario_draft: 장면 하나를 잃은 상황
    o = copy.deepcopy(o)
    if len(o["scenario"]["beats"]) > 1:
        o["scenario"]["beats"] = o["scenario"]["beats"][:-1]
    return o

def _drop_last_step(o):     # flow_design: 마지막 가전 단계를 잃은 상황
    o = copy.deepcopy(o)
    if len(o["flow"]["steps"]) > 1:
        o["flow"]["steps"] = o["flow"]["steps"][:-1]
        o["plan"].steps = o["plan"].steps[:-1]
    return o


# 교란 함수가 **실제로 출력을 바꿨는지** 스스로 확인한다.
# 없는 키를 건드리면 조용히 통과하고 "죽은 단계" 로 오판된다
# (실제로 첫 판에서 procure·prep·converge 셋이 이 함정에 걸렸다).
def _assert_changed(name, before, after):
    if before == after:
        raise AssertionError(
            f"교란이 {name} 출력을 바꾸지 못했다 — 키 이름을 확인하라. "
            f"실제 키: {sorted(before)}")


CASES = [
    # (스킬, 교란 설명, 교란 함수, 검증할 상황)
    ("situation_read",  "쓸 수 있는 시간을 5분으로",       _short_budget,  "p3_알레르기"),
    ("scenario_draft",  "장면 하나를 잃음",                 _drop_beat,     "p3_알레르기"),
    ("flow_design",     "마지막 가전 단계를 잃음",          _drop_last_step,"p3_알레르기"),
    ("recipe_source",   "공개 레시피를 못 받아옴",          _drop_pool,     "p3_알레르기"),
    ("inventory",       "임박 재료가 없다고 봄",            _no_urgent,     "p3_알레르기"),
    ("menu",            "두 번째 후보를 골랐다면",          _other_menu,    "p1_야근"),
    ("procure",         "자동 주문이 하나도 안 됨",         _no_order,      "p2_맞벌이"),
    ("prep",            "계량이 20% 적게 됨",               _light_measure, "p1_야근"),
    ("converge",        "목표에 못 미친 채 끝남",           _stop_early,    "p1_야근"),
]


def main() -> int:
    print("스킬 맞물림 — 절단 실험")
    print("  각 스킬의 출력을 하나씩 망가뜨리고 최종 산출이 달라지는지 본다.")
    print("  '변함 없음' 이 나오면 그 단계는 파이프라인에 기여하지 않는 것이다.")
    print()

    base = {}
    for pid in ("p1_야근", "p2_맞벌이", "p3_알레르기"):
        base[pid] = _run(pid)

    bad = 0
    for skill, what, fn, pid in CASES:
        try:
            got = _run(pid, (skill, fn))
            blocked = None
        except Exception as e:                       # 막히는 것도 의존의 증거
            got, blocked = None, f"{type(e).__name__}: {e}"

        if blocked:
            print(f"  OK  {skill:16s} {what:26s} -> 뒤 단계가 진행 불가 ({blocked[:60]})")
            continue

        ref = base[pid]
        diffs = []
        if got["가전 순서"] != ref["가전 순서"]:
            diffs.append(f"가전 순서 {ref['가전 순서']} -> {got['가전 순서']}")
        if got["장면 수"] != ref["장면 수"]:
            diffs.append(f"장면 {ref['장면 수']} -> {got['장면 수']}")
        if got["개입"] != ref["개입"]:
            diffs.append(f"개입 {ref['개입']} -> {got['개입']}")
        for k in sorted(set(ref["지표"]) | set(got["지표"])):
            a, b = ref["지표"].get(k), got["지표"].get(k)
            if a != b:
                diffs.append(f"{k}: {a} -> {b}")

        if diffs:
            print(f"  OK  {skill:16s} {what:26s} -> {len(diffs)}개 달라짐")
            for d in diffs[:3]:
                print(f"        {d}")
            if len(diffs) > 3:
                print(f"        … 외 {len(diffs) - 3}개")
        else:
            print(f"  !!  {skill:16s} {what:26s} -> 최종 산출 변함 없음 (죽은 단계)")
            bad += 1

    print()
    print(f"판정: {len(CASES) - bad}/{len(CASES)} 단계가 뒤에 실제로 영향을 준다")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
