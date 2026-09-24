# -*- coding: utf-8 -*-
"""공공 데이터 수집 — 식약처 조리식품 레시피 DB (COOKRCP01).

    python fetch_data.py            캐시가 없을 때만 받는다
    python fetch_data.py --refresh  다시 받아 캐시를 갱신한다

인증키는 환경변수 MFDS_KEY 에서 읽는다. 없으면 공개 `sample` 키로 받는다.
sample 키는 100건까지 주고, 발급키는 전체 1,156건을 준다.
키 발급: https://www.foodsafetykorea.go.kr/api/  (무료)

받은 원문은 그대로 두고(data/_raw_recipes.json), 파싱한 결과를 따로 저장한다.
원문을 남겨야 파싱 규칙이 바뀌었을 때 다시 돌릴 수 있다.
"""
from __future__ import annotations
import json
import os
import pathlib
import sys
import urllib.request

from recipe_parse import parse_ingredients

BASE = "http://openapi.foodsafetykorea.go.kr/api"
SERVICE = "COOKRCP01"
DATA = pathlib.Path(__file__).parent / "data"
RAW = DATA / "_raw_recipes.json"
CACHE = DATA / "recipes.json"


def fetch(start: int = 1, end: int = 100) -> dict:
    key = os.environ.get("MFDS_KEY", "sample")
    url = f"{BASE}/{key}/{SERVICE}/json/{start}/{end}"
    shown = url.replace(key, "***") if key != "sample" else url
    print(f"  GET {shown}")
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def _merge(acc: dict, rows: list) -> dict:
    for r in rows:
        acc.setdefault(r.get("RCP_SEQ"), r)
    return acc


def collect() -> list:
    key = os.environ.get("MFDS_KEY")
    if key:
        rows, start = [], 1
        while True:                       # 발급키는 1,000건씩 끊어 받는다
            body = fetch(start, start + 999)[SERVICE]
            got = body.get("row", [])
            rows += got
            total = int(body.get("total_count", 0))
            if not got or len(rows) >= total:
                break
            start += 1000
        return rows

    # 공개 sample 키는 같은 요청에도 5건 또는 100건을 번갈아 준다(서버 동작).
    # 한 번의 응답을 믿지 않고 여러 번 받아 RCP_SEQ 기준으로 병합한다.
    acc, tries = {}, 0
    while tries < 6:
        tries += 1
        try:
            _merge(acc, fetch(1, 100)[SERVICE].get("row", []))
        except Exception as e:
            print(f"  {tries}회차 실패: {e}")
        print(f"  {tries}회차 후 누적 {len(acc)}건")
        if len(acc) >= 100:
            break
    return list(acc.values())


def build(rows: list) -> dict:
    out = []
    for r in rows:
        ings = parse_ingredients(r.get("RCP_PARTS_DTLS", ""))
        if not ings:
            continue
        out.append({
            "recipe_id": r.get("RCP_SEQ"),
            "menu": r.get("RCP_NM"),
            "category": r.get("RCP_PAT2"),      # 반찬·국&찌개·일품·밥·후식
            "method": r.get("RCP_WAY2"),        # 끓이기·굽기·찌기…
            "ingredients": ings,
            "parts_raw": r.get("RCP_PARTS_DTLS", ""),   # 알레르기 원문 검사용
            "kcal": _f(r.get("INFO_ENG")),
            "protein_g": _f(r.get("INFO_PRO")),
            "fat_g": _f(r.get("INFO_FAT")),
            "carb_g": _f(r.get("INFO_CAR")),
            "sodium_mg": _f(r.get("INFO_NA")),
            "weight_g": _f(r.get("INFO_WGT")),
        })
    return {"source": "식품의약품안전처 조리식품의 레시피 DB (COOKRCP01)",
            "endpoint": f"{BASE}/<키>/{SERVICE}/json/<시작>/<끝>",
            "license": "공공데이터 개방 — 식품안전나라 데이터활용서비스",
            "key_used": "발급키" if os.environ.get("MFDS_KEY") else "공개 sample 키",
            "count": len(out), "recipes": out}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    DATA.mkdir(exist_ok=True)
    refresh = "--refresh" in sys.argv
    if CACHE.exists() and not refresh:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        print(f"캐시 사용 — {d['count']}건 ({d['key_used']}). 다시 받으려면 --refresh")
        return
    print("식약처 레시피 DB 수집")
    rows = collect()
    RAW.write_text(json.dumps({SERVICE: {"row": rows}}, ensure_ascii=False),
                   encoding="utf-8")
    d = build(rows)
    CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  원문 {len(rows)}건 → 구조화 {d['count']}건 저장 ({d['key_used']})")


if __name__ == "__main__":
    main()
