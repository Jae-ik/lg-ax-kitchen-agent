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

    def _menu_bind(ctx):
        # 내 기록이 먼저, 공개 레시피가 그다음. 같은 형태로 맞춰 함께 채점한다.
        records = list(K.RECORDS.values())
        records += [recipe_to_record(r) for r in ctx.get("recipe_pool", [])]
        return {"records": records,
                "stock": K.fridge_list_items(),
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
        ctx["missing"] = list(best["missing"])
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

        for a in out["auto_ordered"]:
            K.fridge_add(a["name"], qty_for(a["name"]))
        ctx["order_krw"] = out["total_krw"]
        ctx["order_eta_min"] = out.get("arrive_in_min", 0)
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
                "available": lambda n: K.fridge_check(n) is not None}

    def _prep_absorb(ctx, out):
        ctx["mass_g"] = out["total_mass_g"]
        ctx["extra_water_g"] = out["extra_water_g"]
        ctx["prep_missing"] = out["missing"]
        ctx["prep_short"] = out.get("short_g") or None

    def _converge_setup(ctx):
        K.COOKER.start(ctx["mass_g"], ctx["extra_water_g"], power=3)

    def _converge_bind(ctx):
        # 한 관측 주기(1분)에 증발하는 양보다 '졸일 양' 이 적으면 목표를 지나친다.
        # 화력 3 이면 분당 약 21g 이 날아가므로, 목표 질량비에서 역산한다.
        tgt = ctx["record"]["target_mass_ratio"]
        # converge 가 주기를 최소 0.1분까지 줄이므로, 그 한 번에 날아가는
        # 양(화력3 기준 약 2.1g)보다 졸일 양이 적을 때만 진짜 제어 불가다.
        # 적응 주기를 넣기 전 기준(분당 21g의 2배)을 그대로 두었더니
        # 실제로는 0.8462 로 잘 맞춘 실행에 경고가 붙었다 — 거짓 경보였다.
        min_ctrl = round(2.1 / max(0.01, 1 - tgt), 1)
        return {"observe": lambda: K.COOKER.state(),
                "actuate": K.COOKER.set_power, "step": K.COOKER.tick,
                "metric": "mass_ratio",
                "target": ctx["record"]["target_mass_ratio"],
                "direction": "down", "ready_key": "temp_c", "ready_at": 92.0,
                "max_steps": 30,
                "min_controllable": min_ctrl, "amount_key": "initial_mass_g"}

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
        K.COOKER.stop()
        ctx["cook_min"] = out["steps"]
        ctx["final_ratio"] = out["final"]
        ctx["soil"] = ctx["record"]["soil_score"]

        # 끝난 조리를 다음 번 목표로 저장한다.
        # 다만 목표를 지나친 결과를 그대로 저장하면 오차가 학습된다.
        # 저장 전에 검토하고, 벗어났으면 사용자에게 물어야 한다.
        measured = {"final_ratio": out["final"], "cook_min": out["steps"],
                    "peak_temp_c": st["temp_c"],
                    "soil_score": ctx["record"]["soil_score"]}
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
        return {"soil_score": ctx["soil"], "profile": "dishwasher"}

    def _aftercare_absorb(ctx, out):
        ctx["course"] = out["course"]
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
