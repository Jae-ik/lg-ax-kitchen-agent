# -*- coding: utf-8 -*-
"""LLM 이 시나리오 장면을 **제안**하고, 코드와 실행이 **확정**한다.

왜 필요한가:
  `scenario_draft` 는 도메인이 준 장면 틀(kitchen_beats)에 값을 채운다.
  그리고 장면이 수고를 "덮었는지" 를 **문자열 포함**으로 센다. 그래서
  사람이 자기 말로 한 불편은 하나도 못 덮었다.

      "냄비 앞을 지키는 일"                     → 0/1
      "퇴근하고 뭐 해먹을지 고민하는 거" 외 1건 → 0/2
      p3(알레르기) 페르소나                     → 장면 5/5 달성인데 수고 1/4

  p3 의 "재료마다 알레르기 여부를 확인하는 일" 은 menu 의 기피 필터가
  실제로 덜어 주는데, 글자가 틀과 달라서 안 셌다. **말을 이해하는 일**이
  비어 있었다 — LLM 이 잘하는 일이다.

역할 나누기 — "LLM 이 제안하고, 되돌릴 수 없는 결정은 검증된 계산이나
사람이 확정한다":
      LLM   불편 하나하나에 **어느 기능이 그것을 덜어 주는지** 제안하고
            장면 문장을 쓴다. 덜어 줄 기능이 없으면 없다고 말한다.
      코드  없는 기능을 약속한 장면, 실행 전에 수치를 약속한 장면을
            버린다. LLM 이 놓친 수고 중 틀이 덮는 것은 틀 장면으로 채운다
            (그래서 안전 장면이 빠지지 않는다).
      실행  experience_verify 가 실제로 돌려 장면이 일어났는지 판정한다.

  LLM 이 정하지 **않는** 것: 장면을 검증할 지표 이름(도메인이 기능마다
  정해 둔다), 계획 단계(플래너가 계산한다), 제어(converge 가 한다).

재현:
  LLM 답은 매번 다를 수 있다. 그래서 받은 원문을 `report["raw"]` 에
  남긴다. 같은 원문을 가짜 ask 로 되돌려 주면 같은 시나리오가 나온다 —
  반복 측정과 회귀 검사가 그 위에서 그대로 돈다.
"""
from __future__ import annotations
import json
import re

PROMPT = """너는 가전 서비스의 UX 시나리오 설계자다.
아래 고객의 불편을 하나씩 보고, **아래 기능 목록 중 무엇이 그 불편을 덜어 주는지** 정해
장면으로 써라. JSON 으로만 답하라.

규칙
- 기능 목록에 없는 일을 약속하지 마라. 덜어 줄 기능이 없으면 uncovered 에 넣고 이유를 적어라.
- 장면 문장에 숫자를 쓰지 마라(분·g·원·회 등). 수치는 실행한 뒤에야 안다.
- offset_min 은 귀가 시각({arrive}) 기준 분이다. 귀가 전이면 음수.
- 장면은 고객이 **하지 않아도 되는 일**이 드러나게 써라.

고객
{persona}

불편 (번호로 가리켜라)
{friction}

기능 목록 (skill 이름으로 가리켜라)
{caps}

답 형식
{{"scenes": [{{"offset_min": 0, "user": "고객이 하는/안 하는 일",
              "system": "가전이 하는 일", "skill": "기능 이름",
              "friction": [불편 번호]}}],
  "uncovered": [{{"friction": 불편 번호, "why": "덜어 줄 기능이 없는 이유"}}]}}"""


def _persona_text(persona: dict, constraints: dict) -> str:
    bits = [f"귀가 {persona.get('arrive_home')}",
            f"{constraints.get('household_size', 1)}인",
            f"쓸 수 있는 시간 {constraints.get('time_budget_min')}분"]
    if constraints.get("avoid"):
        bits.append(f"못 먹는 것 {', '.join(constraints['avoid'])}")
    if constraints.get("preorder"):
        bits.append(f"퇴근 {persona.get('leave_office')} — 집에 오는 동안 주문할 수 있다")
    if constraints.get("skip_procurement"):
        bits.append("시간이 모자라 장을 볼 수 없다")
    if constraints.get("finish_cleanup"):
        bits.append("설거지까지 끝내야 한다")
    if constraints.get("quiet_after"):
        bits.append(f"{constraints['quiet_after']} 이후 소음을 싫어한다")
    if persona.get("goal_hint"):
        bits.append(f"한마디: {persona['goal_hint']}")
    return " · ".join(bits)


def _parse(raw: str):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None, "LLM 답에서 JSON 을 찾지 못했다"
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return None, f"JSON 을 읽지 못했다({e})"
    if not isinstance(d, dict) or not isinstance(d.get("scenes"), list):
        return None, "scenes 목록이 없다"
    return d, None


def make_llm_beats(ask, capabilities, fallback, friction_of):
    """LLM 으로 장면을 제안받는 beats_for 를 만든다.

    ask          (프롬프트) -> 문자열. 주입받는다
    capabilities (constraints) -> [{"skill", "can", "metrics"}]
                 **지금 계획에 실제로 들어가는** 기능만. 도메인이 준다
    fallback     틀 장면 함수(kitchen_beats). LLM 이 실패하거나 놓친 수고를 채운다
    friction_of  (persona, constraints) -> [{"what", ...}]  덮어야 할 수고

    돌려준 함수의 `.report` 에 무엇을 받아들이고 버렸는지 남는다.
    """
    report = {}

    def beats_for(persona, constraints, plus):
        report.clear()
        caps = capabilities(constraints)
        by_skill = {c["skill"]: c for c in caps}
        friction = friction_of(persona, constraints)
        t0 = persona.get("arrive_home", "19:00")
        base = list(fallback(persona, constraints, plus))
        report.update(raw=None, accepted=[], rejected=[], filled=[],
                      uncovered=[], by="LLM")

        prompt = (PROMPT
                  .replace("{arrive}", t0)
                  .replace("{persona}", _persona_text(persona, constraints))
                  .replace("{friction}", "\n".join(
                      f"  {i}. {f['what']}" for i, f in enumerate(friction)))
                  .replace("{caps}", "\n".join(
                      f"  {c['skill']}: {c['can']}" for c in caps))
                  .replace("{{", "{").replace("}}", "}"))
        try:
            raw = ask(prompt)
        except Exception as e:                       # 부르지 못하면 틀로 간다
            report.update(by="틀", rejected=[f"LLM 호출 실패: {e}"])
            return [dict(b, source="틀") for b in base]
        report["raw"] = raw
        d, err = _parse(raw)
        if d is None:
            report.update(by="틀", rejected=[err + " — 틀 장면을 쓴다"])
            return [dict(b, source="틀") for b in base]

        beats = []
        for s in d["scenes"]:
            why = _reject_reason(s, by_skill, len(friction))
            if why:
                report["rejected"].append(f"{str(s.get('system'))[:30]} — {why}")
                continue
            cap = by_skill[s["skill"]]
            idx = sorted(set(s["friction"]))
            beat = {"at": plus(t0, int(s["offset_min"])),
                    "user": s["user"].strip(), "system": s["system"].strip(),
                    "removes": " / ".join(friction[i]["what"] for i in idx),
                    "verified_by": cap["skill"], "source": "LLM"}
            # 검증 지표는 **LLM 이 아니라 도메인이** 정한다.
            if len(cap["metrics"]) == 1:
                beat["expect_metric"] = cap["metrics"][0]
            else:
                beat["expect_any"] = list(cap["metrics"])
            beats.append(beat)
            report["accepted"].append(
                f"{beat['at']} {cap['skill']} ← 불편 {idx}")

        for u in d.get("uncovered") or []:
            if isinstance(u, dict) and isinstance(u.get("friction"), int) \
                    and 0 <= u["friction"] < len(friction):
                report["uncovered"].append(
                    f"{friction[u['friction']]['what']} — {u.get('why', '')}")

        # LLM 이 덮지 못한 수고 중 **틀이 덮는 것**은 틀 장면으로 채운다.
        # 알레르기 확인 장면이 여기서 빠지지 않는다 — LLM 이 잊어도 남는다.
        covered = {f["what"] for f in friction
                   if any(f["what"] in b["removes"] for b in beats)}
        for b in base:
            gets = [f["what"] for f in friction
                    if f["what"] not in covered and f["what"] in b["removes"]]
            if gets:
                beats.append(dict(b, source="틀"))
                covered.update(gets)
                report["filled"].append(f"{b['verified_by']} ← {', '.join(gets)}")
        return beats

    beats_for.report = report
    return beats_for


def _reject_reason(s, by_skill, n_friction):
    """장면을 받아들일 수 없는 이유. 받아들이면 None."""
    if not isinstance(s, dict):
        return "형식이 아니다"
    if s.get("skill") not in by_skill:
        return (f"'{s.get('skill')}' 은 이번 계획에 없는 기능이다 — "
                f"실행되지 않을 일을 약속할 수 없다")
    fr = s.get("friction")
    if not isinstance(fr, list) or not fr or not all(
            isinstance(i, int) and 0 <= i < n_friction for i in fr):
        return "덜어 주는 불편 번호가 없거나 틀렸다"
    off = s.get("offset_min")
    if not isinstance(off, int) or isinstance(off, bool) or not -240 <= off <= 240:
        return f"시각이 범위를 벗어났다({off!r})"
    for k in ("user", "system"):
        v = s.get(k)
        if not isinstance(v, str) or not v.strip() or len(v) > 160:
            return f"{k} 문장이 비었거나 너무 길다"
        # 실행 전에 수치를 약속하면 그 수치는 지어낸 것이다
        if re.search(r"\d", v):
            return f"{k} 문장에 수치가 있다('{v[:24]}') — 수치는 실행한 뒤에야 안다"
    return None
