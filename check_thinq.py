# -*- coding: utf-8 -*-
"""자연어 다리를 확인한다 — **키 없이도.**

`thinq.py` 는 LLM 호출을 주입받는다. 그래서 API 키가 없어도 검증할 수
있다 — 가짜 LLM 을 넣으면 된다. 스킬이 관측·조작 함수를 주입받는 것과
같은 구조이고, 그 덕에 `agent.py` 처럼 "구현했지만 검증 안 됨" 이 되지
않는다.

여기서 보는 것 (앞의 둘이 핵심이다)
  1 안전     기피 재료가 재고로 들어가지 않는가
  2 불신     LLM 이 준 값을 그대로 쓰지 않는가 (형식·범위 검사)
  3 지어냄   설명에 실행 결과에 없는 수가 섞이면 알아차리는가
  4 폴백     키가 없어도 끝까지 도는가
  5 정직     못 한 경우에 못 했다고 말하는가
  6 불변     LLM 을 넣어도 **제어 결과가 같은가** (판단은 측정이 한다)
"""
from __future__ import annotations
import json
import sys

import thinq

CHECKS = []


def check(title):
    def deco(fn):
        CHECKS.append((title, fn))
        return fn
    return deco


def fake_ask(reply: str):
    """정해진 답만 돌려주는 가짜 LLM."""
    return lambda prompt: reply


# ── 1 안전 ─────────────────────────────────────────────────────────────
@check("기피 재료가 냉장고 재고로 들어가지 않는다")
def c1():
    """'새우 알레르기 있어' 에서 새우를 재고로 읽으면, 먹으면 안 되는
    것을 재료로 쓰게 된다. 실제로 한 번 그렇게 읽었다."""
    u = thinq.understand("우리 넷이 먹을 건데 새우 알레르기 있는 사람 있어")
    p = u["persona"]
    names = [x["name"] for x in p["fridge"]]
    ok = ("새우" in p["avoid"] and "새우" not in names
          and p["household_size"] == 4)
    return ok, (f"기피 {p['avoid']} · 재고 {names} · {p['household_size']}인")


@check("안전 항목은 읽었어도 사람 확인을 요청한다")
def c2():
    u = thinq.understand("땅콩 알레르기 있어")
    ok = "avoid" in u["needs_confirm"]
    return ok, f"확인 요청 {u['needs_confirm']}"


@check("한 글자 재료가 엉뚱한 말에서 걸리지 않는다")
def c3():
    """'아무것도' 에 '무' 가 들어 있다. 부분 일치로 찾으면 재고가 생긴다."""
    u = thinq.understand("냉장고에 아무것도 없어. 8시 도착이고 45분 있어")
    names = [x["name"] for x in u["persona"]["fridge"]]
    ok = names == []
    return ok, f"재고 {names} (비어 있어야 한다)"


@check("범주어('갑각류')가 실제 레시피를 거른다")
def c3b():
    """c1 은 '새우 알레르기' 처럼 **구체어만** 시험했다. 사람은 '갑각류
    못 먹어' 라고 말한다. LLM 이 '갑각류' 를 정확히 읽었는데도 판정이
    문자열 일치라 갑각류가 든 공개 레시피 15건이 **하나도 안 걸렀다.**
    읽기가 옳아도 받는 쪽이 범주를 모르면 소용없다 — 그래서 끝까지 본다."""
    import kitchen_domain as kd
    from recipe_parse import contains_any, expand_avoid
    from skills import REGISTRY
    SHELL = ("새우", "꽃게", "대게", "랍스터", "가재", "크랩")

    def shellfish(pool):
        return [x["menu"] for x in pool
                if any(w in (x.get("parts_raw") or "") for w in SHELL)]
    src = REGISTRY.get("recipe_source")
    before = src.run(load=kd.load_recipes, match=contains_any,
                     avoid=["갑각류"]).output["recipe_pool"]
    av, _, _ = expand_avoid(["갑각류"])
    after = src.run(load=kd.load_recipes, match=contains_any,
                    avoid=av).output["recipe_pool"]
    # 푼 뒤에 '얇게' 같은 부사가 게로 걸리지 않는지도 본다
    thin = [x["menu"] for x in before if "얇게" in (x.get("parts_raw") or "")
            and not any(w in (x.get("parts_raw") or "") for w in SHELL)]
    wrongly = [m for m in thin if m not in [x["menu"] for x in after]]
    ok = shellfish(before) and not shellfish(after) and not wrongly
    return ok, (f"글자 그대로면 갑각류 {len(shellfish(before))}건 통과 → "
                f"풀면 {len(shellfish(after))}건 · '얇게' 오탐 {len(wrongly)}건")


@check("규칙 파서가 '못 드셔'·조사 붙은 기피를 읽는다")
def c3c():
    cases = {"며느리가 갑각류를 못 드셔": ["갑각류"],
             "새우를 못 먹어": ["새우"],
             "땅콩 알레르기 있어": ["땅콩"]}
    got = {t: thinq.understand(t)["persona"]["avoid"] for t in cases}
    ok = all(got[t] == want for t, want in cases.items())
    return ok, " / ".join(f"{t[-8:]}→{v}" for t, v in got.items())


@check("안전 항목은 승인 없이 실행하지 않는다")
def c3d():
    """처음엔 needs_confirm 을 결과에 적기만 하고 그대로 실행했다.
    경고는 아무것도 막지 않는다."""
    T = "갑각류 알레르기 있어. 7시 도착"
    a = thinq.run(T)                                   # 승인 없음
    b = thinq.run(T, approve=lambda c: False)          # 거절
    c = thinq.run(T, approve=lambda c: True)           # 승인
    ok = (a["stopped"] and a["result"] is None
          and b["stopped"] and b["result"] is None
          and not c["stopped"] and c["result"] is not None
          and "새우" in a["confirm"]["expanded_to"])
    return ok, (f"승인 없음 {'멈춤' if a['stopped'] else '실행'} · 거절 "
                f"{'멈춤' if b['stopped'] else '실행'} · 승인 "
                f"{'멈춤' if c['stopped'] else '실행'} · 확인 문구에 푼 목록 포함")


@check("풀 수 없는 범주어면 승인이 있어도 멈춘다")
def c3e():
    """'곡류' 라는 글자는 레시피에 없어서 아무것도 안 걸러진다. 그런데
    사용자는 걸러졌다고 믿는다 — 조용히 실행하면 안 된다."""
    o = thinq.run("곡류 알레르기 있어", approve=lambda c: True)
    ok = o["stopped"] and o["confirm"]["unresolved"] == ["곡류"]
    return ok, o["explained"]["text"][:60]


# ── 2 불신 ─────────────────────────────────────────────────────────────
@check("LLM 이 준 터무니없는 값을 거른다")
def c4():
    """LLM 은 그럴듯한 값을 지어낼 수 있다. 시간이 9999분이거나 인원이
    0명이면 뒤의 계획이 조용히 이상해진다."""
    bad = json.dumps({"time_budget_min": 9999, "household_size": 0,
                      "arrive_home": "저녁쯤", "avoid": "새우",
                      "몰래끼워넣기": 1})
    u = thinq.understand("아무말", ask=fake_ask(bad))
    p = u["persona"]
    ok = (p["time_budget_min"] == thinq._DEFAULT["time_budget_min"]
          and p["household_size"] == thinq._DEFAULT["household_size"]
          and p["arrive_home"] == thinq._DEFAULT["arrive_home"]
          and p["avoid"] == []
          and len(u["rejected"]) == 5)
    return ok, f"{len(u['rejected'])}건 거름: " + " / ".join(u["rejected"][:3])


@check("LLM 이 옳은 값을 주면 그대로 쓴다")
def c5():
    good = json.dumps({"time_budget_min": 25, "household_size": 2,
                       "arrive_home": "21:40", "avoid": ["표고버섯"],
                       "friction_reported": ["냄비 앞을 지키는 일"]})
    u = thinq.understand("아무말", ask=fake_ask(good))
    p = u["persona"]
    ok = (p["time_budget_min"] == 25 and p["household_size"] == 2
          and p["arrive_home"] == "21:40" and p["avoid"] == ["표고버섯"]
          and not u["rejected"])
    return ok, (f"예산 {p['time_budget_min']}분 · {p['household_size']}인 · "
                f"귀가 {p['arrive_home']} · 기피 {p['avoid']}")


@check("JSON 이 아닌 답이 와도 무너지지 않는다")
def c6():
    for reply in ("미안, 잘 모르겠어", "", "{깨진 json", "null"):
        u = thinq.understand("아무말", ask=fake_ask(reply))
        if u["persona"]["arrive_home"] != thinq._DEFAULT["arrive_home"]:
            return False, f"{reply!r} 에서 기본값으로 가지 않았다"
    return True, "4가지 이상한 답에서 모두 기본값으로 돌아갔다"


# ── 3 지어냄 ───────────────────────────────────────────────────────────
@check("설명에 실행 결과에 없는 수가 섞이면 알아차린다")
def c7():
    """LLM 이 '12분 걸립니다' 를 지어내면 사용자가 그것을 믿는다.
    그래서 답에 있는 수를 실행 결과와 대조한다."""
    m = {"가열 시간(분)": 5.5, "세척 코스": "에코"}
    fake = {"verify": {"metrics": m}}
    good = thinq.explain(fake, ask=fake_ask("가열은 5.5분이고 세척은 에코입니다"))
    bad = thinq.explain(fake, ask=fake_ask("가열은 12분이고 물 999L 를 씁니다"))
    ok = not good["invented"] and set(bad["invented"]) >= {"12", "999"}
    return ok, f"맞는 답 {good['invented']} · 지어낸 답 {bad['invented']}"


# ── 4·5 폴백과 정직 ────────────────────────────────────────────────────
@check("키가 없어도 자연어 한 줄에서 실행까지 간다")
def c8():
    out = thinq.run("혼자 먹어. 된장이랑 배추 두부 있고 7시 도착")
    m = out["result"]["verify"]["metrics"]
    ok = (out["understood"]["by"] == "규칙" and m.get("메뉴") == "된장찌개"
          and m.get("가열 시간(분)"))
    return ok, (f"{out['understood']['by']}로 읽어 {m.get('메뉴')} · "
                f"가열 {m.get('가열 시간(분)')}분")


@check("다시 계획했으면 그렇다고, 예산을 넘으면 넘는다고, 못 만들면 못 만든다고 말한다")
def c9():
    """(가) 재고로는 못 만들어 장보기를 넣어 다시 계획한 경우 — 전에는 여기서
    "만들 수 있는 것이 없다" 로 멈췄다. 이제 만들되, 예산을 넘으면 넘는다고
    말해야 한다. (나) 다시 계획해도 정말 못 만드는 경우는 못 만든다고
    말해야 한다. '-로 정했습니다' 처럼 빈 값을 읽어 주면 안 된다."""
    # 장보기를 넣어 다시 짜려면 사람이 산다고 승인해야 한다(10/1 부터 실제
    # 경로는 승인 없이 사지 않는다) — 이 시험은 재계획 설명을 본다
    a = thinq.run("배추랑 두부만 있어. 9시 반 도착이고 30분 있어",
                  approve=lambda c: True)
    ta, va = a["explained"]["text"], a["result"]["verify"]
    ok_a = ("다시 계획" in ta and ("모자랍니다" in ta) == va.get("over_budget")
            and (va.get("over_budget") is False or not va["verified"]))
    everything = ["대두", "갑각류", "유제품", "난류", "글루텐", "생선류", "견과류",
                  "조개류", "두족류"]
    fake = json.dumps({"arrive_home": "21:30", "time_budget_min": 10,
                       "avoid": everything})
    # 상황만 가짜 LLM 으로 읽고, 설명은 규칙으로 본다(가짜 LLM 은 설명
    # 요청에도 같은 JSON 을 돌려주므로 설명 시험이 되지 않는다)
    b = thinq.run("x", ask=lambda p: fake if "사용자 말:" in p else "",
                  approve=lambda c: True)
    tb = thinq.rule_explain(b["result"])
    ok_b = "만들 수 있는 것이 없" in tb and "-로 정했" not in tb
    return ok_a and ok_b, f"(가) {ta[:70]} · (나) {tb[:50]}"


# ── 6 불변 : 판단은 측정이 한다 ────────────────────────────────────────
@check("LLM 을 써도 제어 결과가 달라지지 않는다")
def c10():
    """이 설계의 핵심이다. LLM 은 **양 끝에만** 있고 제어 루프에는
    없다. 같은 상황을 규칙으로 읽든 LLM 으로 읽든, 읽은 상황이 같으면
    실행 결과도 같아야 한다 — 그래야 200회 반복 측정과 회귀 검사가
    계속 의미를 가진다.
    """
    # **읽은 상황이 정말 같아야** 비교가 된다. 처음엔 이 답에 재고가 없었는데
    # 규칙은 문장에서 된장·배추·두부를 읽었다. 둘 다 우연히 장을 봐서 같게
    # 나왔을 뿐이고, 재계획을 넣자 차이가 드러났다.
    same = json.dumps({"arrive_home": "19:00", "time_budget_min": 45,
                       "household_size": 1,
                       "fridge": [{"name": "배추"}, {"name": "두부"},
                                  {"name": "된장"}],
                       "friction_reported": ["냄비 앞을 지키는 일"]})
    a = thinq.run("된장이랑 배추 두부 있고 7시 도착", ask=None)
    b = thinq.run("된장이랑 배추 두부 있고 7시 도착", ask=fake_ask(same))

    def fp(o):
        v = o["result"]["verify"]
        return (tuple(o["result"]["flow"]["steps"]), v["user_touches"],
                v["metrics"].get("가열 시간(분)"),
                v["metrics"].get("최종 질량비"))
    ok = fp(a) == fp(b)
    return ok, (f"규칙 {fp(a)} · LLM {fp(b)}"
                if not ok else f"둘 다 {fp(a)}")


# ── 7 설계 : LLM 이 장면을 제안하고 코드·실행이 확정한다 ─────────────────
def _design(pid, reply, persona_reply=None):
    """페르소나 하나를 LLM 장면 제안으로 설계한다. reply 는 가짜 LLM 답."""
    import contextlib
    import io
    import run_design
    from orchestrator import Trace
    from llm_design import make_llm_beats
    from kitchen_domain import kitchen_beats, kitchen_capabilities
    ask = reply if callable(reply) else (lambda p: reply)
    fac = (lambda fo: make_llm_beats(ask, kitchen_capabilities, kitchen_beats, fo))
    with contextlib.redirect_stdout(io.StringIO()):
        return run_design.design_for(pid, Trace(), beats_factory=fac)


def _no_leave(pid):
    """퇴근 정보를 지운 판. '퇴근 시각을 모르고 조달을 건너뛰는 가구' 가
    필요한 시험은 가구를 빌리지 않고 이렇게 만든다 — 가구의 퇴근 정보가
    바뀌어도(9/29 p3, 9/30 p1 을 잠시) 시험의 전제가 그대로다."""
    import personas
    p = personas.get(pid)
    for k in ("leave_office", "commute_min", "leave_source"):
        p.pop(k, None)
    new = pid + "_퇴근모름"
    personas.PERSONAS[new] = dict(p, id=new)
    return new


def _scenes(*scenes, uncovered=()):
    return json.dumps({"scenes": [
        {"offset_min": o, "user": u, "system": sy, "skill": sk, "friction": f}
        for o, u, sy, sk, f in scenes], "uncovered": list(uncovered)})


@check("말로 한 불편을 틀은 못 덮고 LLM 제안은 덮는다")
def d1():
    """틀은 '덮었다' 를 문자열 포함으로 센다. 사람 말은 틀의 글자와 다르다."""
    per = json.dumps({"arrive_home": "19:00", "time_budget_min": 45,
                      "household_size": 2,
                      "friction_reported": ["냄비 앞에서 기다리는 것",
                                            "퇴근하고 뭐 해먹을지 고민하는 것"]})
    sc = _scenes((0, "메뉴를 고민하지 않는다", "재고에 맞는 메뉴를 골라 둔다",
                  "menu", [1]),
                 (5, "부를 때만 온다", "화력을 맞추고 다 되면 끈다",
                  "converge", [0]))
    # 두 판 모두 사람이 장보기를 승인한다 — 같은 계획에서 덮는 수만 비교한다
    yes = lambda c: True
    a = thinq.run("아무말", ask=lambda p: per, approve=yes)   # 설명도 같은 답 — 무관
    b = thinq.run("아무말",
                  ask=lambda p: sc if "시나리오 설계자" in p else per, approve=yes)
    ca, cb = a["result"]["scenario"], b["result"]["scenario"]
    ok = ca["covered"] == 0 and cb["covered"] == 2
    return ok, f"틀 {ca['covered']}/{ca['total_friction']} → LLM {cb['covered']}/{cb['total_friction']}"


@check("이번 계획에 없는 기능을 약속한 장면은 버린다")
def d2():
    """퇴근 시각을 모르는 p1 은 조달을 건너뛴다. '주문한다' 장면은 실행되지
    않을 약속이다. 처음엔 플래너를 직접 불러 p1 에 procure 가 허용됐다."""
    sc = _scenes((0, "장을 안 본다", "부족분을 주문한다", "procure", [0]),
                 (1, "썰지 않는다", "로봇팔이 썰어 준다", "robot_arm", [0]),
                 (5, "부를 때만 온다", "화력을 맞춘다", "converge", [1]))
    r = _design(_no_leave("p1_야근"), sc)["design_report"]
    rej = " ".join(r["rejected"])
    ok = ("procure" in rej and "robot_arm" in rej
          and any("converge" in a for a in r["accepted"]))
    return ok, f"버림 {len(r['rejected'])}건(procure·robot_arm) · 받음 {len(r['accepted'])}건"


@check("실행 전에 수치를 약속한 장면은 버린다")
def d3():
    sc = _scenes((5, "부를 때만 온다", "12분 만에 끝낸다", "converge", [1]),
                 (5, "부를 때만 온다", "다 되면 스스로 끈다", "converge", [1]))
    r = _design("p1_야근", sc)["design_report"]
    ok = len(r["rejected"]) == 1 and "수치" in r["rejected"][0] \
        and len(r["accepted"]) == 1
    return ok, r["rejected"][0][:60] if r["rejected"] else "버린 것 없음"


@check("LLM 이 알레르기 장면을 빠뜨려도 틀 장면이 채운다")
def d4():
    """p3 의 '재료마다 못 먹는 것이 섞였는지 확인하는 일' 을 LLM 이 안
    덮으면, 그것을 덮는 틀 장면(menu 기피 필터)이 들어가야 한다."""
    sc = _scenes((10, "부를 때만 온다", "화력을 맞춘다", "converge", []))
    r = _design("p3_알레르기", sc)
    beats = r["scenario"]["beats"]
    safety = [b for b in beats if b.get("source") == "틀"
              and "못 먹는 것" in b["removes"] and b["verified_by"] == "menu"]
    ok = bool(safety) and any("menu" in f for f in r["design_report"]["filled"])
    return ok, f"틀로 채움 {r['design_report']['filled']}"


@check("LLM 이 실패하면 틀 장면으로 돌아간다")
def d5():
    def boom(p):
        raise RuntimeError("연결 끊김")
    outs = {"예외": boom, "빈 답": lambda p: "", "말만": lambda p: "죄송해요",
            "scenes 없음": lambda p: '{"hello": 1}'}
    bad = []
    for k, a in outs.items():
        r = _design("p1_야근", a)
        if r["design_report"]["by"] != "틀" or not r["verify"]["verified"]:
            bad.append(k)
    return not bad, ("4가지 모두 틀로 돌아가 시나리오 달성" if not bad
                     else f"실패: {bad}")


@check("불편과 짝 없는 장면은 받되 덮은 수에 안 센다")
def d6():
    sc = _scenes((10, "부를 때만 온다", "화력을 맞춘다", "converge", []))
    r = _design("p1_야근", sc)
    got = [b for b in r["scenario"]["beats"] if b.get("source") == "LLM"]
    from llm_design import BACKGROUND
    ok = got and got[0]["removes"] == BACKGROUND
    return ok, f"배경 장면 {len(got)}개 · 덮은 수 {r['scenario']['covered']}"


@check("선제 주문이 아니면 귀가 전 장면을 귀가 시각으로 옮긴다")
def d7():
    # 퇴근 시각을 모르는 가구로 잰다. p3 로 재다가 p3 에 퇴근 정보가 생겨
    # 깨졌다 — 가구를 빌리지 않고 **퇴근 정보를 지운 판**을 만든다.
    sc = _scenes((-50, "고민하지 않는다", "메뉴를 골라 둔다", "menu", [0]))
    r = _design(_no_leave("p1_야근"), sc)
    b = [x for x in r["scenario"]["beats"] if x.get("source") == "LLM"][0]
    ok = b["at"] == "21:40" and r["design_report"]["adjusted"]
    return ok, f"장면 시각 {b['at']} · {r['design_report']['adjusted'][:1]}"


@check("진짜 LLM 이 p3 에 준 답을 재생하면 같은 결과가 나온다")
def d8():
    """2026-09-28 에 claude CLI 가 준 원문(fixtures). 키 없이 재생한다.
    이 제안은 p3 수고 3건 중 2건을 덮고 남은 하나(조리 기구 분리)는 '덜어 줄
    기능이 없다' 고 스스로 밝혔다. (전에는 3/4 였다 — 같은 알레르기 확인이 고객
    문장과 상황 추론으로 두 번 세어졌다. 2026-09-30 에 합쳤다.)"""
    import pathlib
    raw = json.loads(pathlib.Path("fixtures/llm_p3_scenes.json")
                     .read_text(encoding="utf-8"))["raw"]
    r1, r2 = _design("p3_알레르기", raw), _design("p3_알레르기", raw)
    s1, v1 = r1["scenario"], r1["verify"]
    same = ([b["at"] + b["system"] for b in s1["beats"]]
            == [b["at"] + b["system"] for b in r2["scenario"]["beats"]])
    # 10/1: 냄비 기록 기능이 생겨, LLM 이 "덜어 줄 기능이 없다" 고 한 조리 기구
    # 불편을 틀 장면이 채운다(녹화는 그 기능 전). 그래서 3/3 이고, LLM 이 못 덮는다고
    # 밝힌 기록(uncovered)은 그대로 남는다.
    ok = (same and s1["covered"] == 3 and s1["total_friction"] == 3
          and v1["verified"] and r1["design_report"]["uncovered"]
          and not r1["design_report"]["rejected"])   # 번호가 밀려 버려지지 않는다
    return ok, (f"덮음 {s1['covered']}/{s1['total_friction']} · 장면 "
                f"{v1['beats_met']}/{v1['beats_total']} · 두 번 같음 {same} · "
                f"못 덮는다고 밝힘 {len(r1['design_report']['uncovered'])}건")


# ── 8 LLM 이 준 값이 물리·요리까지 이어질 때 ────────────────────────────
@check("LLM 이 준 재고 값을 계량·물리에 넣을 수 있는 형태로 만든다")
def e1():
    """'300g' 문자열이면 파이프라인이 예외로 죽었고, -200g 은 냄비에
    음수 질량으로 들어갔고, '두부 한 모' 는 메뉴는 있다고 보고 계량은
    못 찾았다(계량 실패). 호두·완두가 '호'·'완' 으로 잘리지 않는지도 본다."""
    items, notes = thinq._clean_fridge([
        {"name": "두부 한 모", "qty_g": "300g"}, {"name": "두부1모", "qty_g": 100},
        {"name": "배추", "qty_g": -200, "stored_days": -3},
        {"name": "호두"}, {"name": "완두"}, {"name": "배추 반 통"}])
    got = {x["name"]: x["qty_g"] for x in items}
    ok = (got.get("두부") == 400 and "호두" in got and "완두" in got
          and got.get("배추") == 300          # 음수는 버리고 '반 통' 만 남는다
          and any("불가능" in n for n in notes))
    fake = json.dumps({"arrive_home": "19:00", "time_budget_min": 45,
                       "fridge": [{"name": "두부 한 모"}, {"name": "배추 반 통"},
                                  {"name": "된장"}]})
    o = thinq.run("x", ask=lambda p: fake if "설계자" not in p else "")
    m = o["result"]["verify"]["metrics"]
    ok = ok and not m.get("계량 실패") and m.get("가열 시간(분)")
    return ok, f"재고 {got} · 계량 실패 {m.get('계량 실패')}"


@check("시·분 범위를 벗어난 시각을 거른다")
def e2():
    bad = [t for t in ("19:75", "25:00", "24:00") if thinq._is_clock(t)]
    good = [t for t in ("07:05", "23:59", "0:00") if not thinq._is_clock(t)]
    return not bad and not good, f"통과한 잘못된 시각 {bad} · 막힌 옳은 시각 {good}"


@check("보관일을 모르면 임박 판단도 물 계산도 지어내지 않는다")
def e3():
    """전에는 모르는 보관일을 3일로 채웠다. 보관일은 계량 때 채소에서
    나오는 물(하루 2%)까지 정하므로 **물 6% 를 지어내고** 있었다."""
    import kitchen as K
    from skills import REGISTRY
    r = REGISTRY.get("inventory").run(items=[
        {"name": "배추", "qty_g": 300, "stored_days": None, "shelf_life_days": None}])
    K.reset([{"name": "배추", "qty_g": 300, "stored_days": None,
              "shelf_life_days": None}], seed=7)
    w = K.prep_weigh("배추", 200)
    ok = r.output["count"] == 0 and w["expected_extra_water_g"] == 0
    return ok, (f"임박 {r.output['count']}건 · 지어낸 물 "
                f"{w['expected_extra_water_g']}g · {r.evidence[0][:30]}")


@check("글루텐·유당·견과를 풀고, 아무것도 안 걸리는 말은 그렇다고 알린다")
def e4():
    from recipe_parse import expand_avoid
    got = {a: expand_avoid([a])[0] for a in ("글루텐", "유당", "견과")}
    o = thinq.run("x", ask=lambda p: json.dumps(
        {"avoid": ["매운 것"]}) if "설계자" not in p else "")
    ok = ("밀가루" in got["글루텐"] and "우유" in got["유당"]
          and "호두" in got["견과"] and o["stopped"]
          and "아무것도 거르지 않습니다" in o["explained"]["text"])
    return ok, o["explained"]["text"][:80]


def _llm_scenes(pid, *scenes):
    import kitchen_domain as KD
    sc = _scenes(*scenes)
    import contextlib
    import io
    import run_design
    from orchestrator import Trace
    from llm_design import make_llm_beats
    fac = (lambda fo: make_llm_beats(lambda p: sc, KD.kitchen_capabilities,
                                     KD.kitchen_beats, fo,
                                     human_only=KD.HUMAN_ONLY, claims=KD.CLAIMS,
                                     conditional=KD.CONDITIONAL))
    with contextlib.redirect_stdout(io.StringIO()):
        return run_design.design_for(pid, Trace(), beats_factory=fac)


# ── 9 주문 방식은 고객이 정한다 (2026-09-30) ───────────────────────────
@check("오늘의 주문 방식을 말에서 읽고, 부정문은 그 방식으로 읽지 않는다")
def m1():
    cases = {"오늘은 내가 마트 들를게": "self", "장은 내가 볼게": "self",
             "알아서 시켜": "auto", "물어보고 사": "ask",
             "주문하기 전에 확인해줘": "ask",
             # 둘이 섞이면 사람이 하겠다는 쪽이 이긴다
             "알아서 시키지 말고 내가 사 갈게": "self",
             # 부정 — 자동으로 읽으면 안 된다
             "알아서 사지 마": None, "알아서 주문하지 마": None,
             # 조사가 낀 부정·사이에 말이 낀 확인 — 진짜 LLM 은 ask 로 읽었는데
             # 규칙은 auto(반대)로 읽었다(2026-09-30)
             "알아서 주문하지는 마": None,
             "알아서 사지는 말고 사기 전에 나한테 물어봐": "ask",
             "사기 전에 꼭 나한테 확인해": "ask",
             # 말하지 않았으면 비워 둔다(설계가 ask 로 채운다)
             "7시 도착, 두부 있어": None}
    bad = {t: (thinq.rule_understand(t)["fields"].get("order_mode"), want)
           for t, want in cases.items()
           if thinq.rule_understand(t)["fields"].get("order_mode") != want}
    return not bad, f"{len(cases) - len(bad)}/{len(cases)}" + (f" 틀림 {bad}" if bad else "")


@check("LLM 이 준 주문 방식이 세 값 밖이면 버린다")
def m2():
    ok_, bad_ = thinq._validate({"order_mode": "self"})
    ok2, bad2 = thinq._validate({"order_mode": "가끔"})
    ok3, bad3 = thinq._validate({"order_mode": 1})
    ok = (ok_.get("order_mode") == "self" and "order_mode" not in ok2
          and "order_mode" not in ok3 and bad2 and bad3)
    return ok, f"self 받음 · '가끔' 버림 {bad2[:1]} · 1 버림"


@check("어느 주문 방식이든 처음 사는 것·못 먹는 것은 자동으로 사지 않는다")
def m3():
    """같은 부족분을 방식만 바꿔 조달에 준다. 방식은 개입과 시간을 맞바꿀 뿐,
    안전 바닥(처음 사는 것·못 먹는 것·상한)은 어느 방식에서도 안 풀린다."""
    from skills import REGISTRY

    def lookup(n):
        return [{"item": n, "store": "즉시", "price_krw": 3000,
                 "delivery_min": 27, "can_order": True}]
    proc = REGISTRY.get("procure")
    out = {}
    for mode in ("auto", "ask", "self", None, "가끔"):
        r = proc.run(missing=["두부", "찹쌀", "새우"], lookup=lookup,
                     known_items=["두부"], avoid=["새우"], deadline_min=37,
                     mode=mode).output
        out[mode] = ([a["name"] for a in r["auto_ordered"]],
                     [c["name"] for c in r["need_confirm"]],
                     [s["name"] for s in r["self_buy"]], r["mode"])
    ok = (out["auto"][:3] == (["두부"], ["찹쌀", "새우"], [])
          and out["ask"][:3] == ([], ["두부", "찹쌀", "새우"], [])
          # 직접 장보기: 사람이 사니 처음 사는 것도 목록에 오르지만
          # 못 먹는 것은 목록에도 넣지 않고 묻는다
          and out["self"][:3] == ([], ["새우"], ["두부", "찹쌀"])
          # 모르면 묻는 쪽
          and out[None][3] == "ask" and out["가끔"][3] == "ask"
          and out[None][:3] == out["ask"][:3])
    return ok, " · ".join(f"{k}: 자동{v[0]} 확인{v[1]} 직접{v[2]}"
                          for k, v in out.items() if k in ("auto", "ask", "self"))


@check("주문 방식을 정해 두지 않은 가구는 묻는 쪽으로 설계된다")
def m4():
    import contextlib
    import io
    import personas
    import run_design
    from orchestrator import Trace
    p = personas.get("p2_맞벌이")
    p.pop("order_mode", None)
    personas.PERSONAS["_m4"] = dict(p, id="_m4")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            r = run_design.design_for("_m4", Trace(), seed=7)
    finally:
        del personas.PERSONAS["_m4"]
    m = r["verify"]["metrics"]
    ok = (m.get("주문 방식") == "매번 확인" and r["verify"]["user_touches"] >= 1
          and not m.get("자동 주문(원)"))
    return ok, f"주문 방식 {m.get('주문 방식')} · 개입 {r['verify']['user_touches']}"


@check("직접 장보기는 들른 시간만큼 식사가 늦고, 개입은 늘지 않는다")
def m5():
    import mode_sensitivity as ms
    a = ms.run_one("p2_맞벌이", "auto", True)
    s = ms.run_one("p2_맞벌이", "self", True)
    import store
    ok = (s["verified"] and s["touches"] == 0 and s["self_buy"]
          and abs(s["meal_min"] - a["meal_min"] - store.SHOP_DETOUR_MIN) < 0.05)
    return ok, (f"auto {a['meal_min']}분 · self {s['meal_min']}분 "
                f"(+{store.SHOP_DETOUR_MIN}분 가정) · 직접 {s['self_buy']}")


@check("정해 둔 선호를 쓰고, 그날 말이 있으면 그날 말이 이긴다")
def m6():
    """기본값 < 가구 선호 < 그날 말. 안전 항목(avoid)은 10/1 부터 **저장한다** —
    말하지 않았다고 풀리면 안 된다. 잘못된 값(주문 방식 '가끔')만 버린다."""
    u0 = thinq.understand("7시 도착")
    u1 = thinq.understand("7시 도착", profile={"order_mode": "auto"})
    u2 = thinq.understand("7시 도착, 오늘은 내가 마트 들를게",
                          profile={"order_mode": "auto"})
    u3 = thinq.understand("7시 도착", profile={"order_mode": "가끔",
                                               "avoid": ["새우"]})
    got = (u0["persona"].get("order_mode"), u1["persona"].get("order_mode"),
           u2["persona"].get("order_mode"), u3["persona"].get("order_mode"))
    ok = (got == (None, "auto", "self", None)
          and u3["persona"]["avoid"] == ["새우"] and len(u3["rejected"]) == 1
          and any("오늘은 self" in r for r in u2["read"]))
    return ok, (f"없음 {got[0]} · 선호 {got[1]} · 선호+그날 말 {got[2]} · "
                f"잘못된 선호 {got[3]} (버림 {len(u3['rejected'])}건)")


# ── 10 퇴근을 무엇으로 아는가 — 메시지·위치 (2026-09-30) ──────────────
@check("'지금 퇴근해' 를 퇴근 시각으로 읽고, 지금을 모르면 지어내지 않는다")
def n1():
    def p(t, now=None, profile=None):
        return thinq.understand(t, now=now, profile=profile)["persona"]
    a = p("지금 퇴근해, 40분 걸려", now="21:03")
    b = p("30분 뒤 퇴근이야 37분 걸려", now="20:33")
    c = p("지금 퇴근해")                                  # 지금이 몇 시인지 모름
    d = p("6시 반에 퇴근하고 7시 10분 도착")               # 이동은 두 시각의 차
    e = p("두부 있어", profile={"commute_min": 37})        # 이동만 알고 퇴근은 모름
    got = [(x.get("leave_office"), x.get("commute_min"), x.get("arrive_home"))
           for x in (a, b, c, d, e)]
    ok = (got[0] == ("21:03", 40, "21:43") and a["time_budget_min"] == 45
          and got[1] == ("21:03", 37, "21:40")
          and got[2][:2] == (None, None)
          and got[3] == ("18:30", 40, "19:10")
          and got[4][:2] == (None, None))
    # '40분 걸려' 를 쓸 수 있는 시간(예산)으로 읽으면 안 된다
    return ok, f"{got} · 예산 {a['time_budget_min']}(기본값 — '40분 걸려' 를 예산으로 안 읽음)"


@check("위치는 동의·퇴근 시간대·머무름을 모두 통과해야 퇴근으로 본다")
def n2():
    import location as L
    ex = lambda t: {"at": t, "kind": "exit", "place": "office"}
    en = lambda t: {"at": t, "kind": "enter", "place": "office"}
    no, w0 = L.detect_leave([ex("21:03")], consent=False, commute_min=37)
    lunch, _ = L.detect_leave([ex("12:10")], consent=True, commute_min=37)
    back, _ = L.detect_leave([ex("21:00"), en("21:03")], consent=True, commute_min=37)
    ok_, _ = L.detect_leave([ex("21:00"), en("21:03"), ex("21:03")],
                            consent=True, commute_min=37)
    # 인과: 판단 시각(나선 뒤 5분) 뒤에 다시 들어온 것은 판단 때 알 수 없다
    later, _ = L.detect_leave([ex("21:03"), en("21:30")], consent=True, commute_min=37)
    ok = (no is None and "동의" in w0[0] and lunch is None and back is None
          and ok_ and (ok_["leave_office"], ok_["commute_min"], ok_["arrive_home"])
          == ("21:08", 32, "21:40")
          and later and later["leave_office"] == "21:08")
    return ok, (f"동의 없음 {no} · 점심 {lunch} · 5분 안 복귀 {back} · "
                f"퇴근 {ok_ and ok_['leave_office']} (남은 {ok_ and ok_['commute_min']}분)")


@check("그날 말이 있으면 위치보다 말을 믿는다")
def n3():
    u = thinq.understand("지금 퇴근해", now="21:10",
                         profile={"commute_min": 37, "location_consent": True},
                         location=[{"at": "21:03", "kind": "exit", "place": "office"}])
    p = u["persona"]
    ok = (p["leave_office"] == "21:10" and p["leave_source"] == "message"
          and any("위치는 보지 않는다" in r for r in u["read"]))
    return ok, f"퇴근 {p['leave_office']} ({p['leave_source']})"


@check("퇴근을 알면 두부 없는 25분 가구도 성립하고, 위치는 확인 시간만큼 잃는다")
def n4():
    """모름 → 메뉴 못 정함. 메시지·위치(이동 40분) → 성립.
    이동 30분이면 위치는 5분 확인 뒤 25분 < 배송 27분이라 실패, 메시지는 성립."""
    food = "25분 안에 먹어야 해. 냉장고에 배추랑 된장 있어"
    auto = {"order_mode": "auto"}

    def ok_of(**kw):
        # 위치 추정 퇴근은 10/1 부터 자동 주문 대신 묻는다 — 사람이 승인한다
        r = thinq.run(approve=lambda c: True, **kw)["result"]
        return r["verify"]["verified"], r["verify"]["metrics"].get("식사까지(분)")
    loc = [{"at": "18:40", "kind": "exit", "place": "office"}]
    got = {
        "모름": ok_of(text=f"7시 20분 도착. {food}", profile=auto),
        "메시지40": ok_of(text=f"지금 퇴근해, 40분 걸려. {food}", now="18:40", profile=auto),
        "위치40": ok_of(text=food, location=loc,
                      profile=dict(auto, commute_min=40, location_consent=True)),
        "메시지30": ok_of(text=f"지금 퇴근해, 30분 걸려. {food}", now="18:40", profile=auto),
        "위치30": ok_of(text=food, location=loc,
                      profile=dict(auto, commute_min=30, location_consent=True)),
    }
    ok = (not got["모름"][0] and got["메시지40"][0] and got["위치40"][0]
          and got["메시지30"][0] and not got["위치30"][0])
    return ok, " · ".join(f"{k} {'성립' if v[0] else '실패'}" for k, v in got.items())


# ── 11 세부 점검에서 나온 것 (2026-09-30) ──────────────────────────────
@check("조달의 기피 필터는 포함 일치다 — 새우 가구에 새우젓·꽃게를 사지 않는다")
def p1_avoid():
    from skills import REGISTRY
    from recipe_parse import contains_any, expand_avoid

    def lk(n):
        return [{"item": n, "store": "즉시", "price_krw": 3000,
                 "delivery_min": 27, "can_order": True}]
    proc = REGISTRY.get("procure")
    bad = []
    for mode in ("auto", "ask", "self"):
        for match in (None, contains_any):          # 기본값 · 파이프라인이 주는 것
            r = proc.run(missing=["새우젓", "꽃게", "두부"], lookup=lk,
                         known_items=["새우젓", "꽃게", "두부"],
                         avoid=expand_avoid(["갑각류"])[0], mode=mode,
                         deadline_min=40, match=match).output
            bought = ([a["name"] for a in r["auto_ordered"]]
                      + [x["name"] for x in r["self_buy"]])
            flagged = [c["name"] for c in r["need_confirm"] if c.get("safety")]
            if set(bought) & {"새우젓", "꽃게"} or set(flagged) != {"새우젓", "꽃게"}:
                bad.append((mode, bool(match), bought, flagged))
    return not bad, "방식 3 × 비교 2 모두 새우젓·꽃게를 안전 보류" if not bad else str(bad)


@check("시연의 '승인했다고 가정' 이 못 먹는 재료까지 승인하지 않는다")
def p2_safety_approve():
    import kitchen_domain as KD
    import kitchen as K
    K.reset([{"name": "배추", "qty_g": 300, "stored_days": 1, "shelf_life_days": 7}])
    tasks = KD.build_tasks({"avoid": ["새우"], "budget_min": 60, "preorder": False})
    proc = next(t for t in tasks if t.skill == "procure")
    ctx = {"record": {"ingredients": [{"name": "새우젓", "qty_g": 20},
                                      {"name": "두부", "qty_g": 150}]}}
    out = {"auto_ordered": [], "self_buy": [], "total_krw": 0, "arrive_in_min": 0,
           "mode": "ask",
           "need_confirm": [{"name": "새우젓", "reason": "알레르기·기피 목록에 있음",
                             "safety": True},
                            {"name": "두부", "reason": "처음 구매하는 품목"}]}
    proc.absorb(ctx, out)
    ok = ("새우젓" not in ctx["approved_after_ask"] and "두부" in ctx["approved_after_ask"]
          and K.fridge_check("새우젓") is None
          and any("새우젓" in x for x in ctx["late_after_ask"]))
    return ok, f"승인 {ctx['approved_after_ask']} · 보류 {ctx['late_after_ask']}"


@check("자정을 넘겨도 장면이 시간 순이고, 조용 시간에 도는 세척을 알아본다")
def p3_midnight():
    from skills import REGISTRY
    o = thinq.run("지금 퇴근해, 40분 걸려. 25분 안에 먹고 싶어. 배추 두부 된장 "
                  "애호박 있어", now="23:50", profile={"order_mode": "auto"})
    order = [b["at"] for b in o["result"]["scenario"]["beats"]]
    a = REGISTRY.get("aftercare")
    late = a.run(soil_score=0.3, profile="dishwasher", start_at="00:55",
                 quiet_after="23:00").output["quiet_note"]
    noon = a.run(soil_score=0.3, profile="dishwasher", start_at="11:00",
                 quiet_after="23:00").output["quiet_note"]
    ok = (order[0] == "23:50" and order[-1].startswith("00:")
          and "이후" in late and "전에 끝난다" in noon)
    return ok, f"장면 {order} · 00:55 시작 → '{late[:22]}…' · 11:00 → '{noon[:14]}…'"


@check("자정 근처 시각을 바로 읽는다 — 새벽·밤 12시·자정 걸친 퇴근 시간대")
def p4_night_clock():
    import location as L
    cases = {"새벽 1시 도착": "01:00", "밤 12시 반 도착": "00:30",
             "아침 8시 도착": "08:00", "오후 3시 도착": "15:00",
             "9시 반 도착": "21:30", "저녁 7시 도착": "19:00", "21시 도착": "21:00"}
    got = {t: thinq.rule_understand(t)["fields"].get("arrive_home") for t in cases}
    f, _ = L.detect_leave([{"at": "00:30", "kind": "exit", "place": "office"}],
                          consent=True, commute_min=37, window=["21:00", "02:00"])
    g, _ = L.detect_leave([{"at": "12:10", "kind": "exit", "place": "office"}],
                          consent=True, commute_min=37, window=["21:00", "02:00"])
    bad = {t: (got[t], w) for t, w in cases.items() if got[t] != w}
    ok = not bad and f and f["leave_office"] == "00:35" and g is None
    return ok, (f"{len(cases) - len(bad)}/{len(cases)}" + (f" 틀림 {bad}" if bad else "")
                + f" · 21:00~02:00 시간대 00:30 이탈 → {f and f['leave_office']} · 점심 {g}")


@check("말한 귀가 시각을 위치 추정·앞뒤 안 맞는 계산보다 믿는다")
def p5_said_arrive():
    loc = [{"at": "21:03", "kind": "exit", "place": "office"}]
    a = thinq.understand("9시 50분 도착", profile={"commute_min": 37, "location_consent": True},
                         location=loc)["persona"]
    b = thinq.understand("9시 도착", profile={"commute_min": 37, "location_consent": True},
                         location=loc)            # 퇴근 추정(21:08)이 귀가보다 늦다
    c = thinq.understand("지금 퇴근해, 40분 걸려, 9시 도착", now="18:40")
    cp = c["persona"]
    ok = (a["arrive_home"] == "21:50" and a["commute_min"] == 42
          and b["persona"].get("leave_office") is None
          and any("위치를 쓰지 않는다" in r for r in b["read"])
          and cp["arrive_home"] == "21:00" and cp["commute_min"] == 140
          and any("맞지 않는다" in r for r in c["read"]))
    return ok, (f"위치+귀가 {a['arrive_home']}/{a['commute_min']}분 · "
                f"귀가보다 늦은 퇴근 추정 → {b['persona'].get('leave_office')} · "
                f"말끼리 모순 → 집 밖 {cp['commute_min']}분")


# ── 12 남은 약점 처리 (2026-09-30) ─────────────────────────────────────
@check("애매한 시각은 지금 시각·앞뒤 조합으로 고르고, 모르면 애매했다고 남긴다")
def q1_ambiguous():
    f = lambda t, now=None: thinq.rule_understand(t, now=now)
    a = f("12시 반에 퇴근하고 1시 10분 도착", "00:20")["fields"]
    b = f("1시 도착", "00:40")["fields"]
    c = f("1시 도착")
    d = f("밤 1시 도착")["fields"]
    ok = ((a.get("leave_office"), a.get("arrive_home")) == ("00:30", "01:10")
          and b.get("arrive_home") == "01:00"
          and c["fields"].get("arrive_home") == "13:00"
          and any("애매" in w for w in c["read"])
          and d.get("arrive_home") == "01:00")
    return ok, (f"지금 00:20 → {a.get('leave_office')}·{a.get('arrive_home')} · "
                f"지금 00:40 '1시' → {b.get('arrive_home')} · 지금 모름 → "
                f"{c['fields'].get('arrive_home')}(애매 표시) · '밤 1시' → {d.get('arrive_home')}")


@check("위치는 집 쪽으로 움직일 때만 퇴근으로 본다")
def q2_direction():
    import location as L
    ex = {"at": "18:40", "kind": "exit", "place": "office"}
    dist = lambda t, km: {"at": t, "kind": "dist", "km": km}
    away, w1 = L.detect_leave([ex, dist("18:40", 5.0), dist("18:45", 5.1)],
                              consent=True, commute_min=40)
    home, _ = L.detect_leave([ex, dist("18:40", 5.0), dist("18:45", 4.5)],
                             consent=True, commute_min=40)
    unk, w3 = L.detect_leave([ex], consent=True, commute_min=40)
    ok = (away is None and any("집 쪽이 아니라" in w for w in w1)
          and home and home["leave_office"] == "18:45"
          and unk and any("방향은 보지 못했다" in w for w in w3))
    return ok, (f"멀어짐 {away} · 가까워짐 {home and home['leave_office']} · "
                f"거리 모름 {unk and unk['leave_office']}(방향 못 봄 표시)")


@check("근거 없는 가정값은 가구가 정하고, 그 값이 실행까지 닿는다")
def q3_prefs():
    import mode_sensitivity as ms   # noqa: F401  (personas 정리 방식 참고)
    import assumption_sensitivity as A
    e25 = A._design("p2_맞벌이", prefs={"eat_min": 25})
    e39 = A._design("p2_맞벌이", prefs={"eat_min": 39})
    w = lambda r: [b["at"] for b in r["scenario"]["beats"] if b["verified_by"] == "aftercare"][0]
    s10 = A._design("p4_퇴근길", order_mode="self", prefs={"shop_detour_min": 10})
    s20 = A._design("p4_퇴근길", order_mode="self", prefs={"shop_detour_min": 20})
    u = thinq.understand("7시 도착", profile={"eat_min": 500, "shop_detour_min": 12})
    ok = (w(e25) == "19:57" and w(e39) == "20:11"
          and s10["verify"]["verified"] and not s20["verify"]["verified"]
          and u["persona"].get("prefs") == {"shop_detour_min": 12}
          and any("eat_min" in r for r in u["rejected"]))
    return ok, (f"식사 25→39분: 세척 {w(e25)}→{w(e39)} · 들르기 10분 성립 "
                f"{s10['verify']['verified']} / 20분 {s20['verify']['verified']} · "
                f"eat_min 500 버림")


# ── 13 냉장고를 못 본다 — 출처·확인·기한 (2026-09-30) ──────────────────
@check("기한 지난 재료는 부족분으로 다시 사고, 계량에서도 쓰지 않는다")
def r1_expired():
    import personas
    import belief_gap as B
    p = personas.get("p4_퇴근길")
    # 믿음 = 실제. 배추가 기한(7일)을 넘겼다 — 확인 없이도 알 수 있는 사실이다
    fridge = [dict(x, stored_days=9) if x["name"] == "배추" else x for x in p["fridge"]]
    r = B.run("p4_퇴근길", belief=fridge)
    ok = r["verified"] and not r["stale_used"] and "배추" in r["added"]
    return ok, f"상한 사용 {r['stale_used']} · 새로 산 것 {r['added']}"


@check("장부로 아는 재고는 묻지 않고, 말로 받은 재고만 퇴근길에 한 번 묻는다")
def r2_confirm():
    import belief_gap as B
    led = B.run("p2_맞벌이", source="ledger")
    told = B.run("p2_맞벌이", source="told")
    ok = ("묻지 않음" in (led["confirm"] or "") and led["touches"] == 0
          and "퇴근길 1회" in (told["confirm"] or "") and told["touches"] == 1)
    return ok, f"장부: {led['confirm'][:10]}… 개입 {led['touches']} · 말: 개입 {told['touches']}"


@check("확인으로 실제를 미리 알면 E1·E3·E4 를 집에 오기 전에 피한다")
def r3_early():
    import belief_gap as B
    import personas
    item = next(x for x in personas.get("p4_퇴근길")["fridge"] if x["name"] == "배추")
    out = {k: B.with_reality("p4_퇴근길", real, "told") for k, real in {
        "E1": {"배추": {"absent": True}}, "E3": {"배추": {"qty_g": 30}},
        "E4": {"배추": {"stored_days": item["shelf_life_days"] + 2}}}.items()}
    led = B.with_reality("p4_퇴근길", {"배추": {"absent": True}}, "ledger")
    ok = (all(r["verified"] and not r["stale_used"] for r in out.values())
          and not led["verified"])
    return ok, (" · ".join(f"{k} {'성립' if r['verified'] else '실패'}" for k, r in out.items())
                + f" · (장부인데 실제로 없음 → {'성립' if led['verified'] else '실패'})")


@check("긴 재료 이름 안의 짧은 이름을 따로 읽지 않는다 — 닭고기 ≠ 닭고기 + 고기")
def r4_food_overlap():
    f = lambda t: [x["name"] for x in thinq.rule_understand(t)["fields"].get("fridge", [])]
    a, b, c = f("닭고기랑 배추 있어"), f("닭고기랑 고기 있어"), f("고기 있어")
    # 순서는 말한 순서다(2026-10-01 부터) — 무엇을 읽었는지만 본다
    ok = set(a) == {"배추", "닭고기"} and len(a) == 2 and set(b) == {"닭고기", "고기"}         and c == ["고기"]
    return ok, f"'닭고기랑 배추' → {a} · '닭고기랑 고기' → {b} · '고기' → {c}"


@check("확인해도 넣은 날을 모르면 지어내지 않고, 집에서 묻는 날은 그 수고를 덜었다고 세지 않는다")
def r5_nodate_home():
    o = thinq.run("8시 도착. 냉장고에 닭고기랑 배추 있어", profile={"order_mode": "auto"})
    r = o["result"]
    m, sc = r["verify"]["metrics"], r["scenario"]
    first = [b for b in sc["beats"] if b["user"] == "현관에 들어선다"][0]
    ok = ("넣은 날 모름" in (m.get("재고 확인") or "") and m.get("넣은 날 모름")
          and "냉장고를 열어" not in first["removes"])
    return ok, f"{(m.get('넣은 날 모름') or '')[:30]}… · 첫 장면 덜어줌: {first['removes'][:12]}…"


@check("없다고 말한 재료를 재고로 읽지 않는다 — '두부 없어 배추만 있어'")
def r6_negated_food():
    f = lambda t: sorted(x["name"] for x in thinq.rule_understand(t)["fields"].get("fridge", []))
    cases = {"두부 없어 배추만 있어": ["배추"], "새우는 없어": [], "대파는 다 썼어": [],
             "배추랑 두부 없어": [], "배추 있고 두부 없어": ["배추"],
             "무랑 두부 있어": ["두부", "무"], "무척 피곤해. 두부 있어": ["두부"],
             "나무젓가락만 있어": [], "계란 3개랑 우유 있어": ["계란", "우유"],
             "닭고기랑 배추 있어": ["닭고기", "배추"]}
    bad = {t: (f(t), w) for t, w in cases.items() if f(t) != w}
    return not bad, f"{len(cases) - len(bad)}/{len(cases)}" + (f" 틀림 {bad}" if bad else "")


# ── 14 전수 점검(2026-10-01) — 안전·돈·장애·개인정보 ─────────────────
@check("S1 조리 중 센서가 끊겨도 가열을 끈다")
def s1_fault_off():
    import kitchen as K
    from skills import REGISTRY
    K.reset([])
    K.COOKER.start(800, 0, power=5, capacity_g=2000)
    n = [0]

    def obs():
        n[0] += 1
        if n[0] == 4:
            raise TimeoutError("센서 응답 없음")
        return K.COOKER.state()
    r = REGISTRY.get("converge").run(observe=obs, actuate=K.COOKER.set_power,
                                     step=K.COOKER.tick, metric="mass_ratio",
                                     target=0.8, max_power=9)
    ok = (not r.ok) and K.COOKER.power == 0 and "장애" in (r.output.get("recovery") or "")
    return ok, f"ok={r.ok} · 그 뒤 화력 {K.COOKER.power} · {r.output.get('fault')}"


@check("S2 일상어 알레르기도 그것으로 만든 것을 거른다 — 콩→간장·된장, 밀→라면")
def s2_derived():
    from recipe_parse import expand_avoid, contains_any
    want = {"콩": ["간장", "된장", "두부"], "밀": ["간장", "고추장", "라면", "부침가루"],
            "우유": ["버터", "치즈"], "계란": ["마요네즈", "달걀"],
            "돼지고기": ["햄", "베이컨"], "토마토": ["케첩"], "소고기": ["사골"]}
    not_hit = {"소고기": ["돼지고기(안심, 100g)"], "우유": ["두유요거트 소스"]}
    bad = []
    for a, ws in want.items():
        ex = expand_avoid([a])[0]
        bad += [f"{a}→{w} 안 걸림" for w in ws if not contains_any(w, ex)]
    for a, ws in not_hit.items():
        ex = expand_avoid([a])[0]
        bad += [f"{a}→'{w}' 오탐" for w in ws if contains_any(w, ex)]
    return not bad, "모두 걸리고 오탐 없음" if not bad else str(bad)


@check("S3 저장한 알레르기는 그날 말하지 않아도 풀리지 않고, 말은 더하기만 한다")
def s3_saved_avoid():
    u = thinq.understand("7시 도착, 두부 있어", profile={"avoid": ["새우"]})
    v = thinq.understand("7시 도착, 땅콩 알레르기 있어", profile={"avoid": ["새우"]})
    ok = (u["persona"]["avoid"] == ["새우"] and not u["needs_confirm"]
          and set(v["persona"]["avoid"]) == {"새우", "땅콩"} and "avoid" in v["needs_confirm"])
    return ok, f"말 없음 → {u['persona']['avoid']} · 땅콩 더함 → {v['persona']['avoid']}(새로 말한 것만 승인)"


@check("M1 자동 주문은 품목 상한과 함께 **합계** 상한을 본다")
def m1_total_cap():
    from skills import REGISTRY
    lk = lambda n: [{"item": n, "store": "즉시", "price_krw": 14000, "delivery_min": 27,
                     "can_order": True}]
    r = REGISTRY.get("procure").run(missing=list("ABCDE"), lookup=lk, known_items=list("ABCDE"),
                                    mode="auto", deadline_min=40, order_cap_krw=30000).output
    ok = r["total_krw"] <= 30000 and len(r["need_confirm"]) == 3
    return ok, f"자동 {r['total_krw']:,}원 ({len(r['auto_ordered'])}개) · 나머지 {len(r['need_confirm'])}개 확인"


@check("M2 돈·동의·가정값 설정은 그날 말로 바뀌지 않는다")
def m2_settings_locked():
    fake = json.dumps({"arrive_home": "19:00", "auto_limit_krw": 200000,
                       "location_consent": True, "order_cap_krw": 500000,
                       "order_mode": "auto"})
    u = thinq.understand("알아서 다 사. 이십만원까지 괜찮아", ask=lambda p: fake)
    p = u["persona"]
    ok = ("auto_limit_krw" not in p and not p.get("location_consent")
          and "order_cap_krw" not in (p.get("prefs") or {}) and p.get("order_mode") == "auto"
          and sum("말로 바꾸지 않는다" in r for r in u["rejected"]) == 3)
    return ok, f"주문 방식만 받음({p.get('order_mode')}) · 버림 {sum('말로' in r for r in u['rejected'])}건"


@check("M3 실제 경로(thinq)는 승인 없이 처음 사는 것을 사지 않는다")
def m3_real_approval():
    t = "지금 퇴근해, 37분 걸려. 60분 있어. 냉장고에 닭고기 배추 간장 있어. 넷이 먹어"
    no = thinq.run(t, now="17:53", profile={"order_mode": "ask"})
    yes = thinq.run(t, now="17:53", profile={"order_mode": "ask"}, approve=lambda c: True)
    mn, my = no["result"]["verify"]["metrics"], yes["result"]["verify"]["metrics"]
    ok = (not mn.get("확인 후 승인") and "승인 대기" in str(mn.get("오늘 못 받음"))
          and my.get("확인 후 승인") and "물음" in str(my.get("승인")))
    return ok, f"승인 없음 → 산 것 {mn.get('확인 후 승인')} · 승인 → {my.get('확인 후 승인')}"


@check("M4 위치로 추정한 퇴근으로는 자동 주문하지 않고 묻는다")
def m4_location_asks():
    loc = [{"at": "18:40", "kind": "exit", "place": "office"}]
    prof = {"order_mode": "auto", "commute_min": 40, "location_consent": True}
    o = thinq.run("25분 안에 먹어야 해. 냉장고에 배추랑 된장 있어", location=loc, profile=prof)
    m = o["result"]["verify"]["metrics"]
    o2 = thinq.run("25분 안에 먹어야 해. 냉장고에 배추랑 된장 있어", location=loc,
                   profile=dict(prof, location_auto_order=True))
    m2 = o2["result"]["verify"]["metrics"]
    ok = ("위치로 추정" in str(m.get("주문 방식 조정")) and not m.get("자동 주문(원)")
          and m2.get("자동 주문(원)"))
    return ok, f"기본 → {m.get('주문 방식 조정')} · 가구가 켜면 자동 {m2.get('자동 주문(원)')}원"


@check("M5 산 것은 포장 단위로 장부에 들어간다 — 두부 한 모")
def m5_pack():
    import belief_gap as B
    import kitchen as K
    import store
    r = B.run("p4_퇴근길")
    tofu = K.fridge_check("두부")       # 계량 뒤 남은 양
    used = sum(w["actual_g"] for w in r["weighed"] if w["name"] == "두부")
    ok = tofu is not None and abs(tofu["qty_g"] + used - store.pack_of("두부")) < 1
    return ok, f"두부 {store.pack_of('두부')}g 한 모 − 쓴 {used}g = 남은 {tofu and tofu['qty_g']}g"


@check("R1·R2 상점 조회가 실패해도 멈추지 않고, 품절은 사지 않는다")
def r_store_fail():
    from skills import REGISTRY
    proc = REGISTRY.get("procure")

    def boom(n):
        raise ConnectionError("상점 API 끊김")
    a = proc.run(missing=["두부"], lookup=boom, known_items=["두부"], mode="auto", deadline_min=40)
    lk = lambda n: [{"item": n, "store": "즉시", "price_krw": 3000, "delivery_min": 27,
                     "can_order": True, "in_stock": False}]
    b = proc.run(missing=["두부"], lookup=lk, known_items=["두부"], mode="auto", deadline_min=40)
    ok = (a.ok and "조회 실패" in a.output["need_confirm"][0]["reason"]
          and not b.output["auto_ordered"])
    return ok, f"조회 실패 → {a.output['need_confirm'][0]['reason']} · 품절 자동 {b.output['auto_ordered']}"


@check("R3 LLM 이 죽거나 엉뚱하게 답하면 규칙으로 읽는다")
def r3_llm_fallback():
    def boom(p):
        raise TimeoutError("LLM 응답 없음")
    a = thinq.understand("7시 도착 두부 있어", ask=boom)
    b = thinq.understand("7시 도착 두부 있어", ask=lambda p: "죄송해요 모르겠어요")
    ok = all(u["by"].startswith("규칙") and u["persona"]["arrive_home"] == "19:00"
             and [x["name"] for x in u["persona"]["fridge"]] == ["두부"] for u in (a, b))
    return ok, f"죽음 → {a['by']} · 엉뚱 → {b['by']} (두부·19:00 읽음)"


@check("P1 외부 LLM 에는 전화·주소·이메일·주민·카드번호를 가려 보낸다")
def p1_redact():
    sent = []
    thinq.understand("지금 퇴근해, 40분 걸려. 엄마 집 서울 마포구 월드컵로 12, "
                     "010-1234-5678, kim@mail.com 으로 연락. 이름은 김영희. 두부 있어",
                     ask=lambda p: sent.append(p) or "{}", now="18:40")
    t = sent[0]
    leak = [k for k in ("010-1234", "월드컵로 12", "kim@mail", "김영희") if k in t]
    keep = all(k in t for k in ("40분", "두부", "퇴근"))
    return not leak and keep, f"새어 나간 것 {leak} · 일에 필요한 말(40분·두부·퇴근) 남음 {keep}"


# ── 15 남은 위험 5 — 교차 오염·성분표 (2026-10-01) ─────────────────────
@check("5a 못 먹는 재료가 닿은 냄비를 씻은 기록이 없으면 알리고, 씻었으면 그대로 쓴다")
def t5a_cookware():
    import contextlib, io, personas, run_design
    from orchestrator import Trace
    out = {}
    for washed in ("표준", None):
        p = personas.get("p3_알레르기")
        p["cookware_history"] = [dict(p["cookware_history"][0], washed=washed)]
        personas.PERSONAS["_t5a"] = dict(p, id="_t5a")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r = run_design.design_for("_t5a", Trace(), seed=7)
        finally:
            del personas.PERSONAS["_t5a"]
        out[washed] = (r["verify"]["metrics"].get("도구 확인"), r["verify"]["user_touches"])
    ok = ("그대로 쓴다" in out["표준"][0] and "씻은 기록이 없다" in out[None][0]
          and out[None][1] == out["표준"][1] + 1)
    return ok, f"씻음 → 개입 {out['표준'][1]} · 안 씻음 → 개입 {out[None][1]} (알림)"


@check("5b 이름에 안 보이는 알레르기를 상품 원재료로 거르고, 속 모를 원재료면 묻는다")
def t5b_raw():
    from skills import REGISTRY
    from recipe_parse import contains_any, expand_avoid
    import store
    lk = store.make_lookup()
    proc = REGISTRY.get("procure")

    def run(items, avoid):
        r = proc.run(missing=items, lookup=lk, known_items=items, mode="auto",
                     deadline_min=60, avoid=expand_avoid(avoid)[0], match=contains_any).output
        return {c["name"]: c["reason"] for c in r["need_confirm"]}, [a["name"] for a in r["auto_ordered"]]
    a = run(["카레가루", "부침가루"], ["우유"])      # 카레가루엔 분유, 부침가루엔 없다
    b = run(["맛술"], ["새우"])                      # 속 모를 원재료
    c = run(["맛술"], [])                            # 알레르기 없으면 상관없다
    ok = ("카레가루" in a[0] and "분유" in a[0]["카레가루"] and "부침가루" in a[1]
          and "확인할 수 없는" in b[0].get("맛술", "") and "맛술" in c[1])
    return ok, f"우유 → 카레가루 '{a[0].get('카레가루')}' · 새우 → 맛술 '{b[0].get('맛술')}' · 없음 → 맛술 자동"


# ── 16 남은 위험 6 — 주문 사고 (2026-10-01) ─────────────────────────────
@check("6a 배송 지연은 이동 시간 안이면 묻히고, 넘치면 집에서 기다린 만큼 늦는다")
def u6a_late():
    import delivery_gap as D
    base, a, b = D.run("p2_맞벌이"), D.run("p2_맞벌이", {"두부": {"late_min": 10}}), \
        D.run("p2_맞벌이", {"두부": {"late_min": 40}})
    ok = (a["meal_min"] == base["meal_min"]
          and abs(b["meal_min"] - base["meal_min"] - 30) < 0.05)   # 40 − (이동 37 − 배송 27)
    return ok, f"기본 {base['meal_min']} · 10분 지연 {a['meal_min']} · 40분 지연 {b['meal_min']}분"


@check("6b 집에서 안 사고로는 퇴근길부터 다시 짜지 않는다 — 시간을 되돌리지 않는다")
def u6b_no_time_travel():
    import delivery_gap as D
    r = D.run("p3_알레르기", {"찹쌀": {"late_min": 40}})
    # 전에는 집에서 알고도 "퇴근길에 다른 재료를 주문" 해 16.1분이 나왔다
    ok = r["meal_min"] is not None and r["meal_min"] > 40
    return ok, f"알레르기 가구 찹쌀 40분 지연 → {r['menu']} {r['meal_min']}분"


@check("6c 결제 실패·상점 취소는 알게 된 때 묻고, 거절하면 사지 않는다")
def u6c_fail():
    import delivery_gap as D
    pay_y = D.run("p4_퇴근길", {"두부": {"payment_fail": True}}, True)
    pay_n = D.run("p4_퇴근길", {"두부": {"payment_fail": True}}, False)
    can_y = D.run("p4_퇴근길", {"두부": {"cancelled": True}}, True)
    ok = (pay_y["verified"] and pay_y["touches"] == 1 and not pay_n["verified"]
          and can_y["verified"] and can_y["meal_min"] > pay_y["meal_min"]
          and any("들러" in i for i in can_y["issues"]))
    return ok, (f"결제 실패·승인 {pay_y['meal_min']}분(개입 {pay_y['touches']}) · 거절 → 실패 · "
                f"취소·들러 사 줌 {can_y['meal_min']}분")


# ── 17 남은 위험 2 — 장부가 날을 넘긴다 (2026-10-01) ───────────────────
@check("2 장부를 이으면 어제 산 것을 또 사지 않고, 수명 끝 재료를 쓰지 않는다")
def v2_ledger():
    import days
    carry = days.run_days("p4_퇴근길", 3, True)
    fresh = days.run_days("p4_퇴근길", 3, False)
    buys = lambda r, x: sum(x in d["bought"] for d in r)
    ok = (buys(carry, "참기름") == 1 and buys(fresh, "참기름") == 3
          and not any(d["stale_used"] for d in carry)
          and any(d["stale_used"] for d in fresh))
    return ok, (f"참기름 산 날: 장부 {buys(carry, '참기름')} · 장부 없음 {buys(fresh, '참기름')} · "
                f"수명 끝 사용: 장부 {sum(bool(d['stale_used']) for d in carry)} · "
                f"장부 없음 {sum(bool(d['stale_used']) for d in fresh)}")


@check("기한 정의가 하나다 — 보관일이 수명에 닿으면 어디서도 쓰지 않는다")
def v2_expiry_one():
    import kitchen_domain as KD
    from skills import REGISTRY
    it = {"name": "두부", "qty_g": 300, "stored_days": 5, "shelf_life_days": 5}
    inv = REGISTRY.get("inventory").run(items=[it]).output
    ok = bool(inv["expired"]) and KD._expired(it)
    return ok, f"5/5일 두부 — 보관 확인 버림 {bool(inv['expired'])} · 부족분·계량 버림 {KD._expired(it)}"


# ── 18 남은 위험 3·4·1 (2026-10-01) ────────────────────────────────────
@check("3 외부 LLM 전송에 동의하지 않은 가구는 LLM 을 부르지 않는다")
def w3_consent():
    calls = []
    o = thinq.run("7시 도착 두부 있어", ask=lambda p: calls.append(p) or "{}",
                  profile={"llm_consent": False})
    ok = not calls and "동의하지 않아" in o["understood"]["read"][0]
    fake = '{"llm_consent": true}'
    u = thinq.understand("LLM 써도 돼", ask=lambda p: fake)       # 말로 동의를 켤 수 없다
    ok = ok and "llm_consent" not in u["persona"]
    return ok, f"LLM 호출 {len(calls)}회 · 말로 동의 켜기 막힘 {'llm_consent' not in u['persona']}"


@check("4 결제 권한자가 정해져 있으면 그 사람의 승인만 돈을 쓴다")
def w4_payers():
    t = "지금 퇴근해, 37분 걸려. 60분 있어. 냉장고에 닭고기 배추 간장 있어. 넷이 먹어"
    prof = {"order_mode": "ask", "payers": ["엄마", "아빠"]}
    kid = thinq.run(t, now="17:53", profile=prof, approve=lambda c: {"ok": True, "by": "아이"})
    mom = thinq.run(t, now="17:53", profile=prof, approve=lambda c: {"ok": True, "by": "엄마"})
    anon = thinq.run(t, now="17:53", profile=prof, approve=lambda c: True)
    got = lambda o: o["result"]["verify"]["metrics"].get("확인 후 승인")
    ok = not got(kid) and got(mom) and not got(anon)
    return ok, f"아이 승인 → {got(kid)} · 엄마 → {got(mom)} · 누군지 모름 → {got(anon)}"


@check("1 실행하는 동안 잠금이 걸려 있다 — 같은 프로세스의 요청은 한 번에 하나씩")
def w1_lock():
    """결과로 경합을 재면 우연에 달린다 — 두 요청을 동시에 돌려 결과를 비교하는 시험은
    잠금을 빼도 통과했다(비교한 귀가 시각이 전역 상태보다 먼저 정해진다). 그래서
    **실행 중에 잠금이 실제로 걸려 있는지**를 본다(2026-10-01)."""
    import run_design
    held = []
    orig = run_design.design_for

    def spy(*a, **k):
        held.append(thinq._RUN_LOCK.locked())
        return orig(*a, **k)
    run_design.design_for = spy
    try:
        thinq.run("7시 도착 두부 있어")
    finally:
        run_design.design_for = orig
    ok = held == [True] and not thinq._RUN_LOCK.locked()
    return ok, f"실행 중 잠금 {held} · 끝난 뒤 풀림 {not thinq._RUN_LOCK.locked()}"


# ── 19 세부 점검 2차 (2026-10-01) ─────────────────────────────────────
@check("D1·D2 인원은 두 자리·'열두' 까지, 양은 말한 대로(반 모·200g·3개) 읽는다")
def x_people_qty():
    f = lambda t: thinq.rule_understand(t)["fields"]
    hh = {"열두 명이 먹어": 12, "12명 먹어": 12, "4인 가족": 4, "넷이 먹어": 4,
          "혼자 먹어": 1, "2인분 남았어 넷이 먹어": 4}
    qty = {"두부 반 모 있어": ("두부", 150), "닭고기 200g 있어": ("닭고기", 200),
           "계란 3개 있어": ("계란", 180), "배추 한 포기 있어": ("배추", 2000)}
    bad = [t for t, w in hh.items() if f(t).get("household_size") != w]
    bad += [t for t, (n, g) in qty.items()
            if (f(t).get("fridge") or [{}])[0].get("qty_g") != g]
    return not bad, "모두 맞게 읽음" if not bad else f"틀림 {bad}"


@check("D3 산 적 있는 품목은 가구별로 쌓이고, 다음에는 '처음 사는 품목' 이 아니다")
def x_purchase_history():
    import contextlib, io, personas, run_design
    import kitchen as K
    from orchestrator import Trace

    def run(hist):
        p = personas.get("p3_알레르기")
        if hist is not None:
            p["purchase_history"] = hist
        personas.PERSONAS["_xh"] = dict(p, id="_xh")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r = run_design.design_for("_xh", Trace(), seed=7)
        finally:
            del personas.PERSONAS["_xh"]
        return r["verify"]["metrics"].get("확인 요청") or []
    first = run(None)
    learned = set(K.KNOWN)
    again = run(sorted(learned))
    ok = (any("처음" in a for a in first) and "찹쌀" in learned
          and not any("처음" in a for a in again))
    return ok, f"첫날 {first} → 산 것 학습 '찹쌀' {'찹쌀' in learned} → 다음 {again}"


@check("D4 도시 없는 주소·동호수도 가린다")
def x_redact_more():
    t = thinq.redact("마포구 월드컵로 12 101동 1203호로 와. 두부 있어")
    ok = "월드컵로" not in t and "1203" not in t and "두부" in t
    return ok, t


@check("D5 집에서 다시 짤 때 이미 결제한 주문을 결과와 장부에 남긴다")
def x_committed():
    import contextlib, io, run_design, store
    import kitchen as K
    from orchestrator import Trace
    store.set_order_reality({"찹쌀": {"late_min": 40}})
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            r = run_design.design_for("p3_알레르기", Trace(), seed=7)
    finally:
        store.set_order_reality(None)
    m = r["verify"]["metrics"]
    note = str(m.get("앞선 계획에서 이미 주문한 것", ""))
    ok = "찹쌀" in note and K.fridge_check("찹쌀") is not None
    return ok, f"{m.get('메뉴')} · {note[:40]}"


@check("D6 설명은 물은 것을 빠짐없이 말하고, 안전·돈·계획 변경은 반드시 알린다")
def x_explain():
    o = thinq.run("8시 도착. 냉장고에 닭고기랑 배추 있어", profile={"order_mode": "auto"},
                  approve=lambda c: True)
    r = o["result"]
    m, v = r["verify"]["metrics"], r["verify"]
    t = thinq.rule_explain(r)
    ok = (len(thinq.asked_list(m)) == v["user_touches"]
          and "넣은 날 모름" in m and "냄새" in t and "꼭 알릴 것" in t)
    return ok, f"물은 것 {len(thinq.asked_list(m))}건 = 개입 {v['user_touches']} · 냄새·색 안내 {'냄새' in t}"


@check("D7 설명 LLM 이 죽어도 결과를 버리지 않고, LLM 설명에도 안전 안내를 붙인다")
def x_explain_llm():
    def boom_at_explain(p):
        if "두세 문장" in p:
            raise TimeoutError("설명 LLM 응답 없음")
        return "{}"
    a = thinq.run("8시 도착. 냉장고에 닭고기랑 배추 있어", ask=boom_at_explain,
                  profile={"order_mode": "auto"}, approve=lambda c: True)
    b = thinq.run("8시 도착. 냉장고에 닭고기랑 배추 있어",
                  ask=lambda p: "맛있게 드세요." if "두세 문장" in p else "{}",
                  profile={"order_mode": "auto"}, approve=lambda c: True)
    ok = (a["result"] is not None and a["explained"]["by"].startswith("규칙")
          and "꼭 알릴 것" in b["explained"]["text"])
    return ok, f"설명 실패 → {a['explained']['by']} · LLM 설명 + 안내 덧붙임 {'꼭 알릴 것' in b['explained']['text']}"


@check("가전이 할 수 없는 손일을 약속한 장면은 버린다")
def e5():
    """재료 투입·뚜껑·젓기는 사람이 한다(제안서 표1)."""
    r = _llm_scenes("p1_야근",
                    (5, "x", "재료를 자동으로 넣어 준다", "converge", [1]),
                    (5, "x", "뚜껑을 스스로 닫는다", "converge", [1]),
                    (5, "x", "화력을 스스로 맞추고 손이 필요할 때만 알린다",
                     "converge", [1]))
    rep = r["design_report"]
    ok = len(rep["rejected"]) == 2 and len(rep["accepted"]) == 1
    return ok, f"버림 {len(rep['rejected'])} · 받음 {len(rep['accepted'])}"


@check("사람 손이 필요한 단계는 선제 주문이어도 귀가 전에 놓지 않는다")
def e6():
    r = _llm_scenes("p4_퇴근길",
                    (-30, "퇴근길", "부족분을 판단해 산다", "procure", [0]),
                    (-20, "x", "재료를 계량해 둔다", "prep", []),
                    (-10, "x", "화력을 맞춘다", "converge", []))
    at = {b["verified_by"]: b["at"] for b in r["scenario"]["beats"]
          if b.get("source") == "LLM"}
    ok = at["procure"] < "19:20" and at["prep"] >= "19:20" and at["converge"] >= "19:20"
    return ok, f"시각 {at} (귀가 19:20)"


@check("장면 시각이 계획 순서를 거스르면 앞 단계를 당기거나 뒤를 민다")
def e7():
    """p4 에서 메뉴를 귀가 시각, 주문을 30분 전에 두면 **메뉴를 당겨야**
    한다. 처음엔 주문을 귀가 뒤로 밀어 선제 주문의 뜻이 사라졌다."""
    r = _llm_scenes("p4_퇴근길",
                    (-30, "퇴근길", "부족분을 판단해 산다", "procure", [0]),
                    (0, "들어온다", "메뉴를 골라 둔다", "menu", [1]))
    at = {b["verified_by"]: b["at"] for b in r["scenario"]["beats"]
          if b.get("source") == "LLM"}
    r2 = _llm_scenes("p1_야근",
                     (0, "x", "화력을 맞춘다", "converge", []),
                     (10, "x", "메뉴를 골라 둔다", "menu", [1]))
    at2 = {b["verified_by"]: b["at"] for b in r2["scenario"]["beats"]
           if b.get("source") == "LLM"}
    ok = at["menu"] == at["procure"] == "18:50" and at2["converge"] >= at2["menu"]
    return ok, f"p4 {at} · p1 {at2}"


@check("조건 없이 한 약속은 결과로 확인하고, 조건을 단 문장은 기준으로 둔다")
def e8():
    """"저소음으로 돌린다" 고 했는데 결과가 '이미 조용하다' 면 그 장면은
    일어나지 않은 것이다. "넘기면 바꾼다" 는 약속이 아니라 판단 기준이다."""
    r = _llm_scenes("p1_야근",
                    (45, "x", "세척기를 저소음으로 돌린다", "aftercare", [2]))
    r2 = _llm_scenes("p1_야근",
                     (45, "x", "소음 시각을 넘기면 저소음으로 바꾼다",
                      "aftercare", [2]))
    un = [c["why"] for c in r["verify"]["beat_check"] if not c["ok"]]
    un2 = [c["why"] for c in r2["verify"]["beat_check"] if not c["ok"]]
    ok = any("저소음" in w for w in un) and not un2
    return ok, (un[0][:70] if un else "안 잡음")


@check("입력에 있던 수는 장면에 써도 된다")
def e9():
    r = _llm_scenes("p1_야근",
                    (0, "혼자 먹는다", "1인분 기준으로 메뉴를 골라 둔다", "menu", [0]),
                    (0, "x", "3인분 기준으로 메뉴를 골라 둔다", "menu", [0]))
    rep = r["design_report"]
    ok = len(rep["accepted"]) == 1 and "3" in rep["rejected"][0]
    return ok, f"받음 {rep['accepted']} · 버림 {len(rep['rejected'])}"


# ── 진짜 호출 : --live 일 때만 ─────────────────────────────────────────
# 매번 돌리면 느리고 비용이 든다. 기본은 가짜 LLM 으로 구조만 보고,
# 진짜 호출은 손으로 켤 때만 한다.
def live_checks() -> list:
    out = []
    ask = thinq.best_ask()
    T = "혼자 먹어. 된장이랑 배추 두부 있고 7시 도착. 45분 정도 여유 있어"

    a = thinq.run(T, ask=None)
    b = thinq.run(T, ask=ask)

    def fp(o):
        v = o["result"]["verify"]
        return (tuple(o["result"]["flow"]["steps"]), v["user_touches"],
                v["metrics"].get("가열 시간(분)"),
                v["metrics"].get("최종 질량비"))

    out.append(("진짜 LLM 으로 읽어도 제어 결과가 같다", fp(a) == fp(b),
                f"규칙 {fp(a)} / LLM {fp(b)}"))
    out.append(("진짜 LLM 설명에 지어낸 수가 없다",
                not b["explained"]["invented"],
                f"지어낸 수 {b['explained']['invented']} · "
                f"{b['explained']['text'][:60]}"))
    u = b["understood"]
    out.append(("진짜 LLM 이 상황을 읽어낸다", u["by"] == "LLM" and u["read"],
                f"{u['by']} · {u['read'][0][:70] if u['read'] else '읽은 것 없음'}"))
    return out


def main() -> int:
    print("자연어 다리 검사 — 키 없이도 확인한다")
    print(f"  지금 진짜 LLM 을 부를 수 있는가: "
          f"{'예' if thinq.available() else '아니오 (가짜 LLM 으로 시험한다)'}")
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
    total = len(CHECKS)
    if "--live" in sys.argv:
        if not thinq.available():
            print("  !!  --live 를 줬지만 부를 방법이 없다")
            bad += 1
        else:
            print()
            print(f"진짜 호출 ({thinq.how()})")

            for title, ok, note in live_checks():
                print(f"  {'OK ' if ok else '!! '} {title}")
                print(f"        {note}")
                total += 1
                if not ok:
                    bad += 1
    else:
        print(f"  ·   진짜 호출은 건너뛴다 — `python check_thinq.py --live` "
              f"로 켠다 ({thinq.how()})")

    print()
    print(f"판정: {total - bad}/{total} 항목 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())