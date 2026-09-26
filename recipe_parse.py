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
# 한 글자 접두어(간·생·건)는 **뒤에 공백이 있을 때만** 떼어낸다.
# 예전에는 공백 없이도 떼어내, 실제 자료 100건에서
#   간장 -> 장 (6회) · 생강 -> 강 (5회) · 생크림 -> 크림 (8회)
#   생강청 -> 강청 (3회) · 생강즙 -> 강즙 (2회)
# 가 됐다. '장' 은 재고의 '간장' 과 대조되지 않아 없는 재료로 잡힌다.
# 두 글자 이상 접두어는 붙여 쓰는 것이 보통이라 공백을 요구하지 않는다
# (저염간장 -> 간장 9회 · 다진마늘 -> 마늘 6회 · 저염된장 -> 된장 4회).
# 잃는 것은 '건새우 -> 새우' 3회뿐이고, 얻는 것이 24회다.
PREFIX = re.compile(
    r"^(?:(?:다진|저염|말린|삶은|불린|채썬|냉동|시판|무염)\s*|(?:간|생|건)\s+)")
# 이름 뒤에 남는 개수 표기. "달걀 1개 50g" 에서 이름이 '달걀 1개' 가 되면
# 재고의 '달걀' 과 대조되지 않아 없는 재료로 잡힌다.
# (실제 자료 100건·재료 1025개 중 2건: '달걀 1개', '오징어 1마리')
COUNT_SUFFIX = re.compile(
    r"\s*\d+(?:\.\d+)?\s*"
    r"(개|마리|장|알|쪽|톨|뿌리|줄기|컵|큰술|작은술|봉|팩|공기|인분|모|송이|단)?$")
JUNK = {"", "물", "약간", "적당량"}


def clean(name: str) -> str:
    name = PREFIX.sub("", name.strip()).strip()
    name = COUNT_SUFFIX.sub("", name).strip()
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
        if name in JUNK or len(name) > 12:
            continue
        if name in seen:
            # 같은 재료가 두 번 나오면 **합친다.** 전에는 뒤엣것을 버려서
            # 양이 적게 계산됐다 (밑간용 간장 + 조림용 간장처럼 실제
            # 레시피에 흔하다. 자료 100건에서 11건).
            for o in out:
                if o["name"] == name:
                    o["qty_g"] = round(o["qty_g"] + qty, 2)
                    break
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
    out = []
    for k in keywords:
        if k not in t:
            continue
        # **어느 재료에 걸렸는지 함께 적는다.**
        #
        # 짧은 기피어는 다른 재료에 부분 일치한다 — 자료 100건에서
        #   배 → 배추(3)·양배추(2)·적양배추(1)
        #   게 → 스파게티(1)      밀 → 코코넛밀크(1)      파 → 파프리카(2)
        # 안전 판정이므로 **거르는 것 자체는 그대로 둔다**(놓치는 쪽이
        # 훨씬 위험하다). 다만 사용자가 "양배추감자전이 왜 빠졌지" 를
        # 알 수 없으면 그 판단을 검토할 수 없다. 걸린 재료를 적어 둔다.
        names = [i["name"] for i in parse_ingredients(t) if k in i["name"]]
        if any(n == k for n in names) or not names:
            out.append(k)                      # 정확히 그 재료다
        else:
            out.append(f"{k}({'·'.join(names[:3])})")   # 부분 일치
    return out
