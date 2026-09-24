# -*- coding: utf-8 -*-
"""스킬 인터페이스와 레지스트리.

스킬은 '하나의 목적을 수행하는 재사용 가능한 능력 단위'다.
- 도메인 객체가 아니라 일반 타입을 주고받는다 (조리 기록 ID 같은 걸 받지 않는다)
- 전역 상태를 읽지 않는다. 필요한 것은 전부 인자로 주입받는다
- 서로를 모른다. 순서는 오케스트레이터만 안다

이 두 규칙 때문에 같은 스킬을 다른 기기·다른 도메인에 그대로 쓸 수 있다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SkillResult:
    ok: bool
    output: dict
    evidence: list = field(default_factory=list)   # 판단 근거 (EVALUATE 단계에서 출력)

    def __repr__(self):
        return f"SkillResult(ok={self.ok}, output={self.output})"


class Skill:
    """모든 스킬의 공통 인터페이스."""
    name: str = ""
    description: str = ""          # 에이전트가 읽고 스킬을 고르는 근거
    input_schema: dict = {}
    reusable_for: list = []        # 이 스킬이 적용 가능한 다른 도메인

    # 계획 수립용 선언. 플래너는 이 두 개만 보고 호출 순서를 계산한다.
    # 스킬은 여전히 서로를 모른다 — 아는 것은 자기가 무엇을 필요로 하고
    # 무엇을 만들어내는지 뿐이다.
    requires: tuple = ()           # 실행 전에 충족돼야 하는 사실
    provides: tuple = ()           # 실행 후 새로 성립하는 사실

    def run(self, **kwargs) -> SkillResult:
        raise NotImplementedError

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema,
                "reusable_for": self.reusable_for,
                "requires": list(self.requires),
                "provides": list(self.provides)}


class Registry:
    """에이전트는 여기서 스킬을 조회해 고른다. import 로 직접 부르지 않는다."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        self._skills[skill.name] = skill
        return skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"등록되지 않은 스킬: {name}")
        return self._skills[name]

    def list(self) -> list[dict]:
        return [s.spec() for s in self._skills.values()]

    def names(self) -> list[str]:
        return list(self._skills)
