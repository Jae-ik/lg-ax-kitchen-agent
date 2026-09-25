# -*- coding: utf-8 -*-
"""UX 시나리오 설계 직무를 이루는 스킬 4종.

Theme B 의 직무 단계에 그대로 대응한다.

    고객 상황 이해 → 시나리오 작성 → 서비스 흐름 설계 → 경험 검증
    situation_read   scenario_draft   flow_design        experience_verify

이 넷은 '가전을 움직이는 스킬' 이 아니라 '설계를 수행하는 스킬' 이다.
가전을 움직이는 일은 아래 실행 층(inventory/menu/procure/converge/aftercare)이 하고,
experience_verify 가 주입받은 실행 함수로 그것을 돌려 검증한다.

스킬끼리 서로를 import 하지 않는다. 실행 함수는 주입받는다.
"""
from __future__ import annotations
from typing import Callable

from .base import Skill, SkillResult


# ════════════════════ 01 고객 상황 이해 ════════════════════
class SituationReadSkill(Skill):
    name = "situation_read"
    description = ("고객 상황을 읽어 수고 지점과 제약을 뽑아낸다. 지시가 아니라 상황이 "
                   "입력이므로, 상황이 바뀌면 이후 모든 단계가 함께 바뀐다.")
    input_schema = {
        "persona": "dict  고객 상황 (생활 리듬·가구원·제약·보고된 불편)",
        "stage_costs": "dict  단계별 예상 소요 분 — 도메인이 주입한다",
    }
    reusable_for = ["주방(보관·조리·세척)", "세탁·의류관리", "청소 루틴", "공조 운전"]
    provides = ("friction", "constraints")

    def run(self, persona: dict, stage_costs: dict, **_) -> SkillResult:
        need_min = sum(stage_costs.values())
        budget = persona.get("time_budget_min", 999)
        commute = persona.get("commute_min", 0)
        buy_min = stage_costs.get("조달", 0)
        ev = [f"보고된 불편 {len(persona.get('friction_reported', []))}건",
              f"필요 시간 {need_min}분 / 귀가 후 사용 가능 {budget}분"]

        constraints, friction = {}, []

        # 퇴근 시각과 이동 시간을 알면 '집에 없는 동안' 을 쓸 수 있다.
        # 배송이 이동 시간 안에 끝나면 귀가 시점에 재료가 도착해 있다.
        if commute and commute >= buy_min:
            constraints["preorder"] = True
            need_at_home = need_min - buy_min
            ev.append(f"퇴근~귀가 {commute}분 ≥ 배송 {buy_min}분 → "
                      f"집에 없는 동안 조달을 끝낸다 (선제 주문)")
        else:
            constraints["preorder"] = False
            need_at_home = need_min

        # 귀가 후 시간으로 감당되지 않으면 조달을 뺀다
        if budget < need_at_home:
            constraints["skip_procurement"] = True
            ev.append(f"{need_at_home - budget}분 초과 → 대기가 생기는 조달 단계를 "
                      f"빼고 재고 안에서 해결")
        else:
            constraints["skip_procurement"] = False

        # 다음 날 아침에 여유가 없으면 세척까지 끝내 둬야 한다
        if persona.get("next_morning_rush") or persona.get("household_size", 1) >= 2:
            constraints["finish_cleanup"] = True
            ev.append("아침에 여유가 없거나 2인 이상 → 세척까지 완료 목표에 포함")
        else:
            constraints["finish_cleanup"] = False

        # 알레르기·기피는 되돌릴 수 없는 행동(주문)의 안전 필터가 된다
        avoid = list(persona.get("avoid", []))
        constraints["avoid"] = avoid
        if avoid:
            ev.append(f"기피·알레르기 {len(avoid)}건 → 조달 안전 필터 강화: {', '.join(avoid)}")

        na = persona.get("max_sodium_mg")
        constraints["max_sodium_mg"] = na
        if na:
            ev.append(f"저염 권고 {na}mg → 후보 자료를 영양 기준으로 거른다")

        # 몇 인분을 만들지는 조리의 첫 결정이다. 가구원 수와 그 집 조리기의
        # 용량이 함께 정한다 — 4인분이 냄비에 안 들어가면 들어가는 만큼만 한다.
        constraints["time_budget_min"] = budget
        constraints["household_size"] = persona.get("household_size", 1)
        constraints["device"] = persona.get("device")
        ev.append(f"{constraints['household_size']}인 가구 · "
                  f"{constraints['device'] or '기기 미지정'} → 조리량을 그에 맞춘다")

        constraints["budget_min"] = commute if constraints.get("preorder") else budget
        constraints["commute_min"] = commute

        quiet = persona.get("dislike_noise_after")
        if quiet:
            constraints["quiet_after"] = quiet
            ev.append(f"{quiet} 이후 소음 회피 → 세척 코스가 그 시각을 넘겨 돌면 "
                      f"저소음으로 전환한다")
        # 세척을 언제 시작하는지는 실행 층이 알아야 소음 판단을 할 수 있다.
        # 시나리오 문장에만 적어 두면 문장과 동작이 어긋난다.
        home = persona.get("arrive_home")
        if home:
            h, m = map(int, home.split(":"))
            t = h * 60 + m + 45                 # 식사까지 마친 뒤 세척 시작
            constraints["cleanup_at"] = f"{(t // 60) % 24:02d}:{t % 60:02d}"

        # 수고 지점: 고객이 말한 것 + 상황에서 읽히는 것
        for f in persona.get("friction_reported", []):
            friction.append({"what": f, "source": "고객 진술"})
        if budget < need_min:
            friction.append({"what": "시간이 모자란 상태에서 메뉴를 정하는 일",
                             "source": "상황 추론(시간 예산)"})
        if avoid:
            friction.append({"what": "재료마다 못 먹는 것이 섞였는지 확인하는 일",
                             "source": "상황 추론(알레르기)"})

        ev.append(f"수고 지점 {len(friction)}건 확정")
        return SkillResult(True, {"friction": friction, "constraints": constraints,
                                  "need_min": need_min, "budget_min": budget}, ev)


# ════════════════════ 02 시나리오 작성 ════════════════════
class ScenarioDraftSkill(Skill):
    name = "scenario_draft"
    description = ("수고 지점이 사라진 하루를 타임라인으로 쓴다. 아직 어떤 기기가 "
                   "무엇을 할지는 정하지 않는다 — 고객이 겪을 경험만 먼저 그린다.")
    input_schema = {
        "persona": "dict",
        "friction": "list[dict]  없애야 할 수고",
        "constraints": "dict",
    }
    reusable_for = ["주방 경험", "세탁 경험", "외출·귀가 루틴", "수면 루틴"]
    requires = ("friction", "constraints")
    provides = ("scenario",)

    @staticmethod
    def _plus(hhmm: str, minutes: int) -> str:
        h, m = map(int, hhmm.split(":"))
        t = h * 60 + m + minutes
        return f"{(t // 60) % 24:02d}:{t % 60:02d}"

    def run(self, persona: dict, friction: list, constraints: dict, **_) -> SkillResult:
        t0 = persona.get("arrive_home", "19:00")
        beats, ev = [], []

        # verified_by: 이 장면이 실제로 일어났는지 확인할 실행 스킬.
        # 검증 단계가 이 이름으로 실행 로그를 조회한다 — 그림과 실행을 잇는 고리다.
        beats.append({
            "at": t0, "user": "현관에 들어선다",
            "system": "재고와 남은 시간을 이미 읽고 오늘 할 수 있는 것을 정해 둔다",
            "removes": "냉장고를 열어 뭐가 남았는지 확인하는 일",
            "verified_by": "inventory", "expect_metric": "메뉴"})

        if constraints.get("avoid"):
            beats.append({
                "at": self._plus(t0, 1), "user": "아무것도 확인하지 않는다",
                "system": f"못 먹는 재료({', '.join(constraints['avoid'])})가 들어가는 "
                          f"후보를 미리 제외한다",
                "removes": "재료마다 못 먹는 것이 섞였는지 확인하는 일",
                "verified_by": "menu", "expect_metric": "메뉴"})

        if constraints.get("preorder"):
            lv = persona.get("leave_office", t0)
            beats.append({
                "at": lv, "user": "사무실을 나선다",
                "system": "냉장고와 양념 선반을 함께 확인해 부족한 것을 "
                          "귀가 시각에 맞춰 주문한다",
                "removes": "퇴근길에 장을 보러 들르는 일 / 집에 와서 뭐가 없는지 "
                           "그제야 아는 일 / 양념이 떨어진 걸 조리 중에 발견하는 일",
                "verified_by": "procure",
                "expect_any": ["조달 대기", "확인 요청"]})

        if constraints.get("skip_procurement"):
            beats.append({
                "at": self._plus(t0, 2), "user": "장을 보지 않는다",
                "system": "시간이 모자라므로 지금 있는 재료만으로 가능한 것을 고른다",
                "removes": "시간이 모자란 상태에서 메뉴를 정하는 일",
                # 조달을 생략했다는 것은 '자동 주문' 이 결과에 없다는 뜻이다.
                "verified_by": "menu", "expect_metric": "메뉴"})
        elif not constraints.get("preorder"):
            # 선제 주문이면 퇴근길 장면이 이미 조달을 덮는다 — 중복해서 넣지 않는다
            beats.append({
                "at": self._plus(t0, 2), "user": "주문을 누르지 않는다",
                # 설계 시점에는 **자동 주문이 될지 확인이 필요할지 모른다.**
                # "스스로 주문한다" 고만 적었다가, 둘 다 확인 요청이 된 상황에서
                # 장면이 실제와 어긋났다. 판단 기준을 말하고 결과는 열어 둔다.
                "system": "부족분을 이력·상한·안전 기준으로 판단해 "
                          "되는 것은 주문하고, 안 되는 것만 묻는다",
                "removes": "누가 장을 볼지 매번 정하는 일",
                "verified_by": "procure",
                "expect_any": ["조달 대기", "확인 요청"]})

        beats.append({
            # "불 앞을 지키지 않는다" 는 과장이었다. 재료를 넣고 뚜껑을
            # 여닫고 젓는 것은 여전히 사람이 한다(제안서 표1). 달라지는 것은
            # **언제 해야 하는지 몰라 계속 지켜보던 일**이 없어진다는 점이다.
            "at": self._plus(t0, 5), "user": "부를 때만 손을 댄다",
            "system": ("화력은 스스로 맞추고 목표 상태에 닿으면 멈춘다. "
                       "손이 필요한 때(재료 투입·뚜껑·젓기)만 알려 준다"),
            "removes": "조리 중 냄비 앞을 떠나지 못하는 일",
            "verified_by": "converge", "expect_metric": "가열 시간(분)"})

        if constraints.get("finish_cleanup"):
            quiet = constraints.get("quiet_after")
            beats.append({
                "at": self._plus(t0, 45), "user": "코스를 고르지 않는다",
                "system": "조리기가 잰 눌어붙음 정도로 코스를 정하고"
                          + (f", {quiet} 이후까지 돌면 저소음으로 바꾼다"
                             if quiet else " 바로 시작한다"),
                "removes": "먹고 나서 설거지를 미루는 일 / 세척기를 언제 돌릴지 정하는 일",
                "verified_by": "aftercare", "expect_metric": "세척 코스"})

        # 장면은 시각 순으로 읽혀야 한다 — 퇴근이 귀가보다 앞이다
        beats.sort(key=lambda b: b["at"])

        removed = {b["removes"] for b in beats}
        for f in friction:
            hit = any(f["what"] in r for r in removed)
            ev.append(f"{'해소' if hit else '미해소'} — {f['what']}")

        covered = sum(1 for f in friction if any(f["what"] in r for r in removed))
        ev.append(f"수고 {len(friction)}건 중 {covered}건을 시나리오가 덮는다")

        # 덜어낼 수고가 없으면 시나리오를 만들 이유도 없다. 예전에는 이 경우에도
        # 성공을 돌려줘서, **아무 불편도 없는 고객에게 설계가 성립했다**고 보고했다.
        # 하나도 덮지 못한 경우도 마찬가지다 — 장면이 있어도 값이 없다.
        ok = bool(friction) and covered > 0
        if not friction:
            ev.append("덜어낼 수고가 없다 — 설계할 것이 없으므로 성립으로 세지 않는다")
        elif covered == 0:
            ev.append("장면이 어떤 수고도 덜어내지 못한다 — 성립으로 세지 않는다")

        return SkillResult(ok, {
            "scenario": {"persona": persona.get("label"), "beats": beats,
                         "covered": covered, "total_friction": len(friction)}}, ev)


# ════════════════════ 03 서비스 흐름 설계 ════════════════════
class FlowDesignSkill(Skill):
    name = "flow_design"
    description = ("시나리오를 실현할 목표 사실을 정하고, 플래너로 가전 작업 순서를 "
                   "계산한다. 순서를 적어두지 않고 전제조건에서 계산하므로 "
                   "상황이 바뀌면 순서도 바뀐다.")
    input_schema = {
        "constraints": "dict",
        "tasks": "list[Task]  이 도메인에서 쓸 수 있는 작업",
        "planner": "(goal, tasks, known) -> Plan   주입받는다",
    }
    reusable_for = ["가전 작업 계획", "설비 운전 계획", "업무 파이프라인 설계"]
    requires = ("scenario", "constraints")
    provides = ("flow",)

    def run(self, constraints: dict, tasks: list, planner: Callable, **_) -> SkillResult:
        goal = {"cooked"}
        if constraints.get("finish_cleanup"):
            goal.add("cleaned")

        # 조달을 건너뛰기로 했으면 '재고가 갖춰졌다' 를 이미 성립한 사실로 둔다.
        # 그러면 플래너가 procure 를 계획에서 자동으로 뺀다.
        known = set()
        if constraints.get("skip_procurement"):
            known.add("stock_complete")

        p = planner(goal, tasks, known)
        ev = list(p.reasoning)
        ev.insert(0, f"목표 사실 {sorted(goal)} / 이미 성립 {sorted(known) or '없음'}")

        assign = [{"step": i + 1, "skill": t.skill, "note": t.note}
                  for i, t in enumerate(p.steps)]
        return SkillResult(True, {"flow": {"goal": sorted(goal),
                                           "known": sorted(known),
                                           "steps": [t.skill for t in p.steps],
                                           "assignment": assign},
                                  "plan": p}, ev)


# ════════════════════ 04 경험 검증 ════════════════════
class ExperienceVerifySkill(Skill):
    name = "experience_verify"
    description = ("설계한 흐름을 실제로 실행해 시나리오대로 됐는지 확인한다. "
                   "그림으로 끝내지 않고 실행 결과 수치로 판정한다.")
    input_schema = {
        "scenario": "dict",
        "plan": "Plan",
        "execute": "(plan) -> dict   실행 함수. 주입받는다",
        "touch_baseline": "int  에이전트가 없을 때 고객이 눌러야 하는 횟수",
        "budget_min": "int | None  귀가 후 쓸 수 있는 시간. 초과하면 미달성으로 본다",
    }
    reusable_for = ["UX 시나리오 검증", "자동화 회귀 시험", "운전 정책 평가"]
    requires = ("flow", "scenario")
    provides = ("verified",)

    def run(self, scenario: dict, plan, execute: Callable,
            touch_baseline: int = 5, budget_min: int | None = None,
            **_) -> SkillResult:
        result = execute(plan)
        touches = result.get("user_touches", 0)
        ev = [f"계획 {len(plan.steps)}단계 실행",
              f"사용자 개입 {touches}회 (에이전트 없을 때 {touch_baseline}회 기준)"]

        for k, v in result.get("metrics", {}).items():
            ev.append(f"{k}: {v}")

        # 장면마다 그것을 일으킨 스킬이 실제로 성공했는지 대조한다.
        # 시나리오는 그림이고 실행 로그는 사실이다. 둘이 어긋나면 설계가 틀린 것이다.
        m = result.get("metrics", {})
        by_skill = {r["skill"]: r for r in result.get("log", [])}
        beats = scenario.get("beats", [])
        checked, unmet = [], []
        for b in beats:
            need = b.get("verified_by")
            row = by_skill.get(need)
            # **스킬이 성공했다고 장면이 일어난 것은 아니다.** 실행은 됐는데
            # 아무 결과도 안 낸 경우를 잡으려면, 그 장면이 만들어야 할
            # 결과가 실제로 나왔는지 함께 봐야 한다.
            want = b.get("expect_metric")
            any_of = b.get("expect_any") or ([want] if want else [])
            found = [k for k in any_of if k in m]
            if row is None:
                hit, why = False, f"{need} 이 계획에 없다"
            elif not row["ok"]:
                hit, why = False, f"{need} 실행 실패"
            elif any_of and not found:
                hit, why = False, (f"{need} 은 성공했지만 결과에 "
                                   f"{' 또는 '.join(any_of)} 가 없다 — "
                                   f"장면이 말한 일이 일어나지 않았다")
            else:
                hit = True
                why = (f"{need} 실행 성공, {found[0]}={m[found[0]]}" if found
                       else f"{need} 실행 성공")
            checked.append({"at": b["at"], "removes": b["removes"],
                            "ok": hit, "why": why})
            if not hit:
                unmet.append(b["removes"])
            ev.append(f"{b['at']} {'달성' if hit else '미달성'} — {b['removes']} ({why})")

        # 시간 예산은 제안의 핵심 주장이다. 장면이 다 달성돼도 25분 예산에
        # 40분이 걸렸으면 그 시나리오는 성립하지 않는다.
        spent = m.get("식사까지(분)")
        over_budget = False
        if spent is not None and budget_min:
            over_budget = spent > budget_min
            ev.append(f"식사까지 {spent}분 / 예산 {budget_min}분 → "
                      + ("예산 안" if not over_budget else "**예산 초과**"))

        ok = result.get("ok", False) and not unmet and not over_budget
        ev.append(f"장면 {len(beats)}개 중 {len(beats) - len(unmet)}개 달성 → "
                  + ("시나리오 달성" if ok else "시나리오 미달성"))

        saved = max(0, touch_baseline - touches)
        return SkillResult(ok, {
            "verified": ok,
            "user_touches": touches,
            "touch_baseline": touch_baseline,
            "touches_removed": saved,
            "beats_total": len(beats),
            "beats_met": len(beats) - len(unmet),
            "unmet": unmet,
            "beat_check": checked,
            "metrics": result.get("metrics", {}),
            "spent_min": spent, "budget_min": budget_min,
            "over_budget": over_budget,
            "execution": result.get("log", [])}, ev)
