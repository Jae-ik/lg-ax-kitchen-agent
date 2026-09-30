# -*- coding: utf-8 -*-
"""휴대폰 위치로 퇴근을 알아본다 — **시뮬레이터**다.

진짜 GPS·지오펜스는 붙이지 않았다. 상점(store.py)처럼 자리만 만들고,
"회사 영역을 나감/들어옴" 이벤트 목록을 입력으로 받는다. 실제 기기라면
휴대폰이 이 이벤트를 보내 준다.

회사를 나섰다고 퇴근은 아니다. 점심·외근·저녁 회의에서도 나선다.
이걸 퇴근으로 보고 주문하면 되돌릴 수 없는 행동이 오탐으로 실행된다.
그래서 셋을 모두 통과해야 퇴근으로 본다:

  1 동의      위치 사용에 동의하지 않았으면 **아예 보지 않는다**(기본값)
  2 시간대    평소 퇴근 시간대 안의 이탈만 본다 (점심 12:10 이탈은 제외)
  3 머무름    나선 뒤 dwell_min 분 안에 다시 들어오면 퇴근이 아니다
              — 그 분만큼 **기다린 뒤에야** 판단하므로, 선제 주문에 쓸
              시간이 그만큼 준다(이동 37분이면 32분)

그래도 남는 오탐: 퇴근 시간대에 회사를 나가 **다른 곳으로** 가는 날
(저녁 약속·외부 회의)은 가려내지 못한다. 집 방향으로 움직이는지 보면
줄일 수 있지만 구현하지 않았다. 그래서 메시지("지금 퇴근해")가 있으면
위치보다 메시지를 믿는다(thinq.understand).
"""
from __future__ import annotations

DWELL_MIN = 5                         # 나선 뒤 이만큼 안 돌아오면 퇴근으로 본다(가정)
DEFAULT_WINDOW = ("17:00", "23:59")   # 평소 퇴근 시간대(가정 — 가구가 바꿀 수 있다)


def _m(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _hhmm(minutes: float) -> str:
    t = int(round(minutes)) % (24 * 60)
    return f"{t // 60:02d}:{t % 60:02d}"


def detect_leave(events: list, *, consent: bool, commute_min: int | None,
                 window: tuple | list | None = None,
                 dwell_min: int = DWELL_MIN):
    """위치 이벤트에서 퇴근을 찾는다. (찾은 것 | None, 근거 목록) 을 돌려준다.

    events: [{"at": "HH:MM", "kind": "exit"|"enter", "place": "office"}]
    찾은 것: leave_office(판단한 시각) · commute_min(남은 이동) ·
             arrive_home · left_at(실제로 나선 시각) · leave_source="location"

    **인과를 지킨다** — 판단 시각(나선 뒤 dwell_min 분)까지의 이벤트만 본다.
    그 뒤에 다시 들어왔는지는 판단할 때 알 수 없다.
    """
    why = []
    if not consent:
        return None, ["위치 사용에 동의하지 않아 위치를 보지 않는다"]
    if not commute_min:
        return None, ["집까지 걸리는 시간을 몰라 위치로 퇴근길을 쓸 수 없다"]
    lo, hi = (window or DEFAULT_WINDOW)
    evs = sorted((e for e in events or [] if e.get("place") == "office"),
                 key=lambda e: _m(e["at"]))
    for e in evs:
        if e.get("kind") != "exit":
            continue
        t = _m(e["at"])
        # 시간대가 자정을 걸칠 수 있다(야근 21:00~02:00). 전에는 lo<=t<=hi
        # 만 봐서 00:30 퇴근을 못 찾았다 — 퇴근이 불규칙한 야근 가구가
        # 바로 이 경우다(2026-09-30).
        a, b = _m(lo), _m(hi)
        inside = (a <= t <= b) if a <= b else (t >= a or t <= b)
        if not inside:
            why.append(f"{e['at']} 회사 나섬 — 퇴근 시간대({lo}~{hi}) 밖이라 "
                       f"퇴근으로 보지 않는다")
            continue
        back = [x for x in evs if x.get("kind") == "enter"
                and t < _m(x["at"]) <= t + dwell_min]
        if back:
            why.append(f"{e['at']} 회사 나섬 — {back[0]['at']} 에 다시 들어와 "
                       f"({dwell_min}분 안) 퇴근이 아니다")
            continue
        left = commute_min - dwell_min
        if left <= 0:
            why.append(f"{e['at']} 회사 나섬 — 집까지 {commute_min}분인데 확인에 "
                       f"{dwell_min}분이 걸려 퇴근길에 할 시간이 없다")
            continue
        why.append(f"{e['at']} 회사 나섬 → {dwell_min}분 동안 돌아오지 않아 "
                   f"{_hhmm(t + dwell_min)} 에 퇴근으로 본다 (집까지 남은 {left}분)")
        return {"leave_office": _hhmm(t + dwell_min), "commute_min": left,
                "arrive_home": _hhmm(t + commute_min), "left_at": e["at"],
                "leave_source": "location"}, why
    why.append("퇴근으로 볼 회사 이탈이 없다")
    return None, why
