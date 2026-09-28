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


@check("만들 수 없으면 못 만든다고 말한다")
def c9():
    """'-로 정했습니다' 처럼 빈 값을 읽어 주면 무슨 일이 있었는지
    알 수 없다. 실제로 한 번 그렇게 나갔다."""
    out = thinq.run("배추랑 두부만 있어. 9시 반 도착이고 30분 있어")
    text = out["explained"]["text"]
    ok = ("만들 수 있는 것이 없" in text and "-로 정했" not in text
          and len(text) > 30)
    return ok, text[:88]


# ── 6 불변 : 판단은 측정이 한다 ────────────────────────────────────────
@check("LLM 을 써도 제어 결과가 달라지지 않는다")
def c10():
    """이 설계의 핵심이다. LLM 은 **양 끝에만** 있고 제어 루프에는
    없다. 같은 상황을 규칙으로 읽든 LLM 으로 읽든, 읽은 상황이 같으면
    실행 결과도 같아야 한다 — 그래야 200회 반복 측정과 회귀 검사가
    계속 의미를 가진다.
    """
    same = json.dumps({"arrive_home": "19:00", "time_budget_min": 45,
                       "household_size": 1,
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