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


@check("문서에 적힌 기기 측정값이 variance.json 과 같은가")
def c5b():
    """c5 는 **페르소나별 가열 시간**만 본다. 그래서 건조기 수치가 낡은 것을
    한 번 놓쳤다 — 여열 보정을 넣어 11.4분 → 10.6분이 됐는데 문서 5곳이
    옛 값 그대로였다(README·AGENT 정의서·재사용 방안·영상 대본·설명).

    여기서는 `variance.json` 의 값이 문서에 그대로 적혀 있는지 대조한다.
    값이 바뀌면 문서를 고치기 전까지 이 검사가 실패한다.
    """
    import json
    import os
    if not os.path.exists("variance.json"):
        return "variance.json 이 없다 — measure_variance.py 를 먼저 돌려야 한다"
    v = json.load(io.open("variance.json", encoding="utf-8"))["converge"]

    # (문서, 그 문서에 반드시 있어야 하는 문자열, 무엇인가)
    need = []
    for key, unit, what in (("cooker_steps", "분", "조리기 가열 시간"),
                            ("dryer_steps", "분", "건조기 건조 시간")):
        med = v[key]["median"]
        need.append((f"{med}{unit}", f"{what} 중앙값"))
    lo, hi = v["dryer_final"]["min"], v["dryer_final"]["max"]
    need.append((f"{lo}~{hi}", "건조기 최종 함수율 범위"))

    DOCS = ("README.md", "AGENT_정의서.md")
    bad = []
    for doc in DOCS:
        if not os.path.exists(doc):
            continue
        text = io.open(doc, encoding="utf-8").read()
        for token, what in need:
            if token not in text:
                bad.append(f"{doc}: {what} 이 현재 측정값({token})과 다르다")
    return chr(10).join(bad)


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


@check("저장된 산출물이 현재 코드보다 오래되지 않았는가")
def c9():
    """`scenarios.json` 같은 실행 결과는 저장소에 커밋된다 — 산출물이니까.

    그런데 코드를 고친 뒤 다시 돌리지 않으면 **낡은 결과가 저장소에 남는다.**
    실제로 `variance.json` 이 코드보다 79분 낡은 채로 있었다. 그 사이
    건조기 물리와 측정 조건이 바뀌었으니 그 수치는 이미 틀린 것이었다.
    """
    import glob
    import os
    srcs = [f for f in glob.glob("*.py") + glob.glob("skills/*.py")
            if not os.path.basename(f).startswith("check_")]
    newest = max((os.path.getmtime(f) for f in srcs), default=0)
    bad = []
    for out in ("scenarios.json", "trace_design.json", "variance.json"):
        if not os.path.exists(out):
            bad.append(f"{out} 이 없다")
            continue
        age = newest - os.path.getmtime(out)
        if age > 60:                       # 코드보다 1분 이상 오래됐으면
            bad.append(f"{out} 이 코드보다 {age / 60:.0f}분 오래됐다 — 다시 돌려야 한다")
    return "\n".join(bad)


@check("문서의 서술 주장이 실행 결과와 맞는가")
def c10():
    """수치만 맞다고 주장이 맞는 것은 아니다.

    "냄비 앞을 지키는 시간이 조리 시간의 **4분의 1 안팎**" 이라고 적었는데
    실제로는 24~55%(중앙 46%)였다. 4분의 1은 4인분 하나뿐이었다.
    "처음 사는 품목 **두 건**" 도 틀렸다 — 하나는 배송 기한 초과였다.
    숫자 하나하나는 맞는데 그것을 묶어 말한 문장이 틀린 경우다.
    """
    import json
    import os
    if not os.path.exists("scenarios.json"):
        return "scenarios.json 없음"
    rows = {r["persona"]: r for r in json.load(
        open("scenarios.json", encoding="utf-8"))}
    doc = io.open("README.md", encoding="utf-8").read()
    bad = []

    # 지켜보는 시간의 비율
    rat = []
    for r in rows.values():
        m = r["verify"]["metrics"]
        a, c = m.get("지켜보는 시간(분)"), m.get("가열 시간(분)")
        if a and c:
            rat.append(a / c)
    if rat:
        lo, hi = round(min(rat) * 100), round(max(rat) * 100)
        flat = doc.replace(" ", "")
        # 문서는 "24~55%" 로도 "24%~55%" 로도 쓸 수 있다. 둘 다 받는다.
        if not any(f"{lo}~{hi}%" in flat or f"{lo}%~{hi}%" in flat
                   for _ in (0,)):
            bad.append(f"지켜보는 시간 비율이 실제 {lo}~{hi}% 인데 "
                       f"문서에 그 범위가 없다")

    # 확인 요청의 사유가 서로 다른데 한 가지로 뭉뚱그리지 않았는가
    reasons = rows["p3_알레르기"]["verify"]["metrics"].get("확인 요청", [])
    if len(reasons) >= 2 and "처음 사는 품목 2건" in doc:
        bad.append("확인 요청 사유가 서로 다른데 문서는 '처음 사는 품목 2건' 이라 적었다")

    # 기록 출처 구분이 문서와 맞는가
    pub = [p for p, r in rows.items()
           if r["verify"]["metrics"].get("기록 출처") == "공개 레시피"]
    if len(pub) == 1 and "공개 레시피" not in doc:
        bad.append("한 상황만 공개 레시피를 쓰는데 문서에 그 구분이 없다")
    return "\n".join(bad)


@check("README 의 구조·실행 설명이 실제 파일·상황 수와 맞는가")
def c11():
    """구조 설명은 파일이 늘어도 자동으로 바뀌지 않는다.

    실제로 페르소나를 3종에서 4종으로 늘린 뒤에도 README 는 "고객 상황 3종"
    이었고, 검사 스크립트 2개는 목록에 아예 없었다.
    """
    import os
    import personas
    doc = io.open("README.md", encoding="utf-8").read()
    bad = []

    n = len(personas.ids())
    if f"고객 상황 {n}종" not in doc:
        bad.append(f"페르소나가 {n}종인데 README 구조 설명이 다르다")
    if f"고객 상황 {n}건" not in doc:
        bad.append(f"페르소나가 {n}개인데 실행 설명이 다르다")

    # 주요 스크립트가 구조 설명에 있는가
    i = doc.find("## 구조")
    block = doc[i:doc.find("##", i + 5)] if i >= 0 else ""
    for f in ("run_design.py", "planner.py", "kitchen_domain.py", "store.py",
              "check_regression.py", "check_consistency.py", "stress_test.py"):
        if os.path.exists(f) and f not in block:
            bad.append(f"{f} 가 구조 설명에 없다")
    return "\n".join(bad)


@check("같은 구조의 기기가 같은 물리 기능을 갖는가")
def c12():
    """한 기기를 고치고 다른 기기를 안 고치는 일이 반복됐다.

    · 시간 스텝 버그를 조리기만 고치고 건조기는 그대로 뒀다(2026-09-25).
    · 여열 예측·여열 구간을 조리기에만 넣어, 건조기는 목표 0.08 에
      0.0797 로 꺼 놓고 문 열 때 0.0734 였다 — 8.2% 과건조(2026-09-26).

    "같은 스킬이 다른 기기에서 동작한다" 가 제안의 핵심인데, 기기 쪽
    물리가 서로 다르면 그 주장을 뒷받침하지 못한다. 그래서 두 기기가
    같은 이름의 능력을 갖는지 기계로 대조한다.
    """
    import kitchen as K
    import dryer as D

    # (조리기 이름, 건조기 이름, 무엇인가)
    PAIRS = [("tick", "tick", "시간 진행"),
             ("state", "state", "관측"),
             ("set_power", "set_power", "조작"),
             ("stop", "stop", "정지"),
             ("predict_residual_g", "predict_residual", "여열 예측"),
             ("rest_until_still", "rest_until_still", "여열 구간")]
    bad = []
    for a, b, what in PAIRS:
        if not hasattr(K.COOKER, a):
            bad.append(f"조리기에 {a} 가 없다 ({what})")
        if not hasattr(D.DRYER, b):
            bad.append(f"건조기에 {b} 가 없다 ({what}) — "
                       f"조리기에는 {a} 가 있다")
    for dev, name in ((K.COOKER, "조리기"), (D.DRYER, "건조기")):
        if not hasattr(dev, "deterministic"):
            bad.append(f"{name}에 deterministic 플래그가 없다 — 예측이 "
                       f"실제 난수를 오염시킨다")
        if not hasattr(dev, "REST_MIN"):
            bad.append(f"{name}에 REST_MIN 이 없다 — 여열 구간의 길이가 "
                       f"정의되지 않았다")

    # 여열 보정이 실제로 이득인지 건조기에서 직접 잰다.
    # 값이 아니라 **방향**만 본다 — 상수를 바꾸면 값은 달라진다.
    from skills import REGISTRY
    conv = REGISTRY.get("converge")
    errs = {}
    for use in (False, True):
        D.DRYER.deterministic = True
        D.DRYER.start(moisture=0.18, power=2)
        kw = dict(observe=lambda: D.DRYER.state(), actuate=D.DRYER.set_power,
                  step=D.DRYER.tick, metric="moisture", target=0.08,
                  direction="down", max_steps=40)
        if use:
            kw["residual"] = lambda _s: D.DRYER.predict_residual()
        conv.run(**kw)
        errs[use] = abs(0.08 - D.DRYER.rest_until_still()["moisture"])
    if errs[True] >= errs[False]:
        bad.append(f"건조기에서 여열 보정이 이득이 아니다 — "
                   f"보정 {errs[True]:.4f} vs 미보정 {errs[False]:.4f}")
    return chr(10).join(bad)


@check("제안서(HWP)의 수치 주장이 실행 결과와 같은가")
def c13():
    """문서(.md)는 대조해 왔지만 **정작 제출하는 제안서는 대조한 적이 없었다.**
    실제로 여열 잠열을 반영하고 상수를 재보정한 뒤, 제안서에 적힌
    "목표 0.78의 양옆 0.005 안" 이 실제 -0.0099~+0.0002 로 어긋나 있었다.

    PDF 에서 문자열을 찾는 방식이라 문장 구조는 못 보지만, 숫자가
    낡는 것은 잡는다. PyMuPDF 가 없으면 건너뛴다(없는 것이 실패는 아니다).
    """
    import glob
    import json
    import os
    try:
        import fitz
    except ImportError:
        return ""
    import re as _re
    pdfs = glob.glob("../LG_AX해커톤_제안서_v*.pdf")
    if not pdfs:
        return ""
    # 문자열 정렬이면 v9 가 v17 보다 뒤로 간다. 번호로 정렬한다.
    def _ver(path):
        m = _re.search(r"_v(\d+)", os.path.basename(path))
        return int(m.group(1)) if m else -1
    latest = max(pdfs, key=_ver)
    text = chr(10).join(pg.get_text() for pg in fitz.open(latest))

    cv = json.load(io.open("variance.json", encoding="utf-8"))["converge"]
    rows = {r["persona"]: r for r in
            json.load(io.open("scenarios.json", encoding="utf-8"))}
    m3 = rows["p3_알레르기"]["verify"]["metrics"]

    lo, hi = cv["cooker_final"]["min"], cv["cooker_final"]["max"]
    dlo, dhi = lo - 0.78, hi - 0.78
    watch = []
    for pid in rows:
        mm = rows[pid]["verify"]["metrics"]
        heat = mm.get("가열 시간(분)")
        hands = mm.get("손이 가는 일", "")
        n = int(str(hands).split("회")[0]) if "회" in str(hands) else None
        if heat and n:
            watch.append(n / heat)

    need = [
        (f"{lo}~{hi}", "200회 질량비 범위"),
        (f"{cv['dryer_final']['min']}~{cv['dryer_final']['max']}",
         "200회 함수율 범위"),
        (f"{dlo:+.3f}~{dhi:+.3f}".replace("+0.000", "+0.000"),
         "목표 대비 범위"),
        (str(m3.get("자료 선별", "")).split("후보 ")[-1].split("건")[0] + "건",
         "자료 선별 후보 수"),
    ]
    bad = [f"제안서({os.path.basename(latest)})에 '{v}'({why})가 없다"
           for v, why in need if v not in text]
    if watch:
        lo_w, hi_w = min(watch) * 100, max(watch) * 100
        # 문서는 반올림해 적으므로 ±1%p 는 허용한다
        import re
        mm = re.search(r"(\d+)~(\d+)%", text)
        if mm:
            a, b = int(mm.group(1)), int(mm.group(2))
            if not (abs(a - lo_w) <= 1.5 and abs(b - hi_w) <= 1.5):
                bad.append(f"지켜보는 비율: 제안서 {a}~{b}% / 실측 "
                           f"{lo_w:.1f}~{hi_w:.1f}%")
    return chr(10).join(bad)


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
