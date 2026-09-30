# -*- coding: utf-8 -*-
"""주방 도메인 바인딩.

스킬은 도메인을 모른다. 여기서 "이 주방에서 그 스킬을 어떻게 쓰는지" 를 선언한다.
  · 전제조건과 효과 (플래너가 순서를 계산하는 근거)
  · 컨텍스트에서 스킬 인자를 만드는 법
  · 스킬 출력을 컨텍스트에 반영하는 법

이 파일만 바꾸면 같은 스킬 묶음을 세탁실·욕실에 그대로 옮길 수 있다.
"""
from __future__ import annotations
import json
import pathlib

import kitchen as K
from planner import Task, plan as _make_plan
from recipe_parse import contains_any, expand_avoid
import store

RECIPE_CACHE = pathlib.Path(__file__).parent / "data" / "recipes.json"

# 공개 레시피에는 "얼마나 졸일지"와 "얼마나 눌어붙을지"가 없다.
# 개인 기록이 없는 첫 조리에서 쓸 보수적 기본값이다. 실측이 아니라 **가정**이며,
# 한 번 조리하고 나면 그 세션의 측정값으로 대체된다.
METHOD_DEFAULT = {          # 조리법: (목표 질량비, 예상 오염도)
    "끓이기": (0.85, 0.55), "굽기": (0.92, 0.70), "찌기": (0.97, 0.30),
    "볶기": (0.90, 0.60), "튀기기": (0.95, 0.75), "기타": (0.95, 0.40),
}


# 같은 재료를 부르는 다른 이름들. 냉장고 품목명 → 자료에서 쓰이는 표기.
# 공개 자료를 쓰면 반드시 생기는 문제라 도메인 지식으로 따로 둔다.
# 1g 이 빨아들이는 물의 양. 레시피에는 "물 적당히" 라고만 적혀 있고
# 얼마나 필요한지는 적혀 있지 않다 — 에이전트가 계산해서 채운다.
# 값은 일반적인 조리 자료 범위의 가정이며 실측이 아니다.
ABSORBS = {"찹쌀": 2.0, "쌀": 2.2, "국수": 1.6, "당면": 2.5, "미역": 7.0,
           "콩": 1.8, "표고버섯": 0.8, "떡": 0.3}
# 삶으면 거품(단백질·기름)이 뜬다. 1g 당 걷어낼 양.
SCUM = {"닭고기": 0.045, "돼지고기": 0.06, "소고기": 0.055}


# ── 재료 이름 → 물리 계수 표의 종류 ──────────────────────────────────
# 계수 표(흡수·고형분·익힘·거품)를 **이름 정확 일치**로 찾고 있었다.
# 파싱을 바로잡아 이름이 '닭가슴살·닭다리살·삼겹살·소면·건미역' 이 되자
# 표에 없어 계수가 0 이 됐다 — **익힘 요구량 0 이면 생고기가 익지 않아도
# 조리가 끝난다**(끓이기 45건 중 7건). 이름을 종류로 풀어 찾는다.
# 순서가 중요하다(앞에서 먼저 걸린다). None 은 "계수 없음" 이다.
KIND_RULES = (
    # 이름에 들어 있지만 그 종류가 아닌 것을 먼저 막는다
    ("떡갈비", "돼지고기"),     # 떡이 아니라 다진 고기다(소·돼지 — 안전측 돼지)
    ("쌀뜨물", None), ("쌀겨", None), ("찹쌀가루", None),
    ("콩나물", None), ("콩가루", None), ("콩비지", None), ("콩고기", None),
    ("땅콩", None),
    ("단호박", None),
    # 곡물·면·떡 — 물을 먹는다
    ("당면", "당면"), ("찹쌀", "찹쌀"), ("쌀국수", "국수"), ("현미", "쌀"),
    ("쌀", "쌀"),
    ("국수", "국수"), ("소면", "국수"), ("메밀면", "국수"), ("라면", "국수"),
    ("우동", "국수"), ("스파게티", "국수"), ("파스타", "국수"), ("펜네", "국수"),
    ("떡", "떡"),
    # 마른 것만 물을 먹는다 — 불린 미역·표고는 이미 먹었다
    ("건미역", "미역"), ("미역", None),
    ("건표고", "표고버섯"), ("표고", "표고버섯"),
    ("검은콩", "콩"), ("강낭콩", "콩"), ("백태", "콩"), ("대두", "콩"), ("콩", "콩"),
    # 고기 — 익혀야 한다
    ("닭", "닭고기"), ("오리", "닭고기"),          # 가금류(오리 계수는 가정)
    ("돼지", "돼지고기"), ("삼겹", "돼지고기"), ("목살", "돼지고기"),
    ("다짐육", "돼지고기"), ("베이컨", "돼지고기"),
    ("소고기", "소고기"), ("쇠고기", "소고기"), ("한우", "소고기"),
    ("우둔", "소고기"), ("양지", "소고기"), ("등심", "소고기"), ("사태", "소고기"),
    # 채소·두부
    ("두부", "두부"), ("배추", "배추"), ("애호박", "애호박"), ("감자", "감자"),
)


def kind_of(name: str):
    """재료 이름을 계수 표의 종류로. 한 글자 '무' 는 정확히 같을 때만."""
    if name in ("무", "무 "):
        return "무"
    for key, kind in KIND_RULES:
        if key in name:
            return kind
    return name        # 표에 그대로 있으면 그 이름으로 찾힌다


def _coef(table, name, default=0.0):
    k = kind_of(name)
    return table.get(k, default) if k is not None else default


def scum_amount(ingredients) -> float:
    return sum(_coef(SCUM, i["name"]) * i.get("qty_g", 0) for i in ingredients)


# 고형분으로 남는 비율 (나머지는 국물에 섞인다)
SOLID_RATIO = {"닭고기": 0.85, "돼지고기": 0.85, "소고기": 0.85, "두부": 0.75,
               "배추": 0.35, "애호박": 0.30, "무": 0.30, "감자": 0.70,
               "찹쌀": 1.0, "쌀": 1.0, "국수": 1.0, "당면": 1.0,
               "미역": 1.0, "콩": 1.0, "표고버섯": 0.6, "떡": 1.0}


def absorb_capacity(ingredients) -> float:
    """이 재료들이 빨아들일 물의 총량."""
    return sum(_coef(ABSORBS, i["name"]) * i.get("qty_g", 0)
               for i in ingredients)


def solid_mass(ingredients) -> float:
    """국물이 되지 않고 고형으로 남는 무게."""
    return sum(_coef(SOLID_RATIO, i["name"], 0.5) * i.get("qty_g", 0)
               for i in ingredients)


# 조리 중 동작을 **누가 하는가.** 제안서 표1 은 "재료 손질·계량·투입,
# 필요 시 교반" 을 사람 몫으로, "가열 제어 또는 조절 안내" 를 시스템 몫으로
# 적었다. 그런데 코드는 투입·뚜껑·거품까지 스스로 하면서 "개입 0회" 라고
# 보고하고 있었다 — 로봇이 아닌데 로봇처럼 굴었다.
#
# 가전이 실제로 할 수 있는 것은 **화력 조절과 판단·기록** 뿐이다.
# 나머지는 사람의 손이 필요하고, 에이전트의 값은 그것을 **대신 하는 것이
# 아니라 언제 해야 하는지 정확히 알려주는 것**이다. 그래서 냄비 앞을
# 지키지 않아도 된다.
WHO = {"화력": "가전", "종료": "가전", "관측": "가전",
       "투입": "사람", "뚜껑": "사람", "거품": "사람",
       "교반": "사람", "물": "사람"}
HANDS_ON_MIN = 1.0          # 손이 가는 동작 하나에 드는 시간(분)

# 익는 데 필요한 양 (온도-60) x 분. 두꺼운 고기일수록 크다.
# 식약처 권장 중심온도 75도를 기준으로 잡은 가정이며 실측이 아니다.
COOK_UNITS = {"닭고기": 1.05, "돼지고기": 1.2, "소고기": 0.9, "감자": 0.8,
              "찹쌀": 1.1, "쌀": 1.1, "콩": 1.4}


def cook_units_needed(ingredients) -> float:
    """가장 오래 걸리는 재료가 다 익어야 끝난다."""
    return max((_coef(COOK_UNITS, i["name"]) * i.get("qty_g", 0)
                for i in ingredients), default=0.0)

# ── 국물 요리는 국물이 남아야 한다 ────────────────────────────────────
# 공개 레시피에는 물이 적혀 있지 않다(된장국 1인분 재료 합 75g). 물을
# "빨아들일 만큼 + 목표까지 졸일 만큼" 만 부으면 **목표에 닿는 순간 국물이
# 0 이다** — 4인분 삼계탕이 국물 80g · 눌어붙음 0.90 으로 끝났다.
#
# 끝났을 때 남길 양은 **식품 등의 표시기준 [별표 3] 1회 섭취참고량**에서
# 가져온다(즉석조리식품: 국·탕 250g, 찌개 200g, 죽 250g, 스프 150g).
# 이것은 건더기를 포함한 한 그릇의 양이므로, 1인분 국물 = 참고량 − 건더기.
# 건더기가 참고량보다 많은 탕(삼계탕 1인분 건더기+흡수 약 410g)은 그 식이
# 0 이 되므로 **참고량의 절반을 최소 국물로 둔다 — 이 절반은 가정이다.**
SERVING_G = {"국탕": 250, "찌개": 200, "죽": 250, "스프": 150}
MIN_BROTH_SHARE = 0.5


def soup_kind(rec: dict):
    """국물 요리면 그 종류, 아니면 None. 내 기록은 물이 기록에 들어 있다."""
    import re
    if not str(rec.get("record_id", "")).startswith("pub_"):
        return None
    name, cat = rec.get("menu", ""), rec.get("category")
    if cat in ("국&찌개", "일품") or "죽" in name:
        if "찌개" in name:
            return "찌개"
        if "죽" in name:
            return "죽"
        if re.search(r"(스프|수프|차우더)", name) and "파스타" not in name:
            return "스프"
        if re.search(r"(탕|국)(?!수)", name):
            return "국탕"
    if cat == "국&찌개":
        return "국탕"
    return None


def broth_of(rec: dict, placed: list) -> float:
    """끝났을 때 남길 국물(g). 국물 요리가 아니면 0."""
    kind = soup_kind(rec)
    if not kind:
        return 0.0
    n = max(1.0, float(rec.get("servings") or 1))
    per = SERVING_G[kind]
    body = (solid_mass(placed) + absorb_capacity(placed)) / n
    return round(max(per - body, per * MIN_BROTH_SHARE) * n, 1)


# 조리 외 단계의 고정 소요 시간(분). 설계 층의 STAGE_COSTS 와 같은 값이며,
# 실행 층은 조리 시간만 실측하고 나머지는 이 표를 쓴다.
FIXED_MIN = {"보관 확인": 2, "메뉴 결정": 3, "준비": 4, "세척 시작": 2}

ALIAS = {
    "두부": ("두부", "연두부", "순두부", "부침두부", "손두부"),
    "닭고기": ("닭고기", "닭가슴살", "닭안심", "닭다리", "훈제닭"),
    "돼지고기": ("돼지고기", "삼겹살", "목살", "다짐육"),
    "대파": ("대파", "쪽파", "실파"),
    "배추": ("배추", "배춧잎", "알배추", "절임배추"),
    "간장": ("간장", "저염간장", "진간장", "국간장", "양조간장"),
    "된장": ("된장", "저염된장", "재래된장"),
    "애호박": ("애호박", "호박"),
    "표고버섯": ("표고버섯", "건표고", "표고"),
}


# 늘 있다고 보는 상비품. 이것까지 '부족'으로 세면 조미료가 많은 레시피가
# 부당하게 밀린다. 실제 제품에서는 사용자가 등록하거나 소모량으로 추정한다.
PANTRY = {"소금", "후춧가루", "설탕", "식용유", "참기름", "물", "밀가루",
          "녹말가루", "식초", "고춧가루", "마늘", "생강"}


PANTRY_MIN_G = 10        # 이보다 적게 남았으면 '없는 것' 으로 본다


def pantry_stock(low: dict | None = None) -> list:
    """상비품을 재고 항목으로 만든다.

    예전에는 상비품이 무한히 있다고 가정했다. 그런데 참기름도 떨어진다 —
    조리 중에 알면 늦는다. 잔량이 기준 미만이면 재고에서 빼서
    '부족분' 으로 흘러가게 한다.
    """
    low = low or {}
    return [{"name": n, "qty_g": low.get(n, 500), "stored_days": 30,
             "shelf_life_days": 720}
            for n in sorted(PANTRY) if low.get(n, 500) >= PANTRY_MIN_G]


def pantry_refill(low: dict | None = None) -> list:
    """잔량이 바닥난 상비품 목록. 레시피와 무관하게 같이 주문한다."""
    low = low or {}
    return [n for n in sorted(PANTRY) if low.get(n, 500) < PANTRY_MIN_G]


def resolve_stock(ing_name: str, have: dict):
    """자료의 재료명이 재고의 어떤 품목인지 찾는다. 없으면 None."""
    if ing_name in have:
        return ing_name
    for stock_name in have:
        for alias in ALIAS.get(stock_name, (stock_name,)):
            if alias in ing_name or ing_name in alias:
                return stock_name
    for p in PANTRY:
        if p in ing_name:
            return p          # 상비품은 보유로 본다 (임박 목록에는 없으므로 점수에 기여하지 않는다)
    return None


def load_recipes() -> dict:
    """캐시를 읽는다. 없으면 빈 묶음을 준다 (수집은 fetch_data.py 가 한다)."""
    if not RECIPE_CACHE.exists():
        return {"source": "(캐시 없음 — python fetch_data.py 를 먼저 실행)",
                "key_used": "-", "recipes": []}
    d = json.loads(RECIPE_CACHE.read_text(encoding="utf-8"))
    # **재료는 원문(parts_raw)에서 매번 다시 푼다.** 캐시의 ingredients 는
    # 수집할 때의 파서로 풀어 둔 것이라, 그 뒤 고친 파싱(간장→장·생강→강
    # 방지, 소제목 떼기, 표기 통일)이 파이프라인에 **한 번도 닿지 않았다.**
    # 파싱 함수 시험은 통과했는데 파이프라인은 옛 결과를 읽고 있었다.
    from recipe_parse import parse_ingredients
    for r in d.get("recipes", []):
        if r.get("parts_raw"):
            r["ingredients"] = parse_ingredients(r["parts_raw"])
    return d


def recipe_to_record(r: dict) -> dict:
    """공개 레시피를 조리 기록과 같은 형태로 맞춘다.

    개인 기록(satisfaction 1~5)과 달리 satisfaction 을 0 으로 둔다.
    그래서 같은 조건이면 **내 기록이 공개 레시피보다 먼저 선택된다.**
    """
    ratio, soil = METHOD_DEFAULT.get(r.get("method"), METHOD_DEFAULT["기타"])
    total = sum(i["qty_g"] for i in r["ingredients"])
    return {"record_id": f"pub_{r['recipe_id']}", "menu": r["menu"],
            "method": r.get("method"), "category": r.get("category"),
            "saved_by": "공개 레시피", "ingredients": r["ingredients"],
            "initial_mass_g": round(total), "target_mass_ratio": ratio,
            "cook_minutes_observed": None, "soil_score": soil,
            "satisfaction": 0, "estimated": True,
            "sodium_mg": r.get("sodium_mg"), "kcal": r.get("kcal")}

# 취급 품목과 구매 이력 — 실제로는 제휴 장보기 서비스에서 온다
# 취급 품목과 가격은 store.py 의 상점 어댑터가 쥔다.
# 조달 스킬은 가격표가 아니라 '조회 함수' 를 받는다 — 상점이 몇 곳인지,
# 시뮬레이터인지 실제 API 인지 알지 못한다.
CATALOG = store.BASE_PRICE          # 재고 반영·수량 산정에만 쓴다
# 이전에 산 적 있는 품목. 양념·상비품은 반복 구매하므로 이력이 쌓여 있다.
# 찹쌀·미나리처럼 처음 사는 것은 여기 없어서 사용자 확인을 거친다.
KNOWN_ITEMS = ["두부", "대파", "간장", "된장", "닭고기", "양파", "당근",
               "감자", "달걀", "배추", "애호박",
               "참기름", "식용유", "설탕", "소금", "고춧가루", "식초", "밀가루"]
AUTO_LIMIT_KRW = 15000                                # 1회 자동 주문 상한

# 이 조리기가 하는 조리법. 시뮬레이터(converge)는 **냄비에서 끓여 졸이는**
# 물리만 다룬다 — 굽기·튀기기·찌기·무조리(샐러드·주스)는 온도·열전달이 달라
# 이 모델로 조리하면 거짓이 된다. 내 기록은 이 기기로 만든 것이라 맞는다고 본다.
DEVICE_METHODS = {"끓이기"}


def fits_device(rec: dict):
    m = rec.get("method")
    if m is None or m in DEVICE_METHODS:
        return None
    return f"이 조리기로 하지 않는 조리법({m}) — 냄비에서 끓이는 것만 다룬다"


MUST_USE_DAYS = 1       # 남은 날이 이 이하면 오늘 안 쓰면 버린다고 본다


# 주방에서 쓰는 말. situation_read 의 근거 문장이 이 말을 쓴다.
# 설계 층은 "조리량"·"세척" 같은 단어를 모른다.
KITCHEN_TERMS = {"amount": "조리량", "finish": "세척",
                 "finish_course": "세척 코스",
                 "short_time": "시간이 모자란 상태에서 메뉴를 정하는 일",
                 "avoid_check": "재료마다 못 먹는 것이 섞였는지 확인하는 일",
                 # 고객이 같은 불편을 자기 말로 했는지 알아보는 단서
                 "short_time_cues": ("시간이 모자", "시간이 없", "뭘 먹을지"),
                 "avoid_check_cues": ("알레르기", "못 먹", "먹으면 안 되")}


def kitchen_beats(persona: dict, constraints: dict, plus) -> list:
    """주방 도메인의 장면 목록.

    `scenario_draft` 가 이 함수를 주입받는다. 설계 층 스킬은 장면 문장도,
    그 장면을 검증할 스킬 이름(verified_by)도 모른다 — 전부 여기에 있다.

    **퇴근 시각을 알면 현관에 들어서기 전에 끝낸다.** 전에는 첫 장면이
    늘 "현관에 들어선다 → 재고를 읽는다" 였다. 그러면 그때 가서 메뉴를
    정하고 주문하므로 배송을 집에서 기다린다. 퇴근길에 메시지로 메뉴를
    알리고, 처음 사는 것처럼 판단이 안 되는 것만 물어 승인받고, 주문한
    것이 이동 중에 도착하면 현관에 들어설 때 이미 재료가 있다(씽큐 클로의
    "제안 → 사람 승인 → 실행" 이 이 자리다). 퇴근 시각을 모르면 전처럼
    귀가 뒤에 한다.

    장면 시각은 **설계 시점의 추정**이다. 조리·세척 장면은 실행 뒤에
    retime_beats 가 실제 시각으로 다시 맞춘다.

    plus: (시각, 분) -> 시각   시각 계산은 스킬이 준다
    """
    t0 = persona.get("arrive_home", "19:00")
    pre = bool(constraints.get("preorder"))
    lv = persona.get("leave_office", t0) if pre else t0
    beats = []

    # verified_by: 이 장면이 실제로 일어났는지 확인할 실행 스킬.
    if pre:
        beats.append({
            "at": lv, "user": "퇴근길에 메시지를 받는다",
            "system": "냉장고 재고·남은 시간·먹을 사람을 읽고 오늘 메뉴를 정해 알린다",
            # 메뉴를 집 밖에서 정하므로, 집에 와서 시간에 쫓기며 정할 일이 없다
            "removes": "냉장고를 열어 뭐가 남았는지 확인하는 일 / "
                       + KITCHEN_TERMS["short_time"],
            "verified_by": "inventory", "expect_metric": "메뉴"})
    else:
        beats.append({
            "at": t0, "user": "현관에 들어선다",
            "system": "재고와 남은 시간을 읽고 오늘 할 수 있는 것을 정한다",
            "removes": "냉장고를 열어 뭐가 남았는지 확인하는 일",
            "verified_by": "inventory", "expect_metric": "메뉴"})

    if constraints.get("avoid"):
        beats.append({
            "at": plus(lv, 1),
            "user": "메시지로 확인만 한다" if pre else "아무것도 확인하지 않는다",
            "system": f"못 먹는 재료({', '.join(constraints['avoid'])})가 들어가는 "
                      f"후보를 미리 제외한다",
            "removes": "재료마다 못 먹는 것이 섞였는지 확인하는 일",
            "verified_by": "menu", "expect_metric": "메뉴"})

    if pre:
        beats.append({
            "at": plus(lv, 2), "user": "메시지로 승인만 한다",
            # 설계 시점에는 자동 주문이 될지 확인이 필요할지 모른다 —
            # 판단 기준을 말하고 결과는 열어 둔다.
            "system": "냉장고와 양념 선반을 함께 보고 부족한 것 중 되는 것은 주문하고, "
                      "처음 사는 것처럼 판단이 안 되는 것만 물어 승인받는다. "
                      "못 먹는 재료가 든 상품은 사지 않는다. 귀가 시각에 맞춰 도착한다",
            "removes": "퇴근길에 장을 보러 들르는 일 / 누가 장을 볼지 매번 정하는 일 / "
                       "장을 볼 때 성분을 읽는 일 / 양념이 떨어진 걸 조리 중에 발견하는 일",
            "verified_by": "procure",
            "expect_any": ["조달 대기", "확인 요청"]})
        beats.append({
            "at": t0, "user": "현관에 들어서면 재료가 이미 와 있다",
            "system": "먹을 사람 수에 맞춰 넣을 양을 정하고 계량을 안내한다",
            "removes": "집에 와서 뭐가 없는지 그제야 아는 일",
            "verified_by": "prep", "expect_metric": "조리량"})
    elif constraints.get("skip_procurement"):
        beats.append({
            "at": plus(t0, 2), "user": "장을 보지 않는다",
            "system": "시간이 모자라므로 지금 있는 재료만으로 가능한 것을 고른다",
            "removes": "시간이 모자란 상태에서 메뉴를 정하는 일",
            "verified_by": "menu", "expect_metric": "메뉴"})
    else:
        beats.append({
            "at": plus(t0, 2), "user": "주문을 누르지 않는다",
            "system": "부족분을 이력·상한·안전 기준으로 판단해 "
                      "되는 것은 주문하고, 안 되는 것만 묻는다",
            "removes": "누가 장을 볼지 매번 정하는 일 / 장을 볼 때 성분을 읽는 일",
            "verified_by": "procure",
            "expect_any": ["조달 대기", "확인 요청"]})

    beats.append({
        # 재료를 넣고 뚜껑을 여닫고 젓는 것은 여전히 사람이 한다(제안서 표1).
        # 달라지는 것은 언제 해야 하는지 몰라 계속 지켜보던 일이 없어진다는 점이다.
        "at": plus(t0, 5), "user": "부를 때만 손을 댄다",
        "system": ("화력은 스스로 맞추고 목표 상태에 닿으면 멈춘다. "
                   "손이 필요한 때(재료 투입·뚜껑·젓기)만 알려 준다"),
        "removes": "조리 중 냄비 앞을 떠나지 못하는 일",
        "verified_by": "converge", "expect_metric": "가열 시간(분)", "timed": "cook"})

    if constraints.get("finish_cleanup"):
        quiet = constraints.get("quiet_after")
        beats.append({
            "at": plus(t0, 45), "user": "다 먹고 나서 코스를 고르지 않는다",
            "system": "조리기가 잰 눌어붙음 정도로 코스를 정하고"
                      + (f", {quiet} 이후까지 돌면 저소음으로 바꾼다"
                         if quiet else " 바로 시작한다"),
            "removes": "먹고 나서 설거지를 미루는 일 / 세척기를 언제 돌릴지 정하는 일",
            "verified_by": "aftercare", "expect_metric": "세척 코스", "timed": "wash"})

    return beats


# 식사에 드는 시간(분). 세척은 **다 먹은 뒤**에 시작한다. 전에는 귀가 +45분
# 고정이라, 식사까지 48분 걸린 가구에서 **밥이 되기도 전에** 세척기가 돌았다.
# 20분은 가정이다(실측·통계를 확인하지 못했다).
EAT_MIN = 20


def _hhmm_plus(hhmm: str, minutes: float) -> str:
    h, m = map(int, hhmm.split(":"))
    t = int(round(h * 60 + m + minutes))
    return f"{(t // 60) % 24:02d}:{t % 60:02d}"


def retime_beats(scenario: dict, verify: dict, constraints: dict) -> list:
    """조리·세척 장면을 **실제 실행 시각**으로 다시 맞춘다. 바꾼 것을 돌려준다.

    설계 시점에는 배송을 기다릴지·몇 분 끓일지 모른다. 실행 뒤에는 안다.
    """
    t0 = constraints.get("arrive_home")
    m = verify.get("metrics", {})
    if not t0 or m.get("식사까지(분)") is None:
        return []
    spent = m["식사까지(분)"]
    cook_start = spent - (m.get("가열 시간(분)") or 0)
    real = {"cook": _hhmm_plus(t0, cook_start),
            "wash": _hhmm_plus(t0, spent + EAT_MIN)}
    changed = []
    for b in scenario.get("beats", []):
        k = b.get("timed")
        if k in real and b["at"] != real[k]:
            changed.append(f"{b['verified_by']}: {b['at']} → {real[k]}")
            b["at_planned"], b["at"] = b["at"], real[k]
    for c in verify.get("beat_check", []):
        for b in scenario.get("beats", []):
            if b.get("at_planned") == c["at"] and b["removes"] == c["removes"]:
                c["at"] = b["at"]
    return changed


def outcome_notes(verify: dict) -> list:
    """실행 중에 **에이전트가 스스로 한 판단**을 장면으로 남긴다.

    재계획·버림 알림은 시나리오를 그릴 때는 모르고 실행해 봐야 안다.
    결과 지표에만 있으면 시나리오를 읽는 사람은 그 일이 있었는지 모른다.
    """
    m = verify.get("metrics", {})
    out = []
    if m.get("재계획"):
        out.append({"user": "다시 고르지 않는다",
                    "system": f"다시 계획했다 — {m['재계획']}"
                              + (f" ({m['고른 안']})" if m.get("고른 안") else "")})
    if m.get("오늘 못 쓰는 임박 재료"):
        out.append({"user": "버릴 재료를 뒤늦게 발견하지 않는다",
                    "system": f"알린다 — {m['오늘 못 쓰는 임박 재료']}"})
    if m.get("확인 요청"):
        out.append({"user": "물어본 것에만 답한다",
                    "system": "승인받은 뒤 주문했다 — " + "; ".join(m["확인 요청"])})
    return out


# 기능 목록 — LLM 이 장면을 제안할 때 **이 안에서만** 고른다.
# can: LLM 에게 보여 줄 설명. 실제로 그 스킬이 하는 일만 적는다.
# metrics: 그 장면이 일어났는지 확인할 실행 지표. **LLM 이 아니라 여기서** 정한다.
KITCHEN_CAPS = {
    "inventory": ("냉장고 재고와 보관일을 읽어 먼저 써야 할 재료를 고른다",
                  ["가장 급한 재료"]),
    "menu": ("저장된 조리 기록과 공개 레시피 중 재고·못 먹는 재료·시간에 맞는 "
             "메뉴를 고른다. 못 먹는 재료가 든 후보는 미리 뺀다", ["메뉴"]),
    # 떨어진 상비품(양념)도 조달 대상이다(_procure_bind 의 pantry_refill).
    # 처음엔 이 설명에서 빠져, 진짜 LLM 이 p4 의 "양념이 떨어진 걸 조리 중에
    # 발견하는 일" 을 "양념은 추적 대상이 아니다" 며 못 덮는다고 했다.
    "procure": ("냉장고 부족분과 **떨어진 양념·상비품**을 함께 모아, 이전 구매 "
                "이력·금액 상한·못 먹는 재료 기준으로 판단해 주문하거나, 판단이 "
                "안 되는 것만 고객에게 묻는다",
                ["조달 대기", "확인 요청"]),
    "prep": ("먹는 사람 수와 조리기 용량에 맞춰 넣을 양을 정하고 계량을 안내한다",
             ["조리량"]),
    "converge": ("화력을 스스로 조절하고 목표 상태에 닿으면 불을 끈다. 재료 투입·"
                 "뚜껑·젓기처럼 손이 필요한 때만 알린다", ["가열 시간(분)"]),
    "aftercare": ("조리 중에 잰 눌어붙음 정도로 식기세척기 코스를 정하고, "
                  "소음을 싫어하는 시각에 걸리면 저소음으로 바꾼다", ["세척 코스"]),
}


# 사람이 집에 있어야 일어나는 단계. 선제 주문이어도 귀가 전에는 못 한다 —
# 계량은 사람이 담고, 재료 투입·뚜껑은 사람이 하고, 세척기에는 사람이 넣는다.
NEEDS_PERSON = {"prep", "converge", "aftercare"}

# 가전이 **할 수 없는** 손일. 장면이 이것을 가전이 한다고 말하면 버린다.
# (제안서 표1: 재료 손질·투입·뚜껑·젓기·식기 넣기는 사람이 한다)
# 문구로 거르는 것이라 완전하지 않다 — 빠져나간 약속은 실행 검증이 못 잡는다.
HUMAN_ONLY = (
    (r"(스스로|자동으로|알아서|대신)\s*[^.,]{0,12}?(넣|젓|저어|썰|손질|다듬|담아|옮겨)",
     "재료 투입·젓기·썰기·담기는 사람이 한다"),
    (r"(넣어|저어|썰어|손질해|다듬어|담아|옮겨)\s*(준다|둔다|놓는다|드린다)",
     "재료 투입·젓기·썰기·담기는 사람이 한다"),
    (r"뚜껑을\s*(스스로|자동으로|알아서)?\s*(연다|닫는다|덮는다|열고|닫고|덮고)",
     "뚜껑은 사람이 여닫는다 — 가전은 알려 줄 뿐이다"),
    (r"(식기|그릇|냄비)[를을]?\s*[^.,]{0,6}?(넣는다|넣고|정리한다)",
     "세척기에 식기를 넣는 것은 사람이다"),
)

# 조건 없이 말한 약속은 **결과에 그 일이 있었는지** 본다. 조건을 단
# 문장("걸리면 바꾼다")은 약속이 아니라 판단 기준이라 여기서 보지 않는다.
# (약속 문구, 확인할 지표, 지표 값이 맞아야 할 모양, 이름)
CLAIMS = (
    (r"저소음", "소음 조치", r"저소음으로 전환", "저소음으로 바꾼다"),
    (r"주문(한다|해 둔다|해 놓|해 두|을 넣)", "자동 주문(원)", r"\d", "주문한다"),
    (r"여열", "여열 마무리", r"여열", "여열로 마무리한다"),
)
CONDITIONAL = r"(면|경우|때만|되는 것|필요하면|있으면|걸리면|넘기면|모자라면)"


# ── 재계획: 무엇이 막았고, 그중 무엇을 풀 수 있는가 ──────────────────
#
# 설계 층이 되돌아갈 때 **무엇을 풀지는 도메인이 안다.** 여기서는 막힌
# 결과를 보고 원인을 목록으로 낸다. 원인마다 풀 수 있는지(relaxable)와
# 풀면 제약이 어떻게 바뀌는지(apply)를 함께 준다.
#
# 풀 수 있는 것 — **우리가 추정·선택으로 정한 것**
#   · 조달 생략   추정(단계별 예상 분의 합)으로 뺐다. 실제로는 될 수 있다
#   · 고른 메뉴   뒤 단계에서 막혔거나 예산을 넘겼다. 다른 메뉴가 있을 수 있다
# 절대 풀지 않는 것 — **고객의 사실·안전**
#   · 알레르기·기피, 나트륨 상한, 시간 예산, 기기가 못 하는 조리법
#   이것들을 풀어 성립시키면 시나리오가 성립한 것이 아니라 고객을 바꾼 것이다.
NEVER_RELAX = ("avoid", "max_sodium_mg", "time_budget_min", "device_method")


def _cause(key, relaxable, why, apply=None):
    return {"key": key, "relaxable": relaxable, "why": why, "apply": apply}


def _exclude(rid):
    return lambda c: dict(c, exclude_records=list(c.get("exclude_records") or [])
                          + [rid])


def diagnose(constraints: dict, verify: dict) -> list:
    """막힌 원인을 풀 수 있는 것과 없는 것으로 나눠 돌려준다."""
    m = verify.get("metrics", {})
    halted = verify.get("halted_at")
    rid, menu = m.get("사용한 기록"), m.get("메뉴")
    out = []
    if halted == "menu":
        if constraints.get("skip_procurement"):
            out.append(_cause(
                "skip_procurement", True,
                "추정으로 조달을 뺐는데 재고로 만들 메뉴가 없다 — 조달을 되살린다",
                lambda c: dict(c, skip_procurement=False)))
        out.append(_cause("no_menu", False,
                          m.get("메뉴 없음") or m.get("중단") or "메뉴를 정하지 못했다"))
        return out
    if halted and rid:
        out.append(_cause(f"exclude:{rid}", True,
                          f"{menu}({rid})로는 {halted} 에서 막혔다 — 다른 메뉴로 다시 짠다",
                          _exclude(rid)))
        return out
    if halted:
        return [_cause(f"halt:{halted}", False, m.get("중단") or f"{halted} 에서 멈췄다")]
    if verify.get("over_budget") and rid:
        out.append(_cause(
            f"exclude:{rid}", True,
            f"{menu}({rid})는 {verify.get('spent_min')}분으로 예산 "
            f"{verify.get('budget_min')}분을 넘는다 — 더 빨리 되는 메뉴를 찾는다",
            _exclude(rid)))
    return out


def rank_attempt(verify: dict):
    """시도들 가운데 무엇이 나은가. 성립 > 끝까지 감 > 예산 초과가 작음 > 개입이 적음."""
    spent, budget = verify.get("spent_min"), verify.get("budget_min")
    if spent is None:
        over = float("inf")
    else:
        over = max(0.0, spent - budget) if budget else 0.0
    return (bool(verify.get("verified")), verify.get("halted_at") is None,
            -over, -verify.get("user_touches", 0))


def kitchen_capabilities(constraints: dict) -> list:
    """**이번 상황의 계획에 실제로 들어가는** 기능만 돌려준다.

    조달을 빼는 상황에서 "부족분을 주문한다" 장면을 허용하면 실행되지
    않을 일을 약속하게 된다. 그래서 계획에서 거꾸로 뽑는다.

    **flow_design 스킬을 그대로 돌린다.** 처음엔 플래너를 여기서 직접
    불렀는데, flow_design 이 '조달 생략이면 재고가 갖춰진 것으로 본다'
    를 따로 더하고 있어서 p1 에 실제 계획에 없는 procure 가 들어왔다.
    같은 규칙을 두 곳에 적지 않는다.
    """
    from skills import REGISTRY
    flow = REGISTRY.get("flow_design").run(
        constraints=constraints, tasks=build_tasks(constraints),
        planner=_make_plan, goal=domain_goal(constraints)).output["flow"]
    # **계획 순서대로** 돌려준다. 장면 시각이 이 순서를 거스르면 안 된다.
    return [{"skill": k, "can": KITCHEN_CAPS[k][0], "metrics": KITCHEN_CAPS[k][1],
             "step": i, "needs_person": k in NEEDS_PERSON}
            for i, k in enumerate(flow["steps"]) if k in KITCHEN_CAPS]


def domain_goal(constraints: dict) -> set:
    """이 도메인에서 "끝났다" 는 무엇인가.

    설계 층 스킬(flow_design)은 'cooked' 라는 이름을 모른다. 도메인이
    알려 준다. 세탁실 도메인 파일을 쓰면 여기서 {'dried', 'folded'} 를
    돌려주면 되고, 설계 층은 한 줄도 바뀌지 않는다.
    """
    goal = {"cooked"}
    if constraints.get("finish_cleanup"):
        goal.add("cleaned")
    return goal


def build_tasks(constraints: dict) -> list:
    """상황에서 나온 제약을 반영해 이 도메인의 작업 목록을 만든다."""
    # 범주어('갑각류')는 여기서 구체 재료로 푼다. 세 스킬(recipe_source·
    # menu·procure)이 모두 이 목록을 받으므로 **한 곳에서** 푼다.
    avoid, _, _ = expand_avoid(constraints.get("avoid", []))
    require_complete = bool(constraints.get("skip_procurement"))

    def _recipe_bind(ctx):
        return {"load": load_recipes, "match": contains_any, "avoid": avoid,
                "max_sodium_mg": constraints.get("max_sodium_mg"),
                "methods": None}

    def _recipe_absorb(ctx, out):
        ctx["recipe_pool"] = out["recipe_pool"]
        ctx["recipe_source"] = out["source"]
        ctx["recipe_stats"] = {"적재": out["loaded"], "후보": out["kept"],
                               "알레르기제외": len(out["blocked_allergy"]),
                               "나트륨제외": len(out["blocked_sodium"])}

    def _inventory_bind(ctx):
        return {"items": K.fridge_list_items(), "urgency_ratio": 0.6}

    def _inventory_absorb(ctx, out):
        ctx["decided_before_home"] = bool(constraints.get("preorder"))
        ctx["urgent"] = [i["name"] for i in out["urgent"]]
        ctx["must_use"] = [i["name"] for i in out["urgent"]
                           if i["days_left"] <= MUST_USE_DAYS]
        ctx["days_left"] = min((i["days_left"] for i in out["urgent"]), default=99)
        # 수명이 지난 것은 재고에서 뺀다. 목록에서 빼기만 하고 재고에 남겨 두면
        # 메뉴 단계가 그대로 찾아 쓴다.
        ctx["expired"] = [i["name"] for i in out.get("expired", [])]
        if ctx["expired"]:
            ctx["expired_note"] = (
                "수명이 지나 쓰지 않는다: "
                + ", ".join(f"{i['name']}({i['days_over']}일 지남)"
                            for i in out["expired"]))

    def _menu_bind(ctx):
        # 내 기록이 먼저, 공개 레시피가 그다음. 같은 형태로 맞춰 함께 채점한다.
        records = list(K.RECORDS.values())
        records += [recipe_to_record(r) for r in ctx.get("recipe_pool", [])]
        # 재계획이 "이 메뉴로는 안 된다" 고 정한 기록은 뺀다(diagnose 참고)
        out_ = set(constraints.get("exclude_records") or [])
        records = [r for r in records if r["record_id"] not in out_]
        stock = [x for x in K.fridge_list_items()
                 if x["name"] not in set(ctx.get("expired", []))]
        return {"records": records,
                "stock": stock,
                "prefer_items": ctx.get("urgent", []),
                "require_complete": require_complete,
                "avoid": avoid,
                "resolve": resolve_stock,
                # 조달 단계와 **같은 기한**을 쓴다. 기한이 두 곳에서 다르면
                # 고를 때는 된다던 것이 살 때 안 된다.
                "arrival_min": lambda n: store.min_delivery_min(item=n),
                "deadline_min": constraints.get("budget_min"),
                "fits": fits_device,
                "must_use": ctx.get("must_use", [])}

    def _menu_absorb(ctx, out):
        ctx["must_use_unmet"] = out.get("must_use_unmet", [])
        best = out["best"]
        if best is None:
            ctx["record"] = None
            ctx["missing"] = []
            return
        rid = best["record_id"]
        if rid.startswith("pub_"):
            # 공개 레시피가 뽑혔다 — 개인 기록이 없는 메뉴다.
            src = next(r for r in ctx["recipe_pool"]
                       if f"pub_{r['recipe_id']}" == rid)
            ctx["record"] = recipe_to_record(src)
        else:
            ctx["record"] = K.record_get(rid)
        # 고른 기록을 **우리 집 인원**에 맞춘다. 기기 용량이 상한이 된다.
        # 이 한 단계가 없어서 4인 가구가 1인분(237g)을 조리하고 있었다.
        hh = constraints.get("household_size")
        if hh:
            scaled = K.record_scale(ctx["record"], hh, constraints.get("device"))
            ctx["scale_basis"] = scaled["scale_basis"]
            ctx["scale_capped"] = scaled.get("capped_by_device")
            ctx["record"] = scaled

        # **재료 이름을 재고 이름으로 바꿔 둔다.** 메뉴는 별칭으로 '닭가슴살'
        # 을 재고의 '닭고기' 로 찾는데(부분 일치), 계량은 정확한 이름으로
        # 찾는다. 바꾸지 않으면 메뉴는 "있다" 고 하고 계량은 "재고 없음" 으로
        # 실패했다 — 공개 레시피가 뽑히면 반드시 타는 경로였다.
        # 같은 재고로 모이는 재료(대파·쪽파 → 대파)는 양을 합친다.
        stock = {s["name"]: s for s in K.fridge_list_items()}
        merged = {}
        for ing in ctx["record"].get("ingredients", []):
            key = resolve_stock(ing["name"], stock) or ing["name"]
            if key in merged:
                merged[key]["qty_g"] += ing["qty_g"]
            else:
                merged[key] = dict(ing, name=key,
                                   **({"as_written": ing["name"]}
                                      if key != ing["name"] else {}))
        ctx["record"] = dict(ctx["record"], ingredients=list(merged.values()))

        # 인원에 맞춰 양이 바뀌었으니 부족분도 그 양으로 다시 본다.
        need = ctx["record"].get("ingredients", [])
        miss = []
        for ing in need:
            key = resolve_stock(ing["name"], stock)
            if key is None:
                miss.append(ing["name"])
            else:
                have = (stock.get(key) or {}).get("qty_g")
                if have is not None and have < ing["qty_g"]:
                    miss.append(ing["name"])
        ctx["missing"] = miss
        ctx["menu_name"] = best["menu"]
        ctx["menu_from"] = "공개 레시피" if rid.startswith("pub_") else "저장된 기록"

    def _procure_bind(ctx):
        # 레시피에 필요한 부족분 + 바닥난 상비품 보충
        need = list(ctx.get("missing", []))
        for n in ctx.get("pantry_refill", []):
            if n not in need:
                need.append(n)
        return {"missing": need,
                "lookup": store.make_lookup(),
                "known_items": KNOWN_ITEMS, "avoid": avoid,
                "auto_limit_krw": AUTO_LIMIT_KRW,
                "deadline_min": constraints.get("budget_min")}

    def _procure_absorb(ctx, out):
        def qty_for(name):
            return next((i["qty_g"] for i in ctx["record"]["ingredients"]
                         if i["name"] == name), 150)

        # 주문한 것은 **도착해야** 쓸 수 있다. 예전에는 주문 즉시 재고에
        # 넣어서, "20분 뒤 도착" 이라 해놓고 두부 없이 조리를 시작했다.
        # 시뮬레이터에서는 도착을 기다린 것으로 보고 그 시간을 기록한다 —
        # 실제 기기라면 도착 알림을 받고 시작해야 한다.
        eta = out.get("arrive_in_min", 0)
        for a in out["auto_ordered"]:
            K.fridge_add(a["name"], qty_for(a["name"]))
        ctx["order_krw"] = out["total_krw"]
        ctx["order_eta_min"] = eta
        if out["auto_ordered"]:
            # 집에 없는 동안 주문했으면(선제 주문) 그 시간이 이동 시간에
            # 묻히므로 기다림이 아니다.
            pre = constraints.get("preorder")
            ctx["wait_for_delivery_min"] = 0 if pre else eta
            ctx["delivery_note"] = (
                f"이동 {constraints.get('commute_min')}분 안에 도착 — 기다림 없음"
                if pre else
                f"도착까지 {eta}분 기다린 뒤 조리를 시작한다")
        ctx["order_stores"] = sorted({a.get("store") for a in out["auto_ordered"]
                                      if a.get("store")})
        ctx["need_confirm"] = out["need_confirm"]
        # 되돌릴 수 없는 행동을 막은 만큼 사용자가 직접 확인해야 한다
        ctx["touches"] = ctx.get("touches", 0) + len(out["need_confirm"])

        # 시연에서는 사용자가 그 확인에 동의했다고 보고 진행한다.
        # 실제 제품에서는 여기서 멈추고 응답을 기다린다. 동의를 가정한 것이지
        # 자동으로 주문한 것이 아니며, 개입 횟수에는 그대로 남는다.
        #
        # **승인해도 물건은 배송 시간이 지나야 온다.** 전에는 승인 즉시 재고에
        # 넣어서, 720분 뒤에 올 찹쌀로 오늘 삼계탕을 끓였다(2026-09-24 에 자동
        # 주문 쪽에서 고친 것과 같은 버그가 승인 경로에 남아 있었다).
        # 기한 안에 오는 것만 넣고, 기다린 시간을 더한다.
        approved, late = [], []
        deadline = constraints.get("budget_min")
        pre = constraints.get("preorder")
        for c in out["need_confirm"]:
            name = c["name"]
            if name not in CATALOG:
                continue
            a_eta = store.min_delivery_min(item=name)
            if a_eta is None or (deadline is not None and a_eta > deadline):
                late.append(f"{name}({'구할 곳 없음' if a_eta is None else f'{a_eta}분'})")
                continue
            K.fridge_add(name, qty_for(name))
            approved.append(name)
            if not pre:
                ctx["wait_for_delivery_min"] = max(
                    ctx.get("wait_for_delivery_min", 0), a_eta)
        ctx["approved_after_ask"] = approved
        ctx["late_after_ask"] = late
        if approved and pre and not out["auto_ordered"]:
            ctx["delivery_note"] = (f"퇴근길에 확인받아 주문 — 이동 "
                                    f"{constraints.get('commute_min')}분 안에 도착, 기다림 없음")
        elif approved and not pre and not out["auto_ordered"]:
            ctx["delivery_note"] = (f"확인 후 주문한 것을 {ctx['wait_for_delivery_min']}분 "
                                    f"기다린 뒤 조리를 시작한다")
        elif approved and not pre:
            ctx["delivery_note"] = (f"도착까지 {ctx['wait_for_delivery_min']}분 기다린 뒤 "
                                    f"조리를 시작한다 (확인 후 주문 포함)")

    def _prep_bind(ctx):
        return {"record": ctx["record"], "weigh": K.prep_weigh,
                "available": lambda n: K.fridge_check(n) is not None,
                "absorb_of": absorb_capacity, "solid_of": solid_mass,
                "broth_of": broth_of}

    def _prep_absorb(ctx, out):
        ctx["mass_g"] = out["total_mass_g"]
        ctx["add_later"] = out.get("add_later") or []
        ctx["absorb_cap_g"] = out.get("absorb_cap_g") or 0.0
        ctx["solid_g"] = out.get("solid_g") or 0.0
        ctx["water_added_g"] = out.get("water_added_g") or 0.0
        ctx["broth_g"] = out.get("broth_g") or 0.0
        ctx["start_temp_c"] = out.get("start_temp_c", 20.0)
        ctx["scum_g"] = scum_amount(ctx["record"].get("ingredients", []))
        ctx["extra_water_g"] = out["extra_water_g"]
        ctx["prep_missing"] = out["missing"]
        ctx["prep_short"] = out.get("short_g") or None

    def _converge_setup(ctx):
        spec = K.device_spec(constraints.get("device") or "")
        K.COOKER.start(ctx["mass_g"], ctx["extra_water_g"], power=3,
                       capacity_g=spec.get("capacity_g"),
                       solid_g=ctx.get("solid_g", 0.0),
                       absorb_cap_g=ctx.get("absorb_cap_g", 0.0),
                       need_units=cook_units_needed(
                           ctx["record"].get("ingredients", [])),
                       start_temp_c=ctx.get("start_temp_c", 20.0))

    def _make_recover(ctx):
        """지나쳤을 때 물을 부어 되돌린다 — 다만 묽어지는 만큼만."""
        MAX_DILUTION = 0.06          # 6% 넘게 묽어지면 되돌리지 않는다

        def recover(st, target, cur):
            if cur >= target:        # 덜 졸았으면 물을 부을 일이 아니다
                return None
            base = st.get("initial_mass_g") or 0
            need = (target - cur) * base
            if need <= 0:
                return None
            if (st.get("watered_g", 0) + need) / max(1.0, st["mass_g"]) > MAX_DILUTION:
                ctx["recover_declined"] = (
                    f"{round(need)}g 을 부으면 {MAX_DILUTION:.0%} 넘게 묽어진다 "
                    f"— 되돌리지 않고 지나친 채로 알린다")
                return None
            e = K.COOKER.add_water(need)
            if not e:
                return None
            ctx["recovered"] = (f"{e['grams']}g 을 부어 목표로 되돌렸다 "
                                f"(국물이 {e['dilution']:.1%} 묽어지고 "
                                f"{e['temp_drop_c']}도 식었다)")
            return {"note": ctx["recovered"], "water_g": e["grams"]}
        return recover

    def _cook_guard(st):
        """이상 감지. 넘칠 것 같으면 화력을 묶는다."""
        # 국물이 바닥나면 증발이 멎고 바닥이 탄다. 목표 질량비는 영원히
        # 오지 않는다 — 기다리지 말고 멈춰야 한다.
        if st.get("free_ratio", 1.0) <= 0.02:
            return {"stop": True,
                    "note": (f"국물이 바닥났다 (자유 수분 "
                             f"{st.get('free_liquid_g', 0)}g) — 재료가 물을 "
                             f"다 빨아들였다. 더 졸일 물이 없으므로 여기서 "
                             f"멈춘다. 물을 더 붓고 다시 시작해야 한다")}
        # 임계는 실제 분포에서 잡는다. 새 열 모델에서 재니 채움 50% 화력5 가
        # 0.462, 75% 가 0.712, 90% 가 0.853 이다. 옛 임계(0.30/0.45)로는
        # **절반만 찬 냄비도 화력이 묶였다** — 50% 는 넘치지 않는다.
        risk = st.get("overflow_risk", 0.0)
        if risk >= 0.78:
            return {"limit_power": 2,
                    "note": (f"끓어넘침 위험 {risk} (냄비의 "
                             f"{st.get('fill_ratio', 0):.0%} 가 찼고 {st['temp_c']}도) "
                             f"→ 화력을 2 로 묶는다")}
        if risk >= 0.60:
            return {"limit_power": 3,
                    "note": f"끓어넘침 위험 {risk} → 화력을 3 으로 묶는다"}
        return None

    def _make_stage_hook(ctx):
        """조리 단계를 진행시키는 훅. 무엇을 언제 어떻게 하는지는 도메인이 안다.

        조리는 '넣고 기다리기' 가 아니다. 뚜껑을 여닫고, 거품을 걷고, 젓는다.
        제어기(converge)는 이 중 아무것도 모르고, 훅으로 물어보기만 한다.
        """
        pending = list(ctx.get("add_later") or [])
        pending.sort(key=lambda x: -x["at_ratio"])     # 먼저 넣을 것부터
        scum_left = [ctx.get("scum_g", 0.0)]
        lid_opened = [False]          # 한 번 열면 끝까지 연다
        ctx.setdefault("hands_on", [])

        def hand(kind, note):
            """사람이 해야 하는 일. 에이전트는 때를 알려줄 뿐이다."""
            ctx["hands_on"].append(
                {"at_min": round(K.COOKER.elapsed_min, 1), "kind": kind,
                 "who": WHO.get(kind, "사람"), "note": note})

        def hook(state):
            # (0) 졸임이 끝났는데 아직 안 익었으면 **다시 덮는다.**
            #     덮으면 증발이 거의 없어 더 졸지 않으면서 익힐 수 있다 —
            #     "뚜껑 덮고 뭉근히" 가 그것이다. 덮지 않으면 익히는 동안
            #     계속 졸아 목표 0.95 짜리가 0.7777 까지 갔다.
            if (lid_opened[0] and not state.get("lid")
                    and state.get("doneness", 1.0) < 1.0
                    and state["mass_ratio"] <= ctx["record"]["target_mass_ratio"]):
                K.COOKER.set_lid(True)
                hand("뚜껑", "아직 안 익었다 → 뚜껑을 덮고 뭉근히")
                return {"note": ("졸임은 끝났는데 아직 안 익었다 → 뚜껑을 덮어 "
                                 "더 졸지 않게 하고 익힌다"),
                        "resets_baseline": False, "slow_down": True}

            # (1) 뚜껑 — 끓을 때까지 덮고, 끓으면 열어 **끝까지 열어 둔다.**
            #     온도 한 점을 기준으로 여닫으면 그 근처에서 계속 뒤집힌다.
            #     실제로 채터링이 나서 제어가 무너졌다(0.78 목표에 0.61).
            if not lid_opened[0]:
                if state["temp_c"] >= 97.0:
                    lid_opened[0] = True
                    K.COOKER.set_lid(False)
                    hand("뚜껑", "뚜껑을 연다")
                    return {"note": ("끓었다 → 뚜껑을 연다 "
                                     "(덮은 채로는 졸지 않는다). 증발이 갑자기 "
                                     "빨라지므로 주기를 줄여 다시 본다"),
                            "resets_baseline": False, "slow_down": True}
                if not state.get("lid"):
                    K.COOKER.set_lid(True)
                    hand("뚜껑", "뚜껑을 덮는다")
                    return {"note": "뚜껑을 덮는다 — 빨리 끓는다",
                            "resets_baseline": False}

            # (2) 거품 — 고기를 삶으면 뜬다. 걷어낸 양은 증발이 아니다.
            #     분모에서 빼 주지 않으면 제어기가 졸아든 것으로 착각한다.
            if scum_left[0] > 0 and state["temp_c"] >= 96.0:
                e = K.COOKER.skim(scum_left[0], "거품")
                scum_left[0] = 0.0
                if e:
                    hand("거품", f"거품 {e['grams']}g 을 걷는다")
                    return {"note": (f"거품 {e['grams']}g 을 걷어낸다 — 질량이 줄지만 "
                                     f"증발이 아니므로 기준에서 뺀다"),
                            "resets_baseline": True}

            # (3) 젓기 — 눌어붙기 시작하면 사용자에게 알린다.
            #     제안서 표1 에서 교반은 사람이 맡는 일로 적었다.
            if (state.get("soil_score", 0) >= 0.40
                    and state.get("stir_since_min", 0) >= 6.0):
                K.COOKER.stir()
                ctx["stir_prompts"] = ctx.get("stir_prompts", 0) + 1
                hand("교반", "한 번 저어 준다")
                return {"note": (f"눌어붙음 {state['soil_score']} — "
                                 f"\"지금 한 번 저어 주세요\" 안내"),
                        "resets_baseline": False}

            # (4) 중간 투입
            if not pending:
                return None
            nxt = pending[0]
            if state["mass_ratio"] > nxt["at_ratio"]:
                return None
            pending.pop(0)
            e = K.COOKER.add_ingredient(
                nxt["name"], nxt["grams"], temp_c=nxt["temp_c"],
                need_units=COOK_UNITS.get(nxt["name"], 0.0) * nxt["grams"])
            if not e:
                return None
            hand("투입", f"{nxt['name']} {nxt['grams']}g 을 넣는다")
            ctx.setdefault("stage_events", []).append(
                {"name": nxt["name"], "at_ratio": nxt["at_ratio"],
                 "grams": nxt["grams"], "temp_drop_c": e["temp_drop_c"]})
            if e.get("warning"):
                ctx.setdefault("overfill_warn", []).append(e["warning"])
            return {"note": (f"{nxt['name']} {nxt['grams']}g 투입 "
                             f"(질량비 {nxt['at_ratio']} 시점) — 온도 "
                             f"{e['temp_drop_c']}도 하강"
                             + (f". {nxt['why']}" if nxt["why"] else "")),
                    "resets_baseline": True}
        return hook

    def _converge_bind(ctx):
        # 한 걸음(최소 관측 주기 0.1분)에 날아가는 양보다 졸일 양이 적으면
        # 맞출 방법이 없다. 그 양은 **화력에 비례**한다 — 화력3 에서 2.0g,
        # 화력5 에서 3.8g. 최대 화력을 기준으로 잡는다.
        #
        # (고쳐 온 과정: 처음에는 화력3 기준 2.1g 로 고정해 두어 화력이
        #  올라가는 경우를 놓쳤고, 그 전에는 분당 증발량의 2배로 잡아 잘
        #  맞춘 실행에 거짓 경보를 냈다.)
        tgt = ctx["record"]["target_mass_ratio"]
        step_g = round(
            (5 * K.Cooker.WATT_PER_POWER - K.Cooker.LOSS_W_PER_K * 80)
            * 0.1 * 60 / K.Cooker.LATENT_J_PER_G, 2)
        min_ctrl = round(step_g / max(0.01, 1 - tgt), 1)
        return {"observe": lambda: K.COOKER.state(),
                "actuate": K.COOKER.set_power, "step": K.COOKER.tick,
                "metric": "mass_ratio",
                "target": ctx["record"]["target_mass_ratio"],
                # 새 열 모델에서 '끓는다' 는 100도 도달이다. 94.6도에서는
                # 분당 1g 인데 100도에서는 11.6g — 12배 차이다. 92 로 두면
                # 아직 안 끓는 상태를 '준비됨' 으로 보고 화력을 안 올린다.
                "direction": "down", "ready_key": "temp_c", "ready_at": 99.5,
                # 상한은 관측 횟수가 아니라 **시간**으로 둔다. 주기를 줄이면
                # 짧은 조리도 횟수 상한에 걸리기 때문이다. 사용자의 시간
                # 예산 안에서 끝나야 하므로 그 값을 쓴다.
                "max_steps": 400,
                "max_minutes": min(45, constraints.get("budget_min") or 45),
                "min_controllable": min_ctrl, "amount_key": "initial_mass_g",
                "on_observe": _make_stage_hook(ctx),
                # 기기가 자기 여열을 안다. 제어기는 그 값을 받아 앞당겨 끈다.
                "guard": _cook_guard, "recover": _make_recover(ctx),
                # 졸임이 끝나도 안 익었으면 끝이 아니다
                "also_require": lambda st: st.get("doneness", 1.0) >= 1.0,
                "residual": lambda st: (K.COOKER.predict_residual_g()
                                        / st["initial_mass_g"]
                                        if st.get("initial_mass_g") else 0.0)}

    def _converge_absorb(ctx, out):
        st = K.COOKER.state()
        over = out.get("overshoot")
        if out.get("overshot"):
            ctx["too_small"] = (f"목표 {ctx['record']['target_mass_ratio']} 를 "
                                f"{over} 만큼 지나쳤다 — 도달로 세지 않는다")
        elif out.get("too_small"):
            ctx["too_small"] = (f"초기 {st['initial_mass_g']}g 은 최소 관측 주기로도 "
                                f"목표를 지나칠 수 있는 양이다")
        elif over is not None and over > 0.03:
            # 실제로 잰 지나침이 큰 경우에만 말한다. 추정이 아니라 측정이다.
            ctx["too_small"] = (f"목표를 {over} 지나쳤다 (허용 안) — "
                                f"양이 적어 제어가 빡빡했다")
        ctx["overshoot"] = over
        ctx["coasted_from"] = out.get("coasted_from")
        ctx["guard_notes"] = out.get("guard_notes") or []
        ctx["relights"] = out.get("relights") or 0
        ctx["held_for_doneness"] = out.get("held")
        ctx["doneness"] = K.COOKER.state().get("doneness")
        # 기록은 **먹기 직전** 상태로 남긴다. 불을 끄는 순간의 값을 저장하면
        # 재현할 때 그 지점에서 또 여열이 붙어 회차마다 더 졸아든다.
        rested = K.COOKER.rest_until_still()
        # 되돌리기는 **여열까지 끝난 뒤** 해야 한다. 불을 끈 시점에 맞춰
        # 물을 부어도 여열이 남아 있으면 다시 졸아든다.
        _tgt = ctx["record"]["target_mass_ratio"]

        # **덜 졸았으면 다시 가열한다.** 여열 예측이 과대하면 일찍 꺼져
        # 목표에 못 미친다. 물을 붓는 것은 지나쳤을 때 쓰는 수단이고,
        # 모자랄 때는 더 졸이면 된다 — 실제 요리도 그렇게 한다.
        _under = 0
        while rested["mass_ratio"] > _tgt + 2e-3 and _under < 2:
            _under += 1
            K.COOKER.set_power(3)
            for _ in range(240):
                K.COOKER.tick(0.25)
                _st = K.COOKER.state()
                _res = (K.COOKER.predict_residual_g()
                        / max(1.0, _st["initial_mass_g"]))
                if _st["mass_ratio"] - _res <= _tgt:
                    break
            rested = K.COOKER.rest_until_still()
        if _under:
            ctx["reheated"] = (f"여열이 모자라 {_under}회 더 가열했다 "
                               f"(최종 {rested['mass_ratio']})")

        if rested["mass_ratio"] < _tgt - 1e-3:
            _fix = _make_recover(ctx)(rested, _tgt, rested["mass_ratio"])
            if _fix:
                rested = K.COOKER.state()
        pred = out.get("predicted_final")
        if pred is not None:
            ctx["residual_pred_err"] = round(abs(pred - rested["mass_ratio"]), 4)
        ctx["off_ratio"] = out["final"]                 # 불 끄는 순간
        ctx["final_ratio"] = rested["mass_ratio"]       # 먹기 직전 (저장 대상)
        ctx["rest_drop"] = round(out["final"] - rested["mass_ratio"], 4)
        st = rested
        ctx["cook_min"] = out["steps"]

        # 눌어붙음은 **이번 조리에서 잰 값**을 쓴다. 예전에는 기록에 적힌
        # 가정값을 그대로 세척기에 넘겼는데, 그러면 "조리기가 아는 것을
        # 세척기에 넘긴다" 는 주장의 근거가 되지 못한다 — 조리를 하고도
        # 조리 결과를 안 본 것이기 때문이다.
        ctx["soil"] = st["soil_score"]
        ctx["soil_sigma"] = st.get("soil_sigma", 0.0)
        ctx["soil_assumed"] = ctx["record"].get("soil_score")

        # 끝난 조리를 다음 번 목표로 저장한다.
        # 다만 목표를 지나친 결과를 그대로 저장하면 오차가 학습된다.
        # 저장 전에 검토하고, 벗어났으면 사용자에게 물어야 한다.
        measured = {"final_ratio": ctx["final_ratio"], "cook_min": out["steps"],
                    "peak_temp_c": st["peak_temp_c"],
                    "soil_score": st["soil_score"]}
        K.COOKER.stop()
        review = K.record_review(ctx["record"], measured)
        ctx["save_review"] = review

        if review["suggest"] == "ask":
            # 시연에서는 사용자가 "그래도 저장" 을 골랐다고 본다.
            # 만족도를 낮춰 남기므로, 더 나은 기록이 생기면 그쪽이 먼저 뽑힌다.
            sat = 3
        else:
            sat = 4

        saved = K.record_save(
            ctx["record"], measured, saved_by="본인", satisfaction=sat,
            actual_initial_g=ctx.get("mass_g"))
        ctx["saved_record_id"] = saved["record_id"]
        ctx["saved_satisfaction"] = sat
        ctx["target_was_estimated"] = bool(ctx["record"].get("estimated"))

    def _meal_ready_min(ctx):
        """귀가부터 음식이 될 때까지(분). 식사까지 지표와 같은 식이다."""
        if ctx.get("cook_min") is None:
            return None
        return (ctx.get("wait_for_delivery_min", 0)
                + (0 if ctx.get("decided_before_home") else
                   FIXED_MIN["보관 확인"] + FIXED_MIN["메뉴 결정"])
                + FIXED_MIN["준비"] + ctx["cook_min"])

    def _aftercare_bind(ctx):
        return {"soil_score": ctx["soil"], "profile": "dishwasher",
                "soil_sigma": ctx.get("soil_sigma", 0.0),
                # 다 먹은 뒤에 시작한다(귀가 + 식사까지 + 식사 시간)
                "start_at": (_hhmm_plus(constraints["arrive_home"],
                                        _meal_ready_min(ctx) + EAT_MIN)
                             if constraints.get("arrive_home")
                             and _meal_ready_min(ctx) is not None
                             else constraints.get("cleanup_at")),
                "quiet_after": constraints.get("quiet_after")}

    def _aftercare_absorb(ctx, out):
        ctx["course"] = out["course"]
        ctx["quiet_note"] = out.get("quiet_note")
        ctx["need_probs"] = out.get("need_probs")
        ctx["course_min"] = out["minutes"]
        ctx["water_expected_l"] = out["expected_water_l"]
        ctx["water_saved_l"] = out["saved_l"]

    return [
        Task(skill="recipe_source", provides=("recipe_pool",),
             bind=_recipe_bind, absorb=_recipe_absorb,
             note="먹을 수 있는 것만 남긴 후보군을 공개 자료에서 먼저 만든다"),
        Task(skill="inventory", provides=("urgent_items",),
             bind=_inventory_bind, absorb=_inventory_absorb,
             note="무엇이 곧 상하는지 알아야 목표가 생긴다"),
        Task(skill="menu", requires=("urgent_items", "recipe_pool"),
             provides=("chosen_record", "missing_items"),
             bind=_menu_bind, absorb=_menu_absorb,
             note="임박 재료를 쓰는 기록을 골라야 버리는 것이 줄어든다"),
        Task(skill="procure", requires=("missing_items",),
             provides=("stock_complete",),
             bind=_procure_bind, absorb=_procure_absorb,
             note="부족분이 있으면 재고를 먼저 채워야 조리가 가능하다"),
        Task(skill="prep", requires=("stock_complete", "chosen_record"),
             provides=("measured",),
             bind=_prep_bind, absorb=_prep_absorb,
             note="계량과 예상 수분을 넘겨야 조리가 같은 출발점에서 시작한다"),
        Task(skill="converge", requires=("measured",),
             provides=("cooked", "soil_score"),
             setup=_converge_setup, bind=_converge_bind, absorb=_converge_absorb,
             note="시간이 아니라 목표 상태로 조리를 끝낸다"),
        Task(skill="aftercare", requires=("soil_score",), provides=("cleaned",),
             bind=_aftercare_bind, absorb=_aftercare_absorb,
             note="조리기만 아는 눌어붙음 정도를 세척기에 넘긴다"),
    ]


def make_executor(registry, on_step=None, seed_ctx=None):
    """계획을 실제로 실행하는 함수를 만든다.

    experience_verify 스킬에 주입된다. 설계 스킬이 실행 층을 import 하지 않게
    하려는 것이다 — 스킬끼리는 여전히 서로를 모른다.
    """
    def execute(plan):
        ctx = {"touches": 0}
        if seed_ctx:
            ctx.update(seed_ctx)
        log, ok = [], True
        for i, t in enumerate(plan.steps, 1):
            skill = registry.get(t.skill)
            if t.setup:
                t.setup(ctx)
            res = skill.run(**t.kwargs(ctx))
            if t.absorb:
                t.absorb(ctx, res.output)
            log.append({"step": i, "skill": t.skill, "ok": res.ok,
                        "evidence": res.evidence})
            if on_step:
                on_step(i, t, res)
            if not res.ok and t.skill != "procure":
                # procure 의 확인 요청은 실패가 아니라 설계된 정지다
                ok = False
                # 어디서 왜 멈췄는지 남기지 않으면 결과가 조용히 비어 있다.
                # 전에는 실패한 실행도 성공한 실행과 똑같이 '지표 없음' 으로
                # 보였고, 그래서 조리가 통째로 빠진 것을 놓쳤다.
                ctx["halted_at"] = t.skill
                ctx["halt_reason"] = (res.output.get("recovery")
                                      or (res.evidence[-1] if res.evidence
                                          else "사유 없음"))
                if t.skill == "converge":
                    K.COOKER.stop()
                break

        metrics = {}
        if ctx.get("saved_record_id"):
            metrics["저장된 기록"] = (f"{ctx['saved_record_id']} "
                                  f"(만족도 {ctx.get('saved_satisfaction')})")
            rv = ctx.get("save_review") or {}
            if rv.get("overshot"):
                metrics["저장 전 확인"] = rv["why"]
            if ctx.get("target_was_estimated"):
                metrics["목표 갱신"] = (f"가정 {ctx['record']['target_mass_ratio']} → "
                                    f"실측 {ctx['final_ratio']}")
        if ctx.get("halted_at"):
            metrics["중단"] = f"{ctx['halted_at']} 에서 멈춤 — {ctx['halt_reason']}"
        if ctx.get("guard_notes"):
            metrics["이상 감지"] = " / ".join(ctx["guard_notes"])
        # 식사까지 실제로 걸린 시간. 제안의 핵심 주장이 "귀가 후 N분 안에
        # 제대로 된 한 끼" 인데, 지금까지 **그것을 검증하는 곳이 없었다.**
        # 장면 달성과 개입 횟수만 보고 있었다.
        if ctx.get("cook_min") is not None:
            # 선제 주문이면 재고 확인·메뉴 결정은 **퇴근길에 끝났다** — 집에서
            # 쓰는 시간에 넣지 않는다. 전에는 그것까지 집에서 한 것으로 셌다.
            before_home = ctx.get("decided_before_home")
            spent = (ctx.get("wait_for_delivery_min", 0)
                     + (0 if before_home else
                        FIXED_MIN["보관 확인"] + FIXED_MIN["메뉴 결정"])
                     + FIXED_MIN["준비"] + ctx["cook_min"])
            ctx["spent_min"] = round(spent, 1)
            metrics["식사까지(분)"] = ctx["spent_min"]
            budget = ctx.get("time_budget_min")
            if budget:
                metrics["시간 예산"] = (
                    f"{ctx['spent_min']}분 / {budget}분"
                    + ("" if ctx["spent_min"] <= budget else "  ← 초과"))
        if ctx.get("start_temp_c") is not None and ctx["start_temp_c"] < 18:
            metrics["출발 온도"] = (f"{ctx['start_temp_c']}도 — 냉장 재료가 섞여 "
                                f"상온(20도)보다 차다")
        if ctx.get("doneness") is not None and ctx.get("held_for_doneness"):
            metrics["익힘"] = (f"졸임이 먼저 끝나 약불로 유지하며 익혔다 "
                            f"(익힘 {ctx['doneness']})")
        # 제안의 핵심 구분인데 결과에 없었다 — 내 기록을 쓴 것인지
        # 공개 레시피를 쓴 것인지는 "저장한 것이 다시 쓰인다" 의 증거다.
        if ctx.get("overfill_warn"):
            metrics["용량 초과"] = " / ".join(ctx["overfill_warn"])
        if ctx.get("approved_after_ask"):
            metrics["확인 후 승인"] = ctx["approved_after_ask"]
        if ctx.get("overshoot") is not None:
            metrics["목표와의 차이"] = ctx["overshoot"]
        if ctx.get("menu_from"):
            metrics["기록 출처"] = ctx["menu_from"]
        if ctx.get("days_left") is not None and ctx["days_left"] < 99:
            metrics["가장 급한 재료"] = f"{ctx['days_left']}일 남음"
        if ctx.get("prep_missing"):
            metrics["계량 실패"] = ctx["prep_missing"]
        if ctx.get("recipe_stats"):
            st_ = ctx["recipe_stats"]
            metrics["자료 선별"] = (
                f"적재 {st_['적재']}건 → 알레르기 {st_['알레르기제외']}건 · "
                f"나트륨 {st_['나트륨제외']}건 제외 → 후보 {st_['후보']}건"
                + (f" ({ctx.get('recipe_source')})" if ctx.get("recipe_source") else ""))
        if ctx.get("expired_note"):
            metrics["폐기 대상"] = ctx["expired_note"]
        if ctx.get("delivery_note"):
            metrics["조달 대기"] = ctx["delivery_note"]
        elif "procure" in {r["skill"] for r in log}:
            # 확인 요청이 있었으면 "다 있다" 가 아니다 — p3 에서 찹쌀·미나리를
            # 물어 놓고 이 문구가 나갔다.
            metrics["조달 대기"] = ("조달 불필요 — 필요한 것이 이미 다 있다"
                                if not ctx.get("need_confirm") else
                                "기다림 없음 — 부족분은 확인 요청으로 넘겼다")
        if ctx.get("late_after_ask"):
            metrics["오늘 못 받음"] = ctx["late_after_ask"]
        if ctx.get("must_use_unmet"):
            metrics["오늘 못 쓰는 임박 재료"] = (
                f"{', '.join(ctx['must_use_unmet'])} — 오늘 만들 수 있는 메뉴 중 "
                f"쓰는 것이 없다. 냉동 보관을 권한다")
        if ctx.get("reheated"):
            metrics["재가열"] = ctx["reheated"]
        if ctx.get("recovered"):
            metrics["되돌림"] = ctx["recovered"]
        if ctx.get("recover_declined"):
            metrics["되돌리지 않음"] = ctx["recover_declined"]
        hands = ctx.get("hands_on") or []
        if hands:
            from collections import Counter
            kinds = Counter(h["kind"] for h in hands)
            metrics["손이 가는 일"] = (
                f"{len(hands)}회 · "
                + ", ".join(f"{k} {n}" for k, n in kinds.items()))
            # 진짜 값은 '몇 번 손이 가는가' 가 아니라 **얼마나 붙어 있어야
            # 하는가** 다. 에이전트가 없으면 언제 해야 할지 모르므로 조리
            # 내내 냄비 앞을 지켜야 한다.
            attended = round(len(hands) * HANDS_ON_MIN, 1)
            cookm = ctx.get("cook_min") or 0
            metrics["냄비 앞에 있어야 하는 시간"] = (
                f"{attended}분 / 조리 {cookm}분"
                + (f" (에이전트가 없으면 {cookm}분 내내)" if cookm else ""))
            metrics["지켜보는 시간(분)"] = attended
        if ctx.get("water_added_g"):
            # 이제 물에는 흡수분과 **남길 국물**이 함께 들어간다. "빨아들일
            # 만큼" 이라고만 쓰면 국물 몫을 흡수로 잘못 읽는다.
            metrics["물 보충"] = (f"{ctx['water_added_g']}g 을 부었다 "
                              f"(흡수 {round(ctx.get('absorb_cap_g') or 0)}g"
                              + (f" + 남길 국물 {round(ctx['broth_g'])}g"
                                 if ctx.get("broth_g") else "")
                              + " 포함)")
        if ctx.get("scale_basis"):
            metrics["조리량"] = (ctx["scale_basis"]
                              + (" · 기기 용량에 걸림" if ctx.get("scale_capped") else ""))
        if ctx.get("coasted_from") is not None:
            metrics["여열 마무리"] = (
                f"{ctx['coasted_from']}분에 불을 끄고 여열로 마무리 "
                f"(총 {ctx.get('cook_min')}분"
                + (f", 모자라서 {ctx['relights']}회 다시 켬" if ctx.get("relights")
                   else "") + ")")
        if ctx.get("stage_events"):
            metrics["중간 투입"] = " / ".join(
                f"{e['name']} {e['grams']}g @{e['at_ratio']} (-{e['temp_drop_c']}도)"
                for e in ctx["stage_events"])
        if ctx.get("need_probs"):
            pr = ctx["need_probs"]
            metrics["세척 강도 확률"] = ", ".join(
                f"{k}:{v:.0%}" for k, v in sorted(pr.items()) if v >= 0.005)
        if ctx.get("quiet_note"):
            metrics["소음 조치"] = ctx["quiet_note"]
        if ctx.get("soil") is not None:
            a = ctx.get("soil_assumed")
            metrics["눌어붙음(실측)"] = (f"{ctx['soil']:.3f}"
                                   + (f" (기록 가정값 {a})" if a is not None else ""))
        if ctx.get("prep_short"):
            metrics["재고 부족"] = ctx["prep_short"]
        if ctx.get("too_small"):
            metrics["제어 한계"] = ctx["too_small"]
        if "cook_min" in ctx:
            rec_min = ctx["record"].get("cook_minutes_observed")
            metrics["가열 시간(분)"] = ctx["cook_min"]
            if rec_min is not None:
                metrics["기록 고정시간 대비(분)"] = rec_min - ctx["cook_min"]
            else:
                # 공개 레시피에는 실측 조리 시간이 없다. 없는 값을 지어내지 않는다.
                metrics["기록 고정시간 대비(분)"] = "해당 없음(첫 조리·실측 기록 없음)"
            metrics["최종 질량비"] = ctx["final_ratio"]
            if ctx.get("residual_pred_err") is not None:
                metrics["여열 예측 오차"] = ctx["residual_pred_err"]
            if ctx.get("rest_drop"):
                metrics["여열로 더 졸음"] = (
                    f"불 끌 때 {ctx['off_ratio']} → 먹기 직전 {ctx['final_ratio']} "
                    f"({ctx['rest_drop']}) — 저장은 먹기 직전 값으로 한다")
        if "course" in ctx:
            metrics["세척 코스"] = ctx["course"]
            if ctx.get("course_min"):
                metrics["세척 소요(분)"] = ctx["course_min"]
            metrics["기대 물 사용(L)"] = ctx["water_expected_l"]
            metrics["기본 코스 대비 절감(L)"] = ctx["water_saved_l"]
        if ctx.get("order_krw"):
            metrics["자동 주문(원)"] = ctx["order_krw"]
            if ctx.get("order_stores"):
                metrics["주문 상점"] = ", ".join(ctx["order_stores"])
                metrics["도착까지(분)"] = ctx["order_eta_min"]
        if ctx.get("need_confirm"):
            metrics["확인 요청"] = [c["name"] + " — " + c["reason"]
                                 for c in ctx["need_confirm"]]
        metrics["메뉴"] = ctx.get("menu_name", "-")
        if not ctx.get("record"):
            # 조용히 None 으로 끝내면 "메뉴를 못 골랐다" 가 결과에 안 남아,
            # 실패가 지표 없음으로 보인다. 왜 못 골랐는지까지 적는다.
            have = [x["name"] for x in K.fridge_list_items()]
            metrics["메뉴 없음"] = (
                f"지금 있는 것({', '.join(have[:6])}{'…' if len(have) > 6 else ''})으로 "
                f"만들 수 있는 기록이 후보 {ctx.get('recipe_stats', {}).get('후보', 0)}건 "
                f"중에 없다"
                # 장보기가 **계획에 없었던 것**과 **계획에는 있는데 메뉴에서
                # 멈춰 못 간 것**은 다르다. 전에는 둘 다 "계획에서 빠졌다" 고 했다.
                + ("" if "procure" in {r["skill"] for r in log}
                   else " — 메뉴에서 멈춰 장보기 단계까지 가지 못했다"
                   if "procure" in {t.skill for t in plan.steps}
                   else " — 제때 오는 조달도 없어 장보기 단계가 계획에서 빠졌다"))
        if ctx.get("record"):
            metrics["사용한 기록"] = ctx["record"].get("record_id")
            metrics["목표 질량비"] = ctx["record"].get("target_mass_ratio")
            metrics["목표 출처"] = ("조리법 기본값(가정)" if ctx["record"].get("estimated")
                                else "실측 기록")

        return {"ok": ok, "user_touches": ctx["touches"],
                "metrics": metrics, "log": log, "ctx": ctx}

    return execute
