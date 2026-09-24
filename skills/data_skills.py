# -*- coding: utf-8 -*-
"""데이터 수집·선별 스킬.

Agent 가 판단의 근거로 쓸 자료를 **직접 가져오고 거르는** 단계다.
여기서 거르지 않으면 이후 단계가 못 먹는 재료나 제한을 넘는 메뉴를 후보로 삼는다.

자료 출처: 식품의약품안전처 조리식품의 레시피 DB (COOKRCP01)
스킬은 파일도 네트워크도 모른다. 적재 함수를 주입받는다.
"""
from __future__ import annotations
from typing import Callable

from .base import Skill, SkillResult


class RecipeSourceSkill(Skill):
    name = "recipe_source"
    description = ("공개 레시피 자료를 불러와 이 가구가 먹을 수 있는 것만 남긴다. "
                   "기피·알레르기 재료는 구조화된 재료 목록이 아니라 원문 전체에서 "
                   "찾는다 — 수량 표기가 없어 파싱되지 않은 재료도 놓치지 않기 위해서다.")
    input_schema = {
        "load": "() -> dict   자료 적재 함수(캐시 또는 API). 주입받는다",
        "match": "(text, keywords) -> list[str]   원문 검사 함수. 주입받는다",
        "avoid": "list[str]   기피·알레르기 재료",
        "max_sodium_mg": "float | None   1인분 나트륨 상한",
        "methods": "list[str] | None   허용 조리법 (예: 끓이기)",
    }
    reusable_for = ["레시피 DB", "제품 리뷰 수집", "특허·논문 수집", "식자재 카탈로그"]
    provides = ("recipe_pool",)

    def run(self, load: Callable, match: Callable, avoid: list | None = None,
            max_sodium_mg: float | None = None, methods: list | None = None,
            **_) -> SkillResult:
        data = load()
        pool = data.get("recipes", [])
        ev = [f"출처: {data.get('source')}",
              f"인증: {data.get('key_used')} · 적재 {len(pool)}건"]

        avoid = list(avoid or [])
        kept, blocked, over_na, wrong_way = [], [], [], []

        for r in pool:
            hits = match(r.get("parts_raw", ""), avoid) if avoid else []
            if hits:
                blocked.append({"menu": r["menu"], "hits": hits})
                continue
            if methods and r.get("method") not in methods:
                wrong_way.append(r["menu"])
                continue
            na = r.get("sodium_mg")
            if max_sodium_mg is not None and na is not None and na > max_sodium_mg:
                over_na.append({"menu": r["menu"], "sodium_mg": na})
                continue
            kept.append(r)

        if avoid:
            ev.append(f"기피·알레르기({', '.join(avoid)})로 {len(blocked)}건 제외")
            for b in blocked[:4]:
                ev.append(f"  · {b['menu']} — {', '.join(b['hits'])} 포함")
            if len(blocked) > 4:
                ev.append(f"  · 외 {len(blocked) - 4}건")
        if methods:
            ev.append(f"조리법 {methods} 외 {len(wrong_way)}건 제외")
        if max_sodium_mg is not None:
            ev.append(f"나트륨 {max_sodium_mg:.0f}mg 초과 {len(over_na)}건 제외")
            for o in over_na[:3]:
                ev.append(f"  · {o['menu']} — {o['sodium_mg']:.0f}mg")
        ev.append(f"후보 {len(kept)}건 확정 (적재 {len(pool)}건 중)")

        return SkillResult(bool(kept), {
            "recipe_pool": kept,
            "recovery": (f"적재한 {len(pool)}건이 모두 걸러졌다 "
                         f"(기피 {len(blocked)}건, 나트륨 초과 {len(over_na)}건) — "
                         f"자료가 비었거나 조건이 너무 좁다"
                         if not kept else None),
            "loaded": len(pool), "kept": len(kept),
            "blocked_allergy": blocked, "blocked_sodium": over_na,
            "source": data.get("source"), "key_used": data.get("key_used")}, ev)
