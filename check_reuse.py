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
