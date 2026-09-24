# -*- coding: utf-8 -*-
"""식약처 조리식품 레시피 DB(COOKRCP01)의 재료 문자열을 구조화한다.

원문은 사람이 읽으라고 쓴 문장이라 표기가 두 가지로 섞여 있다.

    연두부 75g(3/4모), 칵테일새우 20g(5마리)     ← 이름 뒤에 수량
    돼지고기(50g), 배춧잎(5장), 부추(30g)        ← 괄호 안에 수량
    닭고기(가슴살, 120g)                          ← 괄호 안에 설명과 수량

둘 다 처리한다. g 단위가 없는 항목(5장·3알·1뿌리)은 버린다 —
질량으로 환산할 근거가 없는 값을 지어내지 않기 위해서다.
"""
from __future__ import annotations
import re

NAME = r"[가-힣A-Za-z][가-힣A-Za-z0-9 ]{0,12}?"

# 괄호 안에 수량:  돼지고기(50g) / 닭고기(가슴살, 120g)
P_PAREN = re.compile(rf"({NAME})\s*\(\s*(?:[^()]*?,\s*)?([\d]+(?:\.[\d]+)?)\s*g\s*[^()]*\)")
# 이름 뒤에 수량:  연두부 75g(3/4모)
P_PLAIN = re.compile(rf"({NAME})\s*([\d]+(?:\.[\d]+)?)\s*g")

# 재료명 앞에 붙는 조리 상태. 같은 재료를 다른 이름으로 세지 않기 위해 떼어낸다.
PREFIX = re.compile(r"^(다진|저염|생|말린|삶은|불린|간|채썬|건|냉동|시판|무염)\s*")
JUNK = {"", "물", "약간", "적당량"}


def clean(name: str) -> str:
    name = PREFIX.sub("", name.strip()).strip()
    return re.sub(r"\s+", " ", name)


def _split_items(text: str) -> list[str]:
    """쉼표·줄바꿈으로 나누되, 괄호 안의 쉼표는 자르지 않는다.

    '닭고기(가슴살, 120g)' 을 그냥 쉼표로 자르면 '닭고기(가슴살' 과 '120g)' 이
    되어 수량을 잃는다. 괄호 깊이를 세면서 나눈다.
    """
    items, buf, depth = [], [], 0
    for ch in text or "":
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and (ch == "," or ch == "\n"):
            items.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return items


def parse_ingredients(text: str) -> list[dict]:
    """재료 문자열 → [{name, qty_g}] . 순서는 원문 순서를 지킨다."""
    out, seen = [], set()
    for seg in _split_items(text):
        seg = seg.strip().lstrip("●·•[]-—").strip()
        if not seg:
            continue
        m = P_PAREN.search(seg) or P_PLAIN.search(seg)
        if not m:
            continue
        name, qty = clean(m.group(1)), float(m.group(2))
        if name in JUNK or name in seen or len(name) > 12:
            continue
        seen.add(name)
        out.append({"name": name, "qty_g": qty})
    return out


def contains_any(text: str, keywords) -> list[str]:
    """재료 원문에 기피·알레르기 키워드가 들어 있는지 본다.

    구조화된 재료 목록이 아니라 **원문 전체**를 본다.
    파싱이 놓친 항목(5장·1뿌리처럼 g 표기가 없는 것)에도 알레르기 재료가
    있을 수 있는데, 안전 판정에서 그것을 놓치면 안 되기 때문이다.
    """
    t = text or ""
    return [k for k in keywords if k in t]
