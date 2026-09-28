# -*- coding: utf-8 -*-
"""플래너를 단독으로 확인한다.

"호출 순서를 코드에 적지 않고 전제조건에서 계산한다" 가 이 제안의 뼈대인데,
정작 그 계산기를 따로 본 적이 없었다. 파이프라인이 도는 것으로 대신 봤을
뿐이다 — 그건 **한 가지 입력에서만** 맞다는 뜻이다(2026-09-01 의
"상위 지표가 하위 기전을 고정하지 않는다" 와 같은 부류).

여기서 보는 것:
  1 결정성    선언 순서를 뒤섞어도 같은 계획이 나오는가
  2 최소성    목표에 기여하지 않는 작업이 끼지 않는가
  3 이미 성립  known 을 주면 그 작업이 빠지는가
  4 도달 불가  아무도 못 만드는 사실을 분명히 알리는가
  5 순환      전제가 돌면 멈추는가
  6 경합      같은 사실을 둘이 만들 때 조용히 고르지 않는가
  7 한계      지금 구조가 못 하는 것을 드러낸다(숨기지 않는다)
"""
from __future__ import annotations
import random
import sys

from planner import PlanError, Task, plan


def T(skill, provides, requires=()):
    return Task(skill=skill, provides=tuple(provides),
                requires=tuple(requires), note=f"{skill} 를 위해")


def _names(p):
    return [t.skill for t in p.steps]


CHECKS = []


def check(title):
    def deco(fn):
        CHECKS.append((title, fn))
        return fn
    return deco


@check("선언 순서를 뒤섞어도 같은 계획이 나온다")
def c1():
    tasks = [T("a", ["x"]), T("b", ["y"], ["x"]), T("c", ["z"], ["y"]),
             T("d", ["w"], ["x"])]
    base = None
    for seed in range(60):
        ts = list(tasks)
        random.Random(seed).shuffle(ts)
        got = _names(plan({"z", "w"}, ts))
        if base is None:
            base = got
        elif got != base:
            return False, f"시드 {seed} 에서 {base} → {got}"
    return True, f"60회 뒤섞어도 {base}"


@check("목표에 기여하지 않는 작업은 계획에 들어가지 않는다")
def c2():
    tasks = [T("a", ["x"]), T("b", ["y"], ["x"]),
             T("noise1", ["p"]), T("noise2", ["q"], ["p"])]
    got = _names(plan({"y"}, tasks))
    ok = got == ["a", "b"]
    return ok, f"계획 {got} (관계없는 noise1·noise2 는 빠져야 한다)"


@check("이미 성립한 사실을 주면 그 작업이 빠진다")
def c3():
    tasks = [T("a", ["x"]), T("b", ["y"], ["x"]), T("c", ["z"], ["y"])]
    full = _names(plan({"z"}, tasks))
    part = _names(plan({"z"}, tasks, known={"x"}))
    ok = full == ["a", "b", "c"] and part == ["b", "c"]
    return ok, f"그냥 {full} / x 가 이미 있으면 {part}"


@check("아무도 만들 수 없는 사실은 분명히 알린다")
def c4():
    tasks = [T("a", ["x"])]
    try:
        plan({"없는사실"}, tasks)
        return False, "예외 없이 통과했다"
    except PlanError as e:
        ok = "없는사실" in str(e)
        return ok, f"{e}"


@check("전제가 순환하면 멈춘다")
def c5():
    tasks = [T("a", ["x"], ["y"]), T("b", ["y"], ["x"])]
    try:
        plan({"x"}, tasks)
        return False, "순환인데 계획이 나왔다"
    except PlanError as e:
        return "순환" in str(e), f"{e}"


@check("같은 사실을 둘이 만들면 조용히 고르지 않는다")
def c6():
    tasks = [T("zulu", ["x"]), T("alpha", ["x"]), T("b", ["y"], ["x"])]
    p = plan({"y"}, tasks)
    told = any("만드는 작업이 2개" in r for r in p.reasoning)
    picked = _names(p)
    # 이름순으로 alpha 가 뽑혀야 재현 가능하다
    ok = told and picked == ["alpha", "b"]
    return ok, f"{picked} · 근거에 경합을 적었는가={told}"


@check("목표가 비면 빈 계획을 돌려준다")
def c7():
    p = plan(set(), [T("a", ["x"])])
    return _names(p) == [], f"계획 {_names(p)}"


@check("전제가 여럿이어도 전부 앞에 온다")
def c8():
    tasks = [T("a", ["x"]), T("b", ["y"]), T("c", ["z"], ["x", "y"])]
    got = _names(plan({"z"}, tasks))
    ok = got.index("c") == 2 and set(got[:2]) == {"a", "b"}
    return ok, f"계획 {got}"


@check("깊은 사슬도 순서가 어긋나지 않는다")
def c9():
    n = 12
    tasks = [T(f"s{i:02d}", [f"f{i}"], [f"f{i-1}"] if i else [])
             for i in range(n)]
    random.Random(1).shuffle(tasks)
    got = _names(plan({f"f{n-1}"}, tasks))
    ok = got == [f"s{i:02d}" for i in range(n)]
    return ok, f"{n}단계 사슬 {'정렬됨' if ok else got}"


@check("같은 스킬을 계획에 여러 번 쓸 수 있다")
def c10():
    """볶고 나서 끓이는 요리처럼, 같은 능력을 단계마다 다른 목표로 쓰는
    흐름은 실재한다.

    전에는 `selected` 가 **스킬 이름**을 키로 써서 한 스킬이 계획에 두 번
    들어갈 수 없었다. 작업의 정체성을 (스킬, 만드는 사실)로 바꿔 풀었다.
    두 converge 는 각각 'seared' 와 'stewed' 를 만들므로 다른 작업이다.
    """
    tasks = [T("prep", ["measured"]),
             T("converge", ["seared"], ["measured"]),
             T("converge", ["stewed"], ["seared"]),
             T("aftercare", ["cleaned"], ["stewed"])]
    base = None
    for seed in range(40):
        ts = list(tasks)
        random.Random(seed).shuffle(ts)
        got = [f"{t.skill}({t.provides[0]})" for t in plan({"cleaned"}, ts).steps]
        if base is None:
            base = got
        elif got != base:
            return False, f"시드 {seed} 에서 순서가 달라졌다: {base} → {got}"
    want = ["prep(measured)", "converge(seared)", "converge(stewed)",
            "aftercare(cleaned)"]
    ok = base == want
    return ok, f"{base} (40회 뒤섞어도 동일)"


@check("같은 스킬·같은 사실이면 한 번만 넣는다")
def c11():
    """여러 번 쓸 수 있게 하면서 **중복까지 허용하면** 안 된다.
    같은 사실을 만드는 같은 스킬은 한 번이면 족하다."""
    tasks = [T("a", ["x"]), T("a", ["x"]), T("b", ["y"], ["x"])]
    got = _names(plan({"y"}, tasks))
    ok = got.count("a") == 1
    return ok, f"계획 {got}"


def main() -> int:
    print("플래너 단독 검사 — 순서를 정말 계산하는가")
    print()
    bad = 0
    for title, fn in CHECKS:
        try:
            ok, note = fn()
        except Exception as e:
            print(f"  !!  {title}")
            print(f"        예외 {type(e).__name__}: {e}")
            bad += 1
            continue
        print(f"  {'OK ' if ok else '!! '} {title}")
        print(f"        {note}")
        if not ok:
            bad += 1
    print()
    print(f"판정: {len(CHECKS) - bad}/{len(CHECKS)} 항목 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
