# -*- coding: utf-8 -*-
"""고객 상황 입력.

이 에이전트의 입력은 "된장찌개 해줘" 같은 지시가 아니라 **고객의 상황**이다.
상황이 바뀌면 목표도 제약도 달라지고, 따라서 계획과 시나리오가 달라진다.

같은 코드에 서로 다른 상황을 넣어 서로 다른 시나리오가 나오는 것이
'직무를 반복 수행하는 Agent' 의 증거다.
"""

PERSONAS = {
    "p1_야근": {
        "id": "p1_야근",
        "label": "야근이 잦은 1인 가구",
        "household_size": 1,
        "arrive_home": "21:40",
        "time_budget_min": 25,          # 귀가 후 식사까지 쓸 수 있는 시간
        "next_morning_rush": True,      # 아침에 여유가 없다
        "avoid": [],
        "dislike_noise_after": "22:30",  # 이 시각 이후 소음 회피
        "goal_hint": "늦게 들어와도 데운 것 말고 제대로 된 한 끼",
        "friction_reported": [
            "냉장고를 열어 뭐가 남았는지 확인하는 일",
            "조리 중 냄비 앞을 떠나지 못하는 일",
            "먹고 나서 설거지를 미루는 일",
        ],
        # 가구마다 재고가 다르다. 재고가 다르면 같은 스킬도 다른 결론을 낸다.
        "fridge": [
            {"name": "배추", "qty_g": 320, "stored_days": 5, "shelf_life_days": 7},
            {"name": "두부", "qty_g": 300, "stored_days": 3, "shelf_life_days": 5},
            {"name": "된장", "qty_g": 500, "stored_days": 40, "shelf_life_days": 365},
            {"name": "애호박", "qty_g": 180, "stored_days": 2, "shelf_life_days": 8},
        ],
    },
    "p2_맞벌이": {
        "id": "p2_맞벌이",
        "label": "맞벌이 2인 가구",
        "household_size": 2,
        "arrive_home": "19:20",
        "time_budget_min": 45,
        "next_morning_rush": True,
        "avoid": [],
        "dislike_noise_after": "23:00",
        "goal_hint": "둘 다 지쳐 있어 누가 뭘 할지 정하는 것부터 부담",
        "friction_reported": [
            "누가 장을 볼지 매번 정하는 일",
            "재료가 남아 버리는 일",
            "세척기를 언제 돌릴지 정하는 일",
        ],
        "fridge": [   # 두부가 없다 — 조달이 필요한 상황
            {"name": "배추", "qty_g": 320, "stored_days": 5, "shelf_life_days": 7},
            {"name": "된장", "qty_g": 500, "stored_days": 40, "shelf_life_days": 365},
            {"name": "애호박", "qty_g": 180, "stored_days": 2, "shelf_life_days": 8},
        ],
    },
    "p3_알레르기": {
        "id": "p3_알레르기",
        "label": "알레르기 가족이 있는 4인 가구",
        "household_size": 4,
        "arrive_home": "18:30",
        "time_budget_min": 60,
        "next_morning_rush": False,
        "avoid": ["표고버섯", "새우"],   # 가구원 알레르기
        "max_sodium_mg": 300,           # 가구원 저염 권고 (1인분 기준)
        "dislike_noise_after": "23:30",
        "goal_hint": "한 사람이 못 먹는 재료가 섞이지 않았는지 매번 확인",
        "friction_reported": [
            "재료마다 알레르기 여부를 확인하는 일",
            "장을 볼 때 성분을 읽는 일",
            "조리 기구가 섞이지 않게 신경 쓰는 일",
        ],
        # 닭고기가 임박해 조림이 후보로 오르지만, 그 기록에는 표고버섯이 들어간다
        "fridge": [
            {"name": "닭고기", "qty_g": 500, "stored_days": 2, "shelf_life_days": 3},
            {"name": "간장", "qty_g": 400, "stored_days": 60, "shelf_life_days": 720},
            {"name": "배추", "qty_g": 300, "stored_days": 1, "shelf_life_days": 7},
        ],
    },


    "p4_퇴근길": {
        "id": "p4_퇴근길",
        "label": "퇴근길 1인 가구",
        "household_size": 1,
        # 퇴근 시각과 이동 시간이 있으면 에이전트가 '집에 없는 동안' 을 쓸 수 있다.
        # 앞의 세 상황에는 이 두 값이 없어 귀가 후에야 판단을 시작한다.
        "leave_office": "18:40",
        "commute_min": 40,
        "arrive_home": "19:20",
        "time_budget_min": 25,          # 귀가 후 식사까지
        "next_morning_rush": True,
        "avoid": [],
        "dislike_noise_after": "23:00",
        "goal_hint": "퇴근길에 이미 재료가 오고 있으면 좋겠다",
        "friction_reported": [
            "퇴근길에 장을 보러 들르는 일",
            "집에 와서 뭐가 없는지 그제야 아는 일",
            "양념이 떨어진 걸 조리 중에 발견하는 일",
        ],
        "fridge": [
            {"name": "배추", "qty_g": 320, "stored_days": 5, "shelf_life_days": 7},
            {"name": "된장", "qty_g": 500, "stored_days": 40, "shelf_life_days": 365},
        ],
        # 상비품도 떨어진다. 참기름이 바닥났다 — 조리 중에 알면 늦는다.
        "pantry_low": {"참기름": 5},
    },
}


def get(pid: str) -> dict:
    if pid not in PERSONAS:
        raise KeyError(f"등록되지 않은 고객 상황: {pid}")
    return dict(PERSONAS[pid])


def ids() -> list:
    return list(PERSONAS)
