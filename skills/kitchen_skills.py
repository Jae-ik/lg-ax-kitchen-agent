# -*- coding: utf-8 -*-
"""주방 도메인 스킬 3종.

inventory : 소진 임박 항목 탐색  (냉장고·팬트리·냉동고 공용)
menu      : 재고 × 저장 기록 → 실행 후보
procure   : 부족분 조달 — 자동 주문 상한과 안전 필터를 지킨다
"""
from __future__ import annotations
from .base import Skill, SkillResult


class InventorySkill(Skill):
    name = "inventory"
    description = ("보관 중인 항목에서 곧 소진해야 할 것을 찾는다. 사용자가 묻지 않아도 "
                   "이 결과가 에이전트의 목표(GOAL)를 만든다.")
    input_schema = {"items": "list[dict]  name/stored_days/shelf_life_days",
                    "urgency_ratio": "float  경과/수명 이 값 이상이면 임박"}
    reusable_for = ["냉장고", "팬트리", "냉동고", "화장품·의약품 유효기간"]
    provides = ("urgent_items",)

    def run(self, items: list, urgency_ratio: float = 0.6, **_) -> SkillResult:
        scored, ev = [], []
        for i in items:
            life = max(1, i.get("shelf_life_days", 7))
            r = i.get("stored_days", 0) / life
            left = life - i.get("stored_days", 0)
            if r >= urgency_ratio:
                scored.append({**i, "urgency": round(r, 2), "days_left": left})
                ev.append(f"{i['name']} {i['stored_days']}/{life}일 "
                          f"(소진율 {r:.0%}, 잔여 {left}일)")
            else:
                ev.append(f"{i['name']} {i['stored_days']}/{life}일 (소진율 {r:.0%}) — 여유")
        scored.sort(key=lambda x: -x["urgency"])
        return SkillResult(bool(scored), {"urgent": scored, "count": len(scored)}, ev)


class MenuSkill(Skill):
    name = "menu"
    description = ("재고와 저장된 조리 기록을 대조해 실행 가능한 후보를 만든다. "
                   "임박 재료를 쓰는 기록을 우선한다.")
    input_schema = {"records": "list[dict]", "stock": "list[dict]",
                    "prefer_items": "list[str]  우선 소진할 재료",
                    "require_complete": "bool  재고만으로 가능한 후보로 제한",
                    "avoid": "list[str]  들어가면 안 되는 재료"}
    reusable_for = ["조리 기록", "세탁 코스 기록", "청소 루틴 기록"]
    requires = ("urgent_items",)
    provides = ("chosen_record", "missing_items")

    def run(self, records: list, stock: list, prefer_items: list | None = None,
            require_complete: bool = False, avoid: list | None = None,
            resolve=None, **_) -> SkillResult:
        have = {s["name"]: s for s in stock}
        prefer = set(prefer_items or [])
        avoid = set(avoid or [])

        # 재고 품목명과 자료의 재료명은 같은 것을 다르게 부른다(두부/연두부).
        # 어떤 이름이 같은 것을 가리키는지는 도메인이 안다. 함수로 주입받는다.
        def held(ing_name):
            if resolve:
                return resolve(ing_name, have)
            return ing_name if ing_name in have else None

        out, ev, dropped = [], [], []
        for r in records:
            need = r.get("ingredients", [])
            names = [i["name"] for i in need]
            missing = [n for n in names if held(n) is None]
            uses = [n for n in names if (held(n) or "") in prefer]

            # 못 먹는 재료가 들어간 기록은 후보에서 뺀다. 조달 단계에도 같은
            # 필터가 있지만, 거기까지 가면 사용자가 확인을 받게 된다.
            # 확인을 받지 않는 것이 목표이므로 고르는 단계에서 먼저 거른다.
            blocked = [n for n in names if n in avoid]
            if blocked:
                dropped.append(r["record_id"])
                ev.append(f"{r['record_id']}({r['menu']}) 제외 — 기피·알레르기 "
                          f"재료 포함: {', '.join(blocked)}")
                continue

            # 재고만으로 끝내야 하는 상황이면 부족분이 있는 후보를 아예 뺀다.
            # 조달을 생략하기로 한 것은 상황 판단이고, 그 판단이 여기까지 전해진다.
            if require_complete and missing:
                dropped.append(r["record_id"])
                ev.append(f"{r['record_id']}({r['menu']}) 제외 — 재고만으로 불가"
                          f" (부족 {', '.join(missing)})")
                continue

            score = len(uses) * 10 - len(missing) * 3 + r.get("satisfaction", 0)
            out.append({"record_id": r["record_id"], "menu": r["menu"],
                        "saved_by": r.get("saved_by"), "missing": missing,
                        "uses_urgent": uses, "blocked": [], "score": score})
            ev.append(f"{r['record_id']}({r.get('saved_by')}) 임박재료 {len(uses)}개, "
                      f"부족 {len(missing)}개 → 점수 {score}")
        out.sort(key=lambda x: -x["score"])
        return SkillResult(bool(out), {"candidates": out, "dropped": dropped,
                                       "best": out[0] if out else None}, ev)


class PrepSkill(Skill):
    name = "prep"
    description = ("조리 기록이 지정한 재료를 계량하고, 재료 상태에서 나올 추가 수분을 "
                   "미리 계산한다. 이 값을 넘겨주지 않으면 조리 단계는 같은 목표를 "
                   "다른 출발점에서 쫓게 된다.")
    input_schema = {"record": "dict  조리 기록",
                    "weigh": "(name, qty_g) -> dict   계량 함수. 주입받는다",
                    "available": "(name) -> bool      재고 확인 함수. 주입받는다"}
    reusable_for = ["조리 전 계량", "세제 투입량 산정", "정수량 배분"]
    requires = ("stock_complete", "chosen_record")
    provides = ("measured",)

    def run(self, record: dict, weigh, available, **_) -> SkillResult:
        total, extra, ev, missing = 0.0, 0.0, [], []
        for ing in record.get("ingredients", []):
            if not available(ing["name"]):
                missing.append(ing["name"])
                ev.append(f"{ing['name']}: 재고 없음 — 계량 불가")
                continue
            w = weigh(ing["name"], ing["qty_g"])
            if not w.get("ok"):
                missing.append(ing["name"])
                ev.append(f"{ing['name']}: {w.get('reason')}")
                continue
            total += w["actual_g"]
            extra += w["expected_extra_water_g"]
            ev.append(f"{ing['name']} 목표 {w['target_g']}g → 실계량 {w['actual_g']}g"
                      + (f", 추가 수분 {w['expected_extra_water_g']}g 예상"
                         if w["expected_extra_water_g"] else ""))

        # 기록에 없는 나머지(국물 등)는 기록된 초기 질량으로 맞춘다
        listed = sum(i["qty_g"] for i in record.get("ingredients", []))
        total += record.get("initial_mass_g", 0) - listed
        ev.append(f"총 {round(total)}g (기록 {record.get('initial_mass_g')}g), "
                  f"추가 수분 합 {round(extra, 1)}g")
        return SkillResult(not missing,
                           {"total_mass_g": round(total, 1),
                            "extra_water_g": round(extra, 1),
                            "missing": missing}, ev)


class ProcureSkill(Skill):
    name = "procure"
    description = ("부족한 항목을 조달한다. 되돌릴 수 없는 행동이므로 상한을 지킨다. "
                   "상한을 넘거나 처음 사는 품목이거나 안전 필터에 걸리면 "
                   "자동으로 주문하지 않고 사용자 확인을 요청한다.")
    input_schema = {"missing": "list[str]", "catalog": "dict  품목→가격",
                    "known_items": "list[str]  이전에 산 적 있는 품목",
                    "avoid": "list[str]  알레르기·기피 품목",
                    "auto_limit_krw": "int  1회 자동 주문 상한"}
    reusable_for = ["식재료 조달", "세제·소모품 재주문", "필터·부품 교체"]
    requires = ("missing_items",)
    provides = ("stock_complete",)

    def run(self, missing: list, catalog: dict, known_items: list | None = None,
            avoid: list | None = None, auto_limit_krw: int = 15000, **_) -> SkillResult:
        known = set(known_items or [])
        avoid = set(avoid or [])
        auto, ask, ev = [], [], []
        for name in missing:
            price = catalog.get(name)
            if price is None:
                ask.append({"name": name, "reason": "취급하지 않는 품목"}); continue
            if name in avoid:
                ask.append({"name": name, "reason": "알레르기·기피 목록에 있음"})
                ev.append(f"{name}: 안전 필터에 걸려 자동 주문 보류"); continue
            if name not in known:
                ask.append({"name": name, "reason": "처음 구매하는 품목"})
                ev.append(f"{name}: 구매 이력 없음 → 확인 요청"); continue
            if price > auto_limit_krw:
                ask.append({"name": name, "reason": f"{price:,}원 > 상한 {auto_limit_krw:,}원"})
                ev.append(f"{name}: 금액 상한 초과 → 확인 요청"); continue
            auto.append({"name": name, "price_krw": price})
            ev.append(f"{name}: {price:,}원 — 이력 있고 상한 이내 → 자동 주문")
        total = sum(a["price_krw"] for a in auto)
        return SkillResult(True, {"auto_ordered": auto, "need_confirm": ask,
                                  "total_krw": total}, ev or ["조달할 항목 없음"])
