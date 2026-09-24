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
        ready_key="temp_c", ready_at=92.0, max_steps=30)
    K.COOKER.stop()
    return f"초기 {total}g → {r.output['steps']}분 → {r.output['final']}"


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
            max_steps=40, **kw)
        K.COOKER.stop()
        out.append(f"{'여열O' if use else '여열X'} {r.output['final']} "
                   f"(지나침 {abs(0.78 - r.output['final']):.4f})")
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


@case("뚜껑을 덮으면 빨리 끓고 졸지 않는가")
def t32():
    out = []
    for lid in (False, True):
        K.reset(seed=3)
        K.COOKER.start(620, 0, power=3, capacity_g=1100, lid=lid)
        for _ in range(6):
            K.COOKER.tick(1.0)
        s_ = K.COOKER.state()
        out.append(f"{'덮음' if lid else '엶'} 6분 → {s_['temp_c']}도 "
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
    e = K.COOKER.add_water(need) if need > 0 else None
    after = K.COOKER.state()
    K.COOKER.stop()
    return (f"{before['mass_ratio']} → 물 {round(need,1)}g → {after['mass_ratio']} "
            f"(묽어짐 {e['dilution'] if e else 0:.1%})")


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
