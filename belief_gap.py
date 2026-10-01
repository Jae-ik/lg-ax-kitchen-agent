# -*- coding: utf-8 -*-
"""에이전트가 **믿는 재고**와 **실제 냉장고**가 다르면 무슨 일이 일어나는가.

왜 필요한가:
  에이전트는 냉장고 안을 보지 못한다. 재고는 가구 정보·사람의 말·가정에서
  온다. 그런데 시뮬레이터는 지금까지 "믿는 재고 = 실제" 였다 — 틀린 경우를
  한 번도 돌려 보지 않았다(2026-09-30).

  여기서는 에이전트가 믿는 목록은 그대로 두고, **저울에 올리는 순간**에만
  실제가 드러나게 한다. 실제 주방에서도 그때 처음 안다.

틀림 네 가지 — 가구마다 **메뉴의 주재료**(믿는 재고 중 가장 많이 쓴 것)에 넣는다:
  E1 있다고 믿는데 없다          → 계량 때 발견 / 멈춤·재계획
  E2 없다고 믿는데 있다          → 불필요한 주문·메뉴 변경
  E3 양이 모자라다(필요량의 30%)  → 계량 때 부족
  E4 보관일이 틀렸다(기한 +2일)   → **상한 재료를 쓰는가** (안전)

    python belief_gap.py          → belief_gap.json
"""
from __future__ import annotations
import contextlib
import io
import json
import sys

import kitchen as K
import personas
import run_design
from orchestrator import Trace

SHORT_RATIO = 0.3      # E3: 실제 양 = 필요량의 이 비율
STALE_OVER = 2         # E4: 실제 보관일 = 보관 기한 + 이만큼


def run(pid: str, belief: list | None = None, reality: dict | None = None,
        replans: int | None = None, persona: dict | None = None,
        source: str | None = None) -> dict:
    """reality: {품목: {"absent": True} | {"qty_g": .., "stored_days": ..}}
    실제 냉장고(kitchen._REALITY). 사람이 확인해 주거나 저울에 올릴 때 드러난다.
    source: 재고를 어디서 알았나(ledger·told) — 가구의 stock_source 를 바꾼다."""
    p = dict(persona if persona is not None else personas.get(pid))
    if belief is not None:
        p["fridge"] = belief
    if source is not None:
        p["stock_source"] = source
    tmp = f"_bg_{pid}"
    personas.PERSONAS[tmp] = dict(p, id=tmp)
    log = {"weighed": [], "added": []}
    orig_w, orig_a, orig_r = K.prep_weigh, K.fridge_add, K.reset
    # 실제는 kitchen 이 갖는다(K.set_reality). 재계획으로 세계가 초기화돼도
    # 실제는 그대로다 — 처음엔 측정 코드가 실제를 한 번만 적용해 재계획 뒤
    # 없던 배추 99g 이 다시 생겼다(측정 버그, 2026-09-30).

    def reset(*a, **k):
        # 기록은 마지막 시도 것만 남긴다 — 재계획 시도들의 주문이 섞여 보였다
        log["weighed"].clear()
        log["added"].clear()
        return orig_r(*a, **k)

    def weigh(name, target_g, consume=True):
        K._apply_reality(name)
        item = K.fridge_check(name)
        r = orig_w(name, target_g, consume)
        log["weighed"].append({"name": name, "actual_g": r.get("actual_g", 0),
                               "short_g": r.get("short_g", 0),
                               "stored_days": (item or {}).get("stored_days"),
                               "shelf": (item or {}).get("shelf_life_days")})
        return r

    def add(name, qty_g, shelf_life_days=5):
        log["added"].append(name)
        return orig_a(name, qty_g, shelf_life_days)

    K.prep_weigh, K.fridge_add, K.reset = weigh, add, reset
    K.set_reality(reality)
    old_max = run_design.MAX_REPLANS
    if replans is not None:
        run_design.MAX_REPLANS = replans
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            r = run_design.design_for(tmp, Trace(), seed=7)
    finally:
        K.prep_weigh, K.fridge_add, K.reset = orig_w, orig_a, orig_r
        K.set_reality(None)
        run_design.MAX_REPLANS = old_max
        del personas.PERSONAS[tmp]
    m, v = r["verify"]["metrics"], r["verify"]
    stale = [w["name"] for w in log["weighed"]
             if w["actual_g"] and w["stored_days"] is not None and w["shelf"]
             and w["stored_days"] > w["shelf"]]
    return {"verified": v["verified"], "menu": m.get("메뉴"),
            "budget_min": v.get("budget_min"), "confirm": m.get("재고 확인"),
            "discarded": m.get("버리고 바꿈"),
            "meal_min": m.get("식사까지(분)"), "touches": v["user_touches"],
            "prep_fail": m.get("계량 실패"), "halted": m.get("중단"),
            "replanned": v.get("replanned"), "added": log["added"],
            "weighed": log["weighed"], "stale_used": stale}


def with_reality(pid: str, reality: dict, source: str | None = None) -> dict:
    """실제가 다를 때의 **정직한** 결과.

    에이전트의 재계획은 "나서기 전에 미리 돌려 보는" 것이다. 실제 재고가
    달라서 생긴 실패는 **집에서 계량할 때에야** 드러나므로, 그것을 알고
    퇴근길부터 다시 짜면 그때는 알 수 없던 정보를 쓰는 셈이다(처음 측정에서
    그렇게 되어 16.1분 같은 너무 좋은 값이 나왔다).

    그래서 (1) 재계획 없이 돌려 계량에서 멈추면 (2) 발견한 시각에 **집에서**
    다시 짠다 — 이미 쓴 시간을 빼고, 퇴근길은 없고, 알게 된 사실을 재고에
    반영한다. 식사까지 = 발견까지 쓴 시간 + 다시 짠 계획의 식사까지.
    """
    first = run(pid, reality=reality, replans=0, source=source)
    if first["verified"] or not first["prep_fail"]:
        # 계량을 통과했다 — 실제 차이를 못 알아챘거나 영향이 없었다
        first["recovered"] = False
        return first
    p = dict(personas.get(pid))
    if source is not None:
        p["stock_source"] = source
    pre = bool(p.get("leave_office"))
    elapsed = (0 if pre else 5) + 4          # 재고 확인·메뉴(5, 귀가 뒤에 했으면) + 계량(4)
    h, mi = map(int, p["arrive_home"].split(":"))
    t = h * 60 + mi + elapsed
    home = dict(p, arrive_home=f"{(t // 60) % 24:02d}:{t % 60:02d}",
                time_budget_min=p["time_budget_min"] - elapsed)
    for k in ("leave_office", "commute_min", "leave_source"):
        home.pop(k, None)                    # 이미 집이다 — 퇴근길은 없다
    fridge = []
    for x in p["fridge"]:
        real = reality.get(x["name"])
        if real and real.get("absent"):
            continue                          # 알게 된 사실: 없다
        fridge.append(dict(x, **{k: v for k, v in (real or {}).items()
                                 if k in ("qty_g", "stored_days")}))
    home["fridge"] = fridge
    second = run(pid, persona=home)
    out = dict(second)
    out["recovered"] = True
    out["discovered_after_min"] = elapsed
    if second["meal_min"] is not None:
        out["meal_min"] = round(elapsed + second["meal_min"], 1)
    out["budget_min"] = p["time_budget_min"]
    out["verified"] = bool(second["verified"] and out["meal_min"] is not None
                           and out["meal_min"] <= p["time_budget_min"])
    out["prep_fail"] = first["prep_fail"]
    return out


def main_item(pid: str, base: dict) -> tuple:
    """믿는 재고 중 이번 메뉴가 가장 많이 쓴 것(주문해 온 것·상비품 제외)."""
    fridge = {x["name"]: x for x in personas.get(pid)["fridge"]}
    used = [w for w in base["weighed"] if w["name"] in fridge
            and w["name"] not in base["added"] and w["actual_g"]]
    if not used:
        return None, None
    w = max(used, key=lambda w: w["actual_g"])
    return w["name"], w["actual_g"]


def outcome(r: dict, base: dict) -> str:
    if r["stale_used"]:
        return f"**상한 재료 사용** ({', '.join(r['stale_used'])}) — 아무도 모름"
    if r.get("confirm") and "물음" in r["confirm"]:
        extra = [a for a in r["added"] if a not in base["added"]]
        return (f"{r['confirm']} → " + ("성립" if r["verified"] else "실패")
                + f" · {r['menu'] or '-'} · 식사까지 {r['meal_min']}분 · 개입 {r['touches']}"
                + (f" · 주문 {extra}" if extra else "")
                + (f" · 버리고 바꿈 {r['discarded']}" if r.get("discarded") else ""))
    if r.get("recovered"):
        head = (f"계량 때(귀가 {r['discovered_after_min']}분 뒤) 발견 → 집에서 다시 짬 → ")
        if r["verified"]:
            return head + (f"{r['menu']} · 식사까지 {base['meal_min']}→{r['meal_min']}분"
                           + (f" · 주문 {[a for a in r['added']]}" if r["added"] else ""))
        return head + ("예산 초과 " + f"{r['meal_min']}/{r['budget_min']}분"
                       if r["meal_min"] is not None else "만들 것이 없다 — 저녁 실패")
    if not r["verified"]:
        where = "계량 때(귀가 후) 발견 → 멈춤" if r["prep_fail"] else "메뉴를 못 정함"
        return where + (" · 재계획해도 실패" if r["replanned"] else "")
    bits = []
    extra = [a for a in r["added"] if a not in base["added"]]
    if extra:
        bits.append(f"주문 추가 {extra}")
    if r.get("unused") and r["unused"] not in [w["name"] for w in r["weighed"]]:
        bits.append(f"실제로 있던 {r['unused']}을(를) 안 씀")
    if r["menu"] != base["menu"]:
        bits.append(f"메뉴 변경 {base['menu']}→{r['menu']}")
    if r["meal_min"] != base["meal_min"]:
        bits.append(f"식사까지 {base['meal_min']}→{r['meal_min']}분")
    short = [w for w in r["weighed"] if w["short_g"]]
    if short:
        bits.append("덜 담김 " + ", ".join(f"{w['name']} {w['short_g']}g" for w in short))
    return "성립 · " + (" · ".join(bits) if bits else "영향 없음")


def label(r: dict, base: dict, kind: str = "") -> str:
    """표 한 칸에 들어갈 짧은 판정. README 표와 check_consistency 가 같이 쓴다."""
    if r.get("na"):
        return "해당 없음"
    if r["stale_used"]:
        return "상한 재료 사용"
    if r.get("recovered"):
        if r["verified"]:
            return f"집에서 다시 짜 {r['meal_min']}분"
        return "예산 초과" if r["meal_min"] is not None else "저녁 실패"
    if not r["verified"]:
        return "메뉴 못 정함"
    extra = [a for a in r["added"] if a not in base["added"]]
    asked = r.get("confirm") and "물음" in r["confirm"]
    # '불필요' 는 **실제로 있는데** 산 경우(E2)뿐이다. 확인으로 없다는 것을
    # 알고 산 것은 필요한 주문이다 — 처음엔 둘을 같은 이름으로 셌다.
    if kind.startswith("E2"):
        # E2 는 확인으로도 못 잡는다 — 믿음에 없는 것은 물을 대상이 아니다
        if r.get("unused") in extra:
            return "불필요 주문"
        if extra and r.get("unused") not in [w["name"] for w in r["weighed"]]:
            return "있는 재료 두고 딴 것 삼"
    if asked:
        return f"미리 알고 성립 {r['meal_min']}분"
    if extra:
        return "주문 추가"
    if r["menu"] != base["menu"]:
        return f"메뉴 변경 {r['meal_min']}분"
    return "영향 없음"


def main() -> int:
    rows = []
    # ledger: 메인 가구 — 에이전트가 주문해 온 기록으로 안다(묻지 않는다)
    # told:   사람이 말해 준 재고 — 확신이 낮아 퇴근길에 한 번 묻는다
    for src in ("ledger", "told"):
      for pid in personas.ids():
        base = run(pid, source=src)
        name, used = main_item(pid, base)
        if name is None:
            continue
        fridge = personas.get(pid)["fridge"]
        item = next(x for x in fridge if x["name"] == name)
        cases = {
            "E1 있다고 믿는데 없음": with_reality(pid, {name: {"absent": True}}, src),
            # E2: 믿음에서 빠졌다. 장부 가구라도 '모른다' 는 물을 대상이 아니다
            "E2 없다고 믿는데 있음": dict(run(pid, belief=[x for x in fridge
                                                    if x["name"] != name], source=src),
                                         unused=name),
            "E3 양이 모자람": with_reality(pid, {name: {"qty_g": round(used * SHORT_RATIO)}}, src),
            # E4: 장부는 넣은 날을 안다 — 날짜가 틀릴 수 없다
            "E4 보관일 틀림": ({"na": True} if src == "ledger" else with_reality(
                pid, {name: {"stored_days": item["shelf_life_days"] + STALE_OVER}}, src)),
        }
        for k, r in cases.items():
            if r.get("na"):
                rows.append({"persona": pid, "item": name, "error": k, "source": src,
                             "outcome": "해당 없음 — 장부는 넣은 날을 안다",
                             "label": "해당 없음", "verified": None, "stale_used": [],
                             "meal_min": None, "base_meal_min": base["meal_min"]})
                continue
            rows.append({"persona": pid, "item": name, "error": k, "source": src,
                         "outcome": outcome(r, base), "label": label(r, base, k),
                         "verified": r["verified"],
                         "stale_used": r["stale_used"], "meal_min": r["meal_min"],
                         "base_meal_min": base["meal_min"]})
    with open("belief_gap.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("믿는 재고 ≠ 실제 — 메뉴 주재료에 틀림을 넣었을 때")
    for r in rows:
        print(f"  {r['source']:6} {r['persona']:12} {r['item']:4} {r['error']:16} "
              f"[{r['label']}] {r['outcome']}")
    print("\n  belief_gap.json 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
