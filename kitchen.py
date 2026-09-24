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
        "menu": "된장찌개",
        "saved_by": "어머니",
        "device": "본가 6인용 조리기",
        "saved_at": "2026-08-14",
        "ingredients": [                      # 준비 단계가 읽는다
            {"name": "배추", "qty_g": 200},
            {"name": "두부", "qty_g": 150},
            {"name": "된장", "qty_g": 40},
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
        "menu": "된장찌개",
        "saved_by": "본인",
        "device": "자취방 2인용 조리기",
        "saved_at": "2026-09-02",
        "ingredients": [
            {"name": "배추", "qty_g": 200},
            {"name": "두부", "qty_g": 150},
            {"name": "된장", "qty_g": 35},
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
        "menu": "닭고기 표고 조림",
        "saved_by": "본인",
        "device": "자취방 2인용 조리기",
        "saved_at": "2026-08-30",
        "ingredients": [
            {"name": "닭고기", "qty_g": 400},
            {"name": "표고버섯", "qty_g": 120},
            {"name": "간장", "qty_g": 50},
        ],
        "initial_mass_g": 700,
        "target_mass_ratio": 0.72,            # 조림이라 더 졸인다
        "peak_temp_c": 99,
        "cook_minutes_observed": 14,
        "soil_score": 0.71,                   # 조림은 더 눌어붙는다
        "satisfaction": 5,
    },
}

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
    want = round(target_g * random.uniform(0.97, 1.03))
    actual = min(item["qty_g"], want)
    short = max(0, want - actual)
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
    log: list = field(default_factory=list)

    def start(self, initial_mass_g: float, extra_water_g: float = 0.0, power: int = 3):
        self.running = True
        self.elapsed_min = 0.0
        self.initial_mass_g = initial_mass_g + extra_water_g
        self.mass_g = self.initial_mass_g
        self.extra_water_g = extra_water_g
        self.temp_c = 20.0
        self.power = power
        self.log = [(0.0, self.mass_g, self.temp_c)]

    def tick(self, minutes: float = 1.0):
        """시간을 진행시킨다. 증발량은 화력과 온도에 따라 달라진다."""
        if not self.running:
            return
        self.elapsed_min += minutes
        # 온도: 화력에 따라 100도까지 상승
        target_t = 40 + self.power * 13
        self.temp_c += (min(target_t, 100) - self.temp_c) * 0.55
        # 증발: 끓기 시작(약 90도) 이후 본격화
        boil = max(0.0, (self.temp_c - 88) / 12)
        evap = self.power * 7.0 * boil * minutes
        evap *= random.uniform(0.92, 1.08)          # 회차 간 편차
        self.mass_g = max(0.0, self.mass_g - evap)
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
                "power": self.power}

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
        return {"gap": None, "overshot": False, "suggest": "save",
                "why": "비교할 목표가 없다"}
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
        # 여기가 핵심 — 가정값이 아니라 이번에 잰 값이 다음 목표가 된다
        "target_mass_ratio": measured.get("final_ratio", base.get("target_mass_ratio")),
        "peak_temp_c": measured.get("peak_temp_c"),
        "cook_minutes_observed": measured.get("cook_min"),
        "soil_score": measured.get("soil_score", base.get("soil_score")),
        "satisfaction": satisfaction,
        "estimated": False,                 # 실측이다
        "from_record": base.get("record_id"),
    }
    RECORDS[rid] = rec
    return rec


# ══════════════════════════ 기기 간 기록 이식 ══════════════════════════
def record_import(rec: dict, to_device: str, capacity_ratio: float = 1.0,
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
    out["ingredients"] = [{**i, "qty_g": round(i["qty_g"] * capacity_ratio)}
                          for i in rec.get("ingredients", [])]
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
