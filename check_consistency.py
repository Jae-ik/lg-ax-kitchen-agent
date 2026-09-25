# -*- coding: utf-8 -*-
"""일관성 검사 — 사람이 기억해서 지키는 규칙은 반드시 낡는다.

몇 번이나 같은 일을 겪었다. 모델을 바꾸면 그 위에서 정한 상수가 낡고,
기능을 늘리면 선언이 낡고, 수치를 다시 재면 문서가 낡는다. "예외 0건" 은
그 어느 것도 잡지 못한다. 그래서 **기계가 확인할 수 있는 것**만 모았다.

    python check_consistency.py
"""
from __future__ import annotations
import inspect
import io
import re
import sys

FAIL = []


def check(name):
    def deco(fn):
        def wrap():
            try:
                msg = fn()
            except Exception as e:  # 검사 자체가 깨지면 그것도 실패다
                FAIL.append((name, f"{type(e).__name__}: {e}"))
                return
            if msg:
                FAIL.append((name, msg))
            print(f"  {'OK ' if not msg else '!! '}{name}")
            if msg:
                for ln in str(msg).splitlines():
                    print(f"        {ln}")
        wrap._n = name
        return wrap
    return deco


@check("스킬의 선언(input_schema)과 구현(run 인자)이 일치하는가")
def c1():
    import skills.core, skills.kitchen_skills, skills.data_skills, skills.design_skills
    bad = []
    for mod in (skills.core, skills.kitchen_skills,
                skills.data_skills, skills.design_skills):
        for _, o in vars(mod).items():
            if (inspect.isclass(o) and getattr(o, "name", None)
                    and o.__module__ == mod.__name__):
                sch = set(o.input_schema or {})
                sig = inspect.signature(o.run)
                args = {p for p in sig.parameters
                        if p != "self" and not p.startswith("_")}
                miss = sorted(a for a in args if a not in sch)
                if miss:
                    bad.append(f"{o.name}: 문서에 없는 인자 {miss}")
    return "\n".join(bad)


@check("시뮬레이터가 시간 스텝 크기에 의존하지 않는가")
def c2():
    import kitchen as K
    import dryer as D
    out = []

    vals = []
    for dt in (1.0, 0.5, 0.25, 0.1):
        K.reset(seed=4)
        K.COOKER.start(620, 0, power=3, capacity_g=1100)
        K.COOKER.deterministic = True
        for _ in range(int(round(8 / dt))):
            K.COOKER.tick(dt)
        vals.append(K.COOKER.state()["mass_g"])
    if max(vals) - min(vals) > max(vals) * 0.02:
        out.append(f"조리기: 스텝별 질량 {['%.1f' % v for v in vals]} — 2% 초과")

    vals = []
    for dt in (1.0, 0.5, 0.25, 0.1):
        D.DRYER.start(moisture=0.18, power=2)
        D.DRYER.deterministic = True
        for _ in range(int(round(5 / dt))):
            D.DRYER.tick(dt)
        vals.append(D.DRYER.state()["moisture"])
    if max(vals) - min(vals) > max(vals) * 0.02:
        out.append(f"건조기: 스텝별 함수율 {['%.4f' % v for v in vals]} — 2% 초과")
    return "\n".join(out)


@check("예측이 실제 난수 흐름을 오염시키지 않는가")
def c3():
    import kitchen as K
    got = []
    for predict in (False, True):
        K.reset(seed=11)
        K.COOKER.start(620, 0, power=3, capacity_g=1100)
        for _ in range(8):
            K.COOKER.tick(1.0)
            if predict:
                K.COOKER.predict_residual_g()
        got.append(K.COOKER.state()["mass_g"])
    return ("" if got[0] == got[1]
            else f"예측 호출이 결과를 바꾼다: {got[0]} vs {got[1]}")


@check("계획 그래프가 건전한가 (고아 요구·미사용 생성)")
def c4():
    from kitchen_domain import build_tasks
    prov, req = set(), set()
    for t in build_tasks({}):
        prov |= set(t.provides)
        req |= set(t.requires)
    bad = []
    if req - prov:
        bad.append(f"아무도 만들지 않는 요구: {sorted(req - prov)}")
    unused = prov - req - {"cooked", "cleaned"}
    if unused:
        bad.append(f"아무도 쓰지 않는 생성: {sorted(unused)}")
    return "\n".join(bad)


@check("문서의 수치가 실제 실행 결과와 같은가")
def c5():
    import json
    import os
    if not os.path.exists("scenarios.json"):
        return "scenarios.json 이 없다 — run_design.py 를 먼저 돌려야 한다"
    rows = {r["persona"]: r for r in json.load(
        open("scenarios.json", encoding="utf-8"))}
    doc = io.open("README.md", encoding="utf-8").read()
    bad = []
    for pid, label in (("p1_야근", "야근 1인"), ("p2_맞벌이", "맞벌이 2인"),
                       ("p3_알레르기", "알레르기 4인"), ("p4_퇴근길", "퇴근길 1인")):
        m = rows[pid]["verify"]["metrics"]
        heat = m.get("가열 시간(분)")
        # README 의 상황별 표에서 그 줄을 찾아 가열 시간을 대조한다
        line = next((l for l in doc.splitlines()
                     if l.startswith(f"| {label} |")), None)
        if line is None:
            bad.append(f"README 에 '{label}' 줄이 없다")
            continue
        if f"{heat}분" not in line:
            bad.append(f"{label}: 실행 {heat}분 인데 README 에는 "
                       f"{re.findall(r'[0-9.]+분', line)}")
    return "\n".join(bad)


@check("결과 지표가 장면 검증이 요구하는 이름을 쓰는가")
def c6():
    import json
    import os
    if not os.path.exists("scenarios.json"):
        return "scenarios.json 없음"
    bad = []
    for r in json.load(open("scenarios.json", encoding="utf-8")):
        for b in r["verify"]["beat_check"]:
            if not b["ok"]:
                bad.append(f"{r['persona']}: {b['why']}")
    return "\n".join(bad)


@check("스킬 사이를 잇는 ctx 키가 모두 맞물리는가")
def c7():
    """ctx 는 스킬과 스킬을 잇는 유일한 통로다.

    쓰기만 하고 아무도 읽지 않는 키는 **계산해 놓고 버리는 것**이고,
    읽는데 아무도 쓰지 않는 키는 **끊긴 연결**이다. 둘 다 조용히 지나간다 —
    전자는 값이 사라지고 후자는 None 이 되어 기본값으로 흐른다.
    실제로 '기록 출처'(내 기록 vs 공개 레시피)가 계산만 되고 버려지고 있었다.
    """
    QUOTE = "[\"']"
    W_PAT = re.compile(r"ctx\[" + QUOTE + r"([^\"']+)" + QUOTE + r"\]\s*=")
    SD_PAT = re.compile(r"ctx\.setdefault\(" + QUOTE + r"([^\"']+)" + QUOTE)
    RD_PAT = re.compile(r"ctx\[" + QUOTE + r"([^\"']+)" + QUOTE + r"\]")
    GET_PAT = re.compile(r"ctx\.get\(" + QUOTE + r"([^\"']+)" + QUOTE)
    ASSIGN = re.compile(r"ctx\[" + QUOTE + r"[^\"']+" + QUOTE + r"\]\s*=(?!=)")

    src = io.open("kitchen_domain.py", encoding="utf-8").read().splitlines()
    W, R = set(), set()
    for ln in src:
        W |= set(W_PAT.findall(ln))
        W |= set(SD_PAT.findall(ln))
        R |= set(RD_PAT.findall(ASSIGN.sub("", ln)))   # 대입 좌변은 빼고 센다
        R |= set(GET_PAT.findall(ln))

    seeded = {"pantry_refill", "time_budget_min", "touches"}
    bad = []
    orphan = sorted(k for k in W if k not in R)
    if orphan:
        bad.append(f"쓰기만 하고 읽지 않는 키: {orphan}")
    dangling = sorted(k for k in R if k not in W and k not in seeded)
    if dangling:
        bad.append(f"읽는데 아무도 쓰지 않는 키: {dangling}")
    return "\n".join(bad)


@check("스킬이 적어 둔 requires/provides 가 Task 선언과 어긋나지 않는가")
def c8():
    """스킬 클래스의 선언은 **문서용**이다 — 플래너는 Task 만 본다.

    문서용이라 깨져도 아무 일이 안 일어나고, 그래서 조용히 낡는다.
    비워 둔 것(기기마다 달라지는 스킬)은 눈감고, **적어 놓고 틀린 것**만 잡는다.
    """
    from kitchen_domain import build_tasks
    from skills import REGISTRY
    bad = []
    for t in build_tasks({}):
        sk = REGISTRY.get(t.skill)
        for attr in ("requires", "provides"):
            declared = set(getattr(sk, attr, ()) or ())
            actual = set(getattr(t, attr, ()) or ())
            if declared and declared != actual:
                bad.append(f"{t.skill}.{attr}: 스킬은 {sorted(declared)} 인데 "
                           f"Task 는 {sorted(actual)}")
    return "\n".join(bad)


def main():
    print("=" * 78)
    print("일관성 검사 — 기계가 확인할 수 있는 것만")
    print("=" * 78)
    for fn in [v for k, v in sorted(globals().items())
               if k.startswith("c") and callable(v) and hasattr(v, "_n")]:
        fn()
    print("-" * 78)
    if FAIL:
        print(f"  {len(FAIL)}건 불일치")
        return 1
    print("  전부 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
