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
import re
from typing import Callable

from .base import Skill, SkillResult


def friction_hit(f: dict, removes) -> bool:
    """장면의 removes 문장(들)이 이 수고를 덮는가.

    고객 문장 그대로이거나, 같은 일로 표시된 상황 추론 문장(same_as)이
    들어 있으면 덮은 것이다. (여전히 글자 포함이다 — 뜻으로 짝짓는 것은
    LLM 장면 제안이 한다. 여기서는 같은 수고를 두 번 세지 않는 것만 본다.)
    """
    if isinstance(removes, str):
        removes = [removes]
    names = [f["what"]] + ([f["same_as"]] if f.get("same_as") else [])
    return any(n in r for n in names for r in removes)


# ════════════════════ 01 고객 상황 이해 ════════════════════
class SituationReadSkill(Skill):
    name = "situation_read"
    description = ("고객 상황을 읽어 수고 지점과 제약을 뽑아낸다. 지시가 아니라 상황이 "
                   "입력이므로, 상황이 바뀌면 이후 모든 단계가 함께 바뀐다.")
    input_schema = {
        "persona": "dict  고객 상황 (생활 리듬·가구원·제약·보고된 불편)",
        "stage_costs": "dict  단계별 예상 소요 분 — 도메인이 주입한다",
        "terms": "dict | None  이 도메인의 말 — amount(양)·finish(마무리)·"
                 "finish_course(마무리 코스). 주입하지 않으면 중립어를 쓴다. "
                 "판단 로직은 도메인과 무관하고 **근거 문장만** 이 말을 쓴다",
    }
    reusable_for = ["주방(보관·조리·세척)", "세탁·의류관리", "청소 루틴", "공조 운전"]
    provides = ("friction", "constraints")

    # 근거 문장에 쓰는 말. 도메인이 주입하지 않으면 중립어를 쓴다.
    # 판단 로직은 도메인을 모른다 — 세탁 상황을 넣어도 수고·제약이
    # 그대로 나온다. 다만 문장이 "조리량" 이라고 말하면 세탁에서 어색하다.
    NEUTRAL_TERMS = {"amount": "양", "finish": "마무리",
                     "finish_course": "마무리 코스",
                     # 상황에서 읽히는 수고의 이름도 도메인이 안다
                     "short_time": "시간이 모자란 상태에서 무엇을 할지 정하는 일",
                     "avoid_check": "항목마다 피해야 할 것이 섞였는지 확인하는 일",
                     # 고객이 이미 자기 말로 한 불편인지 알아보는 단서.
                     # 중립 도메인은 단서가 없어 합치지 않는다.
                     "short_time_cues": (), "avoid_check_cues": (),
                     "finish_start": "마무리 시작"}

    def run(self, persona: dict, stage_costs: dict, terms: dict | None = None,
            **_) -> SkillResult:
        T = dict(self.NEUTRAL_TERMS)
        T.update(terms or {})
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
            ev.append(f"아침에 여유가 없거나 2인 이상 → {T['finish']}까지 완료 목표에 포함")
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
                  f"{constraints['device'] or '기기 미지정'} → {T['amount']}을 그에 맞춘다")

        constraints["budget_min"] = commute if constraints.get("preorder") else budget
        constraints["commute_min"] = commute

        quiet = persona.get("dislike_noise_after")
        if quiet:
            constraints["quiet_after"] = quiet
            ev.append(f"{quiet} 이후 소음 회피 → {T['finish_course']}가 그 시각을 넘겨 돌면 "
                      f"저소음으로 전환한다")
        # 세척을 언제 시작하는지는 실행 층이 알아야 소음 판단을 할 수 있다.
        # 시나리오 문장에만 적어 두면 문장과 동작이 어긋난다.
        home = persona.get("arrive_home")
        constraints["arrive_home"] = home
        if home:
            h, m = map(int, home.split(":"))
            t = h * 60 + m + 45          # 일을 마친 뒤 마무리 기기를 돌린다
            constraints["cleanup_at"] = f"{(t // 60) % 24:02d}:{t % 60:02d}"

        # 수고 지점: 고객이 말한 것 + 상황에서 읽히는 것.
        # 상황에서 읽은 수고를 **고객이 이미 자기 말로 했으면 새로 더하지
        # 않는다** — 고객 문장에 same_as 로 표시해 한 번만 센다. 전에는 p3 의
        # "재료마다 알레르기 여부를 확인하는 일"(고객)과 "재료마다 못 먹는 것이
        # 섞였는지 확인하는 일"(추론)이 따로 세어져, 같은 일이 한쪽은 덮이고
        # 한쪽은 안 덮여 "수고 4건 중 2건" 이 됐다(실제 3건 중 2건).
        for f in persona.get("friction_reported", []):
            friction.append({"what": f, "source": "고객 진술"})

        def infer(key, source):
            said = [f for f in friction if f["source"] == "고객 진술"
                    and any(c in f["what"] for c in T.get(key + "_cues", ()))]
            if said:
                said[0]["same_as"] = T[key]
                ev.append(f"상황 추론 '{T[key]}' 은 고객이 이미 말했다 — "
                          f"'{said[0]['what']}' 로 한 번만 센다")
            else:
                friction.append({"what": T[key], "source": source})

        if budget < need_min:
            infer("short_time", "상황 추론(시간 예산)")
        if avoid:
            infer("avoid_check", "상황 추론(알레르기)")

        ev.append(f"수고 지점 {len(friction)}건 확정")
        # 덜어낼 수고가 하나도 없으면 이 에이전트를 부를 이유가 없다. 제약은
        # 읽었지만 **할 일을 찾지 못한 것**이므로 성공으로 세지 않는다.
        # (inventory 의 "임박한 것 0건" 과는 다르다 — 그건 뒤 단계가 그대로
        #  쓸 수 있는 유효한 결론이지만, 여기서 0건이면 뒤가 전부 무의미하다.
        #  scenario_draft 는 2026-09-25 에 같은 이유로 이미 고쳤다.)
        if not friction:
            ev.append("덜어낼 수고를 찾지 못했다 — 고객이 보고한 불편도 없고 "
                      "상황에서 읽히는 것도 없다")
        return SkillResult(bool(friction),
                           {"friction": friction, "constraints": constraints,
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
        "beats_for": "(persona, constraints, plus) -> list[dict]   장면을 "
                     "만드는 함수. **도메인이 주입한다** — 이 스킬은 주방을 "
                     "모른다. 각 beat 는 at·user·system·removes 와 "
                     "verified_by·expect_metric(또는 expect_any)를 갖는다",
    }
    reusable_for = ["주방 경험", "세탁 경험", "외출·귀가 루틴", "수면 루틴"]
    requires = ("friction", "constraints")
    provides = ("scenario",)

    @staticmethod
    def _plus(hhmm: str, minutes: int) -> str:
        h, m = map(int, hhmm.split(":"))
        t = h * 60 + m + minutes
        return f"{(t // 60) % 24:02d}:{t % 60:02d}"

    def run(self, persona: dict, friction: list, constraints: dict,
            beats_for=None, **_) -> SkillResult:
        """장면을 그리고, 그것이 수고를 실제로 덮는지 센다.

        **어떤 장면을 그릴지는 도메인이 준다.** 전에는 "현관에 들어선다 →
        재고를 읽는다", "화력은 스스로 맞춘다" 같은 주방 전용 문장과
        verified_by 스킬 이름이 이 안에 박혀 있었다. 세탁실에 쓰려면
        설계 층 코드를 고쳐야 했다는 뜻이다.

        스킬에 남는 일: 시각 계산, 시각순 정렬, **수고와 장면 짝짓기**,
        덮은 수고 집계, 빈 입력 판정. 장면 문구만 도메인이 채운다.
        """
        if beats_for is None:
            raise ValueError(
                "scenario_draft: 장면을 만드는 함수(beats_for)가 없다. "
                "도메인이 beats_for(persona, constraints, plus) -> list[beat] "
                "를 주입해야 한다. beat 는 at·user·system·removes 와, "
                "실행과 잇는 verified_by·expect_metric(또는 expect_any)를 갖는다")
        beats = list(beats_for(persona, constraints, self._plus))
        ev = [f"도메인이 준 장면 후보 {len(beats)}개"]

        # 장면은 시각 순으로 읽혀야 한다 — 퇴근이 귀가보다 앞이다
        beats.sort(key=lambda b: b["at"])

        removed = {b["removes"] for b in beats}
        for f in friction:
            hit = friction_hit(f, removed)
            ev.append(f"{'해소' if hit else '미해소'} — {f['what']}")

        covered = sum(1 for f in friction if friction_hit(f, removed))
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
        "constraints": "dict   도메인 제약. goal_facts 로 목표를 줘도 된다",
        "goal": "set[str] | None   이 도메인에서 '끝났다' 는 사실들. "
                "주방이면 {cooked, cleaned}, 세탁실이면 {dried, ...}. "
                "**스킬은 도메인 사실 이름을 모른다** — 반드시 주입받는다",
        "tasks": "list[Task]  이 도메인에서 쓸 수 있는 작업",
        "planner": "(goal, tasks, known) -> Plan   주입받는다",
    }
    reusable_for = ["가전 작업 계획", "설비 운전 계획", "업무 파이프라인 설계"]
    requires = ("scenario", "constraints")
    provides = ("flow",)

    def run(self, constraints: dict, tasks: list, planner: Callable,
            goal: list | set | None = None, **_) -> SkillResult:
        # 목표 사실은 **도메인이 정한다.** 전에는 {"cooked"} 가 이 스킬 안에
        # 하드코딩돼 있었다. 설계 층 스킬이 주방 도메인의 사실 이름을 알고
        # 있었다는 뜻이고, 세탁실에 쓰려면 이 줄을 고쳐야 했다 —
        # "kitchen_domain.py 만 바꾸면 된다" 는 설명과 어긋난다.
        goal = set(goal or constraints.get("goal_facts") or ())
        if not goal:
            raise ValueError(
                "flow_design: 목표 사실이 없다. goal=... 로 넘기거나 "
                "constraints['goal_facts'] 에 적어라 "
                "(예: {'cooked', 'cleaned'} / 세탁실이면 {'dried'})")

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
        log = result.get("log", [])
        by_skill = {r["skill"]: r for r in log}
        planned = [t.skill for t in plan.steps]
        # 계획에 있는데 로그에 없는 단계 = 도달하지 못한 단계
        not_run = [n for n in planned if n not in by_skill]
        failed = [r["skill"] for r in log if not r.get("ok")]
        halted_at = failed[0] if failed else (log[-1]["skill"] if log else "시작 전")
        if not_run:
            ev.append(f"실행이 {halted_at} 에서 멈춰 "
                      f"{len(not_run)}단계에 도달하지 못했다: {', '.join(not_run)}")
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
                # 계획에 아예 없는 것과, 계획에는 있는데 **앞 단계에서 멈춰
                # 도달하지 못한 것**은 다르다. 전에는 둘 다 "계획에 없다" 로
                # 보고해, 1단계에서 멈춘 실행이 "설계가 그 단계를 안 넣었다" 처럼
                # 읽혔다.
                hit = False
                why = (f"{need} 까지 가지 못했다 — {halted_at} 에서 실행이 멈췄다"
                       if need in planned else f"{need} 이 계획에 없다")
            elif not row["ok"]:
                hit, why = False, f"{need} 실행 실패"
            elif any_of and not found:
                hit, why = False, (f"{need} 은 성공했지만 결과에 "
                                   f"{' 또는 '.join(any_of)} 가 없다 — "
                                   f"장면이 말한 일이 일어나지 않았다")
            elif b.get("expect_value") and not re.search(
                    b["expect_value"]["pattern"],
                    str(m.get(b["expect_value"]["metric"], ""))):
                # 장면이 조건 없이 "저소음으로 바꾼다" 고 했는데 결과는
                # "소음 조치 불필요" 인 경우. 지표가 있다고 약속이 지켜진 것은 아니다.
                ev_ = b["expect_value"]
                hit, why = False, (f"장면은 '{ev_['claim']}' 고 했지만 결과의 "
                                   f"{ev_['metric']} 는 "
                                   f"'{str(m.get(ev_['metric'], '없음'))[:40]}'")
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

        # 중단된 실행에서 개입이 0인 것은 수고를 덜어서가 아니라 **거기까지
        # 가지도 못해서**다. 그것을 절감으로 세면 실패가 성과로 집계된다.
        saved = 0 if not_run else max(0, touch_baseline - touches)
        return SkillResult(ok, {
            "verified": ok,
            "user_touches": touches,
            "touch_baseline": touch_baseline,
            "touches_removed": saved,
            "steps_planned": len(planned),
            "steps_run": len(log),
            "not_run": not_run,
            "halted_at": halted_at if not_run else None,
            "beats_total": len(beats),
            "beats_met": len(beats) - len(unmet),
            "unmet": unmet,
            "beat_check": checked,
            "metrics": result.get("metrics", {}),
            "spent_min": spent, "budget_min": budget_min,
            "over_budget": over_budget,
            "execution": result.get("log", [])}, ev)
