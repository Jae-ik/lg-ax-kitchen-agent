# -*- coding: utf-8 -*-
"""주방 도메인 스킬 3종.

inventory : 소진 임박 항목 탐색  (냉장고·팬트리·냉동고 공용)
menu      : 재고 × 저장 기록 → 실행 후보
procure   : 부족분 조달 — 자동 주문 상한과 안전 필터를 지킨다
"""
from __future__ import annotations
from .base import Skill, SkillResult

FRIDGE_TEMP_C = 4.0        # 냉장 보관 온도
ROOM_TEMP_C = 20.0         # 물·상온 재료


class InventorySkill(Skill):
    name = "inventory"
    description = ("보관 중인 항목에서 곧 소진해야 할 것을 찾는다. 사용자가 묻지 않아도 "
                   "이 결과가 에이전트의 목표(GOAL)를 만든다.")
    input_schema = {"items": "list[dict]  name/stored_days/shelf_life_days",
                    "urgency_ratio": "float  경과/수명 이 값 이상이면 임박"}
    reusable_for = ["냉장고", "팬트리", "냉동고", "화장품·의약품 유효기간"]
    provides = ("urgent_items",)

    def run(self, items: list, urgency_ratio: float = 0.6, **_) -> SkillResult:
        scored, ev, expired = [], [], []
        for i in items:
            life = max(1, i.get("shelf_life_days", 7))
            r = i.get("stored_days", 0) / life
            left = life - i.get("stored_days", 0)

            # 수명이 지난 것은 '가장 급한 것' 이 아니라 **쓰면 안 되는 것**이다.
            # 소진율만 보고 정렬하면 상한 재료가 1순위로 올라온다 —
            # 닭고기 5일/3일이 소진율 1.67 로 맨 앞에 섰다.
            if left <= 0:
                expired.append({**i, "days_over": -left})
                ev.append(f"{i['name']} {i['stored_days']}/{life}일 — "
                          f"수명 {-left}일 지남. 쓰지 않고 버릴 대상으로 알린다")
                continue

            if r >= urgency_ratio:
                scored.append({**i, "urgency": round(r, 2), "days_left": left})
                ev.append(f"{i['name']} {i['stored_days']}/{life}일 "
                          f"(소진율 {r:.0%}, 잔여 {left}일)")
            else:
                ev.append(f"{i['name']} {i['stored_days']}/{life}일 (소진율 {r:.0%}) — 여유")
        scored.sort(key=lambda x: -x["urgency"])
        return SkillResult(bool(scored), {"urgent": scored, "count": len(scored),
                                          "expired": expired}, ev)


class MenuSkill(Skill):
    name = "menu"
    description = ("재고와 저장된 조리 기록을 대조해 실행 가능한 후보를 만든다. "
                   "임박 재료를 쓰는 기록을 우선한다.")
    input_schema = {
        "records": "list[dict]            저장 기록 + 공개 레시피를 같은 형태로",
        "stock": "list[dict]              지금 있는 것 (이름과 수량)",
        "prefer_items": "list[str]        우선 소진할 재료",
        "require_complete": "bool         재고만으로 가능한 후보로 제한",
        "avoid": "list[str]               들어가면 안 되는 재료",
        "resolve": "(재료명, 재고) -> 재고명 | None   같은 것을 다르게 부르는 "
                   "이름을 푼다(두부/연두부). 무엇이 같은지는 도메인이 안다",
    }
    reusable_for = ["조리 기록", "세탁 코스 기록", "청소 루틴 기록"]
    # 공개 자료(recipe_source)를 나중에 붙이면서 Task 만 고치고 여기를
    # 안 고쳐 두 선언이 어긋나 있었다. 문서용이라 깨져도 아무 일이 안 난다.
    requires = ("urgent_items", "recipe_pool")
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

            # 이름만 보면 '있다' 가 되지만, 배추 20g 으로 200g 짜리를 시작할 수는
            # 없다. 수량까지 보고 모자란 것도 조달 대상에 넣는다.
            missing, short = [], {}
            for ing in need:
                key = held(ing["name"])
                if key is None:
                    missing.append(ing["name"]); continue
                # resolve 는 팬트리처럼 stock 목록 밖에 있는 것도 찾아 준다.
                # 그럴 때는 수량을 알 수 없으므로 수량 검사를 건너뛴다.
                have_g = (have.get(key) or {}).get("qty_g")
                want_g = ing.get("qty_g", 0)
                if have_g is not None and want_g and have_g < want_g:
                    short[ing["name"]] = round(want_g - have_g)
                    missing.append(ing["name"])
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
                        "short_g": short,
                        "uses_urgent": uses, "blocked": [], "score": score})
            ev.append(f"{r['record_id']}({r.get('saved_by')}) 임박재료 {len(uses)}개, "
                      f"부족 {len(missing)}개 → 점수 {score}")
            if short:
                ev.append("  수량 부족(이름은 있으나 모자람): "
                          + ", ".join(f"{k} {v}g" for k, v in short.items()))
        out.sort(key=lambda x: -x["score"])
        # 실패했을 때 "마지막 로그 줄" 이 사유로 읽히면 장황하고 부정확하다.
        # 왜 후보가 하나도 안 남았는지를 스킬이 직접 요약한다.
        recovery = None
        if not out:
            if not records:
                recovery = ("고를 후보 자체가 없다 — 저장 기록도 공개 레시피도 "
                            "들어오지 않았다")
            else:
                recovery = (f"실행 가능한 후보가 없다 — 검토한 {len(records)}건 중 "
                            f"{len(dropped)}건이 제외됐다"
                            + (" (기피 재료 또는 재고 부족)" if dropped else ""))
        return SkillResult(bool(out), {"candidates": out, "dropped": dropped,
                                       "recovery": recovery,
                                       "best": out[0] if out else None}, ev)


class PrepSkill(Skill):
    name = "prep"
    description = ("조리 기록이 지정한 재료를 계량하고, 재료 상태에서 나올 추가 수분을 "
                   "미리 계산한다. 이 값을 넘겨주지 않으면 조리 단계는 같은 목표를 "
                   "다른 출발점에서 쫓게 된다.")
    input_schema = {
        "record": "dict                   조리 기록",
        "weigh": "(name, qty_g) -> dict   계량 함수. 주입받는다",
        "available": "(name) -> bool      재고 확인 함수. 주입받는다",
        "min_fill_ratio": "float          목표의 이 비율도 못 담으면 넘기지 않는다",
        "absorb_of": "(재료들) -> float   빨아들일 물의 양. 그만큼 더 붓는다",
        "solid_of": "(재료들) -> float    국물이 되지 않는 고형분",
    }
    reusable_for = ["조리 전 계량", "세제 투입량 산정", "정수량 배분"]
    requires = ("stock_complete", "chosen_record")
    provides = ("measured",)

    def run(self, record: dict, weigh, available,
            min_fill_ratio: float = 0.5,
            absorb_of=None, solid_of=None, **_) -> SkillResult:
        total, extra, ev, missing = 0.0, 0.0, [], []
        short = {}
        # 계량은 전부 하지만 **처음부터 냄비에 들어가는 것**은 일부다.
        # 나중에 넣을 것을 처음 질량에 더하면 졸임 기준이 틀어진다.
        later = []
        chilled_g = [0.0]
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
            # 목표의 절반도 못 담았으면 그 메뉴가 아니다. 배추 20g 으로
            # 200g 짜리 된장찌개를 시작하면 조리 단계는 그대로 성공을 보고한다.
            # menu 가 수량까지 보므로 정상 경로에서는 여기까지 오지 않지만,
            # 사이에 재고가 줄어드는 경우를 위해 한 번 더 막는다.
            fill = w["actual_g"] / w["target_g"] if w.get("target_g") else 1.0
            if fill < min_fill_ratio:
                missing.append(ing["name"])
                ev.append(f"{ing['name']}: 목표 {w['target_g']}g 중 "
                          f"{w['actual_g']}g({fill:.0%})만 담김 — "
                          f"{min_fill_ratio:.0%} 미만이라 조리로 넘기지 않는다")
                continue
            if ing.get("add_at") is not None:
                later.append({"name": ing["name"], "grams": w["actual_g"],
                              "at_ratio": ing["add_at"],
                              "temp_c": ing.get("temp_c", 8.0),
                              "why": ing.get("why", "")})
                ev.append(f"{ing['name']} {w['actual_g']}g 은 질량비 "
                          f"{ing['add_at']} 에서 투입 — {ing.get('why', '')}")
                continue
            # 냉장 보관 중인 재료는 차다. 질량가중 평균으로 출발 온도를 낸다.
            chilled_g[0] += w["actual_g"]
            total += w["actual_g"]
            extra += w["expected_extra_water_g"]
            if w.get("short_g"):
                short[ing["name"]] = w["short_g"]
            ev.append(f"{ing['name']} 목표 {w['target_g']}g → 실계량 {w['actual_g']}g"
                      + (f", 추가 수분 {w['expected_extra_water_g']}g 예상"
                         if w["expected_extra_water_g"] else "")
                      + (f"  ⚠ {w['short_g']}g 모자람" if w.get("short_g") else ""))

        # 재료가 빨아들일 물을 **미리 채운다.**
        #
        # 찹쌀 400g 은 국물을 800g 쯤 먹는다. 레시피에는 "물 적당히" 라고만
        # 적혀 있어서, 기록된 초기 질량대로만 담으면 조리 중에 국물이 바닥나고
        # 바닥이 탄다. 질량비만 보는 제어기는 그 순간을 눈치채지 못한다
        # (실제로 목표 0.85 를 노리다 0.9336 에서 멈추고 눌어붙음이 1.0 이 됐다).
        placed = [i for i in record.get("ingredients", [])
                  if i.get("add_at") is None] + [
            {"name": x["name"], "qty_g": x["grams"]} for x in later]
        absorb_g = absorb_of(placed) if absorb_of else 0.0
        solid_g = solid_of(placed) if solid_of else 0.0

        # 기록에 없는 나머지(국물 등)는 기록된 초기 질량으로 맞춘다.
        # 나중에 넣을 재료는 아직 냄비에 없으므로 여기서 빼 둔다.
        listed = sum(i["qty_g"] for i in record.get("ingredients", []))
        total += record.get("initial_mass_g", 0) - listed
        total -= sum(x["grams"] for x in later)

        water_added = 0.0
        if absorb_g > 0:
            # 빨아들일 만큼만 부으면 모자란다. **졸일 물까지** 있어야 한다.
            #
            #   총량 M 중 고형 S 와 흡수 A 는 졸일 수 없다.
            #   목표 질량비 r 까지 가려면 M(1-r) 을 날려야 하므로
            #   자유 수분 M - S - A ≥ M(1-r),  즉  M ≥ (S+A)/r 이어야 한다.
            #
            # 흡수량만 채웠다가 삼계탕이 0.9352 에서 국물이 바닥나 멈췄다.
            r = record.get("target_mass_ratio") or 1.0
            need_total = (solid_g + absorb_g) / max(0.05, r) * 1.05   # 5% 여유
            # 흡수량은 need_total 에 **이미 포함**돼 있다. max(absorb_g, ...) 로
            # 두면 기록에 물이 이미 들어 있어도 흡수량만큼 또 붓는다 —
            # 두 번째 조리에서 2036g 이 3038g 이 되어 용량 상한에 걸렸다.
            water_added = round(max(0.0, need_total - total), 1)
            total += water_added
            ev.append(f"재료가 물 {round(absorb_g)}g 을 빨아들인다. "
                      f"목표 {r} 까지 졸이려면 고형 {round(solid_g)}g + 흡수분을 "
                      f"빼고도 졸일 물이 남아야 하므로 총 {round(need_total)}g 필요 "
                      f"→ {water_added}g 을 더 붓는다")
        ev.append(f"총 {round(total)}g (기록 {record.get('initial_mass_g')}g), "
                  f"추가 수분 합 {round(extra, 1)}g"
                  + (f", 고형분 {round(solid_g)}g" if solid_g else ""))
        if short:
            ev.append("재고가 모자라 목표보다 적게 담은 재료: "
                      + ", ".join(f"{k} {v}g" for k, v in short.items())
                      + " — 조리 목표를 그만큼 낮춰 잡아야 한다")
        return SkillResult(not missing,
                           {"total_mass_g": round(total, 1),
                            "extra_water_g": round(extra, 1),
                            "recovery": (f"계량하지 못한 재료 {len(missing)}건: "
                                         f"{', '.join(missing)} — 조달이 끝나지 "
                                         f"않았거나 재고가 목표의 절반에 못 미친다"
                                         if missing else None),
                            "start_temp_c": round(
                                (chilled_g[0] * FRIDGE_TEMP_C
                                 + max(0.0, total - chilled_g[0]) * ROOM_TEMP_C)
                                / max(1.0, total), 1),
                            "add_later": later,
                            "absorb_cap_g": round(absorb_g, 1),
                            "solid_g": round(solid_g, 1),
                            "water_added_g": water_added,
                            "missing": missing, "short_g": short}, ev)


class ProcureSkill(Skill):
    name = "procure"
    description = ("부족한 항목을 장보기 서비스에서 조달한다. 여러 상점의 가격과 "
                   "배송 시간을 비교해 제때 도착하는 것 중 가장 싼 것을 고른다. "
                   "되돌릴 수 없는 행동이므로 상한·이력·안전 필터를 지키며, "
                   "하나라도 걸리면 주문하지 않고 사용자 확인을 요청한다.")
    input_schema = {
        "missing": "list[str]",
        "lookup": "(품목) -> list[Offer]   상점 조회 함수. 주입받는다",
        "known_items": "list[str]  이전에 산 적 있는 품목",
        # lookup 이 돌려주는 Offer 에 can_order 가 실려 온다. 주문까지 되는
        # 상점이 없으면 값이 얼마든 자동 주문하지 않는다.
        "avoid": "list[str]  알레르기·기피 품목",
        "auto_limit_krw": "int  1회 자동 주문 상한",
        "deadline_min": "int | None  이 시간 안에 도착해야 한다",
    }
    reusable_for = ["식재료 조달", "세제·소모품 재주문", "필터·부품 교체"]
    requires = ("missing_items",)
    provides = ("stock_complete",)

    def run(self, missing: list, lookup, known_items: list | None = None,
            avoid: list | None = None, auto_limit_krw: int = 15000,
            deadline_min: int | None = None, **_) -> SkillResult:
        known = set(known_items or [])
        avoid = set(avoid or [])
        auto, ask, ev = [], [], []
        # 기한이 없으면 **2일 배송도 통과한다.** 조용히 그렇게 되지 않도록
        # 로그에 남긴다 — 호출자가 기한을 안 넘긴 것이 의도인지 보이게.
        if deadline_min is None:
            ev.append("도착 기한이 주어지지 않았다 — 배송 시간을 따지지 않는다")

        for name in missing:
            offers = lookup(name)
            if not offers:
                ask.append({"name": name, "reason": "취급하는 상점 없음"})
                ev.append(f"{name}: 어느 상점에도 없음 → 확인 요청"); continue
            if name in avoid:
                ask.append({"name": name, "reason": "알레르기·기피 목록에 있음"})
                ev.append(f"{name}: 안전 필터에 걸려 자동 주문 보류"); continue

            # 제때 도착하는 것만 남기고, 그중 가장 싼 것을 고른다
            fit = [o for o in offers
                   if deadline_min is None or o.delivery_min <= deadline_min]
            if not fit:
                fastest = min(offers, key=lambda o: o.delivery_min)
                ask.append({"name": name,
                            "reason": f"가장 빠른 배송 {fastest.delivery_min}분 > "
                                      f"남은 {deadline_min}분"})
                ev.append(f"{name}: 제때 도착하는 상점 없음 "
                          f"(최속 {fastest.store} {fastest.delivery_min}분) → 확인 요청")
                continue
            # 주문까지 되는 상점만 자동 주문 대상이다. 공개 주문 API 가 없는
            # 상점은 조회만 된다 — 제휴 여부를 모른 채 전부 주문하고 있었다.
            orderable = [o for o in fit if getattr(o, "can_order", False)]
            if not orderable:
                ask.append({"name": name,
                            "reason": "제때 오는 상점 중 주문까지 되는 곳이 없다"
                                      " (조회만 가능)"})
                ev.append(f"{name}: 제때 도착하는 {len(fit)}곳 모두 조회만 가능 "
                          f"→ 사용자가 직접 주문해야 한다")
                continue
            best = min(orderable, key=lambda o: o.price_krw)
            ev.append(f"{name}: 상점 {len(offers)}곳 비교 → "
                      + " / ".join(f"{o.store} {o.price_krw:,}원 {o.delivery_min}분"
                                   for o in offers))

            if name not in known:
                ask.append({"name": name, "reason": "처음 구매하는 품목"})
                ev.append(f"  {name}: 구매 이력 없음 → 확인 요청"); continue
            if best.price_krw > auto_limit_krw:
                ask.append({"name": name,
                            "reason": f"{best.price_krw:,}원 > 상한 {auto_limit_krw:,}원"})
                ev.append(f"  {name}: 금액 상한 초과 → 확인 요청"); continue

            auto.append({"name": name, "price_krw": best.price_krw,
                         "store": best.store, "delivery_min": best.delivery_min,
                         "source": best.source})
            ev.append(f"  {name}: {best.store} {best.price_krw:,}원 "
                      f"{best.delivery_min}분 — 이력 있고 상한 이내 → 자동 주문")

        total = sum(a["price_krw"] for a in auto)
        eta = max((a["delivery_min"] for a in auto), default=0)
        return SkillResult(True, {"auto_ordered": auto, "need_confirm": ask,
                                  "total_krw": total, "arrive_in_min": eta},
                           ev or ["조달할 항목 없음"])
