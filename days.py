# -*- coding: utf-8 -*-
"""장부가 날을 넘긴다 — 어제 산 두부 한 모의 남은 양을 오늘 안다.

왜 필요한가:
  메인 가구는 "장보기를 에이전트로 해 와서 주문 기록(장부)으로 냉장고를 안다" 고
  두었다(2026-09-30 결정). 그런데 실행마다 처음 상태에서 시작해, **장부가 하루를
  넘기지 못했다** — 그 가정을 보여 줄 수 없었다(2026-10-01).

  하루가 끝나면 냉장고(산 것·쓴 양·출처·상비품)와 냄비 기록을 남기고, 다음 날은
  보관일을 하루 늘린 그 장부에서 시작한다.

  비교 — **실제 냉장고는 어느 쪽이든 줄어든다.**
    장부를 잇는다   에이전트가 믿는 재고 = 어제 끝난 장부(= 실제)
    장부 없음       에이전트는 매일 첫날 목록을 믿는다. 실제와 다른 것은 계량 때
                   (집에서) 드러나거나, 있는 것을 또 산다(belief_gap 과 같은 틀)
  처음엔 장부 없음도 실제가 매일 첫날로 돌아가게 둬서, 배추가 매일 320g 으로 다시
  생기는 바람에 "장부 없는 쪽이 싸다" 가 나왔다 — 비교가 틀렸다.

    python days.py [가구] [날수]      → days.json
"""
from __future__ import annotations
import contextlib
import io
import json
import sys

import kitchen as K
import personas
import run_design
import store
from kitchen_domain import PANTRY
from orchestrator import Trace


def _state() -> dict:
    """지금 냉장고(실제) — 다 쓴 것은 지운다."""
    return {x["name"]: dict(x) for x in K.fridge_list_items() if (x.get("qty_g") or 0) > 0}


def _age(state: dict) -> dict:
    """하루가 지났다 — 보관일을 하루 늘린다(모르는 날짜는 그대로 모른다)."""
    out = {}
    for n, x in state.items():
        y = dict(x)
        if y.get("stored_days") is not None:
            y["stored_days"] += 1
        out[n] = y
    return out


def run_days(pid: str, n: int = 3, carry: bool = True) -> list:
    base = personas.get(pid)
    first = {x["name"]: dict(x) for x in base["fridge"]}
    real = None                       # 실제 냉장고(상비품 포함) — 어느 쪽이든 줄어든다
    cookware = base.get("cookware_history")
    out = []
    for day in range(1, n + 1):
        p = dict(base)
        reality = None
        have = dict(real or first)        # 오늘 시작할 때의 실제(첫날은 목록 = 실제)
        if real is not None:
            if carry:
                # 장부: 믿는 재고 = 실제
                p["fridge"] = [x for nm, x in real.items() if nm not in PANTRY]
                p["pantry_low"] = {nm: x["qty_g"] for nm, x in real.items() if nm in PANTRY}
            else:
                # 장부 없음: 첫날 목록을 믿는다. 실제와 다른 것은 실제(reality)로 둔다
                p["fridge"] = list(first.values())
                reality = {}
                for nm, x in first.items():
                    r = real.get(nm)
                    reality[nm] = ({"absent": True} if r is None else
                                   {"qty_g": r["qty_g"], "stored_days": r.get("stored_days")})
                # 상비품은 첫날 설정(참기름 바닥)을 그대로 믿는다 — 어제 산 것을 모른다
            p["cookware_history"] = cookware
        bought, touched, used = [], [], {}
        orig, orig_w = K.fridge_add, K.prep_weigh

        def spy(name, qty, *a, **k):
            bought.append(name)
            return orig(name, qty, *a, **k)

        stale = []

        def spy_w(name, *a, **k):
            touched.append(name)
            K._apply_reality(name)              # 저울에 올린 그것의 실제 날짜
            it = K.fridge_check(name) or {}
            r_ = orig_w(name, *a, **k)
            used[name] = used.get(name, 0) + (r_.get("actual_g") or 0)
            sd, sl = it.get("stored_days"), it.get("shelf_life_days")
            if r_.get("actual_g") and sd is not None and sl and sd >= sl:
                stale.append(name)
            return r_
        tmp = f"_day_{pid}"
        personas.PERSONAS[tmp] = dict(p, id=tmp)
        K.fridge_add, K.prep_weigh = spy, spy_w
        K.set_reality(reality)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r = run_design.design_for(tmp, Trace(), seed=7 + day,
                                          keep_records=day > 1)
        finally:
            K.fridge_add, K.prep_weigh = orig, orig_w
            K.set_reality(None)
            del personas.PERSONAS[tmp]
        m, v = r["verify"]["metrics"], r["verify"]
        end = _state()
        if not carry and real is not None:
            # 장부 없음: 계량하거나 산 것은 냉장고 상태에 실제가 반영됐다. 손대지 않은
            # 것은 믿음(첫날 값)이 남아 있으므로, 실제는 어제 실제 그대로다.
            moved = set(touched) | set(bought)
            fixed = {}
            for nm in set(end) | set(real):
                x = end.get(nm) if nm in moved else real.get(nm)
                if x and (x.get("qty_g") or 0) > 0:
                    fixed[nm] = dict(x)
            end = fixed
        real = _age(end)
        cookware = [dict(c) for c in K.COOKWARE]
        got = sorted(set(bought))
        # 금액: 자동 주문뿐 아니라 승인해서 산 것까지 — 기준가 × 1개(추정)
        cost = sum(store.BASE_PRICE.get(b, 0) for b in got)
        # 실제로 넉넉히(100g 이상, 가정) 있었는데 또 산 것
        # 실제로 이미 있었는데 또 산 것 — 그날 쓴 양만큼 이미 있었으면 불필요했다
        # (처음엔 "100g 이상 있었으면" 으로 세어, 123g 있는데 200g 이 필요해 산 배추를
        #  불필요로 셌다)
        def usable(it):                     # 수명 끝(보관일 ≥ 수명)은 있던 것으로 치지 않는다
            sd, sl = it.get("stored_days"), it.get("shelf_life_days")
            return not (sd is not None and sl and sd >= sl)
        extra = [b for b in got if usable(have.get(b) or {})
                 and (have.get(b) or {}).get("qty_g", 0) >= max(1, used.get(b, 0))]
        out.append({"day": day, "verified": v["verified"], "menu": m.get("메뉴"),
                    "bought": got, "order_krw": cost, "extra_bought": extra,
                    "stale_used": sorted(set(stale)),
                    "meal_min": m.get("식사까지(분)"), "halted": m.get("중단"),
                    "found_home": m.get("다시 짠 곳"),
                    "stock_after": {nm: round(x["qty_g"]) for nm, x in real.items()
                                    if nm not in PANTRY}})
    return out


def summary(days: list) -> str:
    """표 한 칸: 성립 일수 · 주문 합계(추정) · 있는데 또 산 횟수 · 집에서 안 날."""
    n = len(days)
    ok = sum(d["verified"] for d in days)
    krw = sum(d["order_krw"] for d in days)
    extra = sum(len(d["extra_bought"]) for d in days)
    home = sum(1 for d in days if d["found_home"])
    stale = sum(1 for d in days if d["stale_used"])
    return (f"성립 {ok}/{n} · {krw:,}원 · 또 산 것 {extra}"
            + (f" · 집에서 안 날 {home}" if home else "")
            + (f" · **수명 끝 재료 쓴 날 {stale}**" if stale else ""))


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    res = {}
    for pid in personas.ids():
        res[pid] = {"carry": run_days(pid, n, True), "fresh": run_days(pid, n, False)}
    with open("days.json", "w", encoding="utf-8") as f:
        json.dump({"days": n, "results": res,
                   "summary": {pid: {k: summary(v[k]) for k in v} for pid, v in res.items()}},
                  f, ensure_ascii=False, indent=1)
    for pid, v in res.items():
        print(f"\n{pid}")
        for k, title in (("carry", "장부"), ("fresh", "장부 없음")):
            print(f"  {title:6} {summary(v[k])}")
            for d in v[k]:
                state = ("성립" if d["verified"] else "실패") + (" · 집에서 다시 짬" if d["found_home"] else "")
                print(f"     {d['day']}일차 [{state}] {d['menu']} {d['meal_min']}분 · 산 것 "
                      f"{d['bought'] or '없음'}" + (f" (또 산 것 {d['extra_bought']})" if d["extra_bought"] else ""))
    print("\n  days.json 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
