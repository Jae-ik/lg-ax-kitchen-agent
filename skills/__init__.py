# -*- coding: utf-8 -*-
"""스킬 레지스트리 구성.

에이전트는 REGISTRY 를 통해서만 스킬에 접근한다.
스킬을 추가해도 에이전트 코드는 바뀌지 않는다.

두 층이 같은 레지스트리에 들어간다.
  설계 층  situation_read · scenario_draft · flow_design · experience_verify
  실행 층  inventory · menu · procure · prep · converge · aftercare
설계 층이 실행 층을 import 하지 않는다 — 실행은 주입받는다.
"""
from .base import Skill, SkillResult, Registry
from .core import ConvergeSkill, AftercareSkill
from .kitchen_skills import InventorySkill, MenuSkill, ProcureSkill, PrepSkill
from .design_skills import (SituationReadSkill, ScenarioDraftSkill,
                            FlowDesignSkill, ExperienceVerifySkill)
from .data_skills import RecipeSourceSkill

REGISTRY = Registry()

DESIGN_SKILLS = (SituationReadSkill(), ScenarioDraftSkill(),
                 FlowDesignSkill(), ExperienceVerifySkill())
EXEC_SKILLS = (RecipeSourceSkill(), InventorySkill(), MenuSkill(),
               ProcureSkill(), PrepSkill(), ConvergeSkill(), AftercareSkill())

for s in DESIGN_SKILLS + EXEC_SKILLS:
    REGISTRY.register(s)

__all__ = ["Skill", "SkillResult", "Registry", "REGISTRY",
           "DESIGN_SKILLS", "EXEC_SKILLS"]
