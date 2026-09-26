# -*- coding: utf-8 -*-
"""스킬 하나하나를 **단독으로** 확인한다.

다른 검사와 무엇이 다른가:
  check_consistency  선언과 구현이 맞는가 (정적)
  check_interlock    스킬이 서로 맞물리는가 (파이프라인)
  check_generalize   처음 보는 상황에서 도는가 (파이프라인)
  **check_skills**   스킬 하나를 떼어 놓고 봐도 성립하는가

스킬은 "재사용 가능한 능력 단위" 라고 선언해 두었다. 그 말이 참이려면
파이프라인 밖에서 혼자 불러도 말이 되어야 한다. 여기서 보는 것:

  1 순수성    같은 입력을 두 번 주면 같은 출력이 나오는가
  2 경계값    빈 목록·None·극단값에 예외 없이 답하는가
  3 근거      판단 근거(evidence)가 비어 있지 않은가
  4 정직성    아무것도 못 했는데 ok=True 를 돌려주지 않는가
  5 격리      전역 상태(kitchen/store)를 몰래 읽지 않는가

4번은 실제로 한 번 깨졌다 — inventory 가 반대 방향으로 깨져 있었다.
"임박한 것이 0건" 을 실패로 보는 바람에, 냉장고가 신선한 가구에서
파이프라인이 1단계에서 멈췄다(check_generalize 참고).
"""
from __future__ import annotations
import copy
import inspect
import sys

from skills import REGISTRY

# ── 스킬마다 "제대로 된 입력" 과 "경계 입력" 을 하나씩 준다 ──────────────
# 도메인 객체를 쓰지 않는다. 스킬이 일반 타입만 받는다는 선언을 여기서
# 지키는지도 함께 보는 셈이다.

_RECORDS = [{"record_id": "r1", "menu": "된장찌개", "servings": 2,
             "target_mass_ratio": 0.78, "soil_score": 0.3,
             "ingredients": [{"name": "배추", "qty_g": 200},
                             {"name": "두부", "qty_g": 150}]}]
_STOCK = [{"name": "배추", "qty_g": 300, "stored_days": 5, "shelf_life_days": 7},
          {"name": "두부", "qty_g": 200, "stored_days": 2, "shelf_life_days": 5}]


# procure 는 제안을 **속성 접근**(o.delivery_min)으로 읽는다. dict 를 주면
# deadline_min 경로에서 AttributeError 가 난다 — base.py 의 "스킬은 일반
# 타입을 주고받는다" 와 어긋나는 유일한 자리다. 다른 도메인에 이 스킬을
# 쓰려면 같은 속성을 가진 객체를 만들어 줘야 한다는 뜻이므로, 여기서는
# 실제 자료형(store.Offer)을 그대로 써서 그 사실을 드러낸다.
import store as _store


def _lookup(name):
    return [_store.Offer(item=name, store="가게", price_krw=3000,
                         delivery_min=20, in_stock=True, can_order=True)]


CASES = {
    "inventory": {
        "정상": dict(items=_STOCK, urgency_ratio=0.6),
        "빈 목록": dict(items=[], urgency_ratio=0.6),
        "전부 여유": dict(items=[{"name": "쌀", "qty_g": 1000,
                               "stored_days": 1, "shelf_life_days": 365}]),
        "수명 0일": dict(items=[{"name": "우유", "qty_g": 500,
                              "stored_days": 9, "shelf_life_days": 0}]),
    },
    "menu": {
        "정상": dict(records=_RECORDS, stock=_STOCK, prefer_items=["배추"]),
        "기록 없음": dict(records=[], stock=_STOCK),
        "재고 없음": dict(records=_RECORDS, stock=[]),
        "전부 기피": dict(records=_RECORDS, stock=_STOCK, avoid=["배추", "두부"]),
    },
    "procure": {
        "정상": dict(missing=["대파"], lookup=_lookup, known_items=["대파"],
                   auto_limit_krw=15000),
        "부족분 없음": dict(missing=[], lookup=_lookup, known_items=[],
                       auto_limit_krw=15000),
        "기한 0분": dict(missing=["대파"], lookup=_lookup, known_items=["대파"],
                      auto_limit_krw=15000, deadline_min=0),
        "예산 0원": dict(missing=["대파"], lookup=_lookup, known_items=["대파"],
                      auto_limit_krw=0),
    },
    "aftercare": {
        "정상": dict(soil_score=0.36),
        "0": dict(soil_score=0.0),
        "1": dict(soil_score=1.0),
        "다른 기기": dict(soil_score=0.36, profile="washer"),
    },
}

# 스킬이 "아무것도 못 한" 입력. 여기서 ok=True 면 성과가 부풀려진다.
EMPTY_CASE = {"inventory": "빈 목록", "menu": "기록 없음",
              "procure": None, "aftercare": None}


def _run(name, kw):
    return REGISTRY.get(name).run(**copy.deepcopy(kw))


def main() -> int:
    print("스킬 단독 검사 — 파이프라인 밖에서도 성립하는가")
    print()
    bad = 0

    # ── 5 격리 : 스킬 모듈이 도메인 모듈을 import 하지 않는가 ──
    leaked = []
    for mod in {REGISTRY.get(s["name"]).__class__.__module__
                for s in REGISTRY.list()}:
        src = inspect.getsource(sys.modules[mod])
        for forbidden in ("import kitchen", "import store", "import personas",
                          "import dryer"):
            if forbidden in src:
                leaked.append(f"{mod} 가 {forbidden}")
    if leaked:
        print(f"  !!  격리        스킬이 도메인을 직접 읽는다: {leaked}")
        bad += 1
    else:
        print(f"  OK  격리        스킬 {len(REGISTRY.list())}개가 도메인 모듈을 "
              f"import 하지 않는다")

    # ── 1~4 ──
    for name, cases in CASES.items():
        for label, kw in cases.items():
            tag = f"{name}·{label}"
            try:
                r1 = _run(name, kw)
            except Exception as e:
                print(f"  !!  {tag:22s} 예외 {type(e).__name__}: {e}")
                bad += 1
                continue

            # 1 순수성 — 같은 입력을 두 번
            try:
                r2 = _run(name, kw)
                same = (r1.ok == r2.ok and r1.output == r2.output)
            except Exception as e:
                print(f"  !!  {tag:22s} 두 번째 호출에서 예외 {e}")
                bad += 1
                continue
            if not same:
                print(f"  !!  {tag:22s} 같은 입력인데 결과가 다르다 "
                      f"(전역 상태나 난수를 쓴다)")
                bad += 1
                continue

            # 3 근거
            if not r1.evidence:
                print(f"  !!  {tag:22s} 판단 근거가 비어 있다")
                bad += 1
                continue

            # 4 정직성 — 아무것도 못 한 입력에 성공을 주지 않는가
            if EMPTY_CASE.get(name) == label and r1.ok:
                print(f"  !!  {tag:22s} 아무것도 못 했는데 ok=True")
                bad += 1
                continue

            print(f"  OK  {tag:22s} ok={str(r1.ok):5s} 근거 {len(r1.evidence)}줄")

    print()
    total = 1 + sum(len(c) for c in CASES.values())
    print(f"판정: {total - bad}/{total} 항목 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
