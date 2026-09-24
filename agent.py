# -*- coding: utf-8 -*-
"""조리 오케스트레이션 에이전트.

보관(냉장고) → 준비(계량) → 조리(조리기) → 세척(식기세척기) 네 기기를
하나의 에이전트가 이어서 운용한다. 기기 사이를 잇는 매개는 레시피가 아니라
'측정된 조리 상태 기록(τ*)' 이다.

실행:  pip install anthropic  &&  set ANTHROPIC_API_KEY=...  &&  python agent.py
키가 없으면 demo_offline.py 로 기기·오케스트레이션만 검증할 수 있다.
"""
from __future__ import annotations
import os, sys, json

import kitchen

SYSTEM = """너는 주방 기기를 잇는 조리 오케스트레이션 에이전트다.

기기는 네 개다: 냉장고(보관), 계량(준비), 조리기(조리), 식기세척기(세척).
너는 저장된 '조리 기록'(사용자가 만족했던 조리에서 측정된 상태 궤적)을 목표로 삼아
네 기기를 순서대로 운용한다.

지켜야 할 규칙:
1. 조리 기록이 요구하는 재료를 먼저 냉장고에서 확인한다. 없으면 임의로 대체하지 말고
   사용자에게 선택지를 제시하고 확인을 받는다.
2. 재료의 보관 기간이 길면 수분이 더 나오므로 조리가 길어질 수 있다. 미리 알린다.
3. 조리 중에는 cooker_tick 으로 시간을 진행시키고 record_progress 로 목표와의 차이를
   확인한다. 진행이 느리면 화력을 올리고, 목표에 도달하면 즉시 멈춘다.
   시간이 다 되어서가 아니라 목표 상태에 도달했을 때 끝낸다.
4. 조리가 끝나면 조리 기록의 눌어붙음 점수로 식기세척기 코스를 고르고 예약한다.
   이 정보는 조리기만 알고 있던 것이므로, 네가 넘겨주지 않으면 식기세척기는 모른다.
5. 센서 값이 이상하거나 판단이 불확실하면 임의로 진행하지 말고 사용자에게 묻는다.

답변은 한국어로, 근거가 된 수치를 함께 말한다."""


def build_tools():
    """@beta_tool 로 감싼 도구 목록을 만든다 (anthropic SDK 필요)."""
    from anthropic import beta_tool

    @beta_tool
    def record_list(menu: str = "") -> str:
        """저장된 조리 기록 목록을 조회한다.

        Args:
            menu: 메뉴 이름으로 거르려면 지정한다. 비우면 전체.
        """
        return json.dumps(kitchen.record_list(menu or None), ensure_ascii=False)

    @beta_tool
    def record_get(record_id: str) -> str:
        """조리 기록 하나의 상세(필요 재료, 목표 질량비, 눌어붙음 점수)를 조회한다.

        Args:
            record_id: 조리 기록 ID.
        """
        return json.dumps(kitchen.record_get(record_id), ensure_ascii=False)

    @beta_tool
    def fridge_list_items() -> str:
        """냉장고 재고(품목, 무게, 보관 일수)를 조회한다."""
        return json.dumps(kitchen.fridge_list_items(), ensure_ascii=False)

    @beta_tool
    def prep_weigh(name: str, target_g: int) -> str:
        """재료를 계량한다. 실제 계량값과 예상 추가 수분량을 돌려준다.

        Args:
            name: 재료 이름.
            target_g: 목표 계량값(g).
        """
        return json.dumps(kitchen.prep_weigh(name, target_g), ensure_ascii=False)

    @beta_tool
    def cooker_start(initial_mass_g: int, extra_water_g: float = 0.0,
                     power: int = 3) -> str:
        """조리를 시작한다.

        Args:
            initial_mass_g: 투입 직후 총 질량(g).
            extra_water_g: 재료에서 더 나올 것으로 예상되는 수분(g).
            power: 화력 0~5.
        """
        kitchen.COOKER.start(initial_mass_g, extra_water_g, power)
        return json.dumps(kitchen.COOKER.state(), ensure_ascii=False)

    @beta_tool
    def cooker_tick(minutes: float = 1.0) -> str:
        """조리 시간을 진행시키고 현재 상태를 돌려준다.

        Args:
            minutes: 진행시킬 시간(분).
        """
        kitchen.COOKER.tick(minutes)
        return json.dumps(kitchen.COOKER.state(), ensure_ascii=False)

    @beta_tool
    def cooker_set_power(level: int) -> str:
        """조리기 화력을 바꾼다.

        Args:
            level: 화력 0~5.
        """
        return json.dumps(kitchen.COOKER.set_power(level), ensure_ascii=False)

    @beta_tool
    def cooker_stop() -> str:
        """조리를 종료한다."""
        return json.dumps(kitchen.COOKER.stop(), ensure_ascii=False)

    @beta_tool
    def record_progress(record_id: str) -> str:
        """현재 조리 상태와 목표 궤적의 차이(진행도, 잔여량, 예상 시간)를 계산한다.

        Args:
            record_id: 목표로 삼은 조리 기록 ID.
        """
        return json.dumps(kitchen.record_progress(record_id), ensure_ascii=False)

    @beta_tool
    def dishwasher_schedule_for(record_id: str, start_after_min: int = 30) -> str:
        """조리 기록의 눌어붙음 점수로 세척 코스를 골라 예약한다.

        Args:
            record_id: 방금 실행한 조리 기록 ID.
            start_after_min: 몇 분 뒤에 시작할지.
        """
        rec = kitchen.record_get(record_id)
        if "error" in rec:
            return json.dumps(rec, ensure_ascii=False)
        course = kitchen.dishwasher_recommend_course(rec["soil_score"])
        res = kitchen.dishwasher_schedule(course["course"], start_after_min)
        return json.dumps({**course, **res}, ensure_ascii=False)

    return [record_list, record_get, fridge_list_items, prep_weigh,
            cooker_start, cooker_tick, cooker_set_power, cooker_stop,
            record_progress, dishwasher_schedule_for]


def main():
    try:
        import anthropic
    except ImportError:
        print("anthropic SDK 가 없습니다.  pip install anthropic")
        print("키 없이 기기·오케스트레이션만 확인하려면:  python demo_offline.py")
        return 1
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("ANTHROPIC_API_KEY 가 설정되지 않았습니다.")
        print("키 없이 기기·오케스트레이션만 확인하려면:  python demo_offline.py")
        return 1

    client = anthropic.Anthropic()
    user_input = " ".join(sys.argv[1:]) or "오늘 저녁에 어머니가 저장해둔 된장찌개 해줘."
    print("사용자:", user_input, "\n" + "─" * 70)

    runner = client.beta.messages.tool_runner(
        model="claude-opus-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        tools=build_tools(),
        messages=[{"role": "user", "content": user_input}],
    )
    for message in runner:
        for block in message.content:
            if block.type == "text" and block.text.strip():
                print("\n[에이전트]", block.text.strip())
            elif block.type == "tool_use":
                print(f"  → {block.name}({json.dumps(block.input, ensure_ascii=False)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
