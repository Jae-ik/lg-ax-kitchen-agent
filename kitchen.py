# -*- coding: utf-8 -*-
"""주방 기기 4종 시뮬레이터 + 조리 기록(상태 궤적) 저장소.

LLM 과 무관한 순수 로직이다. 에이전트는 이 모듈의 함수만 호출한다.
실제 기기(LG ThinQ 등)로 교체할 때 이 파일만 바꾸면 된다.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field

random.seed(7)

# ══════════════════════════ 조리 기록 (상태 궤적 τ*) ══════════════════════════
# 사용자가 "이 맛 저장" 을 누른 세션에서 측정된 값이다.
RECORDS = {
    "rec_001": {
        "record_id": "rec_001",
        "servings": 2,
        "menu": "된장찌개",
        "saved_by": "어머니",
        "device": "본가 6인용 조리기",
        "saved_at": "2026-08-14",
        # add_at: 이 질량비가 되면 넣는다. 없으면 처음부터 넣는다.
        # 두부를 처음부터 넣으면 오래 끓어 부서진다 — 조리법이 순서를 정하는
        # 이유이고, "재료 투입 전후를 구분한다" 는 말이 뜻하는 바다.
        "ingredients": [                      # 준비 단계가 읽는다
            {"name": "배추", "qty_g": 200},
            {"name": "된장", "qty_g": 40},
            {"name": "두부", "qty_g": 150, "add_at": 0.93,
             "why": "일찍 넣으면 오래 끓어 부서진다", "temp_c": 8},
        ],
        "initial_mass_g": 620,                # 투입 직후 측정값
        "target_mass_ratio": 0.78,            # 초기 대비 78% 까지 졸임
        "peak_temp_c": 98,
        "cook_minutes_observed": 11,
        "soil_score": 0.62,                   # 세척 단계가 읽는다 (눌어붙음 예측)
        "satisfaction": 5,
    },
    "rec_002": {
        "record_id": "rec_002",
        "servings": 2,
        "menu": "된장찌개",
        "saved_by": "본인",
        "device": "자취방 2인용 조리기",
        "saved_at": "2026-09-02",
        "ingredients": [
            {"name": "배추", "qty_g": 200},
            {"name": "된장", "qty_g": 35},
            {"name": "두부", "qty_g": 150, "add_at": 0.93,
             "why": "일찍 넣으면 오래 끓어 부서진다", "temp_c": 8},
        ],
        "initial_mass_g": 610,
        "target_mass_ratio": 0.88,            # 같은 메뉴, 덜 졸인 취향
        "peak_temp_c": 96,
        "cook_minutes_observed": 7,
        "soil_score": 0.31,
        "satisfaction": 4,
    },
    "rec_003": {
        "record_id": "rec_003",
        "servings": 2,
        "menu": "닭고기 표고 조림",
        "saved_by": "본인",
        "device": "자취방 2인용 조리기",
        "saved_at": "2026-08-30",
        "ingredients": [
            {"name": "닭고기", "qty_g": 400},
            {"name": "간장", "qty_g": 50},
            {"name": "표고버섯", "qty_g": 120, "add_at": 0.90,
             "why": "먼저 넣으면 물러진다", "temp_c": 20},
        ],
        "initial_mass_g": 700,
        "target_mass_ratio": 0.72,            # 조림이라 더 졸인다
        "peak_temp_c": 99,
        "cook_minutes_observed": 14,
        "soil_score": 0.71,                   # 조림은 더 눌어붙는다
        "satisfaction": 5,
    },
}

# ═══════════════════════ 기기 제원 (냄비가 바뀌면) ═══════════════════════
# 기록을 다른 기기로 옮길 때 줄일 비율을 **손으로 넣지 않으려면**, 기기가
# 자기 제원을 알고 있어야 한다. ThinQ Connect 로 붙이면 이 표가 기기 프로필
# 조회로 바뀐다 — 지금은 그 자리를 비워 두지 않고 값을 적어 둔 것이다.
#
#   servings   : 이 기기가 한 번에 만드는 기준 인분
#   capacity_g : 넘치지 않고 담기는 물리적 상한
DEVICES = {
    "본가 6인용 조리기":   {"servings": 6, "capacity_g": 3000},
    "자취방 2인용 조리기": {"servings": 2, "capacity_g": 1100},
    "원룸 1인용 조리기":   {"servings": 1, "capacity_g": 600},
}
FILL_LIMIT = 0.85          # 상한의 85% 까지만 담는다 (끓어 넘침 여유)


def device_spec(name: str) -> dict:
    """등록되지 않은 기기는 추측하지 않는다 — 비율 1 로 두고 호출자가 알게 한다."""
    return DEVICES.get(name, {})


def capacity_ratio_between(from_device: str, to_device: str,
                           initial_mass_g: float = 0.0) -> tuple:
    """두 기기의 제원만으로 줄일 비율을 계산한다. (비율, 근거 문장)

    두 가지를 함께 본다.
      1) 인분 - 2인 가구가 6인분을 만들 이유가 없다
      2) 물리적 상한 - 인분을 맞춰도 냄비에 안 들어가면 소용없다
    """
    src, dst = device_spec(from_device), device_spec(to_device)
    if not src or not dst:
        miss = from_device if not src else to_device
        return 1.0, f"'{miss}' 의 제원을 모른다 — 비율 1 로 둔다(사용자 확인 필요)"

    ratio = dst["servings"] / src["servings"]
    why = (f"{from_device}({src['servings']}인분) → "
           f"{to_device}({dst['servings']}인분) = {ratio:.3g}배")

    limit = dst["capacity_g"] * FILL_LIMIT
    if initial_mass_g and initial_mass_g * ratio > limit:
        ratio = limit / initial_mass_g
        why += (f" 인데 {round(initial_mass_g * dst['servings'] / src['servings'])}g 은 "
                f"{to_device} 상한 {dst['capacity_g']}g 의 {FILL_LIMIT:.0%} 를 넘는다 "
                f"→ {ratio:.3g}배로 더 줄임")
    return ratio, why


# ══════════════════════════ 냉장고 (보관) ══════════════════════════
_FRIDGE = [
    # shelf_life_days: 품목별 보관 수명. 장류처럼 오래 두는 것과 채소를 구분한다.
    {"name": "배추", "qty_g": 320, "stored_days": 5, "shelf_life_days": 7},
    {"name": "된장", "qty_g": 500, "stored_days": 40, "shelf_life_days": 365},
    {"name": "애호박", "qty_g": 180, "stored_days": 2, "shelf_life_days": 8},
    # 두부는 일부러 없다 — 에이전트가 판단해야 하는 상황
]


_FRIDGE_DEFAULT = [dict(x) for x in _FRIDGE]
_RECORDS_DEFAULT = {k: dict(v) for k, v in RECORDS.items()}


def reset(fridge_items=None, seed: int = 7, keep_records: bool = False):
    """가구를 바꿔 가며 실행할 때 상태를 격리한다.

    모듈 전역 재고를 그대로 두고 여러 상황을 연달아 돌리면, 앞 실행에서
    주문해 넣은 재료가 다음 실행의 재고로 남는다. 상황별 결과를 비교하려면
    매번 같은 출발점에서 시작해야 한다.
    """
    global _FRIDGE
    random.seed(seed)
    src = fridge_items if fridge_items is not None else _FRIDGE_DEFAULT
    _FRIDGE = [dict(x) for x in src]
    # 조리하면 기록이 쌓인다. 상황을 바꿔 가며 비교할 때는 같은 출발점이어야
    # 하므로 기록도 함께 되돌린다. (keep_records=True 면 이어서 쌓는다)
    if not keep_records:
        RECORDS.clear()
        RECORDS.update({k: dict(v) for k, v in _RECORDS_DEFAULT.items()})
    COOKER.stop()
    COOKER.elapsed_min = 0.0
    COOKER.mass_g = 0.0
    COOKER.initial_mass_g = 0.0
    COOKER.extra_water_g = 0.0
    COOKER.temp_c = 20.0
    COOKER.power = 0
    COOKER.log.clear()
    return fridge_list_items()


def fridge_add(name: str, qty_g: int, shelf_life_days: int = 5):
    """조달된 품목을 재고에 반영한다."""
    for x in _FRIDGE:
        if x["name"] == name:
            x["qty_g"] += qty_g
            return dict(x)
    _FRIDGE.append({"name": name, "qty_g": qty_g, "stored_days": 0,
                    "shelf_life_days": shelf_life_days})
    return dict(_FRIDGE[-1])


def fridge_consume(name: str, qty_g: float):
    """쓴 만큼 재고에서 뺀다. 다 쓰면 목록에서 지운다.

    이게 없으면 조리를 해도 냉장고가 그대로라, 다음 판단이 틀린 재고 위에서
    이뤄진다.
    """
    for i, x in enumerate(_FRIDGE):
        if x["name"] == name:
            x["qty_g"] = max(0, x["qty_g"] - qty_g)
            if x["qty_g"] <= 0:
                _FRIDGE.pop(i)
            return True
    return False


def fridge_list_items():
    """냉장고 재고를 반환한다."""
    return [dict(x) for x in _FRIDGE]


def fridge_check(name: str):
    for x in _FRIDGE:
        if x["name"] == name:
            return dict(x)
    return None


# ══════════════════════════ 준비 (계량) ══════════════════════════
def prep_weigh(name: str, target_g: int, consume: bool = True):
    """계량한다. 실제 계량값은 목표와 조금 다르다(사람이 담기 때문).

    예전에는 재고가 모자라도 있는 만큼만 조용히 담고 끝냈다. 모자랐다는 사실이
    아무 데도 남지 않아, 조리 단계가 잘못된 출발점을 정상으로 알았다.
    이제 부족분을 함께 돌려주고, 담은 만큼 재고에서 뺀다.
    """
    item = fridge_check(name)
    if item is None:
        return {"ok": False, "reason": f"{name} 없음", "short_g": target_g}
    # 소금 0.2g 처럼 1g 미만인 양념은 정수 반올림하면 0g 이 된다. 실제로
    # 담기는데 '못 담았다' 가 되어, 뒤에서 조리가 통째로 막혔다.
    # 저울 분해능을 0.01g 으로 두고, 1g 이상만 정수로 읽는다.
    raw = target_g * random.uniform(0.97, 1.03)
    want = round(raw) if target_g >= 1 else round(raw, 2)
    actual = min(item["qty_g"], want)
    short = round(max(0, want - actual), 2)
    if consume:
        fridge_consume(name, actual)
    # 수분이 많은 채소만 추가 수분이 나온다. 장류·건조 재료는 해당 없음.
    WATERY = {"배추", "애호박", "무", "양파", "버섯"}
    if name in WATERY:
        # 보관이 길수록 조직이 무너져 물이 더 나온다 (무게에 비례)
        extra_water = round(actual * 0.02 * min(item["stored_days"], 7), 1)
    else:
        extra_water = 0.0
    return {"ok": True, "name": name, "target_g": target_g,
            "actual_g": actual, "short_g": short,
            "expected_extra_water_g": extra_water}


# ══════════════════════════ 조리기 ══════════════════════════
@dataclass
class Cooker:
    running: bool = False
    elapsed_min: float = 0.0
    mass_g: float = 0.0
    initial_mass_g: float = 0.0
    temp_c: float = 20.0
    power: int = 0                    # 0~5
    extra_water_g: float = 0.0
    soil: float = 0.0                 # 눌어붙음 누적 (0~1)
    added_g: float = 0.0              # 조리 도중 넣은 양 (중간 투입)
    capacity_g: float = 1100.0        # 이 냄비에 담기는 최대량

    # 열 모델 상수. 모두 **시뮬레이터 가정**이며 실측이 아니다.
    # 실제 기기에서는 냄비별로 재서 채워 넣어야 하는 자리다.
    HEAT_K: float = 0.55              # 데워지는 속도
    # 물 620g 을 100→90도로 식히는 열이 전부 증발에 쓰이면 약 11g 이다.
    # 실제로는 냄비와 공기로도 빠져나가므로 그보다 적다. 아래 값은 여열이
    # 5~10g(질량비 0.01 안팎) 나오도록 잡은 것이며, 실측으로 교체해야 한다.
    COOL_K: float = 0.04              # 식는 속도 (620g 기준)
    REF_MASS_G: float = 620.0         # COOL_K 를 잰 기준 질량
    BASE_EVAP: float = 3.5            # 끓는 동안 화력 없이도 나가는 양 g/분
    POWER_EVAP: float = 5.83          # 화력 한 단계당 g/분 (화력3 에서 약 21g)
    peak_temp_c: float = 0.0
    log: list = field(default_factory=list)

    def overflow_risk(self) -> float:
        """끓어넘칠 위험 (0~1).

        국물이 냄비에 가득 찬 상태에서 화력을 올리면 넘친다. 제안서는
        "가열 상한, 이상 감지, 사용자 개입 우선권을 먼저 구현한다" 고
        적어 두었는데, 코드에는 화력 상한(5)만 있고 **이상 감지가 없었다.**
        상한은 기기 사양이지 상황 판단이 아니다.
        """
        if not self.running or self.capacity_g <= 0:
            return 0.0
        fill = self.mass_g / self.capacity_g
        boil = max(0.0, (self.temp_c - 95) / 5)          # 95도부터 거품이 인다
        return round(min(1.0, fill * boil * (self.power / 5)), 3)

    def start(self, initial_mass_g: float, extra_water_g: float = 0.0, power: int = 3,
              capacity_g: float | None = None):
        self.running = True
        if capacity_g:
            self.capacity_g = capacity_g
        self.elapsed_min = 0.0
        self.initial_mass_g = initial_mass_g + extra_water_g
        self.mass_g = self.initial_mass_g
        self.extra_water_g = extra_water_g
        self.temp_c = 20.0
        self.power = power
        self.soil = 0.0
        self.added_g = 0.0
        self.peak_temp_c = 20.0
        self.log = [(0.0, self.mass_g, self.temp_c)]

    def tick(self, minutes: float = 1.0):
        """시간을 진행시킨다. 증발량은 화력과 온도에 따라 달라진다."""
        if not self.running:
            return
        self.elapsed_min += minutes
        # 온도: 화력에 따라 100도까지 상승.
        # **식는 속도는 데우는 속도보다 느리다** — 냄비와 내용물에 열이 남아
        # 있기 때문이다. 불을 꺼도 한동안 계속 끓는다. 예전 모델은 화력을 0 으로
        # 하면 증발이 즉시 멈춰, '미리 끄는' 판단이 필요 없는 세계였다.
        target_t = 40 + self.power * 13
        k = self.HEAT_K if target_t > self.temp_c else self._cool_k(self.mass_g)
        # k 는 **1분당** 비율이다. 그대로 쓰면 tick(0.5) 도 tick(1.0) 과 같은
        # 양만큼 온도를 바꾼다 — 관측을 자주 할수록 빨리 식는 세계가 된다.
        # 관측 주기를 가변으로 만들면서 이 갱신식을 그대로 둔 것이 원인이었고,
        # 그래서 여열 예측(1분 단위 계산)이 실제(0.45분 단위)와 2배 어긋났다.
        # 지수 감쇠의 올바른 이산화로 고친다.
        k_eff = 1 - (1 - k) ** minutes
        t_before = self.temp_c
        self.temp_c += (min(target_t, 100) - self.temp_c) * k_eff
        # 증발량은 **스텝 동안의 평균 온도**로 구한다. 끝 온도만 쓰면 큰
        # 스텝에서 과소평가된다(5분을 1분 단위로 가면 7.3g, 0.1분 단위로
        # 가면 8.8g 으로 20% 어긋났다). 중점을 쓰면 스텝 크기에 덜 의존한다.
        t_mid = (t_before + self.temp_c) / 2
        # 증발: 끓기 시작(약 90도) 이후 본격화.
        # 증발은 화력이 아니라 **온도**로 일어난다. 화력은 온도를 유지할 뿐이다.
        # 그래서 화력이 0 이어도 끓는 동안에는 계속 준다 — 이것이 여열이다.
        boil = max(0.0, (t_mid - 88) / 12)
        evap = (self.BASE_EVAP + self.power * self.POWER_EVAP) * boil * minutes
        evap *= random.uniform(0.92, 1.08)          # 회차 간 편차
        self.mass_g = max(0.0, self.mass_g - evap)

        # 눌어붙음은 **조리기만 알 수 있는 값**이다. 끓는 상태에서 수분이 줄수록,
        # 화력이 셀수록 바닥에 눌어붙는다. 예전에는 이 값을 기록의 가정값으로
        # 두고 세척 코스를 골랐다 — 조리를 하고도 조리 결과를 안 본 셈이다.
        # 계수 0.30 은 시뮬레이터 값이며 실측이 아니다.
        dryness = 1.0 - (self.mass_g / self.initial_mass_g) if self.initial_mass_g else 0.0
        self.soil = min(1.0, self.soil +
                        boil * (0.30 + dryness) * (self.power / 5) * minutes * 0.30)
        self.peak_temp_c = max(self.peak_temp_c, self.temp_c)
        self.log.append((round(self.elapsed_min, 1), round(self.mass_g, 1),
                         round(self.temp_c, 1)))

    def state(self):
        return {"running": self.running,
                "elapsed_min": round(self.elapsed_min, 1),
                "mass_g": round(self.mass_g, 1),
                "initial_mass_g": round(self.initial_mass_g, 1),
                "mass_ratio": round(self.mass_g / self.initial_mass_g, 4)
                if self.initial_mass_g else 0.0,
                "temp_c": round(self.temp_c, 1),
                "peak_temp_c": round(self.peak_temp_c, 1),
                "soil_score": round(self.soil, 4),
                # 증발 편차(±8%)가 누적되므로 눌어붙음도 회차마다 흔들린다.
                # 상대 표준편차 2.5% 는 파이프라인 120회에서 잰 값이다
                # (평균 0.5286, 표준편차 0.0133).
                "soil_sigma": round(self.soil * 0.025, 4),
                "added_g": round(self.added_g, 1),
                "fill_ratio": round(self.mass_g / self.capacity_g, 3)
                if self.capacity_g else 0.0,
                "overflow_risk": self.overflow_risk(),
                "power": self.power}

    def _cool_k(self, mass_g: float) -> float:
        """식는 속도는 양에 따라 다르다.

        작은 냄비는 빨리 식는다 — 열용량은 질량에 비례하는데 열이 빠져나가는
        표면적은 질량의 2/3 제곱으로만 늘기 때문이다. 이것을 무시하고 상수로
        두었더니, 237g 짜리 삼계탕에서 여열을 620g 기준으로 과대평가해
        4.5분에 불을 껐다가 목표에 닿지 못했다.
        """
        return self.COOL_K * (self.REF_MASS_G / max(1.0, mass_g)) ** (1 / 3)

    def predict_residual_g(self, horizon_min: int = 12) -> float:
        """지금 불을 끄면 **앞으로 더 날아갈 양**.

        숙련자는 목표에 닿고 나서 끄지 않는다. 닿기 전에 끈다 — 여열로
        조금 더 가기 때문이다. 그 '조금' 이 얼마인지는 기기가 자기 열 모델로
        안다. 제어기가 이 값을 모르면 항상 목표를 지나친다.
        """
        # tick 과 같은 순서로 계산해야 한다 — tick 은 온도를 먼저 낮추고
        # 증발을 구한다. 순서를 뒤집으면 첫 항이 과대평가돼 3배 틀린다.
        t, total, m = self.temp_c, 0.0, self.mass_g
        for _ in range(horizon_min):
            t0 = t
            t += (40 - t) * self._cool_k(m)
            boil = max(0.0, ((t0 + t) / 2 - 88) / 12)   # tick 과 같은 중점법
            if boil <= 0:
                break
            gone = self.BASE_EVAP * boil          # 화력 0 기준
            total += gone
            m = max(1.0, m - gone)                # 줄어든 양은 더 빨리 식는다
        return round(total, 2)

    def rest_until_still(self, max_min: int = 20) -> dict:
        """불을 끄고 **끓음이 멎을 때까지** 둔다. 그리고 그때 상태를 돌려준다.

        사람이 먹는 것은 불을 끄는 순간의 음식이 아니라 여열이 끝난 음식이다.
        그런데 지금까지 기록에 저장한 값은 **불을 끄는 순간**의 것이었다.
        그 값을 다음 목표로 삼으면, 재현할 때 그 지점에서 또 여열이 붙어
        매번 조금씩 더 졸아든다. 5회 반복하니 실제로 먹는 상태가
        0.7654 에서 0.7599 로 흘러갔다.

        "만족한 결과" 는 먹은 상태다. 그 시점에 재야 한다.
        """
        self.set_power(0)
        for _ in range(max_min):
            before = self.mass_g
            self.tick(1.0)
            if before - self.mass_g < 0.01:      # 더 이상 줄지 않는다
                break
        return self.state()

    def add_ingredient(self, name: str, grams: float, temp_c: float = 8.0):
        """조리 도중 재료를 넣는다.

        된장찌개에서 두부는 처음부터 넣지 않는다 — 부서진다. 그런데 지금까지
        이 시뮬레이터는 모든 재료를 한 번에 넣고 졸이기만 했다. 제안서는
        "재료 투입 전후를 구분한 뒤 같은 조리 단계의 목표와 현재 상태를
        비교한다" 고 적어 두었는데, 코드에는 조리 단계가 없었다.

        투입은 두 가지를 바꾼다.
          1) 질량이 **늘어난다** — 졸임 정도의 분모(총 투입량)도 같이 늘어야 한다.
             분모를 그대로 두면 비율이 1 을 넘어 목표 판정이 깨진다.
          2) 온도가 **떨어진다** — 찬 재료가 열을 가져간다. 다시 끓기까지
             걸리는 시간이 실제 조리 시간의 큰 몫이다.
        """
        if not self.running or grams <= 0:
            return None
        before_t = self.temp_c
        # 섞인 뒤 온도 = 질량가중 평균 (비열은 같다고 본다 — 물 기준 근사)
        total = self.mass_g + grams
        self.temp_c = (self.mass_g * self.temp_c + grams * temp_c) / total
        self.mass_g = total
        self.initial_mass_g += grams          # 졸임 비율의 분모도 늘린다
        self.added_g += grams
        self.log.append((round(self.elapsed_min, 1), round(self.mass_g, 1),
                         round(self.temp_c, 1)))
        return {"name": name, "grams": grams,
                "temp_drop_c": round(before_t - self.temp_c, 1),
                "mass_g": round(self.mass_g, 1)}

    def set_power(self, level: int):
        self.power = max(0, min(5, int(level)))
        return self.state()

    def stop(self):
        self.running = False
        return self.state()


COOKER = Cooker()


# ══════════════════════════ 식기세척기 (세척) ══════════════════════════
_DISHWASHER = {"scheduled": None}


def dishwasher_recommend_course(soil_score: float):
    """조리 이력에서 나온 눌어붙음 점수로 세척 코스를 고른다.

    이 정보는 지금까지 조리기에만 있었고 식기세척기는 알 수 없었다.
    """
    if soil_score >= 0.55:
        c = {"course": "강력(불림 포함)", "minutes": 145, "temp_c": 70}
    elif soil_score >= 0.30:
        c = {"course": "표준", "minutes": 95, "temp_c": 60}
    else:
        c = {"course": "에코", "minutes": 60, "temp_c": 50}
    c["reason"] = f"조리 기록의 눌어붙음 점수 {soil_score}"
    return c


def dishwasher_schedule(course: str, start_after_min: int):
    _DISHWASHER["scheduled"] = {"course": course, "start_after_min": start_after_min}
    return {"ok": True, **_DISHWASHER["scheduled"]}


# ══════════════════════════ 조리 기록 조회 ══════════════════════════
def record_list(menu: str | None = None):
    out = []
    for r in RECORDS.values():
        if menu and menu not in r["menu"]:
            continue
        out.append({"record_id": r["record_id"], "menu": r["menu"],
                    "saved_by": r["saved_by"], "saved_at": r["saved_at"],
                    "target_mass_ratio": r["target_mass_ratio"],
                    "satisfaction": r["satisfaction"]})
    return out


def record_get(record_id: str):
    r = RECORDS.get(record_id)
    return dict(r) if r else {"error": f"{record_id} 없음"}


def record_progress(record_id: str):
    """현재 상태와 목표 궤적의 차이를 계산한다. 에이전트의 판단 근거."""
    r = RECORDS.get(record_id)
    if not r:
        return {"error": f"{record_id} 없음"}
    s = COOKER.state()
    if not s["initial_mass_g"]:
        return {"error": "조리가 시작되지 않음"}
    target = r["target_mass_ratio"]
    now = s["mass_ratio"]
    done_pct = (1 - now) / (1 - target) * 100 if target < 1 else 100.0
    # 최근 1분 증발률로 잔여 시간 추정
    rate = 0.0
    if len(COOKER.log) >= 2:
        (t0, m0, _), (t1, m1, _) = COOKER.log[-2], COOKER.log[-1]
        if t1 > t0:
            rate = (m0 - m1) / (t1 - t0)
    remain_g = max(0.0, s["mass_g"] - target * s["initial_mass_g"])
    eta = round(remain_g / rate, 1) if rate > 0.1 else None
    return {"record_id": record_id, "target_mass_ratio": target,
            "current_mass_ratio": now, "progress_pct": round(done_pct, 1),
            "remaining_g": round(remain_g, 1), "eta_min": eta,
            "temp_c": s["temp_c"], "power": s["power"],
            "reached": now <= target}


# ══════════════════════════ 조리 기록 저장 ══════════════════════════
OVERSHOOT_TOL = 0.02        # 목표를 이만큼 넘게 지나쳤으면 그대로 저장하지 않는다


def record_review(base: dict, measured: dict) -> dict:
    """저장하기 전에 이번 결과가 목표대로 됐는지 본다.

    목표를 지나친 값을 그대로 다음 목표로 저장하면 **오차가 학습된다.**
    한 번 5%p 더 졸면 다음엔 그 자리에서 또 지나칠 수 있다.
    그래서 저장은 자동이 아니라 **사용자 판단**을 거친다 —
    제안서가 말한 "평소 조리에 저장 한 번을 더한다" 가 이 지점이다.
    """
    aimed = base.get("target_mass_ratio")
    got = measured.get("final_ratio")
    if aimed is None or got is None:
        # 비교할 목표가 없으면 잘 됐는지 알 수 없다. 모르는 채로 저장하면
        # 근거 없는 값이 다음 목표가 된다 — 물어보는 것이 맞다.
        return {"gap": None, "overshot": False, "suggest": "ask",
                "why": "비교할 목표가 없어 결과를 판정할 수 없다 — 저장 여부를 묻는다"}
    gap = round(aimed - got, 4)            # 양수면 목표보다 더 졸았다
    over = gap > OVERSHOOT_TOL
    return {
        "aimed": aimed, "got": got, "gap": gap, "overshot": over,
        "suggest": "ask" if over else "save",
        "why": (f"목표 {aimed} 보다 {gap} 더 졸았다 — 이 결과가 마음에 들었는지 "
                f"확인이 필요하다" if over
                else f"목표 {aimed} 에 {abs(gap)} 이내로 도달했다"),
    }


def record_save(base: dict, measured: dict, saved_by: str = "본인",
                satisfaction: int = 4, device: str = "자취방 조리기",
                actual_initial_g: float | None = None) -> dict:
    """끝난 조리를 다음 번 목표로 저장한다.

    제안의 핵심이 "좋아했던 결과를 기록하고 다시 쓴다" 인데, 지금까지 저장하는
    쪽이 없었다. 공개 레시피로 처음 만든 메뉴는 목표 질량비가 **가정값**이고,
    한 번 만들고 나면 그 자리를 **이번에 실제로 잰 값**이 대신해야 한다.

    actual_initial_g 를 주면 그 값을 초기 질량으로 쓴다. 예전에는 원본 기록의
    값을 그대로 복사해서, 재고가 모자라 적게 담았어도 "다 넣었다" 고 남았다.
    """
    rid = f"rec_{len(RECORDS) + 1:03d}"
    rec = {
        "record_id": rid,
        "menu": base.get("menu"),
        "saved_by": saved_by,
        "device": device,                   # 어느 기기에서 만들었나
        "saved_at": "실행 시점",
        "ingredients": [dict(i) for i in base.get("ingredients", [])],
        # 원본을 베끼지 않고 이번에 실제로 담은 양을 남긴다
        "initial_mass_g": (round(actual_initial_g) if actual_initial_g
                           else base.get("initial_mass_g")),
        # 목표는 **가정값일 때만** 실측으로 갈아 끼운다.
        #
        # 매번 이번 실측을 다음 목표로 삼으면 목표가 흘러간다. 제어에는 늘
        # 작은 편향이 있고(여열 보정을 해도 평균 0.0034 더 졸았다), 그것이
        # 회차마다 쌓이기 때문이다. 5회 반복하니 0.78 이 0.7509 까지 갔다.
        #
        # "엄마 된장찌개" 의 목표는 한 번 정해지면 매번 바뀌지 않는다.
        # 목표를 바꾸는 것은 사용자가 "더 졸여줘" 라고 할 때지, 기계가
        # 조금 빗나갔을 때가 아니다.
        "target_mass_ratio": (measured.get("final_ratio")
                              if base.get("estimated")
                              else base.get("target_mass_ratio")),
        "target_from": "이번 실측" if base.get("estimated") else "이전 목표 유지",
        "peak_temp_c": measured.get("peak_temp_c"),
        "cook_minutes_observed": measured.get("cook_min"),
        "soil_score": measured.get("soil_score", base.get("soil_score")),
        "satisfaction": satisfaction,
        "estimated": False,                 # 실측이다
        "from_record": base.get("record_id"),
    }
    RECORDS[rid] = rec
    return rec


def record_scale(rec: dict, to_servings: int, device: str | None = None) -> dict:
    """기록을 우리 집 인원에 맞춰 늘리거나 줄인다.

    지금까지 가구원 수는 '세척까지 할지' 를 정하는 데만 쓰였다. 그래서
    4인 가구가 1인분짜리 공개 레시피(237g)를 그대로 조리했다 — 한 사람
    몫도 안 되는 양이다. 몇 인분을 만들지는 **조리의 첫 번째 결정**인데
    그것이 빠져 있었다.

    기기 용량은 상한이다. 4인분이 냄비에 안 들어가면 들어가는 만큼만 한다.
    """
    src = max(1, rec.get("servings", 1))
    ratio = to_servings / src
    why = f"{src}인분 기록 → {to_servings}인분 = {ratio:.3g}배"

    base = rec.get("initial_mass_g") or sum(
        i["qty_g"] for i in rec.get("ingredients", []))
    spec = device_spec(device or "")
    capped = False
    if spec:
        limit = spec["capacity_g"] * FILL_LIMIT
        if base * ratio > limit:
            ratio = limit / base
            capped = True
            why += (f" 인데 {round(base * to_servings / src)}g 은 {device} 상한 "
                    f"{spec['capacity_g']}g 의 {FILL_LIMIT:.0%} 를 넘는다 "
                    f"→ {ratio:.3g}배로 제한")

    out = dict(rec)
    out["ingredients"] = [{**i, "qty_g": max(1, round(i["qty_g"] * ratio))}
                          for i in rec.get("ingredients", [])]
    if rec.get("initial_mass_g"):
        out["initial_mass_g"] = round(rec["initial_mass_g"] * ratio)
    out["servings"] = round(src * ratio, 1)
    out["scale_basis"] = why
    out["capped_by_device"] = capped
    return out


# ══════════════════════════ 기기 간 기록 이식 ══════════════════════════
def record_import(rec: dict, to_device: str, capacity_ratio: float | None = None,
                  new_id: str | None = None) -> dict:
    """다른 기기에서 만든 기록을 이 기기로 가져온다.

    본가 6인용 냄비에서 만든 것을 자취방 2인용으로 옮기면 재료량은 줄여야 한다.
    그런데 **목표 질량비는 그대로 둔다** — 비율이라 용량과 무관하기 때문이다.

    이것이 '제어 입력을 재생하는' 선행 특허와 갈리는 지점이다. 화력·시간을
    복사하면 기기가 바뀔 때 결과도 바뀐다. 우리는 목표를 상태로 두었으므로
    기기가 알아서 다른 시간을 쓴다.
    """
    rid = new_id or f"rec_{len(RECORDS) + 1:03d}"
    out = dict(rec)
    out["record_id"] = rid

    # 비율을 주지 않으면 **기기 제원에서 계산한다.** 냄비가 바뀌어도
    # 사람이 숫자를 넣지 않아도 되는 것이 이 함수의 요점이다.
    if capacity_ratio is None:
        capacity_ratio, why = capacity_ratio_between(
            rec.get("device", ""), to_device, rec.get("initial_mass_g", 0))
        out["scale_basis"] = why
    else:
        out["scale_basis"] = f"호출자가 지정한 비율 {capacity_ratio:.3g}배"

    # 크게 줄이면 반올림으로 0g 이 되는 재료가 생긴다. 된장 0g 인 된장찌개는
    # 된장찌개가 아니다. 최소 1g 을 보장하고, 그런 항목을 기록에 남긴다.
    rounded, floored = [], []
    for i in rec.get("ingredients", []):
        q = i["qty_g"] * capacity_ratio
        if q < 1:
            floored.append(i["name"])
            q = 1
        rounded.append({**i, "qty_g": round(q)})
    out["ingredients"] = rounded
    if floored:
        out["scale_warning"] = (f"용량을 {capacity_ratio:.3g}배로 줄이면서 "
                                f"{', '.join(floored)} 이(가) 1g 미만이 되어 "
                                f"1g 으로 올렸다 — 맛이 달라질 수 있다")
    if rec.get("initial_mass_g"):
        out["initial_mass_g"] = round(rec["initial_mass_g"] * capacity_ratio)
    out["cook_minutes_observed"] = None      # 시간은 기기마다 다르다 — 버린다
    out["imported_from"] = {"record_id": rec.get("record_id"),
                            "device": rec.get("device"),
                            "saved_by": rec.get("saved_by"),
                            "capacity_ratio": capacity_ratio}
    out["device"] = to_device
    RECORDS[rid] = out
    return out
