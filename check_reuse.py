# -*- coding: utf-8 -*-
"""`reusable_for` 에 적어 둔 것이 말뿐인지 실제인지 확인한다.

스킬마다 "이 능력은 여기에도 쓸 수 있다" 고 적어 두었다. 그런데 적어 두는
것과 실제로 되는 것은 다르다. 실제로 `aftercare` 는 로봇청소기를 적어
놓고 코스표가 식기세척기·세탁기뿐이어서, 쓰려면 스킬 코드를 고쳐야 했다
— 선언이 거짓이었다(고쳐서 이제 코스표를 주입받는다).

그래서 여기서는 **적어 둔 도메인을 실제로 한 번 돌린다.** 규칙은 하나다.

    스킬 파일을 **한 줄도 고치지 않고**, 주입하는 것만 바꿔서 돌아가야 한다.

주방과 상관없는 대상을 일부러 고른다 — 의약품 유효기간, 제습기, 에어컨,
로봇청소기, 세제 투입량. 주방 냄새가 남아 있으면 그건 재사용이 아니다.
"""
from __future__ import annotations
import sys

from skills import REGISTRY


# ── converge 를 위한 기기들 ─────────────────────────────────────────────
class _Box:
    """습도를 낮추는 상자. 제습기이기도 하고 에어컨이기도 하다."""

    def __init__(self, value, key, rate):
        self.value = value
        self.key = key          # 관측 이름 (humidity / feels_like_c)
        self.rate = rate        # 세기 1 당 1분에 줄어드는 양
        self.power = 0
        self.elapsed = 0.0

    def state(self):
        return {self.key: round(self.value, 4), "power": self.power,
                "elapsed_min": round(self.elapsed, 2)}

    def set_power(self, p):
        self.power = max(0, min(5, int(p)))
        return self.state()

    def tick(self, minutes=1.0):
        self.elapsed += minutes
        self.value = max(0.0, self.value - self.power * self.rate * minutes)


def _converge_on(dev, target):
    r = REGISTRY.get("converge").run(
        observe=dev.state, actuate=dev.set_power, step=dev.tick,
        metric=dev.key, target=target, direction="down", max_steps=60)
    return r, dev


CASES = []


def case(skill, domain):
    def deco(fn):
        CASES.append((skill, domain, fn))
        return fn
    return deco


# ── inventory : 주방이 아니라 약통 ──────────────────────────────────────
@case("inventory", "화장품·의약품 유효기간")
def _meds():
    items = [{"name": "타이레놀", "stored_days": 700, "shelf_life_days": 730},
             {"name": "안약", "stored_days": 25, "shelf_life_days": 28},
             {"name": "비타민", "stored_days": 30, "shelf_life_days": 365},
             {"name": "연고", "stored_days": 400, "shelf_life_days": 365}]
    r = REGISTRY.get("inventory").run(items=items, urgency_ratio=0.6)
    urgent = [i["name"] for i in r.output["urgent"]]
    expired = [i["name"] for i in r.output["expired"]]
    ok = r.ok and "안약" in urgent and "연고" in expired and "비타민" not in urgent
    return ok, f"임박 {urgent} · 기한 지남 {expired}"


# ── menu : 조리 기록이 아니라 세탁 코스 기록 ────────────────────────────
@case("menu", "세탁 코스 기록")
def _wash_records():
    records = [
        {"record_id": "w1", "menu": "수건 삶음", "servings": 1,
         "ingredients": [{"name": "수건", "qty_g": 2000},
                         {"name": "산소표백제", "qty_g": 60}]},
        {"record_id": "w2", "menu": "울 섬세", "servings": 1,
         "ingredients": [{"name": "니트", "qty_g": 1200},
                         {"name": "울세제", "qty_g": 40}]}]
    basket = [{"name": "수건", "qty_g": 2400},
              {"name": "산소표백제", "qty_g": 300}]
    r = REGISTRY.get("menu").run(records=records, stock=basket,
                                 prefer_items=["수건"])
    best = r.output.get("best")
    ok = r.ok and best and best["menu"] == "수건 삶음"
    return ok, f"고른 코스 {best['menu'] if best else None}"


# ── prep : 조리 계량이 아니라 세제 투입량 ───────────────────────────────
@case("prep", "세제 투입량 산정")
def _detergent():
    recipe = {"record_id": "d1", "menu": "표준 세탁", "servings": 1,
              "initial_mass_g": 3000,
              "ingredients": [{"name": "세제", "qty_g": 45},
                              {"name": "섬유유연제", "qty_g": 30}]}

    def weigh(name, qty_g):
        return {"ok": True, "name": name, "target_g": qty_g,
                "actual_g": qty_g, "expected_extra_water_g": 0.0,
                "short_g": 0}

    r = REGISTRY.get("prep").run(record=recipe, weigh=weigh,
                                 available=lambda n: True)
    ok = r.ok and abs(r.output["total_mass_g"] - 3000) < 1e-6
    return ok, f"투입 합계 {r.output['total_mass_g']}g"


# ── procure : 식재료가 아니라 소모품 재주문 ─────────────────────────────
@case("procure", "세제·소모품 재주문")
def _supplies():
    import store as S

    def lookup(item):
        return [S.Offer(item=item, store="생활몰", price_krw=8900,
                        delivery_min=120, in_stock=True, can_order=True)]

    r = REGISTRY.get("procure").run(missing=["정수기 필터", "세탁조 클리너"],
                                    lookup=lookup,
                                    known_items=["정수기 필터"],
                                    auto_limit_krw=15000)
    auto = [a["name"] for a in r.output["auto_ordered"]]
    ask = [c["name"] for c in r.output["need_confirm"]]
    # 사 본 적 있는 것만 자동, 처음 사는 것은 묻는다 — 주방과 같은 판단
    ok = r.ok and auto == ["정수기 필터"] and ask == ["세탁조 클리너"]
    return ok, f"자동 {auto} · 확인 {ask}"


# ── converge : 조리기·건조기가 아닌 두 기기 ─────────────────────────────
@case("converge", "제습기(습도)")
def _dehumidifier():
    r, dev = _converge_on(_Box(0.72, "humidity", 0.02), target=0.50)
    ok = r.output["reached"] and dev.power == 0
    return ok, f"{r.output['steps']}분에 습도 {r.output['final']} (꺼짐)"


@case("converge", "에어컨(체감온도)")
def _aircon():
    r, dev = _converge_on(_Box(31.0, "feels_like_c", 0.35), target=26.0)
    ok = r.output["reached"] and dev.power == 0
    return ok, f"{r.output['steps']}분에 체감 {r.output['final']}도 (꺼짐)"


# ── aftercare : 식기세척기·세탁기가 아니라 로봇청소기 ───────────────────
@case("aftercare", "로봇청소기(바닥 오염)")
def _vacuum():
    # 코스표만 주입한다. 스킬은 로봇청소기를 모른다.
    VAC = {"vacuum": [(2, "꼼꼼(물걸레 2회)", 95, 0, 0.9, 68),
                      (1, "표준", 55, 0, 0.5, 62),
                      (0, "조용", 40, 0, 0.3, 54)]}
    sk = REGISTRY.get("aftercare")
    dirty = sk.run(soil_score=0.72, profile="vacuum", profiles=VAC)
    clean = sk.run(soil_score=0.10, profile="vacuum", profiles=VAC)
    ok = (dirty.ok and clean.ok
          and dirty.output["course"] == "꼼꼼(물걸레 2회)"
          and clean.output["course"] == "조용")
    return ok, f"오염 0.72 → {dirty.output['course']} / 0.10 → {clean.output['course']}"


# ── recipe_source : 레시피가 아니라 제품 리뷰 ───────────────────────────
@case("recipe_source", "제품 리뷰 수집")
def _reviews():
    def load():
        return {"source": "리뷰 API", "key_used": "없음", "recipes": [
            {"recipe_id": "r1", "menu": "A 청소기", "parts_raw": "흡입력 소음",
             "sodium_mg": None, "method": "무선"},
            {"recipe_id": "r2", "menu": "B 청소기", "parts_raw": "광고 협찬",
             "sodium_mg": None, "method": "유선"}]}

    r = REGISTRY.get("recipe_source").run(
        load=load, match=lambda text, kw: [k for k in kw if k in (text or "")],
        avoid=["협찬"], methods=["무선"])
    kept = [x["menu"] for x in r.output["recipe_pool"]]
    ok = r.ok and kept == ["A 청소기"]
    return ok, f"남은 후보 {kept} (협찬·유선 제외)"


def _design_domain_words():
    """설계 층 코드에 도메인 어휘가 얼마나 남아 있는지 잰다.

    "설계 스킬은 주방을 모른다" 는 문장이 문서에 있었는데, 재 보니
    situation_read 와 scenario_draft 는 장면 문장·단계 이름이 주방에
    묶여 있었다. 말로 쓰지 말고 매번 재서 숫자로 말한다.
    """
    import inspect
    import skills.design_skills as D
    WORDS = ("조리", "요리", "냄비", "식기", "레시피", "메뉴", "재료",
             "세척", "가열", "끓")
    CLS = {"situation_read": "SituationReadSkill",
           "scenario_draft": "ScenarioDraftSkill",
           "flow_design": "FlowDesignSkill",
           "experience_verify": "ExperienceVerifySkill"}
    out = {}
    for name, cls in CLS.items():
        src = inspect.getsource(getattr(D, cls))
        body = chr(10).join(l for l in src.splitlines()
                            if l.strip() and not l.strip().startswith("#")
                            and "reusable_for" not in l)
        hits = {w: body.count(w) for w in WORDS if body.count(w)}
        out[name] = hits
    return out



# ── 세탁실 도메인 : 설계 층 4종을 주방 밖에서 돌린다 ───────────────────
# 주방 도메인 파일(kitchen_domain.py)에 있는 것과 같은 자리를 세탁으로
# 채운다. **설계 층 스킬은 한 줄도 고치지 않는다.**
LAUNDRY_TERMS = {"amount": "세탁량", "finish": "건조",
                 "finish_course": "건조 코스",
                 "short_time": "시간이 모자란 상태에서 코스를 정하는 일",
                 "avoid_check": "옷마다 상하는 소재가 섞였는지 확인하는 일"}

LAUNDRY_PERSONA = {
    "id": "w1", "label": "맞벌이 빨래", "household_size": 2,
    "arrive_home": "19:00", "time_budget_min": 150, "next_morning_rush": True,
    "avoid": ["실크"], "dislike_noise_after": "22:00",
    "goal_hint": "내일 입을 옷",
    "friction_reported": ["빨래를 언제 돌릴지 정하는 일",
                          "건조기에서 꺼내는 시점을 놓치는 일"]}

LAUNDRY_COSTS = {"분류": 3, "세탁": 45, "건조": 60, "정리": 10}


def laundry_beats(persona, constraints, plus):
    """세탁실 장면. kitchen_beats 와 같은 자리다."""
    t0 = persona.get("arrive_home", "19:00")
    beats = [{
        "at": t0, "user": "세탁물을 넣는다",
        "system": "소재와 오염도를 읽고 코스를 정해 둔다",
        "removes": "빨래를 언제 돌릴지 정하는 일",
        "verified_by": "inventory", "expect_metric": "코스"}]
    if constraints.get("avoid"):
        beats.append({
            "at": plus(t0, 1), "user": "옷을 일일이 확인하지 않는다",
            "system": "상하는 소재(" + ", ".join(constraints["avoid"]) + ")를 골라낸다",
            "removes": "옷마다 상하는 소재가 섞였는지 확인하는 일",
            "verified_by": "menu", "expect_metric": "코스"})
    beats.append({
        "at": plus(t0, 50), "user": "꺼내는 때를 안 본다",
        "system": "함수율을 재서 목표에 닿으면 멈추고 알린다",
        "removes": "건조기에서 꺼내는 시점을 놓치는 일",
        "verified_by": "converge", "expect_metric": "건조 시간(분)"})
    return beats


@case("situation_read", "세탁·의류관리")
def _sr_laundry():
    r = REGISTRY.get("situation_read").run(
        persona=LAUNDRY_PERSONA, stage_costs=LAUNDRY_COSTS,
        terms=LAUNDRY_TERMS)
    c = r.output["constraints"]
    kitchen_words = [e for e in r.evidence
                     if "조리" in e or "세척" in e or "재료" in e]
    ok = (r.ok and len(r.output["friction"]) >= 2
          and c.get("time_budget_min") == 150
          and c.get("quiet_after") == "22:00"
          and not kitchen_words)
    return ok, ("수고 " + str(len(r.output["friction"])) + "건 · 제약(시간 "
                + str(c.get("time_budget_min")) + "분, 소음 "
                + str(c.get("quiet_after")) + ") · 근거의 주방 어휘 "
                + str(len(kitchen_words)) + "건")


@case("scenario_draft", "세탁 경험")
def _sd_laundry():
    sr = REGISTRY.get("situation_read").run(
        persona=LAUNDRY_PERSONA, stage_costs=LAUNDRY_COSTS,
        terms=LAUNDRY_TERMS)
    r = REGISTRY.get("scenario_draft").run(
        persona=LAUNDRY_PERSONA, friction=sr.output["friction"],
        constraints=sr.output["constraints"], beats_for=laundry_beats)
    beats = r.output["scenario"]["beats"]
    bad = [b for b in beats if "조리" in b["system"] or "화력" in b["system"]]
    ok = r.ok and len(beats) >= 3 and not bad
    return ok, ("장면 " + str(len(beats)) + "개 · 덮은 수고 "
                + str(r.output["scenario"]["covered"]) + "건 · 주방 문장 "
                + str(len(bad)) + "건")


@case("flow_design", "설비 운전 계획")
def _fd_laundry():
    from planner import Task, plan as make_plan
    tasks = [Task(skill="inventory", provides=("sorted",), bind=lambda c: {},
                  absorb=lambda c, o: None, note="분류"),
             Task(skill="converge", provides=("washed",), requires=("sorted",),
                  bind=lambda c: {}, absorb=lambda c, o: None, note="세탁"),
             Task(skill="converge", provides=("dried",), requires=("washed",),
                  bind=lambda c: {}, absorb=lambda c, o: None, note="건조")]
    r = REGISTRY.get("flow_design").run(
        constraints={}, tasks=tasks, planner=make_plan, goal={"dried"})
    steps = r.output["flow"]["steps"]
    ok = r.ok and steps == ["inventory", "converge", "converge"]
    return ok, str(steps) + " (converge 를 세탁·건조에 두 번)"


@case("experience_verify", "자동화 회귀 시험")
def _ev_generic():
    """주방도 세탁도 아닌 **임의 자동화**를 검증한다."""
    from planner import Task, plan as make_plan
    tasks = [Task(skill="inventory", provides=("done",), bind=lambda c: {},
                  absorb=lambda c, o: None, note="한 단계")]
    p = make_plan({"done"}, tasks)
    scen = {"beats": [{"at": "09:00", "user": "버튼을 누르지 않는다",
                       "system": "배치가 스스로 돈다", "removes": "수동 실행",
                       "verified_by": "inventory",
                       "expect_metric": "처리 건수"}]}

    def fake_exec(_plan):
        return {"ok": True, "user_touches": 0,
                "metrics": {"처리 건수": 120},
                "log": [{"skill": t.skill, "ok": True} for t in _plan.steps]}

    r = REGISTRY.get("experience_verify").run(
        scenario=scen, plan=p, execute=fake_exec, touch_baseline=1)
    ok = r.ok and r.output["beats_met"] == 1
    return ok, ("장면 " + str(r.output["beats_met"]) + "/"
                + str(r.output["beats_total"]) + " · 개입 "
                + str(r.output["user_touches"]))


# ── 실행 층 : 아직 안 돌려 본 선언들 ───────────────────────────────────
@case("inventory", "냉동고")
def _freezer():
    items = [{"name": "냉동만두", "stored_days": 300, "shelf_life_days": 365},
             {"name": "아이스크림", "stored_days": 10, "shelf_life_days": 730}]
    r = REGISTRY.get("inventory").run(items=items, urgency_ratio=0.6)
    urgent = [i["name"] for i in r.output["urgent"]]
    ok = r.ok and urgent == ["냉동만두"]
    return ok, "임박 " + str(urgent)


@case("inventory", "팬트리")
def _pantry():
    items = [{"name": "파스타면", "stored_days": 500, "shelf_life_days": 730},
             {"name": "통조림", "stored_days": 100, "shelf_life_days": 1095}]
    r = REGISTRY.get("inventory").run(items=items, urgency_ratio=0.6)
    urgent = [i["name"] for i in r.output["urgent"]]
    ok = r.ok and urgent == ["파스타면"]
    return ok, "임박 " + str(urgent)


@case("menu", "청소 루틴 기록")
def _clean_routine():
    records = [{"record_id": "c1", "menu": "주말 대청소", "servings": 1,
                "ingredients": [{"name": "물걸레포", "qty_g": 50},
                                {"name": "세정제", "qty_g": 200}]},
               {"record_id": "c2", "menu": "간단 먼지제거", "servings": 1,
                "ingredients": [{"name": "먼지봉투", "qty_g": 30}]}]
    stock = [{"name": "물걸레포", "qty_g": 100},
             {"name": "세정제", "qty_g": 500}]
    r = REGISTRY.get("menu").run(records=records, stock=stock,
                                 prefer_items=["세정제"])
    best = r.output.get("best")
    ok = bool(r.ok and best and best["menu"] == "주말 대청소")
    return ok, "고른 루틴 " + str(best["menu"] if best else None)


@case("procure", "필터·부품 교체")
def _parts():
    def lookup(item):
        return [{"item": item, "store": "부품몰", "price_krw": 12000,
                 "delivery_min": 2880, "in_stock": True, "can_order": True}]
    r = REGISTRY.get("procure").run(missing=["헤파필터", "배수호스"],
                                    lookup=lookup, known_items=["헤파필터"],
                                    auto_limit_krw=15000)
    auto = [a["name"] for a in r.output["auto_ordered"]]
    ask = [c["name"] for c in r.output["need_confirm"]]
    ok = r.ok and auto == ["헤파필터"] and ask == ["배수호스"]
    return ok, "자동 " + str(auto) + " · 확인 " + str(ask)


@case("prep", "정수량 배분")
def _water_split():
    rec = {"record_id": "w1", "menu": "하루 급수", "servings": 1,
           "initial_mass_g": 2000,
           "ingredients": [{"name": "아침", "qty_g": 500},
                           {"name": "점심", "qty_g": 800},
                           {"name": "저녁", "qty_g": 700}]}

    def weigh(name, qty_g):
        return {"ok": True, "name": name, "target_g": qty_g, "actual_g": qty_g,
                "expected_extra_water_g": 0.0, "short_g": 0}

    r = REGISTRY.get("prep").run(record=rec, weigh=weigh,
                                 available=lambda n: True)
    ok = r.ok and abs(r.output["total_mass_g"] - 2000) < 1e-6
    return ok, "배분 합계 " + str(r.output["total_mass_g"]) + "g"


@case("recipe_source", "특허·논문 수집")
def _papers():
    def load():
        return {"source": "논문 API", "key_used": "없음", "recipes": [
            {"recipe_id": "a1", "menu": "저온 조리 제어",
             "parts_raw": "PID 제어 열전대", "method": "실험",
             "sodium_mg": None},
            {"recipe_id": "a2", "menu": "리뷰 논문",
             "parts_raw": "메타분석 설문", "method": "리뷰",
             "sodium_mg": None}]}
    r = REGISTRY.get("recipe_source").run(
        load=load, match=lambda t, kw: [k for k in kw if k in (t or "")],
        avoid=["설문"], methods=["실험"])
    kept = [x["menu"] for x in r.output["recipe_pool"]]
    ok = r.ok and kept == ["저온 조리 제어"]
    return ok, "남은 후보 " + str(kept) + " (설문·리뷰 제외)"


@case("recipe_source", "식자재 카탈로그")
def _catalog():
    def load():
        return {"source": "카탈로그", "key_used": "없음", "recipes": [
            {"recipe_id": "c1", "menu": "유기농 배추 1kg", "parts_raw": "배추",
             "method": "신선", "sodium_mg": 20},
            {"recipe_id": "c2", "menu": "절임배추 10kg",
             "parts_raw": "배추 소금", "method": "절임", "sodium_mg": 900}]}
    r = REGISTRY.get("recipe_source").run(
        load=load, match=lambda t, kw: [k for k in kw if k in (t or "")],
        max_sodium_mg=100)
    kept = [x["menu"] for x in r.output["recipe_pool"]]
    ok = r.ok and kept == ["유기농 배추 1kg"]
    return ok, "나트륨 100mg 이하만 " + str(kept)



# ── 남은 선언들도 전부 돌린다 ──────────────────────────────────────────
# "이론상 될 것" 과 "돌려봤다" 는 다르다. 설계 층 4종은 앞에서 세탁으로
# 한 번 증명했지만, 선언에 적어 둔 다른 도메인도 같은 자리에 값을 넣어
# 실제로 돌려 본다. 여기서도 스킬 코드는 한 줄도 고치지 않는다.

def _sr_on(label, persona, costs, terms):
    r = REGISTRY.get("situation_read").run(persona=persona, stage_costs=costs,
                                           terms=terms)
    kitchen = [e for e in r.evidence
               if "조리" in e or "세척" in e or "재료" in e]
    ok = r.ok and r.output["friction"] and not kitchen
    return ok, ("수고 " + str(len(r.output["friction"])) + "건 · 주방 어휘 "
                + str(len(kitchen)) + "건")


@case("situation_read", "청소 루틴")
def _sr_clean():
    return _sr_on("청소", {
        "id": "v1", "label": "주 2회 청소", "household_size": 3,
        "arrive_home": "18:00", "time_budget_min": 90,
        "next_morning_rush": False, "avoid": [],
        "dislike_noise_after": "21:00", "goal_hint": "먼지 없는 바닥",
        "friction_reported": ["청소기를 언제 돌릴지 정하는 일",
                              "먼지통을 비우는 때를 놓치는 일"]},
        {"준비": 5, "청소": 40, "물걸레": 25, "정리": 10},
        {"amount": "청소 범위", "finish": "물걸레",
         "finish_course": "물걸레 코스",
         "short_time": "시간이 모자란 상태에서 범위를 정하는 일",
         "avoid_check": "구역마다 들어가면 안 되는 곳을 확인하는 일"})


@case("situation_read", "공조 운전")
def _sr_hvac():
    return _sr_on("공조", {
        "id": "h1", "label": "여름 재택", "household_size": 2,
        "arrive_home": "09:00", "time_budget_min": 600,
        "next_morning_rush": False, "avoid": [],
        "dislike_noise_after": "23:00", "goal_hint": "덥지도 춥지도 않게",
        "friction_reported": ["온도를 계속 다시 맞추는 일",
                              "습도가 높은지 몰라 불쾌한 일"]},
        {"감지": 2, "냉방": 120, "제습": 60, "환기": 15},
        {"amount": "운전 강도", "finish": "환기", "finish_course": "환기 모드",
         "short_time": "시간이 모자란 상태에서 모드를 정하는 일",
         "avoid_check": "방마다 피해야 할 설정을 확인하는 일"})


def _sd_on(persona, terms, beats_fn):
    sr = REGISTRY.get("situation_read").run(persona=persona,
                                            stage_costs={"a": 5, "b": 30},
                                            terms=terms)
    r = REGISTRY.get("scenario_draft").run(
        persona=persona, friction=sr.output["friction"],
        constraints=sr.output["constraints"], beats_for=beats_fn)
    beats = r.output["scenario"]["beats"]
    bad = [b for b in beats if "조리" in b["system"] or "화력" in b["system"]]
    ok = r.ok and beats and not bad
    return ok, ("장면 " + str(len(beats)) + "개 · 덮은 수고 "
                + str(r.output["scenario"]["covered"]) + "건")


@case("scenario_draft", "외출·귀가 루틴")
def _sd_away():
    P = {"id": "o1", "label": "외출 잦은 1인", "household_size": 1,
         "arrive_home": "20:00", "time_budget_min": 60,
         "next_morning_rush": True, "avoid": [],
         "dislike_noise_after": "23:00", "goal_hint": "들어오면 편하게",
         "friction_reported": ["나갈 때 기기를 하나씩 끄는 일",
                               "들어와서 불·온도를 다시 맞추는 일"]}
    T = {"amount": "운전 범위", "finish": "귀가 준비",
         "finish_course": "귀가 모드",
         "short_time": "시간이 모자란 상태에서 무엇을 켤지 정하는 일",
         "avoid_check": "방마다 꺼야 할 것을 확인하는 일"}

    def beats(persona, constraints, plus):
        t0 = persona["arrive_home"]
        return [{"at": plus(t0, -60) if False else "08:30",
                 "user": "현관을 나선다",
                 "system": "사람이 없는 것을 알고 기기를 한 번에 정리한다",
                 "removes": "나갈 때 기기를 하나씩 끄는 일",
                 "verified_by": "inventory", "expect_metric": "정리한 기기"},
                {"at": t0, "user": "들어온다",
                 "system": "도착 시각에 맞춰 불과 온도를 미리 맞춰 둔다",
                 "removes": "들어와서 불·온도를 다시 맞추는 일",
                 "verified_by": "converge", "expect_metric": "도달 온도"}]
    return _sd_on(P, T, beats)


@case("scenario_draft", "수면 루틴")
def _sd_sleep():
    P = {"id": "s1", "label": "잠이 얕은 2인", "household_size": 2,
         "arrive_home": "22:00", "time_budget_min": 60,
         "next_morning_rush": True, "avoid": [],
         "dislike_noise_after": "22:30", "goal_hint": "깨지 않는 밤",
         "friction_reported": ["자기 전 온도를 맞추는 일",
                               "밤에 더워서 깨는 일"]}
    T = {"amount": "취침 설정", "finish": "기상 준비",
         "finish_course": "기상 모드",
         "short_time": "시간이 모자란 상태에서 설정을 고르는 일",
         "avoid_check": "방마다 피해야 할 소음원을 확인하는 일"}

    def beats(persona, constraints, plus):
        t0 = persona["arrive_home"]
        return [{"at": t0, "user": "리모컨을 찾지 않는다",
                 "system": "잠들 시각에 맞춰 온도를 내리기 시작한다",
                 "removes": "자기 전 온도를 맞추는 일",
                 "verified_by": "converge", "expect_metric": "도달 온도"},
                {"at": plus(t0, 180), "user": "깨지 않는다",
                 "system": "체감온도가 오르면 소리 없이 조정한다",
                 "removes": "밤에 더워서 깨는 일",
                 "verified_by": "aftercare", "expect_metric": "야간 모드"}]
    return _sd_on(P, T, beats)


@case("flow_design", "업무 파이프라인 설계")
def _fd_pipeline():
    """가전이 아니라 **업무 절차**로 계획을 짠다."""
    from planner import Task, plan as make_plan
    tasks = [Task(skill="recipe_source", provides=("자료수집",),
                  bind=lambda c: {}, absorb=lambda c, o: None, note="수집"),
             Task(skill="menu", provides=("초안",), requires=("자료수집",),
                  bind=lambda c: {}, absorb=lambda c, o: None, note="초안"),
             Task(skill="experience_verify", provides=("검토",),
                  requires=("초안",), bind=lambda c: {},
                  absorb=lambda c, o: None, note="검토"),
             Task(skill="aftercare", provides=("배포",), requires=("검토",),
                  bind=lambda c: {}, absorb=lambda c, o: None, note="배포")]
    r = REGISTRY.get("flow_design").run(constraints={}, tasks=tasks,
                                        planner=make_plan, goal={"배포"})
    steps = r.output["flow"]["steps"]
    ok = r.ok and steps == ["recipe_source", "menu", "experience_verify",
                            "aftercare"]
    return ok, str(steps)


@case("experience_verify", "운전 정책 평가")
def _ev_policy():
    """가전 운전 정책이 목표를 지키는지 평가한다 — 시나리오가 아니라
    정책 문장을 검증 대상으로 둔다."""
    from planner import Task, plan as make_plan
    tasks = [Task(skill="converge", provides=("유지",), bind=lambda c: {},
                  absorb=lambda c, o: None, note="온도 유지")]
    p = make_plan({"유지"}, tasks)
    scen = {"beats": [{"at": "00:00", "user": "설정을 바꾸지 않는다",
                       "system": "목표 온도를 벗어나면 스스로 되돌린다",
                       "removes": "밤새 온도를 다시 맞추는 일",
                       "verified_by": "converge",
                       "expect_metric": "목표 이탈(분)"}]}

    def run_policy(_plan):
        return {"ok": True, "user_touches": 0,
                "metrics": {"목표 이탈(분)": 4},
                "log": [{"skill": t.skill, "ok": True} for t in _plan.steps]}

    r = REGISTRY.get("experience_verify").run(
        scenario=scen, plan=p, execute=run_policy, touch_baseline=3)
    ok = r.ok and r.output["beats_met"] == 1
    return ok, ("장면 " + str(r.output["beats_met"]) + "/"
                + str(r.output["beats_total"]) + " · 절감 "
                + str(r.output["touches_removed"]) + "회")


def main() -> int:
    import hashlib
    import os

    print("재사용 검사 — reusable_for 가 말뿐인지 실제인지")
    print("  규칙: 스킬 파일을 한 줄도 고치지 않고 주입만 바꿔 돌아가야 한다.")
    print()

    # 스킬 파일이 이 검사 도중 바뀌지 않았음을 해시로 못 박는다.
    before = {f: hashlib.md5(open(f, "rb").read()).hexdigest()
              for f in sorted(os.listdir("skills")) if f.endswith(".py")
              for f in [os.path.join("skills", f)]}

    bad = 0
    for skill, domain, fn in CASES:
        declared = REGISTRY.get(skill).reusable_for
        if not any(domain.split("(")[0] in d for d in declared):
            print(f"  !!  {skill:14s} '{domain}' 이 reusable_for 에 없다")
            bad += 1
            continue
        try:
            ok, note = fn()
        except Exception as e:
            print(f"  !!  {skill:14s} {domain} — 예외 {type(e).__name__}: {e}")
            bad += 1
            continue
        print(f"  {'OK ' if ok else '!! '} {skill:14s} {domain:22s} {note}")
        if not ok:
            bad += 1

    after = {f: hashlib.md5(open(f, "rb").read()).hexdigest() for f in before}
    if before != after:
        print("  !!  검사 도중 스킬 파일이 바뀌었다 — 이 결과는 무효다")
        bad += 1

    # 본래 도메인(주방·세탁)은 파이프라인·데모가 매 실행 확인한다.
    # 그것까지 "미확인" 으로 세면 과하게 비관적이다.
    COVERED = {
        ("situation_read", "주방"), ("scenario_draft", "주방 경험"),
        ("flow_design", "가전 작업 계획"),
        ("experience_verify", "UX 시나리오 검증"),
        ("recipe_source", "레시피 DB"), ("inventory", "냉장고"),
        ("menu", "조리 기록"), ("procure", "식재료 조달"),
        ("prep", "조리 전 계량"), ("converge", "조리기"),
        ("converge", "건조기"), ("aftercare", "식기세척기"),
        ("aftercare", "세탁기"),
    }

    # 선언했는데 한 번도 시험하지 않은 도메인을 드러낸다(숨기지 않는다).
    tested = {(s, d) for s, d, _ in CASES}
    untested = []
    for sp in REGISTRY.list():
        for d in sp["reusable_for"]:
            head = d.split("(")[0]
            if any(s == sp["name"] and head in dd for s, dd in tested):
                continue
            if any(s == sp["name"] and head.startswith(c) for s, c in COVERED):
                continue
            untested.append(f"{sp['name']}·{d}")
    print()
    print("설계 층에 남은 도메인 어휘 (0 이면 그 스킬은 도메인을 모른다):")
    for name, hits in _design_domain_words().items():
        mark = "OK " if not hits else "·  "
        detail = (" · ".join(f"{w} {n}" for w, n in hits.items())
                  if hits else "0건")
        print(f"  {mark} {name:18s} {detail}")

    print()
    print(f"판정: {len(CASES) - bad}/{len(CASES)} 도메인이 코드 수정 없이 돌았다")
    print(f"본래 도메인 {len(COVERED)}건은 파이프라인·데모가 매 실행 확인한다.")
    if untested:
        print(f"아직 실행으로 확인하지 않은 선언 {len(untested)}건 "
              f"(주장에 쓰려면 먼저 돌려야 한다):")
        for u in untested:
            print(f"   · {u}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
