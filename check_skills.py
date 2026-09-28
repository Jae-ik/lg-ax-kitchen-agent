# -*- coding: utf-8 -*-
"""스킬 하나하나를 **단독으로** 확인한다.

다른 검사와 무엇이 다른가:
  check_consistency  선언과 구현이 맞는가 (정적)
  check_interlock    스킬이 서로 맞물리는가 (파이프라인)
  check_generalize   처음 보는 상황에서 도는가 (파이프라인)
  **check_skills**   스킬 하나를 떼어 놓고 봐도 성립하는가

스킬은 "재사용 가능한 능력 단위" 라고 선언해 두었다. 그 말이 참이려면
파이프라인 밖에서 혼자 불러도 말이 되어야 한다. 여기서 보는 것:

  1 순수성    같은 입력을 두 번 주면 같은 출력이 나오는가
  2 경계값    빈 목록·None·극단값에 예외 없이 답하는가
  3 근거      판단 근거(evidence)가 비어 있지 않은가
  4 정직성    아무것도 못 했는데 ok=True 를 돌려주지 않는가
  5 격리      전역 상태(kitchen/store)를 몰래 읽지 않는가

4번은 실제로 한 번 깨졌다 — inventory 가 반대 방향으로 깨져 있었다.
"임박한 것이 0건" 을 실패로 보는 바람에, 냉장고가 신선한 가구에서
파이프라인이 1단계에서 멈췄다(check_generalize 참고).
"""
from __future__ import annotations
import copy
import inspect
import sys

from skills import REGISTRY

# ── 스킬마다 "제대로 된 입력" 과 "경계 입력" 을 하나씩 준다 ──────────────
# 도메인 객체를 쓰지 않는다. 스킬이 일반 타입만 받는다는 선언을 여기서
# 지키는지도 함께 보는 셈이다.

_RECORDS = [{"record_id": "r1", "menu": "된장찌개", "servings": 2,
             "target_mass_ratio": 0.78, "soil_score": 0.3,
             "ingredients": [{"name": "배추", "qty_g": 200},
                             {"name": "두부", "qty_g": 150}]}]
_STOCK = [{"name": "배추", "qty_g": 300, "stored_days": 5, "shelf_life_days": 7},
          {"name": "두부", "qty_g": 200, "stored_days": 2, "shelf_life_days": 5}]


# procure 는 제안을 dict 로도 객체로도 받는다. 예전에는 속성 접근
# (o.delivery_min)만 해서 dict 를 주면 AttributeError 가 났고, base.py 가
# 선언한 "스킬은 일반 타입을 주고받는다" 와 어긋나는 유일한 자리였다.
# 이제 둘 다 되므로 **둘 다** 시험한다.
import store as _store


def _lookup(name):
    """일반 dict — 다른 도메인에서 Offer 클래스 없이 쓰는 경우."""
    return [{"item": name, "store": "가게", "price_krw": 3000,
             "delivery_min": 20, "in_stock": True, "can_order": True,
             "source": "시험"}]


def _lookup_obj(name):
    """도메인 객체 — 주방이 실제로 쓰는 형태."""
    return [_store.Offer(item=name, store="가게", price_krw=3000,
                         delivery_min=20, in_stock=True, can_order=True)]



# ── converge 를 위한 **제3의 기기** ──────────────────────────────────────
# 조리기도 건조기도 아니다. "관측값을 목표로 수렴시킨다" 는 구조만 같은
# 가상의 기기다. 여기서 돌면 재사용 주장이 말이 아니라 실행으로 선다.
class FakeDevice:
    """탱크의 수위를 빼내려 한다. 화력(밸브 세기)에 비례해 줄어든다."""

    def __init__(self, level=1.0, leak=0.0, stuck=False):
        self.level = level
        self.power = 0
        self.leak = leak          # 0 이면 밸브를 열어도 안 줄어든다
        self.stuck = stuck        # True 면 조작을 무시한다(고장)
        self.elapsed = 0.0
        self.initial = level

    def state(self):
        return {"level": round(self.level, 5), "power": self.power,
                "elapsed_min": round(self.elapsed, 2),
                "initial_mass_g": self.initial * 1000}

    def set_power(self, p):
        if not self.stuck:
            self.power = max(0, min(5, int(p)))
        return self.state()

    def tick(self, minutes=1.0):
        self.elapsed += minutes
        self.level = max(0.0, self.level - self.power * self.leak * minutes)


def _converge_case(**kw):
    """converge 한 판을 돌리고 (결과, 기기) 를 돌려준다."""
    dev = FakeDevice(**{k: v for k, v in kw.items()
                        if k in ("level", "leak", "stuck")})
    run_kw = dict(observe=dev.state, actuate=dev.set_power, step=dev.tick,
                  metric="level", target=kw.get("target", 0.5),
                  direction=kw.get("direction", "down"),
                  max_steps=kw.get("max_steps", 40))
    for extra in ("min_controllable", "tolerance"):
        if extra in kw:
            run_kw[extra] = kw[extra]
    return run_kw, dev



# ── 설계 층을 위한 최소 입력 ────────────────────────────────────────────
_PERSONA = {"id": "t1", "label": "시험용 1인 가구", "household_size": 1,
            "arrive_home": "20:00", "time_budget_min": 30,
            "next_morning_rush": False, "avoid": [],
            "dislike_noise_after": "23:00", "goal_hint": "시험",
            "friction_reported": ["냉장고를 여는 일", "냄비 앞을 지키는 일"],
            "fridge": []}
_COSTS = {"보관 확인": 2, "메뉴 결정": 3, "조달": 20, "준비": 4,
          "조리": 12, "세척 시작": 2}


def _design_cases():
    """설계 층 4종의 입력을 만든다. 앞 스킬의 출력을 뒤가 받으므로 순서대로."""
    from planner import Task, plan as make_plan
    sr = REGISTRY.get("situation_read").run(persona=_PERSONA,
                                            stage_costs=_COSTS)
    fr, cons = sr.output["friction"], sr.output["constraints"]
    # 장면은 도메인이 준다 — 스킬은 어떤 장면을 그릴지 모른다.
    def _beats(persona, constraints, plus):
        t0 = persona.get("arrive_home", "19:00")
        return [{"at": t0, "user": "아무것도 안 한다",
                 "system": "상태를 읽고 할 일을 정해 둔다",
                 "removes": persona["friction_reported"][0],
                 "verified_by": "inventory", "expect_metric": "메뉴"}]

    sd = REGISTRY.get("scenario_draft").run(persona=_PERSONA, friction=fr,
                                            constraints=cons,
                                            beats_for=_beats)
    scen = sd.output["scenario"]

    # 가짜 도메인: 두 단계짜리 작업 그래프
    tasks = [Task(skill="inventory", provides=("a",), bind=lambda c: {},
                  absorb=lambda c, o: None, note="1"),
             Task(skill="menu", requires=("a",), provides=("done",),
                  bind=lambda c: {}, absorb=lambda c, o: None, note="2")]
    fd = REGISTRY.get("flow_design").run(constraints={**cons, "goal_facts": ["done"]},
                                          tasks=tasks, planner=make_plan)
    plan = fd.output["plan"]

    def fake_exec(_plan):
        return {"ok": True, "user_touches": 0,
                "metrics": {"메뉴": "된장찌개"},
                "log": [{"skill": t.skill, "ok": True} for t in _plan.steps]}

    return {
        "situation_read": {
            "정상": dict(persona=_PERSONA, stage_costs=_COSTS),
            # 고객 진술뿐 아니라 **상황 추론도 0건**이어야 진짜 0건이다.
            # 시간 예산이 빠듯하면 "시간이 모자란 상태에서 메뉴를 정하는 일" 이
            # 자동으로 추가돼 0건이 되지 않는다(단계 합 43분).
            "불편 0건": dict(persona={**_PERSONA, "friction_reported": [],
                                   "time_budget_min": 120},
                          stage_costs=_COSTS),
        },
        "scenario_draft": {
            "정상": dict(persona=_PERSONA, friction=fr, constraints=cons,
                       beats_for=_beats),
            "불편 0건": dict(persona=_PERSONA, friction=[], constraints=cons,
                          beats_for=_beats),
        },
        "flow_design": {
            "정상": dict(constraints={**cons, "goal_facts": ["done"]}, tasks=tasks,
                       planner=make_plan),
        },
        "experience_verify": {
            "정상": dict(scenario=scen, plan=plan, execute=fake_exec,
                       touch_baseline=2),
            "실행이 실패": dict(scenario=scen, plan=plan, touch_baseline=2,
                          execute=lambda p: {"ok": False, "user_touches": 0,
                                             "metrics": {}, "log": []}),
        },
    }


def _pool(rows):
    """recipe_source.load 의 계약대로 만든다 — {recipes, source, key_used}."""
    return {"source": "시험용", "key_used": "없음",
            "recipes": [{"recipe_id": f"p{i}", "menu": m, "parts_raw": raw,
                         "servings": 2, "sodium_mg": na,
                         "ingredients": [{"name": raw.split()[0], "qty_g": 100}]}
                        for i, (m, raw, na) in enumerate(rows)]}


def _match(text, keywords):
    """계약: 원문에서 걸린 키워드들을 돌려준다."""
    return [k for k in keywords if k in (text or "")]


def _weigh(ratio):
    """계약대로 돌려주는 계량 함수. ratio 로 '덜 담김' 을 만든다."""
    def w(name, qty_g):
        got = round(qty_g * ratio, 1)
        return {"ok": True, "name": name, "target_g": qty_g, "actual_g": got,
                "expected_extra_water_g": round(got * 0.1, 1),
                "short_g": round(qty_g - got, 1) if got < qty_g else 0}
    return w


CASES = {
    "inventory": {
        "정상": dict(items=_STOCK, urgency_ratio=0.6),
        "빈 목록": dict(items=[], urgency_ratio=0.6),
        "전부 여유": dict(items=[{"name": "쌀", "qty_g": 1000,
                               "stored_days": 1, "shelf_life_days": 365}]),
        "수명 0일": dict(items=[{"name": "우유", "qty_g": 500,
                              "stored_days": 9, "shelf_life_days": 0}]),
    },
    "menu": {
        "정상": dict(records=_RECORDS, stock=_STOCK, prefer_items=["배추"]),
        "기록 없음": dict(records=[], stock=_STOCK),
        "재고 없음": dict(records=_RECORDS, stock=[]),
        "전부 기피": dict(records=_RECORDS, stock=_STOCK, avoid=["배추", "두부"]),
    },
    "recipe_source": {
        "정상": dict(load=lambda: _pool([("된장찌개", "배추 두부", 250),
                                       ("버섯전골", "표고버섯 무", 900)]),
                   match=_match),
        "자료 0건": dict(load=lambda: _pool([]), match=_match),
        "전부 기피": dict(load=lambda: _pool([("버섯전골", "표고버섯 무", 250)]),
                     match=_match, avoid=["표고버섯"]),
        "나트륨 0 제한": dict(load=lambda: _pool([("된장찌개", "배추 두부", 250)]),
                        match=_match, max_sodium_mg=0),
    },
    "prep": {
        "정상": dict(record=_RECORDS[0],
                   weigh=_weigh(1.0), available=lambda n: True),
        "재고 전무": dict(record=_RECORDS[0],
                      weigh=lambda n, g: {"ok": False, "reason": f"{n} 없음"},
                      available=lambda n: False),
        "재료 0건": dict(record={**_RECORDS[0], "ingredients": []},
                     weigh=_weigh(1.0), available=lambda n: True),
        "계량이 절반만": dict(record=_RECORDS[0], weigh=_weigh(0.4),
                       available=lambda n: True),
        "계약 위반": dict(record=_RECORDS[0],
                      weigh=lambda n, g: {"ok": True, "actual_g": g},
                      available=lambda n: True),
    },
    "procure": {
        "정상": dict(missing=["대파"], lookup=_lookup, known_items=["대파"],
                   auto_limit_krw=15000),
        "부족분 없음": dict(missing=[], lookup=_lookup, known_items=[],
                       auto_limit_krw=15000),
        "기한 0분": dict(missing=["대파"], lookup=_lookup, known_items=["대파"],
                      auto_limit_krw=15000, deadline_min=0),
        "예산 0원": dict(missing=["대파"], lookup=_lookup, known_items=["대파"],
                      auto_limit_krw=0),
        "도메인 객체로도": dict(missing=["대파"], lookup=_lookup_obj,
                        known_items=["대파"], auto_limit_krw=15000),
    },
    "aftercare": {
        "정상": dict(soil_score=0.36),
        "0": dict(soil_score=0.0),
        "1": dict(soil_score=1.0),
        "다른 기기": dict(soil_score=0.36, profile="washer"),
    },
}

# 계약을 어긴 입력. 여기서는 **예외가 나는 것이 정상**이고, 그 메시지가
# 무엇이 빠졌는지 말해 줘야 한다 (KeyError 한 줄로 끝나면 재사용하는 쪽이
# 소스를 읽어야 한다).
EXPECT_ERROR = {("prep", "계약 위반"): "weigh()"}

# 설계 층: 덜어낼 수고가 0건인 입력은 "할 일이 없었다" 지 "해냈다" 가 아니다.
DESIGN_EMPTY = {("situation_read", "불편 0건"), ("scenario_draft", "불편 0건"),
                ("experience_verify", "실행이 실패")}

# 스킬이 "아무것도 못 한" 입력. 여기서 ok=True 면 성과가 부풀려진다.
EMPTY_CASE = {"inventory": "빈 목록", "menu": "기록 없음",
              "procure": None, "aftercare": None,
              "recipe_source": "자료 0건", "prep": "재료 0건"}


def _run(name, kw):
    return REGISTRY.get(name).run(**copy.deepcopy(kw))


def main() -> int:
    print("스킬 단독 검사 — 파이프라인 밖에서도 성립하는가")
    print()
    bad = 0

    # ── 5 격리 : 스킬 모듈이 도메인 모듈을 import 하지 않는가 ──
    leaked = []
    for mod in {REGISTRY.get(s["name"]).__class__.__module__
                for s in REGISTRY.list()}:
        src = inspect.getsource(sys.modules[mod])
        for forbidden in ("import kitchen", "import store", "import personas",
                          "import dryer"):
            if forbidden in src:
                leaked.append(f"{mod} 가 {forbidden}")
    if leaked:
        print(f"  !!  격리        스킬이 도메인을 직접 읽는다: {leaked}")
        bad += 1
    else:
        print(f"  OK  격리        스킬 {len(REGISTRY.list())}개가 도메인 모듈을 "
              f"import 하지 않는다")

    # ── 1~4 (설계 층 입력을 만들어 합친다) ──
    ALL = dict(CASES)
    try:
        for k, v in _design_cases().items():
            ALL[k] = v
    except Exception as e:
        print(f"  !!  설계층      입력을 만들지 못했다: {type(e).__name__}: {e}")
        bad += 1
    for name, cases in ALL.items():
        for label, kw in cases.items():
            tag = f"{name}·{label}"
            want_err = EXPECT_ERROR.get((name, label))
            try:
                r1 = _run(name, kw)
                if want_err:
                    print(f"  !!  {tag:22s} 계약을 어겼는데 조용히 통과했다")
                    bad += 1
                    continue
            except Exception as e:
                msg = str(e)
                if want_err:
                    # 어느 키가 걸리든 상관없다. 중요한 것은 (1) 어느 주입 함수인지,
                    # (2) 계약을 보라고 말하는지, (3) 받은 키를 보여 주는지다.
                    helpful = (want_err in msg and "계약" in msg
                               and "받은 키" in msg)
                    print(f"  {'OK ' if helpful else '!! '} {tag:22s} "
                          + (f"계약 위반을 알림: {msg[:60]}" if helpful
                             else f"메시지가 불친절하다: {type(e).__name__}: {msg[:50]}"))
                    if not helpful:
                        bad += 1
                    continue
                print(f"  !!  {tag:22s} 예외 {type(e).__name__}: {e}")
                bad += 1
                continue

            # 1 순수성 — 같은 입력을 두 번
            try:
                r2 = _run(name, kw)
                same = (r1.ok == r2.ok and r1.output == r2.output)
            except Exception as e:
                print(f"  !!  {tag:22s} 두 번째 호출에서 예외 {e}")
                bad += 1
                continue
            if not same:
                print(f"  !!  {tag:22s} 같은 입력인데 결과가 다르다 "
                      f"(전역 상태나 난수를 쓴다)")
                bad += 1
                continue

            # 3 근거
            if not r1.evidence:
                print(f"  !!  {tag:22s} 판단 근거가 비어 있다")
                bad += 1
                continue

            # 4 정직성 — 아무것도 못 한 입력에 성공을 주지 않는가
            if ((EMPTY_CASE.get(name) == label
                 or (name, label) in DESIGN_EMPTY) and r1.ok):
                print(f"  !!  {tag:22s} 아무것도 못 했는데 ok=True")
                bad += 1
                continue

            print(f"  OK  {tag:22s} ok={str(r1.ok):5s} 근거 {len(r1.evidence)}줄")

    # ── converge : 제3의 기기에서 돌려 본다 ────────────────────────────
    # 조리기도 건조기도 아닌 가짜 탱크다. 여기서 돌면 "관측·조작·진행만
    # 주입하면 어느 기기에나 쓴다" 가 말이 아니라 실행으로 선다.
    # 그리고 **모든 종료 경로에서 액추에이터가 꺼져 있는지** 함께 본다 —
    # 되돌릴 수 없는 과정에서 켜 둔 채 돌아가면 안전 문제다.
    CONV = [
        ("정상 수렴",    dict(level=1.0, leak=0.04, target=0.5), True),
        ("이미 달성",    dict(level=0.4, leak=0.04, target=0.5), True),
        ("빠지지 않음",  dict(level=1.0, leak=0.0,  target=0.5), False),
        ("조작이 먹히지 않음", dict(level=1.0, leak=0.04, target=0.5,
                            stuck=True), False),
        ("목표가 0",     dict(level=1.0, leak=0.04, target=0.0), True),
        # 내리는 방향인데 목표가 현재보다 위다 — 되돌릴 수 없으므로
        # 이미 만족한 것으로 보고 손대지 않아야 한다.
        ("목표가 위쪽",   dict(level=0.3, leak=0.04, target=0.9), True),
    ]
    conv = REGISTRY.get("converge")
    for label, kw, want_reach in CONV:
        tag = f"converge·{label}"
        run_kw, dev = _converge_case(**kw)
        try:
            r = conv.run(**run_kw)
        except Exception as e:
            print(f"  !!  {tag:22s} 예외 {type(e).__name__}: {e}")
            bad += 1
            continue
        reached = bool(r.output.get("reached"))
        left_on = dev.power != 0
        if left_on:
            print(f"  !!  {tag:22s} 끝났는데 액추에이터가 켜져 있다 "
                  f"(power={dev.power})")
            bad += 1
            continue
        if reached != want_reach:
            print(f"  !!  {tag:22s} reached={reached} 인데 {want_reach} 여야 한다")
            bad += 1
            continue
        if not reached and not (r.output.get("recovery")
                                or r.output.get("too_small")
                                or r.output.get("guard_notes")):
            print(f"  !!  {tag:22s} 도달하지 못했는데 사유가 없다")
            bad += 1
            continue
        print(f"  OK  {tag:22s} reached={str(reached):5s} "
              f"{r.output['steps']}단계 → {r.output['final']} (꺼짐)")

    print()
    total = 1 + len(CONV) + sum(len(c) for c in ALL.values())
    print(f"판정: {total - bad}/{total} 항목 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
