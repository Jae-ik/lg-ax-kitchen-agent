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
    a = thinq.run("배추랑 두부만 있어. 9시 반 도착이고 30분 있어")
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
    a = thinq.run("아무말", ask=lambda p: per)          # 설명도 같은 답 — 무관
    b = thinq.run("아무말",
                  ask=lambda p: sc if "시나리오 설계자" in p else per)
    ca, cb = a["result"]["scenario"], b["result"]["scenario"]
    ok = ca["covered"] == 0 and cb["covered"] == 2
    return ok, f"틀 {ca['covered']}/{ca['total_friction']} → LLM {cb['covered']}/{cb['total_friction']}"


@check("이번 계획에 없는 기능을 약속한 장면은 버린다")
def d2():
    """p1 은 조달을 건너뛴다. '주문한다' 장면은 실행되지 않을 약속이다.
    처음엔 플래너를 직접 불러 p1 에 procure 가 허용됐다."""
    sc = _scenes((0, "장을 안 본다", "부족분을 주문한다", "procure", [0]),
                 (1, "썰지 않는다", "로봇팔이 썰어 준다", "robot_arm", [0]),
                 (5, "부를 때만 온다", "화력을 맞춘다", "converge", [1]))
    r = _design("p1_야근", sc)["design_report"]
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
    # p1 은 퇴근 시각을 모른다(야근). p3 로 재다가 p3 에 퇴근 정보가 생겨
    # 선제 주문 가구가 되자 이 시험이 성립하지 않았다.
    sc = _scenes((-50, "고민하지 않는다", "메뉴를 골라 둔다", "menu", [0]))
    r = _design("p1_야근", sc)
    b = [x for x in r["scenario"]["beats"] if x.get("source") == "LLM"][0]
    ok = b["at"] == "21:40" and r["design_report"]["adjusted"]
    return ok, f"장면 시각 {b['at']} · {r['design_report']['adjusted'][:1]}"


@check("진짜 LLM 이 p3 에 준 답을 재생하면 같은 결과가 나온다")
def d8():
    """2026-09-28 에 claude CLI 가 준 원문(fixtures). 키 없이 재생한다.
    틀은 p3 수고를 1/4 덮었고, 이 제안은 3/4 를 덮고 남은 하나(조리 기구
    분리)는 '덜어 줄 기능이 없다' 고 스스로 밝혔다."""
    import pathlib
    raw = json.loads(pathlib.Path("fixtures/llm_p3_scenes.json")
                     .read_text(encoding="utf-8"))["raw"]
    r1, r2 = _design("p3_알레르기", raw), _design("p3_알레르기", raw)
    s1, v1 = r1["scenario"], r1["verify"]
    same = ([b["at"] + b["system"] for b in s1["beats"]]
            == [b["at"] + b["system"] for b in r2["scenario"]["beats"]])
    ok = (same and s1["covered"] == 3 and s1["total_friction"] == 4
          and v1["verified"] and r1["design_report"]["uncovered"])
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