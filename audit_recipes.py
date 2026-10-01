# -*- coding: utf-8 -*-
"""공개 레시피 100건을 **전수** 규칙으로 검사한다 — 사례를 찾아 고치는 대신.

보는 것:
  N 영양값   열량 ≈ 4·단백질 + 4·탄수화물 + 9·지방 (±40%, 가정) · 범위(열량 10~2000,
             나트륨 0~5000mg) · 빈 값
  P 파싱     원문의 "숫자g" 가 재료 목록에 빠짐없이 들어갔나(양 합 비교) · 이름 이상
             (한 글자·숫자·괄호·소제목이 재료로)
  M 조리법   메뉴 이름이 말하는 조리법과 분류(method)가 맞나 — 끓이기만 조리기에 넣는다
  D 중복     같은 메뉴 이름

    python audit_recipes.py          → audit_recipes.json  (문제 목록)
"""
from __future__ import annotations
import json
import re
import sys

import kitchen_domain as KD

# 메뉴 이름이 조리법을 말하는 낱말(가정 — 한국어 요리 이름의 관례)
BOIL = ("찌개", "국", "탕", "전골", "죽", "스튜", "수프", "스프", "조림")
ONE_CHAR_FOOD = {"무", "꿀", "잣", "배", "쌀", "파", "김", "밤", "굴", "콩", "떡", "깨", "게",
                 "귤", "감", "술", "젓", "엿", "밥"}       # 실제 한 글자 재료(검사 오탐 방지)
# '찜' 은 넣지 않는다 — 찜닭·갈비찜·토마토소고기찜은 냄비에서 조린다(찐 것은 method='찌기')
NOT_BOIL = ("튀김", "구이", "볶음", "샐러드", "주스", "무침", "전", "부침",
            "말이", "롤", "쌈", "스무디", "라떼", "에이드", "빵", "케이크", "쿠키")
SECTION = ("양념장", "양념", "소스", "고명", "드레싱", "재료", "주재료", "부재료", "육수")


def audit() -> list:
    rows = KD.load_recipes()["recipes"]
    out = []

    def bad(r, kind, why):
        out.append({"id": r["recipe_id"], "menu": r["menu"], "kind": kind, "why": why})

    names = {}
    for r in rows:
        names.setdefault(r["menu"], []).append(r["recipe_id"])
        # ── N 영양값 ──
        k, p, c, f, na = (r.get(x) for x in ("kcal", "protein_g", "carb_g", "fat_g", "sodium_mg"))
        if None in (k, p, c, f, na):
            bad(r, "N빈값", f"열량 {k} · 단 {p} · 탄 {c} · 지 {f} · 나트륨 {na}")
        elif not r.get("nutrition_ok"):
            # 원본 영양값이 앞뒤가 안 맞는 행(열량·범위 기준은 kitchen_domain.nutrition_ok).
            # 고칠 수 없는 원본 오류라, 나트륨을 믿지 않는 처리가 됐는지만 기록한다.
            est = 4 * p + 4 * c + 9 * f
            bad(r, "N믿을수없음(처리됨)", f"열량 {k} · 4·{p}+4·{c}+9·{f} = {est:.0f} "
                                     f"— 저염 가구에서는 고르지 않는다")
        # ── P 파싱 ──
        raw = r.get("parts_raw") or ""
        raw_g = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*g(?![a-z가-힣])", raw)]
        parsed = [i["qty_g"] for i in r.get("ingredients", [])] + [r.get("water_g") or 0]
        if raw_g and abs(sum(raw_g) - sum(parsed)) > max(5, 0.05 * sum(raw_g)):
            bad(r, "P양누락", f"원문 g 합 {sum(raw_g):.0f} · 파싱 합 {sum(parsed):.0f}")
        for i in r.get("ingredients", []):
            n = i["name"]
            if (len(n) < 2 and n not in ONE_CHAR_FOOD) or re.search(r"[\d()\[\]:]", n) \
                    or n in SECTION:
                bad(r, "P이름", f"'{n}' {i['qty_g']}g")
            if i["qty_g"] <= 0 or i["qty_g"] > 5000:
                bad(r, "P양범위", f"'{n}' {i['qty_g']}g")
        # ── M 조리법 ──
        menu, method = r["menu"], r.get("method")
        says_boil = any(w in menu.replace("국수", "") for w in BOIL)   # '국수' 는 국이 아니다
        says_not = any(w in menu for w in NOT_BOIL)
        if says_boil and not says_not and method != "끓이기":
            bad(r, "M끓이기누락", f"'{menu}' 가 {method} 로 분류 — 조리기 후보에서 빠진다")
        if says_not and not says_boil and method == "끓이기" and \
                KD.fits_device({"method": method, "menu": menu,
                                "record_id": "pub_" + r["recipe_id"]}) is None:
            bad(r, "M끓이기오분류", f"'{menu}' 가 끓이기로 분류 — 냄비에서 졸이게 된다")
    for menu, ids in names.items():
        if len(ids) > 1:
            out.append({"id": ",".join(ids), "menu": menu, "kind": "D중복", "why": f"{len(ids)}건"})
    return out


def main() -> int:
    out = audit()
    with open("audit_recipes.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    kinds = {}
    for o in out:
        kinds.setdefault(o["kind"], []).append(o)
    print(f"레시피 100건 전수 검사 — 문제 {len(out)}건")
    for k, v in sorted(kinds.items()):
        print(f"\n  [{k}] {len(v)}건")
        for o in v[:12]:
            print(f"    {o['id']:>5} {o['menu'][:16]:16} {o['why']}")
        if len(v) > 12:
            print(f"    … {len(v) - 12}건 더")
    # 처리되지 않은 문제가 있으면 실패 — check_all 이 매번 돈다(고칠 수 없는 원본 오류는
    # "(처리됨)" 으로 표시되고, 처리된 것으로 센다)
    open_ = [o for o in out if not o["kind"].endswith("(처리됨)")]
    print(f"\n  처리되지 않은 문제 {len(open_)}건")
    return 1 if open_ else 0


if __name__ == "__main__":
    sys.exit(main())
