# -*- coding: utf-8 -*-
"""오케스트레이터 — GOAL · PLAN · SKILL · EXECUTE · EVALUATE · OUTPUT.

각 단계를 로그에 명시적으로 찍는다. 평가 기준 03(스스로 계획·실행하고
그 과정을 설명하는가)에 직접 대응한다.

여기서는 단계 진행을 결정적으로 구현했다(LLM 없이도 돌아간다).
agent.py 는 LLM 이 도구를 스스로 고르는 경로다. 다만 **레지스트리를
쓰지 않고** kitchen 함수를 직접 감싼다 — 미검증이고 제출 경로가 아니다.
"""
from __future__ import annotations
import json

W = 78


class Trace:
    """실행 로그. 그대로 '실행 시연' 산출물이 된다."""

    def __init__(self):
        self.rows = []

    def stage(self, tag: str, text: str, data=None):
        self.rows.append({"stage": tag, "text": text, "data": data})
        print(f"\n[{tag:9}] {text}")
        if data is not None:
            s = json.dumps(data, ensure_ascii=False)
            print(f"{'':12}{s[:200]}{'…' if len(s) > 200 else ''}")

    def detail(self, lines):
        for ln in lines:
            print(f"{'':12}· {ln}")

    def dump(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.rows, f, ensure_ascii=False, indent=1)


def banner(title):
    print("\n" + "=" * W)
    print(title)
    print("=" * W)


def run_skill(registry, trace, name, **kwargs):
    """SKILL 선택 → EXECUTE → EVALUATE 를 한 묶음으로 찍는다."""
    skill = registry.get(name)
    trace.stage("SKILL", f"{skill.name} 선택 — {skill.description.split('.')[0]}")
    res = skill.run(**kwargs)
    shown = {k: v for k, v in res.output.items() if k != "trace"}
    trace.stage("EXECUTE", f"{skill.name} 실행", shown)
    trace.stage("EVALUATE", "근거")
    trace.detail(res.evidence[:12])
    if len(res.evidence) > 12:
        print(f"{'':12}… ({len(res.evidence) - 12}줄 생략)")
    return res
