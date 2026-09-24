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
from planner import Task
from recipe_parse import contains_any
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


def scum_amount(ingredients) -> float:
    return sum(SCUM.get(i["name"], 0.0) * i.get("qty_g", 0) for i in ingredients)


# 고형분으로 남는 비율 (나머지는 국물에 섞인다)
SOLID_RATIO = {"닭고기": 0.85, "돼지고기": 0.85, "소고기": 0.85, "두부": 0.75,
               "배추": 0.35, "애호박": 0.30, "무": 0.30, "감자": 0.70,
               "찹쌀": 1.0, "쌀": 1.0, "국수": 1.0, "당면": 1.0,
               "미역": 1.0, "콩": 1.0, "표고버섯": 0.6, "떡": 1.0}


def absorb_capacity(ingredients) -> float:
    """이 재료들이 빨아들일 물의 총량."""
    return sum(ABSORBS.get(i["name"], 0.0) * i.get("qty_g", 0)
               for i in ingredients)


def solid_mass(ingredients) -> float:
    """국물이 되지 않고 고형으로 남는 무게."""
    return sum(SOLID_RATIO.get(i["name"], 0.5) * i.get("qty_g", 0)
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
    return max((COOK_UNITS.get(i["name"], 0.0) * i.get("qty_g", 0)
                for i in ingredients), default=0.0)


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
    return json.loads(RECIPE_CACHE.read_text(encoding="utf-8"))


def recipe_to_record(r: dict) -> dict:
    """공개 레시피를 조리 기록과 같은 형태로 맞춘다.

    개인 기록(satisfaction 1~5)과 달리 satisfaction 을 0 으로 둔다.
    그래서 같은 조건이면 **내 기록이 공개 레시피보다 먼저 선택된다.**
    """
    ratio, soil = METHOD_DEFAULT.get(r.get("method"), METHOD_DEFAULT["기타"])
    total = sum(i["qty_g"] for i in r["ingredients"])
    return {"record_id": f"pub_{r['recipe_id']}", "menu": r["menu"],
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


def build_tasks(constraints: dict) -> list:
    """상황에서 나온 제약을 반영해 이 도메인의 작업 목록을 만든다."""
    avoid = list(constraints.get("avoid", []))
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
        ctx["urgent"] = [i["name"] for i in out["urgent"]]
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
        stock = [x for x in K.fridge_list_items()
                 if x["name"] not in set(ctx.get("expired", []))]
        return {"records": records,
                "stock": stock,
                "prefer_items": ctx.get("urgent", []),
                "require_complete": require_complete,
                "avoid": avoid,
                "resolve": resolve_stock}

    def _menu_absorb(ctx, out):
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

        # 인원에 맞춰 양이 바뀌었으니 부족분도 그 양으로 다시 본다.
        need = ctx["record"].get("ingredients", [])
        stock = {s["name"]: s for s in K.fridge_list_items()}
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
        approved = [c["name"] for c in out["need_confirm"] if c["name"] in CATALOG]
        for name in approved:
            K.fridge_add(name, qty_for(name))
        ctx["approved_after_ask"] = approved

    def _prep_bind(ctx):
        return {"record": ctx["record"], "weigh": K.prep_weigh,
                "available": lambda n: K.fridge_check(n) is not None,
                "absorb_of": absorb_capacity, "solid_of": solid_mass}

    def _prep_absorb(ctx, out):
        ctx["mass_g"] = out["total_mass_g"]
        ctx["add_later"] = out.get("add_later") or []
        ctx["absorb_cap_g"] = out.get("absorb_cap_g") or 0.0
        ctx["solid_g"] = out.get("solid_g") or 0.0
        ctx["water_added_g"] = out.get("water_added_g") or 0.0
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
                                f"(국물이 {e['dilution']:.1%} 묽어졌다)")
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
            e = K.COOKER.add_ingredient(nxt["name"], nxt["grams"],
                                        temp_c=nxt["temp_c"])
            if not e:
                return None
            hand("투입", f"{nxt['name']} {nxt['grams']}g 을 넣는다")
            ctx.setdefault("stage_events", []).append(
                {"name": nxt["name"], "at_ratio": nxt["at_ratio"],
                 "grams": nxt["grams"], "temp_drop_c": e["temp_drop_c"]})
            return {"note": (f"{nxt['name']} {nxt['grams']}g 투입 "
                             f"(질량비 {nxt['at_ratio']} 시점) — 온도 "
                             f"{e['temp_drop_c']}도 하강"
                             + (f". {nxt['why']}" if nxt["why"] else "")),
                    "resets_baseline": True}
        return hook

    def _converge_bind(ctx):
        # 한 관측 주기(1분)에 증발하는 양보다 '졸일 양' 이 적으면 목표를 지나친다.
        # 화력 3 이면 분당 약 21g 이 날아가므로, 목표 질량비에서 역산한다.
        tgt = ctx["record"]["target_mass_ratio"]
        # converge 가 주기를 최소 0.1분까지 줄이므로, 그 한 번에 날아가는
        # 양(화력3 기준 약 2.1g)보다 졸일 양이 적을 때만 진짜 제어 불가다.
        # 적응 주기를 넣기 전 기준(분당 21g의 2배)을 그대로 두었더니
        # 실제로는 0.8462 로 잘 맞춘 실행에 경고가 붙었다 — 거짓 경보였다.
        # 한 걸음(최소 주기 0.1분)에 날아가는 양보다 졸일 양이 적으면 맞출 수
        # 없다. 그 양은 **화력에 비례**한다 — 화력3 에서 2.0g, 화력5 에서 3.8g.
        # 예전에는 화력3 기준 2.1 로 고정해 두어, 화력이 올라가는 경우를
        # 놓쳤다. 최대 화력 기준으로 잡는다.
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
            ctx["save_asked"] = review["why"]
        else:
            sat = 4

        saved = K.record_save(
            ctx["record"], measured, saved_by="본인", satisfaction=sat,
            actual_initial_g=ctx.get("mass_g"))
        ctx["saved_record_id"] = saved["record_id"]
        ctx["saved_satisfaction"] = sat
        ctx["target_was_estimated"] = bool(ctx["record"].get("estimated"))

    def _aftercare_bind(ctx):
        return {"soil_score": ctx["soil"], "profile": "dishwasher",
                "soil_sigma": ctx.get("soil_sigma", 0.0),
                "start_at": constraints.get("cleanup_at"),
                "quiet_after": constraints.get("quiet_after")}

    def _aftercare_absorb(ctx, out):
        ctx["course"] = out["course"]
        ctx["quiet_note"] = out.get("quiet_note")
        ctx["border_note"] = out.get("border_note")
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
            spent = (ctx.get("wait_for_delivery_min", 0)
                     + FIXED_MIN["보관 확인"] + FIXED_MIN["메뉴 결정"]
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
        if ctx.get("expired_note"):
            metrics["폐기 대상"] = ctx["expired_note"]
        if ctx.get("delivery_note"):
            metrics["조달 대기"] = ctx["delivery_note"]
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
            ctx["attended_min"] = attended
        if ctx.get("water_added_g"):
            metrics["물 보충"] = (f"재료가 빨아들일 {ctx['water_added_g']}g 을 "
                              f"미리 더 부었다")
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
        if ctx.get("border_note"):
            metrics["경계 판정"] = ctx["border_note"]
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
            if ctx.get("rest_drop"):
                metrics["여열로 더 졸음"] = (
                    f"불 끌 때 {ctx['off_ratio']} → 먹기 직전 {ctx['final_ratio']} "
                    f"({ctx['rest_drop']}) — 저장은 먹기 직전 값으로 한다")
        if "course" in ctx:
            metrics["세척 코스"] = ctx["course"]
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
        if ctx.get("record"):
            metrics["사용한 기록"] = ctx["record"].get("record_id")
            metrics["목표 질량비"] = ctx["record"].get("target_mass_ratio")
            metrics["목표 출처"] = ("조리법 기본값(가정)" if ctx["record"].get("estimated")
                                else "실측 기록")

        return {"ok": ok, "user_touches": ctx["touches"],
                "metrics": metrics, "log": log, "ctx": ctx}

    return execute
