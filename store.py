# -*- coding: utf-8 -*-
"""장보기 서비스 어댑터.

지금까지 조달 스킬은 내가 손으로 적은 가격표를 봤다. 두 가지가 문제였다.
  1) 가격이 합성 데이터다
  2) **배송 시간이 없다** — "조달 20분" 은 내가 정한 숫자였고,
     1인 가구가 조달을 빼는 근거가 거기서 나왔다

상점을 어댑터로 두면 조달 스킬이 가격표가 아니라 **서비스**를 본다.
converge 가 관측·액추에이터 함수를 주입받는 것과 같은 구조다.

    조회(lookup)  — 공개 API 로 가능한 영역
    주문(order)   — 공개 API 가 없다. 어댑터 자리만 만들어 둔다

실제 연동 가능성 (2026-09 확인)
  · 가격   한국농수산식품유통공사 KAMIS 일별 소매가격 (공공데이터, 무료·키 필요)
  · 주문   쿠팡 Open API 는 **판매자용**이다. 마켓컬리·B마트는 공개 개발자 API 없음.
           따라서 자동 주문은 제휴 없이는 불가능하며, 여기서는 시뮬레이터로 둔다.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Offer:
    """한 상점이 한 품목에 대해 내놓는 조건."""
    store: str
    item: str
    price_krw: int
    delivery_min: int          # 주문부터 수령까지 걸리는 시간(분)
    in_stock: bool = True
    # 이 상점에서 **주문까지** 할 수 있는가. 공개 주문 API 가 없는 상점은
    # 조회만 된다. 예전에는 이 값이 Store 에만 있고 Offer 로 넘어오지 않아,
    # 조달 스킬이 제휴 여부를 모른 채 전부 자동 주문했다.
    can_order: bool = False
    source: str = "시뮬레이터"

    def __repr__(self):
        return (f"{self.store} {self.item} {self.price_krw:,}원 "
                f"{self.delivery_min}분{'' if self.can_order else ' (조회만)'}")


@dataclass
class Store:
    """장보기 서비스 하나.

    can_order 가 False 면 조회만 가능한 상점이다. 실제 제휴 전에는
    모든 상점이 여기에 해당하며, 에이전트는 주문 대신 사용자 확인을 요청한다.
    """
    name: str
    delivery_min: int
    price_factor: float = 1.0          # 기준가 대비 배율
    catalog: dict = field(default_factory=dict)   # 품목 → 기준가
    can_order: bool = False
    # 품절. 무작위로 만들면 재현이 안 되므로 **명시적 목록**으로 둔다.
    # 실제 API 를 붙이면 조회 응답이 이 자리를 대신한다.
    out_of_stock: tuple = ()
    source: str = "시뮬레이터"

    def lookup(self, item: str) -> Offer | None:
        base = self.catalog.get(item)
        if base is None:
            return None
        return Offer(store=self.name, item=item,
                     price_krw=int(round(base * self.price_factor / 10) * 10),
                     delivery_min=self.delivery_min,
                     in_stock=item not in self.out_of_stock,
                     can_order=self.can_order, source=self.source)


# 기준가 — KAMIS 같은 실제 가격원으로 교체할 자리다.
# 지금은 합성값이며, 이 사실을 결과에 source 로 표시한다.
BASE_PRICE = {
    "두부": 2800, "대파": 1900, "표고버섯": 4500, "간장": 3200, "된장": 5400,
    "닭고기": 9800, "한우등심": 32000, "찹쌀": 4200, "미나리": 2500,
    "양파": 2200, "당근": 2300, "감자": 3100, "애호박": 1800, "배추": 4800,
    "무": 2600, "달걀": 6500, "우유": 2900, "시금치": 2700, "오이": 1700,
    # 양념·상비품도 장보기 대상이다. 떨어지면 같이 주문해야 한다.
    "참기름": 8900, "식용유": 5400, "설탕": 2400, "소금": 1800,
    "고춧가루": 9500, "식초": 2600, "밀가루": 2800,
}

# 신선식품은 빠른 배송에서 취급 품목이 좁다 — 상점마다 다른 조건을 만든다
_QUICK = {k: v for k, v in BASE_PRICE.items()
          if k not in ("한우등심", "표고버섯", "찹쌀")}

# 즉시배송 20분은 이전 STAGE_COSTS 의 "조달 20분" 을 상점 조건으로 옮긴 값이다.
# 값을 바꾼 것이 아니라 **출처를 바꾼 것** — 내가 정한 상수에서 상점이 알려주는
# 조건으로 옮겼다. 상점을 바꾸면 상황 판단도 따라 바뀐다.
# can_order 는 **제휴 여부**다. 공개 주문 API 가 없으므로 실제로는 전부
# False 여야 하지만, 그러면 자동 주문 경로를 시연할 수 없다. 하나만
# "제휴를 가정한 상점" 으로 두고 그 사실을 결과에 남긴다.
STORES = [
    Store("즉시배송", delivery_min=20, price_factor=1.10, catalog=_QUICK,
          can_order=True, source="시뮬레이터(제휴 가정)"),
    Store("새벽배송", delivery_min=720, price_factor=1.00, catalog=BASE_PRICE,
          out_of_stock=("미나리",)),
    Store("일반배송", delivery_min=2880, price_factor=0.95, catalog=BASE_PRICE),
]


def make_lookup(stores=None):
    """품목 하나에 대해 모든 상점의 조건을 돌려주는 함수를 만든다.

    조달 스킬은 이 함수만 받는다. 상점이 몇 개인지, 실제 API 인지 시뮬레이터인지
    알지 못한다.
    """
    stores = stores if stores is not None else STORES

    def lookup(item: str) -> list:
        out = []
        for s in stores:
            o = s.lookup(item)
            if o and o.in_stock:
                out.append(o)
        return out

    return lookup


def min_delivery_min(stores=None, item: str | None = None) -> int:
    """가장 빠른 배송 시간. 상황 판단에서 '조달에 걸리는 시간' 으로 쓴다.

    품목을 주면 **그 품목을 실제로 취급하는** 상점만 본다. 예전에는 품목을
    보지 않아, 즉시배송이 취급하지 않는 찹쌀도 20분에 받을 수 있다고 봤다.
    """
    stores = stores if stores is not None else STORES
    if item is None:
        return min((s.delivery_min for s in stores), default=0)
    fit = [s.delivery_min for s in stores
           if s.lookup(item) and s.lookup(item).in_stock]
    return min(fit, default=0)
