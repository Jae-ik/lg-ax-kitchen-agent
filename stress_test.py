# -*- coding: utf-8 -*-
"""일부러 망가뜨려 본다 — 어디까지 버티는지 알아야 한계를 말할 수 있다.

정상 경로만 돌려서는 "잘 된다" 밖에 말할 수 없다. 극단을 넣어 보고
**어떤 입력에서 무너지는지** 를 먼저 알아야 한다.

    python stress_test.py
"""
from __future__ import annotations
import io
import contextlib
import traceback

import kitchen as K
import store
from skills import REGISTRY
from kitchen_domain import (build_tasks, make_executor, pantry_stock,
                            resolve_stock, load_recipes)
from planner import plan as make_plan, PlanError
from recipe_parse import contains_any

RESULT = []


def case(name):
    def deco(fn):
        def wrap():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    msg = fn()
                RESULT.append(("OK", name, msg or ""))
            except Exception as e:
                RESULT.append(("깨짐", name,
                               f"{type(e).__name__}: {str(e)[:110]}"))
        wrap._name = name
        return wrap
    return deco


# ═══════════════ 1. 재고·후보가 비는 극단 ═══════════════
@case("냉장고가 완전히 비었을 때")
def t1():
    K.reset([])
    r = REGISTRY.get("inventory").run(items=K.fridge_list_items())
    return f"임박 {r.output['count']}건, ok={r.ok}"


@case("모든 후보가 기피 재료를 포함할 때")
def t2():
    K.reset()
    recs = list(K.RECORDS.values())
    avoid = sorted({i["name"] for r in recs for i in r["ingredients"]})
    r = REGISTRY.get("menu").run(records=recs, stock=K.fridge_list_items(),
                                 avoid=avoid, resolve=resolve_stock)
    return f"후보 {len(r.output['candidates'])}건, best={r.output['best']}, ok={r.ok}"


@case("상점이 하나도 없을 때")
def t3():
    r = REGISTRY.get("procure").run(missing=["두부"],
                                    lookup=store.make_lookup([]),
                                    known_items=["두부"])
    return f"자동 {len(r.output['auto_ordered'])}, 확인요청 {len(r.output['need_confirm'])}"


@case("레시피 캐시가 비었을 때")
def t4():
    r = REGISTRY.get("recipe_source").run(
        load=lambda: {"source": "(없음)", "key_used": "-", "recipes": []},
        match=contains_any, avoid=["새우"])
    return f"후보 {r.output['kept']}건, ok={r.ok}"


# ═══════════════ 2. 조리가 실패하는 극단 ═══════════════
@case("목표에 도달할 수 없을 때 (물만 넣고 0.1 까지 졸이기)")
def t5():
    K.reset()
    K.COOKER.start(500, 0, power=1)
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio", target=0.10,
        direction="down", ready_key="temp_c", ready_at=92.0, max_steps=15)
    K.COOKER.stop()
    return (f"reached={r.output['reached']} steps={r.output['steps']} "
            f"final={r.output['final']} ok={r.ok}")


@case("목표가 이미 달성된 상태에서 시작")
def t6():
    K.reset()
    K.COOKER.start(500, 0, power=3)
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio", target=1.5,
        direction="down", max_steps=10)
    K.COOKER.stop()
    return f"steps={r.output['steps']} reached={r.output['reached']}"


@case("초기 질량이 0 일 때")
def t7():
    K.reset()
    K.COOKER.start(0, 0, power=3)
    s = K.COOKER.state()
    K.COOKER.tick(1.0)
    return f"mass_ratio={s['mass_ratio']} (0 나눗셈 방어)"


# ═══════════════ 3. 기기 이식의 극단 ═══════════════
@case("용량 10배로 이식")
def t8():
    K.reset()
    imp = K.record_import(K.RECORDS["rec_001"], "대형", capacity_ratio=10)
    return f"재료 {[i['qty_g'] for i in imp['ingredients']]}, 초기 {imp['initial_mass_g']}g"


@case("용량 1/100 로 이식 (반올림으로 0 이 되는가)")
def t9():
    K.reset()
    imp = K.record_import(K.RECORDS["rec_001"], "소형", capacity_ratio=0.01)
    zeros = [i["name"] for i in imp["ingredients"] if i["qty_g"] <= 0]
    return f"재료 {[(i['name'], i['qty_g']) for i in imp['ingredients']]}, 0g 된 항목 {zeros}"


@case("이식한 기록으로 실제 조리")
def t10():
    K.reset()
    imp = K.record_import(K.RECORDS["rec_001"], "소형", capacity_ratio=0.01)
    total = sum(i["qty_g"] for i in imp["ingredients"])
    K.COOKER.start(max(total, 1), 0, power=3)
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio",
        target=imp["target_mass_ratio"], direction="down",
        ready_key="temp_c", ready_at=92.0, max_steps=30,
        # 파이프라인이 주는 값과 같게 — 한 걸음(약 2.1g)보다 졸일 양이
        # 적으면 맞출 방법이 없다
        min_controllable=round(2.1 / (1 - imp["target_mass_ratio"]), 1))
    K.COOKER.stop()
    return (f"초기 {total}g → 거부={r.output.get('refused')} "
            f"{r.output['steps']}분 → {r.output['final']}")


# ═══════════════ 4. 계획 수립의 극단 ═══════════════
@case("아무도 만들 수 없는 목표를 요구")
def t11():
    try:
        make_plan({"존재하지_않는_사실"}, build_tasks({}))
        return "예외가 안 났다 — 잘못된 목표가 통과됐다"
    except PlanError as e:
        return f"PlanError 로 막힘: {str(e)[:60]}"


@case("모든 목표가 이미 달성된 상태")
def t12():
    p = make_plan({"cooked", "cleaned"}, build_tasks({}),
                  known={"cooked", "cleaned"})
    return f"계획 {len(p.steps)}단계 (0 이어야 정상)"


# ═══════════════ 5. 저장·검토의 극단 ═══════════════
@case("목표가 없는 기록을 검토")
def t13():
    r = K.record_review({}, {"final_ratio": 0.7})
    return f"suggest={r['suggest']} gap={r['gap']}"


@case("목표보다 덜 졸았을 때 (반대 방향 오차)")
def t14():
    r = K.record_review({"target_mass_ratio": 0.78}, {"final_ratio": 0.95})
    return f"suggest={r['suggest']} gap={r['gap']} overshot={r['overshot']}"


@case("재고보다 많이 요구했을 때 부족분이 잡히는가")
def t15():
    K.reset()
    w = K.prep_weigh("배추", 5000)
    return f"담김 {w['actual_g']}g / 부족 {w['short_g']}g / 남은 재고 {K.fridge_check('배추')}"


@case("이름은 있는데 수량이 모자랄 때 (menu 가 잡아내는가)")
def t16():
    K.reset([{"name": "배추", "qty_g": 20, "stored_days": 6, "shelf_life_days": 7},
             {"name": "두부", "qty_g": 300, "stored_days": 3, "shelf_life_days": 5},
             {"name": "된장", "qty_g": 500, "stored_days": 40, "shelf_life_days": 365}])
    r = REGISTRY.get("menu").run(records=list(K.RECORDS.values()),
                                 stock=K.fridge_list_items(),
                                 resolve=resolve_stock)
    b = r.output["best"]
    return f"best={b['record_id']} 부족={b['missing']} 수량부족={b.get('short_g')}"


@case("수량이 모자란 채로 계량까지 갔을 때 (prep 이 막는가)")
def t17():
    K.reset([{"name": "배추", "qty_g": 20, "stored_days": 6, "shelf_life_days": 7},
             {"name": "두부", "qty_g": 300, "stored_days": 3, "shelf_life_days": 5},
             {"name": "된장", "qty_g": 500, "stored_days": 40, "shelf_life_days": 365}])
    r = REGISTRY.get("prep").run(
        record=K.RECORDS["rec_001"], weigh=K.prep_weigh,
        available=lambda n: K.fridge_check(n) is not None)
    return f"ok={r.ok} 못 담은 재료={r.output['missing']}"


@case("등록되지 않은 기기로 이식")
def t18():
    K.reset()
    imp = K.record_import(K.RECORDS["rec_001"], to_device="처음 보는 조리기")
    return f"비율 근거: {imp['scale_basis']}"


@case("작은 냄비로 이식 후 조리 (적응 주기가 먹는가)")
def t19():
    K.reset()
    imp = K.record_import(K.RECORDS["rec_001"], to_device="원룸 1인용 조리기")
    tot = sum(i["qty_g"] for i in imp["ingredients"])
    K.COOKER.start(imp["initial_mass_g"], 0, power=3)
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio",
        target=imp["target_mass_ratio"], direction="down",
        ready_key="temp_c", ready_at=92.0, max_steps=60)
    K.COOKER.stop()
    return (f"{imp['initial_mass_g']}g → {r.output['steps']}분 → "
            f"{r.output['final']} (목표 {imp['target_mass_ratio']}, "
            f"지나침 {r.output.get('overshoot')}) ok={r.ok}")


@case("세척이 소음 시각을 넘겨 도는 경우")
def t20():
    r = REGISTRY.get("aftercare").run(soil_score=0.7, profile="dishwasher",
                                      start_at="22:25", quiet_after="22:30")
    o = r.output
    return f"{o['course']} {o['minutes']}분 {o['noise_db']}dB — {o['quiet_note']}"


@case("소음 시각 안에 끝나면 손대지 않는가")
def t21():
    r = REGISTRY.get("aftercare").run(soil_score=0.7, profile="dishwasher",
                                      start_at="18:00", quiet_after="23:00")
    o = r.output
    return f"{o['course']} {o['minutes']}분 {o['noise_db']}dB — 조치={o['quiet_note']}"


@case("자정을 넘는 소음 시각")
def t22():
    r = REGISTRY.get("aftercare").run(soil_score=0.2, profile="dishwasher",
                                      start_at="23:40", quiet_after="00:30")
    o = r.output
    return f"{o['course']} {o['minutes']}분 — {o['quiet_note']}"


@case("눌어붙음을 실측하면 코스가 달라지는가")
def t23():
    K.reset()
    out = []
    for g, tgt in ((620, 0.78), (240, 0.88)):
        K.COOKER.start(g, 0, power=3)
        while K.COOKER.state()["mass_ratio"] > tgt and K.COOKER.elapsed_min < 40:
            K.COOKER.tick(1.0)
            if K.COOKER.state()["temp_c"] < 92:
                K.COOKER.set_power(min(5, K.COOKER.power + 1))
        soil = K.COOKER.state()["soil_score"]
        K.COOKER.stop()
        c = REGISTRY.get("aftercare").run(soil_score=soil).output["course"]
        out.append(f"{g}g→{tgt}: 눌어붙음 {soil:.3f} → {c}")
    return " / ".join(out)


@case("수렴에 실패하면 가열을 끄는가")
def t24():
    K.reset()
    K.COOKER.start(500, 0, power=1)
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio", target=0.10,
        direction="down", ready_key="temp_c", ready_at=92.0, max_steps=12)
    power_after = K.COOKER.power
    K.COOKER.stop()
    return (f"ok={r.ok} 진행 {r.output['progress_pct']}% "
            f"화력={power_after}(0이어야 함) 복구안={r.output['recovery'][:46]}")


@case("냄비를 가득 채우고 조리 (끓어넘침을 잡는가)")
def t25():
    K.reset()
    K.COOKER.start(1000, 0, power=3, capacity_g=1100)
    notes = []

    def guard(st):
        r = st.get("overflow_risk", 0)
        if r >= 0.45:
            return {"limit_power": 2, "note": f"위험 {r} → 화력 2"}
        if r >= 0.30:
            return {"limit_power": 3, "note": f"위험 {r} → 화력 3"}
        return None

    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio", target=0.80,
        direction="down", ready_key="temp_c", ready_at=92.0,
        max_steps=40, guard=guard)
    peak = max(t.get("power", 0) for t in r.output["trace"] if "power" in t)
    K.COOKER.stop()
    return (f"최고 화력 {peak}(상한 5인데 묶였는가) · {r.output['steps']}분 · "
            f"감지 {r.output.get('guard_notes')}")


@case("여열 예측이 과대할 때 헛돌지 않는가")
def t26():
    K.reset()
    K.COOKER.start(300, 0, power=3, capacity_g=1100)
    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio", target=0.80,
        direction="down", ready_key="temp_c", ready_at=92.0, max_steps=40,
        residual=lambda st: 0.25)          # 일부러 크게 속인다
    K.COOKER.stop()
    return (f"ok={r.ok} {r.output['steps']}분 최종 {r.output['final']} "
            f"여열시작 {r.output.get('coasted_from')} "
            f"부족 {r.output.get('coast_short')}")


@case("여열로 마무리하면 지나침이 줄어드는가")
def t27():
    out = []
    for use in (False, True):
        K.reset(seed=11)
        K.COOKER.start(620, 0, power=3, capacity_g=1100)
        kw = {}
        if use:
            kw["residual"] = lambda st: (K.COOKER.predict_residual_g()
                                         / st["initial_mass_g"])
        r = REGISTRY.get("converge").run(
            observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
            step=K.COOKER.tick, metric="mass_ratio", target=0.78,
            direction="down", ready_key="temp_c", ready_at=92.0,
            max_steps=400, max_minutes=45, **kw)
        # **먹기 직전** 값으로 비교해야 한다. 불 끄는 시점으로 재면
        # 여열을 안 쓴 쪽이 좋아 보인다 — 아직 여열이 안 붙었으니까.
        eaten = K.COOKER.rest_until_still()["mass_ratio"]
        K.COOKER.stop()
        out.append(f"{'여열O' if use else '여열X'} {eaten} "
                   f"(먹기 직전 기준 지나침 {abs(0.78 - eaten):.4f})")
    return " / ".join(out)


@case("4인분이 냄비에 안 들어갈 때")
def t28():
    K.reset()
    r = K.record_scale(K.RECORDS["rec_001"], 8, "원룸 1인용 조리기")
    return f"{r['initial_mass_g']}g · {r['scale_basis']} · 걸림={r['capped_by_device']}"


@case("중간 투입이 기준을 다시 잡는가")
def t29():
    K.reset()
    K.COOKER.start(470, 0, power=3, capacity_g=1100)
    done = [False]

    def hook(st):
        if not done[0] and st["mass_ratio"] <= 0.93:
            done[0] = True
            e = K.COOKER.add_ingredient("두부", 150, temp_c=8)
            return {"note": f"두부 투입 -{e['temp_drop_c']}도", "resets_baseline": True}
        return None

    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio", target=0.78,
        direction="down", ready_key="temp_c", ready_at=92.0,
        max_steps=40, on_observe=hook)
    K.COOKER.stop()
    over_one = [t for t in r.output["trace"] if t.get("mass_ratio", 0) > 1.0]
    return (f"{r.output['steps']}분 최종 {r.output['final']} · 투입 "
            f"{len(r.output['events'])}회 · 비율이 1 을 넘은 관측 {len(over_one)}건")


@case("물을 먹는 재료 — 국물이 바닥나는가")
def t30():
    K.reset()
    K.COOKER.start(960, 0, power=3, capacity_g=3000,
                   solid_g=600, absorb_cap_g=320)
    for _ in range(10):
        K.COOKER.tick(1.0)
        if K.COOKER.state()["temp_c"] < 92:
            K.COOKER.set_power(min(5, K.COOKER.power + 1))
    s_ = K.COOKER.state()
    K.COOKER.stop()
    return (f"질량비 {s_['mass_ratio']} (목표 0.85 에 못 감) · 자유수분 "
            f"{s_['free_liquid_g']}g · 눌어붙음 {s_['soil_score']}")


@case("흡수량 + 졸일 양을 계산해 물을 붓는가")
def t31():
    from kitchen_domain import absorb_capacity, solid_mass
    rec = {"menu": "삼계탕", "target_mass_ratio": 0.85, "initial_mass_g": 960,
           "ingredients": [{"name": "닭고기", "qty_g": 480},
                           {"name": "찹쌀", "qty_g": 400},
                           {"name": "미나리", "qty_g": 80}]}
    K.reset()
    for i in rec["ingredients"]:
        K.fridge_add(i["name"], i["qty_g"] + 100)
    r = REGISTRY.get("prep").run(record=rec, weigh=K.prep_weigh,
                                 available=lambda n: K.fridge_check(n) is not None,
                                 absorb_of=absorb_capacity, solid_of=solid_mass)
    o = r.output
    return (f"총 {o['total_mass_g']}g (물 {o['water_added_g']}g 추가) · "
            f"흡수용량 {o['absorb_cap_g']}g · 고형 {o['solid_g']}g")


@case("뚜껑을 덮으면 졸지 않는가")
def t32():
    out = []
    for lid in (False, True):
        K.reset(seed=3)
        K.COOKER.start(620, 0, power=3, capacity_g=1100, lid=lid)
        for _ in range(6):
            K.COOKER.tick(1.0)
        s_ = K.COOKER.state()
        out.append(f"{'덮음' if lid else '엶'} 6분 → {s_['temp_c']}도 "
                   f"비율 {s_['mass_ratio']} (덮으면 증기가 맺혀 돌아와 "
                   f"거의 졸지 않는다)" if lid else
                   f"{'덮음' if lid else '엶'} 6분 → {s_['temp_c']}도 "
                   f"비율 {s_['mass_ratio']}")
        K.COOKER.stop()
    return " / ".join(out)


@case("거품을 걷으면 졸은 것으로 세는가")
def t33():
    K.reset()
    K.COOKER.start(1000, 0, power=3, capacity_g=3000, solid_g=400)
    before = K.COOKER.state()
    e = K.COOKER.skim(45, "거품")
    after = K.COOKER.state()
    K.COOKER.stop()
    return (f"걷기 전 비율 {before['mass_ratio']} → 걷은 뒤 {after['mass_ratio']} "
            f"(분모도 {before['initial_mass_g']}→{after['initial_mass_g']}g 로 줄어 "
            f"졸은 것으로 세지 않는다)")


@case("저으면 눌어붙음이 줄어드는가")
def t34():
    out = []
    for stir in (False, True):
        K.reset(seed=5)
        K.COOKER.start(700, 0, power=3, capacity_g=1100, solid_g=300)
        for i in range(9):
            K.COOKER.tick(1.0)
            if K.COOKER.state()["temp_c"] < 92:
                K.COOKER.set_power(min(4, K.COOKER.power + 1))
            if stir and i % 2 == 1:
                K.COOKER.stir()
        out.append(f"{'저음' if stir else '안 저음'} {K.COOKER.state()['soil_score']}")
        K.COOKER.stop()
    return " / ".join(out)


@case("너무 졸았을 때 물로 되돌리는가")
def t35():
    K.reset()
    K.COOKER.start(620, 0, power=3, capacity_g=1100)
    for _ in range(9):
        K.COOKER.tick(1.0)
        if K.COOKER.state()["temp_c"] < 92:
            K.COOKER.set_power(min(5, K.COOKER.power + 1))
    before = K.COOKER.state()
    need = (0.78 - before["mass_ratio"]) * before["initial_mass_g"]
    # 파이프라인과 같은 상한(6%)을 지킨다 — 넘으면 되돌리지 않는다
    ok_to = need > 0 and need / before["mass_g"] <= 0.06
    e = K.COOKER.add_water(need) if ok_to else None
    after = K.COOKER.state()
    K.COOKER.stop()
    return (f"{before['mass_ratio']} → 필요 {round(need,1)}g → "
            + (f"{after['mass_ratio']} (묽어짐 {e['dilution']:.1%})" if e
               else f"되돌리지 않음 ({need/before['mass_g']:.1%} 묽어져 상한 6% 초과)"))


@case("수명이 지난 재료를 쓰려 하는가")
def t36():
    r = REGISTRY.get("inventory").run(items=[
        {"name": "닭고기", "qty_g": 500, "stored_days": 5, "shelf_life_days": 3},
        {"name": "배추", "qty_g": 300, "stored_days": 6, "shelf_life_days": 7}])
    o = r.output
    return (f"임박 {[u['name'] for u in o['urgent']]} / "
            f"폐기 {[(e['name'], e['days_over']) for e in o['expired']]}")


@case("졸임은 끝났는데 안 익었을 때")
def t37():
    K.reset()
    # 거의 안 졸이는 목표 + 두꺼운 고기 → 익힘이 제약이 된다
    K.COOKER.start(900, 0, power=3, capacity_g=3000, solid_g=700,
                   need_units=1400)
    def lid_hook(st):
        # 파이프라인과 같은 동작: 졸임이 끝났는데 안 익었으면 뚜껑을 덮는다
        if (not st.get("lid") and st.get("doneness", 1.0) < 1.0
                and st["mass_ratio"] <= 0.95):
            K.COOKER.set_lid(True)
            return {"note": "뚜껑을 덮고 뭉근히", "resets_baseline": False,
                    "slow_down": True}
        return None

    r = REGISTRY.get("converge").run(
        observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
        step=K.COOKER.tick, metric="mass_ratio", target=0.95,
        direction="down", ready_key="temp_c", ready_at=92.0,
        max_steps=400, max_minutes=60, on_observe=lid_hook,
        also_require=lambda st: st.get("doneness", 1.0) >= 1.0)
    s_ = K.COOKER.state()
    K.COOKER.stop()
    return (f"ok={r.ok} {r.output['steps']}분 · 질량비 {r.output['final']} · "
            f"익힘 {s_['doneness']} · 유지 {r.output.get('held')}")


@case("양이 많으면 데우는 데 오래 걸리는가")
def t39():
    out = []
    for g in (310, 620, 2000):
        K.reset(seed=4)
        K.COOKER.start(g, 0, power=3, capacity_g=3000)
        n = 0
        while K.COOKER.state()["temp_c"] < 95 and n < 400:
            K.COOKER.tick(0.25)
            if K.COOKER.state()["temp_c"] < 92:
                K.COOKER.set_power(min(5, K.COOKER.power + 1))
            n += 1
        out.append(f"{g}g {n * 0.25:.2f}분")
        K.COOKER.stop()
    return " / ".join(out)


@case("출발 온도 차이가 유지되는가")
def t38():
    out = []
    for t0 in (20.0, 6.0):
        K.reset(seed=4)
        K.COOKER.start(620, 0, power=3, capacity_g=1100, start_temp_c=t0)
        n = 0
        while K.COOKER.state()["temp_c"] < 95 and n < 200:
            K.COOKER.tick(0.25)
            if K.COOKER.state()["temp_c"] < 92:
                K.COOKER.set_power(min(5, K.COOKER.power + 1))
            n += 1
        out.append(f"{t0}도 출발 {n * 0.25:.2f}분")
        K.COOKER.stop()
    return (" / ".join(out)
            + "  ← 열량 수지 모델이라 차이가 남는다. 1차 지연이던 때는 "
              "둘 다 3.75분으로 차이가 지워졌다")


# ═══════════════ 6. 설계 층 스킬의 극단 ═══════════════
@case("시간 예산이 0 인 상황")
def t40():
    import run_design
    pr = {"id": "t", "label": "시험", "household_size": 1, "arrive_home": "20:00",
          "time_budget_min": 0, "avoid": [], "friction_reported": []}
    r = REGISTRY.get("situation_read").run(persona=pr,
                                           stage_costs=run_design.STAGE_COSTS)
    c = r.output["constraints"]
    return (f"조달 생략={c.get('skip_procurement')} 세척포함={c.get('finish_cleanup')} "
            f"예산={c.get('time_budget_min')}")


@case("퇴근 시각은 있는데 이동 시간이 0")
def t41():
    import run_design
    pr = {"id": "t", "label": "시험", "household_size": 1, "arrive_home": "19:00",
          "leave_office": "18:00", "commute_min": 0, "time_budget_min": 30,
          "avoid": [], "friction_reported": []}
    r = REGISTRY.get("situation_read").run(persona=pr,
                                           stage_costs=run_design.STAGE_COSTS)
    c = r.output["constraints"]
    return f"선제주문={c.get('preorder')} (이동 0분이면 미리 받을 수 없다)"


@case("불편이 하나도 없는 고객")
def t42():
    import run_design
    pr = {"id": "t", "label": "시험", "household_size": 1, "arrive_home": "19:00",
          "time_budget_min": 60, "avoid": [], "friction_reported": []}
    sr = REGISTRY.get("situation_read").run(persona=pr,
                                            stage_costs=run_design.STAGE_COSTS)
    r = REGISTRY.get("scenario_draft").run(
        persona=pr, friction=sr.output["friction"],
        constraints=sr.output["constraints"])
    sc = r.output["scenario"]
    return (f"장면 {len(sc['beats'])}개 · 덮은 수고 {sc['covered']}/"
            f"{sc['total_friction']} · ok={r.ok} (덜어낼 것이 없으면 False 여야 한다)")


@case("세척 코스 프로파일을 모르는 기기")
def t43():
    r = REGISTRY.get("aftercare").run(soil_score=0.5, profile="없는기기")
    return f"ok={r.ok} {r.output}"


@case("오염도가 범위를 벗어날 때 (-0.5 / 1.5)")
def t44():
    out = []
    for v in (-0.5, 1.5):
        r = REGISTRY.get("aftercare").run(soil_score=v)
        out.append(f"{v} → {r.output['course']}")
    return " / ".join(out)


@case("자동 주문 상한이 0 원일 때")
def t45():
    r = REGISTRY.get("procure").run(missing=["두부"], lookup=store.make_lookup(),
                                    known_items=["두부"], auto_limit_krw=0)
    return (f"자동 {len(r.output['auto_ordered'])} / "
            f"확인요청 {[a['reason'] for a in r.output['need_confirm']]}")


@case("보관 수명이 0 일 때 (0 으로 나누기)")
def t46():
    r = REGISTRY.get("inventory").run(items=[
        {"name": "무엇", "qty_g": 100, "stored_days": 3, "shelf_life_days": 0}])
    return f"임박 {len(r.output['urgent'])} 폐기 {len(r.output['expired'])}"


@case("기록이 하나도 없을 때 메뉴 고르기")
def t47():
    r = REGISTRY.get("menu").run(records=[], stock=[], resolve=resolve_stock)
    return f"ok={r.ok} best={r.output['best']} 사유={r.output.get('recovery')}"


# ═══════════════ 7. 장보기 어댑터의 극단 ═══════════════
@case("주문까지 되는 상점이 없을 때 (조회만 가능)")
def t48():
    only_view = [store.Store("조회전용", delivery_min=30, catalog={"두부": 2800},
                             can_order=False)]
    r = REGISTRY.get("procure").run(missing=["두부"],
                                    lookup=store.make_lookup(only_view),
                                    known_items=["두부"], deadline_min=60)
    return (f"자동 {len(r.output['auto_ordered'])} / "
            f"사유 {[a['reason'] for a in r.output['need_confirm']]}")


@case("품절이 조회에서 빠지는가")
def t49():
    s_ = store.Store("품절있음", delivery_min=30, catalog=store.BASE_PRICE,
                     out_of_stock=("두부", "대파"), can_order=True)
    lk = store.make_lookup([s_])
    return (f"두부 {len(lk('두부'))}건 / 배추 {len(lk('배추'))}건 "
            f"(품절은 0건이어야 한다)")


@case("취급하지 않는 품목의 최속 배송")
def t50():
    return (f"찹쌀 {store.min_delivery_min(item='찹쌀')}분 / "
            f"두부 {store.min_delivery_min(item='두부')}분 / "
            f"품목 무시 {store.min_delivery_min()}분")


@case("가격표에 없는 품목")
def t51():
    r = REGISTRY.get("procure").run(missing=["트러플"], lookup=store.make_lookup(),
                                    known_items=["트러플"])
    return f"확인요청 {[a['reason'] for a in r.output['need_confirm']]}"


@case("같은 메뉴를 세 번 — 조리량이 부풀지 않는가")
def t52():
    K.reset()
    rec = dict(K.RECORDS["rec_001"])
    sizes = []
    for _ in range(3):
        scaled = K.record_scale(rec, 4, "본가 6인용 조리기")
        sizes.append(scaled["initial_mass_g"])
        saved = K.record_save(scaled, {"final_ratio": 0.78, "cook_min": 10},
                              actual_initial_g=scaled["initial_mass_g"])
        rec = saved
    return f"{sizes} (같아야 한다 — 저장에 인분 수가 남아야)"

# ═══════════════ 8. 요리 물리의 모순 ═══════════════
@case("찬물을 부으면 온도가 떨어지는가")
def t53():
    K.reset()
    K.COOKER.start(500, 0, power=3, capacity_g=1100)
    for _ in range(8):
        K.COOKER.tick(1.0)
    b = K.COOKER.state()["temp_c"]
    e = K.COOKER.add_water(50)
    K.COOKER.stop()
    return (f"{b}도 → {e['temp_drop_c']}도 하강 "
            f"(0 이면 물리 모순)")


@case("재료를 넣어 냄비를 넘기면 알리는가")
def t54():
    K.reset()
    K.COOKER.start(900, 0, power=3, capacity_g=1000)
    e = K.COOKER.add_ingredient("두부", 300)
    K.COOKER.stop()
    return f"채움 {e['fill_ratio']} 초과={e['overfilled']} · {e.get('warning', '')[:44]}"


@case("늦게 넣은 재료는 그때부터 익는가")
def t55():
    K.reset()
    K.COOKER.start(400, 0, power=3, capacity_g=1100, need_units=200)
    for _ in range(4):
        K.COOKER.tick(1.0)
    d1 = K.COOKER.state()["doneness"]
    K.COOKER.add_ingredient("닭고기", 300, need_units=300)
    d2 = K.COOKER.state()["doneness"]
    K.COOKER.stop()
    return f"투입 전 {d1:.3f} → 후 {d2:.3f} (내려가야 한다)"


@case("뚜껑을 덮으면 더 잘 넘치는가")
def t56():
    out = []
    for lid in (False, True):
        K.reset(seed=3)
        K.COOKER.start(900, 0, power=3, capacity_g=1100, lid=lid)
        while K.COOKER.state()["temp_c"] < 99.9 and K.COOKER.elapsed_min < 30:
            K.COOKER.tick(0.5)
        K.COOKER.set_power(5)
        K.COOKER.tick(1.0)
        out.append(f"{'덮음' if lid else '엶'} {K.COOKER.state()['overflow_risk']}")
        K.COOKER.stop()
    return " / ".join(out)


@case("거품을 걷으면 고형분에서 빠지는가")
def t57():
    K.reset()
    K.COOKER.start(1000, 0, power=3, capacity_g=3000, solid_g=400)
    b = K.COOKER.state()["free_liquid_g"]
    K.COOKER.skim(50)
    a = K.COOKER.state()["free_liquid_g"]
    solid = K.COOKER.solid_g
    K.COOKER.stop()
    return (f"고형분 400 → {solid:.0f} · 자유수분 {b} → {a} "
            f"(국물이 거의 안 줄어야 한다)")


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("t") and callable(v) and hasattr(v, "_name")]
    for t in tests:
        t()
    print("=" * 84)
    print("스트레스 시험 — 일부러 망가뜨린 입력")
    print("=" * 84)
    bad = 0
    for status, name, msg in RESULT:
        mark = "  OK  " if status == "OK" else "  !!  "
        if status != "OK":
            bad += 1
        print(f"{mark}{name}")
        if msg:
            print(f"        {msg}")
    print("-" * 84)
    print(f"  {len(RESULT)}건 중 예외 {bad}건")
    print("  (OK 는 '예외 없이 끝났다' 는 뜻이지 '결과가 옳다' 는 뜻이 아니다 —")
    print("   각 줄의 값을 직접 읽어야 한다)")


if __name__ == "__main__":
    main()
