# -*- coding: utf-8 -*-
"""주방 기기 4종 시뮬레이터 + 조리 기록(상태 궤적) 저장소.

LLM 과 무관한 순수 로직이다. 에이전트는 이 모듈의 함수만 호출한다.
실제 기기(LG ThinQ 등)로 교체할 때 이 파일만 바꾸면 된다.
"""
from __future__ import annotations
import copy
import math
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
            # 사람이 담으면 목표와 조금 다르다. 그 '담으려던 양' 을 함께
            # 돌려준다 — 없으면 "목표 80g → 실계량 80g, 1g 모자람" 처럼
            # 모순으로 읽힌다(81g 을 담으려다 재고가 80g 이었던 것이다).
            "attempted_g": want,
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
    solid_g: float = 0.0              # 고형분 (재료 자체 무게)
    absorbed_g: float = 0.0           # 재료가 빨아들인 물
    absorb_cap_g: float = 0.0         # 재료가 더 먹을 수 있는 양
    skimmed_g: float = 0.0            # 걷어낸 양 (거품·기름)
    watered_g: float = 0.0            # 되돌리려고 부은 물
    lid: bool = False                 # 뚜껑
    stir_since_min: float = 0.0       # 마지막으로 저은 뒤 지난 시간
    deterministic: bool = False       # True 면 회차 편차를 넣지 않는다(예측용)
    cook_units: float = 0.0           # 익힘 누적 (온도 x 시간)
    need_units: float = 0.0           # 다 익으려면 필요한 양

    # ══════════════════════════════════════════════════════════════════
    # 상수 표 — **무엇에서 나왔는가**를 적어 둔다.
    #
    # 열 모델을 한 번 갈아엎고 나서, 그 위에서 정한 상수·임계·시험 기대값이
    # 전부 낡았는데 "예외 0건·회귀 통과" 로는 하나도 안 잡혔다. 값을 처음부터
    # 다시 읽어서야 찾았다. 그래서 상수마다 근거를 남긴다 — 근거를 못 적는
    # 숫자는 다음 사람이 고칠 수 없다.
    #
    #   측정에서 역산   C_WATER · LATENT · POT_EQ_G · WATT_PER_POWER · LOSS_W_PER_K
    #   실제 조리 관찰  REST_MIN · ABSORB_BASE_C · LID_LOSS · LID_EVAP
    #   시뮬레이터 가정 SURF_EVAP · ABSORB_RATE · STIR_RELIEF · soil 계수
    #
    # 모델을 바꾸면 **아래 전부를 다시 재야 한다.** 함께 낡는 것:
    #   kitchen_domain: min_ctrl(한 걸음 크기) · ready_at(끓음 판정) ·
    #                   넘침 임계 · 교반 임계
    #   skills/core:    ETA_SAFETY(가속 폭) · hold_power(유지 중 증발)
    #   skills/core:    AftercareSkill.CUTS(눌어붙음 분포)
    # ══════════════════════════════════════════════════════════════════
    ABSORB_RATE: float = 0.35         # 분당 흡수 속도 (남은 용량의 비율)
    # 불리기는 끓어야 시작하는 일이 아니다. 미지근해도 쌀은 물을 먹는다.
    # 익힘 기준(60도)을 그대로 쓰다가 2kg 짜리에서 **6분까지 흡수가 0** 이었다.
    ABSORB_BASE_C: float = 40.0
    STIR_RELIEF: float = 0.55         # 저으면 그 뒤 눌어붙음이 이 비율로 준다
    COOK_BASE_C: float = 60.0         # 이 온도 위에서만 익는다

    # ── 열 모델 ──────────────────────────────────────────────────────
    # 예전에는 온도를 `T += (목표온도 - T) * k` 로 밀었다(1차 지연). 그러면
    #   · 양이 달라도 데우는 시간이 같고 (310g 과 2000g 이 둘 다 3.75분)
    #   · 출발 온도 차이가 지수적으로 사라진다 (냉장 재료를 넣으나 마나)
    # 실제 화구는 **일정한 열량**을 넣는다. 온도는 열량 수지로 정해진다.
    #
    #   dT/dt = (투입 - 손실) / (유효질량 x 비열)
    #   끓는점에 닿으면 남는 열은 온도가 아니라 **증발**로 간다.
    #
    # 상수는 두 가지 실제 값에서 역산했다(둘 다 가정이며 실측 교체 대상).
    #   · 620g 을 불 끄면 100→90도가 약 3분  → 방열 2.28 W/K
    #   · 620g 을 화력 3 으로 20→100도 약 5.5분 → 화력당 298 W
    C_WATER: float = 4.18             # 물 비열 J/g·K
    LATENT_J_PER_G: float = 2260.0    # 증발 잠열
    POT_EQ_G: float = 172.0           # 냄비 열용량 (물 환산 g)
    WATT_PER_POWER: float = 298.0     # 화력 한 단계당 투입 열량 W
    LOSS_W_PER_K: float = 2.28        # 주변으로 나가는 열 (뚜껑 열었을 때)
    LID_LOSS: float = 0.45            # 뚜껑을 덮으면 손실이 이 비율
    LID_EVAP: float = 0.15            # 뚜껑을 덮으면 증발한 물이 맺혀 돌아온다
    LID_OVERFLOW: float = 1.6         # 뚜껑을 덮으면 거품이 갇혀 더 잘 넘친다
    SKIM_SOLID: float = 0.8           # 걷어낸 거품 중 고형분(단백질·기름) 비율
    SURF_EVAP: float = 0.02           # 끓지 않을 때 표면 증발 g/(K·분)
    # 불을 끄고 상에 올리기까지. 이 사이에도 물은 날아가므로 **기록에 남길
    # 값은 이 시점의 것**이다. 3분은 가정이며, 실제로는 가구마다 다르다.
    REST_MIN: int = 3
    BOIL_C: float = 100.0
    AMBIENT_C: float = 20.0
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
        # 뚜껑을 덮으면 거품이 갇혀 **더 잘 넘친다.** 국물 요리에서 넘치는
        # 것은 대개 뚜껑을 덮어 둔 채 화력을 올렸을 때다.
        lid_factor = self.LID_OVERFLOW if self.lid else 1.0
        return round(min(1.0, fill * boil * (self.power / 5) * lid_factor), 3)

    def free_liquid_g(self) -> float:
        """졸일 수 있는 **자유 수분**. 고형분과 재료가 빨아들인 물은 뺀다.

        찹쌀 100g 은 국물을 200g 쯤 먹는다. 먹어도 **총 질량은 그대로**라
        질량비만 보면 아무 일도 없는 것처럼 보이지만, 국물은 줄어 있다.
        자유 수분이 바닥나면 증발이 멎고 바닥이 타기 시작한다 —
        질량비만 쫓는 제어기는 그 순간을 영원히 기다린다.
        """
        return max(0.0, self.mass_g - self.solid_g - self.absorbed_g)

    def skim(self, grams: float, what: str = "거품"):
        """거품·기름을 걷어낸다. 질량이 주는데 이것은 증발이 아니다."""
        if not self.running or grams <= 0:
            return None
        take = min(grams, max(0.0, self.mass_g - self.solid_g))
        self.mass_g -= take
        self.skimmed_g += take
        # 걷어내는 것은 **떠오른 단백질·기름**이지 국물이 아니다.
        # 고형분에서 빼지 않으면 자유 수분(졸일 수 있는 물)이 그만큼
        # 줄어든 것으로 계산돼, 실제보다 빨리 "국물이 바닥났다" 고 본다.
        self.solid_g = max(0.0, self.solid_g - take * self.SKIM_SOLID)
        # 걷어낸 만큼 분모에서도 뺀다. 그러지 않으면 제어기가 이것을
        # 졸아든 것으로 읽어 목표에 일찍 닿았다고 착각한다.
        self.initial_mass_g = max(1.0, self.initial_mass_g - take)
        return {"what": what, "grams": round(take, 1),
                "mass_g": round(self.mass_g, 1)}

    def add_water(self, grams: float, temp_c: float = 18.0):
        """물을 부어 되돌린다.

        졸이는 것은 되돌릴 수 없다고 가정해 왔지만, 요리에는 되돌릴 수단이
        하나 있다 — 물을 더 붓는 것이다. 다만 **공짜가 아니다.** 국물이
        묽어진다. 그래서 얼마나 부었는지를 함께 남겨, 사용자가 판단할 수
        있게 한다. 졸임 비율의 분모(총 투입량)는 건드리지 않는다.
        """
        if not self.running or grams <= 0:
            return None
        # 찬물을 부으면 **온도가 떨어진다.** 재료를 넣을 때는 이 계산을
        # 하면서 물만 빠뜨리고 있었다 — 같은 물리인데 한쪽만 구현돼 있었다.
        before_t = self.temp_c
        total = self.mass_g + grams
        self.temp_c = (self.mass_g * self.temp_c + grams * temp_c) / total
        self.mass_g = total
        self.watered_g += grams
        return {"grams": round(grams, 1), "mass_g": round(self.mass_g, 1),
                "temp_drop_c": round(before_t - self.temp_c, 1),
                "dilution": round(self.watered_g / max(1.0, self.mass_g), 4)}

    def set_lid(self, closed: bool):
        """뚜껑. 덮으면 빨리 끓고 증발은 거의 없다 — 졸이려면 열어야 한다."""
        before = self.lid
        self.lid = bool(closed)
        return {"lid": self.lid, "changed": before != self.lid}

    def stir(self):
        """젓는다. 바닥에 가라앉은 것을 띄워 눌어붙음을 줄인다."""
        if not self.running:
            return None
        self.stir_since_min = 0.0
        return {"stirred_at_min": round(self.elapsed_min, 1),
                "soil": round(self.soil, 4)}

    def start(self, initial_mass_g: float, extra_water_g: float = 0.0, power: int = 3,
              capacity_g: float | None = None, solid_g: float = 0.0,
              absorb_cap_g: float = 0.0, lid: bool = False,
              need_units: float = 0.0, start_temp_c: float = 20.0):
        self.running = True
        if capacity_g:
            self.capacity_g = capacity_g
        self.solid_g = solid_g
        self.absorb_cap_g = absorb_cap_g
        self.lid = lid
        self.need_units = need_units
        self.elapsed_min = 0.0
        self.initial_mass_g = initial_mass_g + extra_water_g
        self.mass_g = self.initial_mass_g
        self.extra_water_g = extra_water_g
        # 냉장고에서 갓 꺼낸 재료는 20도가 아니다. 출발 온도가 낮으면
        # 끓기까지 더 걸리고, 그만큼 조리 시간이 길어진다.
        self.temp_c = start_temp_c
        self.power = power
        self.soil = 0.0
        self.added_g = 0.0
        self.absorbed_g = 0.0
        self.skimmed_g = 0.0
        self.watered_g = 0.0
        self.stir_since_min = 0.0
        self.cook_units = 0.0
        self.peak_temp_c = 20.0
        self.log = [(0.0, self.mass_g, self.temp_c)]

    def tick(self, minutes: float = 1.0):
        """시간을 진행시킨다. 증발량은 화력과 온도에 따라 달라진다."""
        if not self.running:
            return
        self.elapsed_min += minutes
        # 온도: 화력에 따라 100도까지 상승.
        # **식는 속도는 데우는 속도보다 느리다** — 냄비와 내용물에 열이 남아
        # 있기 때문이다. 불을 꺼도 한동안 계속 끓는다.
        #
        # 열량 수지로 푼다. 투입(화력) - 손실(방열) 이 남으면 온도가 오르고,
        # 끓는점에 닿으면 남는 열은 **온도가 아니라 증발**로 간다.
        m_eff = self.mass_g + self.POT_EQ_G
        p_in = self.power * self.WATT_PER_POWER
        h = self.LOSS_W_PER_K * (self.LID_LOSS if self.lid else 1.0)
        p_loss = h * (self.temp_c - self.AMBIENT_C)
        p_net = p_in - p_loss
        sec = minutes * 60.0
        heat_cap = m_eff * self.C_WATER          # J/K

        t_before = self.temp_c
        evap_boil = 0.0
        if self.temp_c < self.BOIL_C:
            dT = p_net * sec / heat_cap
            if self.temp_c + dT > self.BOIL_C:
                # 끓는점까지 올리고 남은 열은 증발에 쓰인다
                used = (self.BOIL_C - self.temp_c) * heat_cap
                self.temp_c = self.BOIL_C
                evap_boil = max(0.0, p_net * sec - used) / self.LATENT_J_PER_G
            else:
                self.temp_c += dT
        else:
            if p_net > 0:
                # 끓는 중에는 온도가 유지되고 투입분이 전부 증발로 간다
                evap_boil = p_net * sec / self.LATENT_J_PER_G
            else:
                self.temp_c += p_net * sec / heat_cap

        # 재료가 국물을 빨아들인다. 총 질량은 그대로지만 졸일 수 있는 물이 준다.
        room = max(0.0, self.absorb_cap_g - self.absorbed_g)
        if room > 0 and self.temp_c > self.ABSORB_BASE_C:
            take = min(room * self.ABSORB_RATE * minutes, self.free_liquid_g())
            self.absorbed_g += take

        # 끓지 않아도 뜨거운 표면에서는 물이 날아간다. 여열 구간이 이 몫이다.
        t_mid = (t_before + self.temp_c) / 2
        evap = evap_boil + self.SURF_EVAP * max(0.0, t_mid - 40) * minutes
        # 뚜껑을 덮으면 증발한 물이 맺혀 돌아온다 — 졸지 않는다.
        if self.lid:
            evap *= self.LID_EVAP
        # 자유 수분보다 많이 날아갈 수는 없다. 바닥나면 증발이 멎는다.
        evap = min(evap, self.free_liquid_g())
        # 회차 간 편차. **예측용 복사본에서는 넣지 않는다** — 예측이 전역
        # 난수를 소비하면 예측을 몇 번 했느냐에 따라 실제 조리 결과가 달라진다.
        if not self.deterministic:
            evap *= random.uniform(0.92, 1.08)
        self.mass_g = max(0.0, self.mass_g - evap)

        # 눌어붙음은 **조리기만 알 수 있는 값**이다. 끓는 상태에서 수분이 줄수록,
        # 화력이 셀수록 바닥에 눌어붙는다. 예전에는 이 값을 기록의 가정값으로
        # 두고 세척 코스를 골랐다 — 조리를 하고도 조리 결과를 안 본 셈이다.
        # 계수 0.30 은 시뮬레이터 값이며 실측이 아니다.
        # 눌어붙음은 **자유 수분이 적을수록** 심해진다. 총 질량이 아니라
        # 국물이 있느냐가 기준이다 — 찹쌀이 물을 다 먹으면 바닥이 탄다.
        boil = max(0.0, (t_mid - 88) / 12)
        free_ratio = (self.free_liquid_g() / self.mass_g) if self.mass_g else 0.0
        dryness = 1.0 - min(1.0, free_ratio / 0.5)      # 자유 수분 50% 이하부터
        self.stir_since_min += minutes
        # 저은 지 오래될수록 바닥에 가라앉은 것이 눌어붙는다
        stir_factor = self.STIR_RELIEF if self.stir_since_min < 3 else 1.0
        # 누적은 자르지 않고 쌓고, 점수로 바꿀 때만 0~1 로 누른다.
        # min(1.0, ...) 으로 자르면 긴 조리에서 **상한에 붙어 정보를 잃는다**
        # (24분짜리 삼계탕이 1.0 으로 포화해 흔들림도 0 이 됐다).
        # 1 - exp(-누적) 은 1 에 점근하되 닿지 않아 구분이 남는다.
        self.soil += (boil * (0.30 + dryness) * (self.power / 5) * minutes
                      * 0.30 * stir_factor)
        self.peak_temp_c = max(self.peak_temp_c, self.temp_c)

        # 익힘. 제안서는 "익힘·졸임의 판단이 어려운" 사용자를 대상으로 적었는데
        # 코드에는 졸임만 있었다. 질량비가 목표에 닿아도 닭고기가 안 익었으면
        # 그 요리는 끝난 것이 아니다. 60도 위에서 (온도-60) x 시간 을 쌓는다.
        if self.temp_c > self.COOK_BASE_C:
            self.cook_units += (t_mid - self.COOK_BASE_C) * minutes
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
                "soil_score": round(1.0 - math.exp(-self.soil), 4),
                # 증발 편차(±8%)가 누적되므로 눌어붙음도 회차마다 흔들린다.
                # 상대 표준편차 2.5% 는 파이프라인 120회에서 잰 값이다
                # (평균 0.5286, 표준편차 0.0133).
                # 점수의 흔들림. 누적의 상대 오차가 점수로 전달될 때
                # 기울기 exp(-누적) 이 곱해진다.
                "soil_sigma": round(self.soil * 0.025 * math.exp(-self.soil), 4),
                "added_g": round(self.added_g, 1),
                "free_liquid_g": round(self.free_liquid_g(), 1),
                "free_ratio": round(self.free_liquid_g() / self.mass_g, 3)
                if self.mass_g else 0.0,
                "absorbed_g": round(self.absorbed_g, 1),
                "skimmed_g": round(self.skimmed_g, 1),
                "watered_g": round(self.watered_g, 1),
                "lid": self.lid,
                "stir_since_min": round(self.stir_since_min, 1),
                "doneness": round(self.cook_units / self.need_units, 3)
                if self.need_units else 1.0,
                "fill_ratio": round(self.mass_g / self.capacity_g, 3)
                if self.capacity_g else 0.0,
                "overflow_risk": self.overflow_risk(),
                "power": self.power}

    def predict_residual_g(self, horizon_min: int | None = None) -> float:
        """지금 불을 끄면 **앞으로 더 날아갈 양**.

        숙련자는 목표에 닿고 나서 끄지 않는다. 닿기 전에 끈다 — 여열로
        조금 더 가기 때문이다. 그 '조금' 이 얼마인지는 기기가 자기 열 모델로
        안다. 제어기가 이 값을 모르면 항상 목표를 지나친다.

        예측은 **실제와 같은 코드**로 한다. 따로 식을 적어 두면 본체를 고칠
        때 어긋난다 — 실제로 한 번 어긋나서 예측이 2배 틀렸다.
        """
        ghost = copy.copy(self)
        ghost.log = []
        ghost.power = 0
        ghost.deterministic = True
        before = ghost.mass_g
        for _ in range(horizon_min or self.REST_MIN):
            prev = ghost.mass_g
            ghost.tick(1.0)
            if prev - ghost.mass_g < 0.01:
                break
        return round(before - ghost.mass_g, 2)

    def rest_until_still(self, max_min: int | None = None) -> dict:
        """불을 끄고 **상에 올리기까지** 둔다. 그리고 그때 상태를 돌려준다.

        예전에는 "끓음이 멎을 때까지" 로 두었는데, 열 모델을 열량 수지로
        바꾸자 끓음이 멎은 뒤에도 표면 증발이 계속돼 **끝나는 지점이
        사라졌다**(15분 두면 11.3g). 여열은 물리 현상이지만 "얼마나 두는가"
        는 사람의 행동이다. 불 끄고 상에 올리기까지의 시간으로 정의한다.

        사람이 먹는 것은 불을 끄는 순간의 음식이 아니라 여열이 끝난 음식이다.
        기록에 저장하는 값도 그 시점의 것이어야 한다 — 불 끄는 순간의 값을
        저장하면 재현할 때 그 지점에서 또 여열이 붙어 회차마다 더 졸아든다.
        """
        self.set_power(0)
        for _ in range(max_min or self.REST_MIN):
            before = self.mass_g
            self.tick(1.0)
            if before - self.mass_g < 0.01:
                break
        return self.state()

    def add_ingredient(self, name: str, grams: float, temp_c: float = 8.0,
                       need_units: float = 0.0):
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
        # 나중에 넣은 재료는 **넣은 뒤부터** 익는다. 익힘 누적은 냄비 전체
        # 온도로만 쌓이므로, 늦게 들어온 재료가 익힘을 요구하면 요구량을
        # 그만큼 늘려 아직 덜 익은 것으로 본다. (재료별로 따로 세는 것이
        # 정확하지만, 지금 모델에서는 이 근사로 방향은 맞춘다.)
        if need_units > 0:
            self.need_units += need_units
        self.log.append((round(self.elapsed_min, 1), round(self.mass_g, 1),
                         round(self.temp_c, 1)))
        # 넣고 나서 냄비를 넘치면 그것은 투입이 아니라 사고다.
        over = (self.mass_g / self.capacity_g) if self.capacity_g else 0.0
        return {"name": name, "grams": grams,
                "temp_drop_c": round(before_t - self.temp_c, 1),
                "mass_g": round(self.mass_g, 1),
                "fill_ratio": round(over, 3),
                "overfilled": over > 1.0,
                "warning": (f"{name} 을 넣으면 냄비 용량의 {over:.0%} 가 된다 — "
                            f"넘친다. 나눠 담거나 더 큰 냄비가 필요하다"
                            if over > 1.0 else None)}

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
    # 마지막 두 관측 사이의 증발률로 잔여 시간을 추정한다.
    # 관측 주기가 가변이므로 "1분" 이 아니라 실제 간격으로 나눈다.
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
        # **몇 인분을 만든 것인지** 남긴다. 예전에는 이 값이 빠져서,
        # 4인분으로 만든 것이 1인분 기록으로 저장됐다. 다음 회차에 또 4배를
        # 하니 조리량이 회차마다 부풀어 기기 용량에 걸렸다(2036g → 2550g,
        # 조리 시간 21분 → 42분).
        "servings": base.get("servings", 1),
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
