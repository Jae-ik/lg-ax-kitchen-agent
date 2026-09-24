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
