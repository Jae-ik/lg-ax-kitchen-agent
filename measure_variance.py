# -*- coding: utf-8 -*-
"""흔들림 측정 — 시드를 바꿔 반복하고 분포를 낸다.

두 가지를 나눠 잰다.
  A. converge 단계 수    제안서에 인용한 "조리기 8단계 / 건조기 11단계" 가 얼마나 흔들리는지
  B. 설계 에이전트 전체   계획·메뉴·코스가 시드에 따라 바뀌는지 (바뀌면 안 된다)

난수는 세 곳에만 들어간다.
  kitchen.prep_weigh  계량 오차 ±3%
  kitchen.Cooker.tick 증발 편차 ±8%
  dryer.Dryer.tick    건조 편차 ±10%
따라서 계획 순서·메뉴 선택·세척 코스는 불변일 것으로 **예상**되지만,
예상은 근거가 아니므로 고유값 개수를 직접 센다.

    python measure_variance.py            A 500회 · B 200회
    python measure_variance.py 100 50     개수 지정
"""
from __future__ import annotations
import contextlib
import io
import json
import random
import statistics as st
import sys
from collections import Counter

import kitchen as K
import dryer as D
import personas
from kitchen_domain import pantry_stock
from orchestrator import Trace
from skills import REGISTRY


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def summary(name, xs, unit=""):
    u = sorted(set(xs))
    line = (f"  {name:22} n={len(xs):<4} 중앙 {st.median(xs):>7.4g}{unit}"
            f"  범위 {min(xs):.4g}~{max(xs):.4g}{unit}")
    if len(xs) > 1:
        line += f"  표준편차 {st.pstdev(xs):.4g}"
    print(line)
    print(f"  {'':22} 5%~95% {pct(xs,0.05):.4g}~{pct(xs,0.95):.4g}{unit}"
          f"  고유값 {len(u)}개")
    return {"n": len(xs), "median": st.median(xs), "min": min(xs), "max": max(xs),
            "sd": st.pstdev(xs) if len(xs) > 1 else 0.0,
            "p05": pct(xs, 0.05), "p95": pct(xs, 0.95), "unique": len(u)}


# ═══════════════ A. converge 단계 수 ═══════════════
def measure_converge(n: int) -> dict:
    rec = K.RECORDS["rec_001"]
    cook_steps, cook_final, dry_steps, dry_final = [], [], [], []
    conv = REGISTRY.get("converge")

    for s in range(n):
        random.seed(s)
        # 조리기 — 계량 오차까지 포함해 매 회 새로 계량한다.
        # 기본 재고에는 두부가 없다. run_demo 는 조달 단계에서 두부를 채운 뒤
        # 계량하므로, 같은 출발점을 만들려면 여기서도 채워야 한다.
        # (이 한 줄이 빠져 있을 때 7단계가 나왔고 파이프라인 값 8단계와 어긋났다)
        K.reset(seed=s)
        K.fridge_add("두부", 150)
        total = 0.0
        extra = 0.0
        for ing in rec["ingredients"]:
            w = K.prep_weigh(ing["name"], ing["qty_g"])
            if w.get("ok"):
                total += w["actual_g"]; extra += w["expected_extra_water_g"]
        total += rec["initial_mass_g"] - sum(i["qty_g"] for i in rec["ingredients"])
        K.COOKER.start(total, extra, power=3)
        r = conv.run(observe=lambda: K.COOKER.state(), actuate=K.COOKER.set_power,
                     step=K.COOKER.tick, metric="mass_ratio",
                     target=rec["target_mass_ratio"], direction="down",
                     ready_key="temp_c", ready_at=92.0, max_steps=30)
        K.COOKER.stop()
        cook_steps.append(r.output["steps"]); cook_final.append(r.output["final"])

        # 건조기 — 같은 스킬, 다른 기기
        D.DRYER.start(moisture=0.18, power=2)
        r2 = conv.run(observe=lambda: D.DRYER.state(), actuate=D.DRYER.set_power,
                      step=D.DRYER.tick, metric="moisture", target=0.08,
                      direction="down", ready_key="drum_temp_c", ready_at=45.0,
                      max_steps=40)
        D.DRYER.stop()
        dry_steps.append(r2.output["steps"]); dry_final.append(r2.output["final"])

    print(f"\n[A] converge 단계 수 — 시드 {n}회")
    out = {}
    out["cooker_steps"] = summary("조리기 단계 수", cook_steps, "분")
    out["cooker_final"] = summary("조리기 최종 질량비", cook_final)
    out["dryer_steps"] = summary("건조기 단계 수", dry_steps, "분")
    out["dryer_final"] = summary("건조기 최종 함수율", dry_final)
    print("  조리기 단계 분포:", dict(sorted(Counter(cook_steps).items())))
    print("  건조기 단계 분포:", dict(sorted(Counter(dry_steps).items())))
    return out


# ═══════════════ B. 설계 에이전트 전체 ═══════════════
def measure_agent(n: int) -> dict:
    import run_design
    out = {}
    for pid in personas.ids():
        plans, menus, courses, touches, beats, cooks = [], [], [], [], [], []
        for s in range(n):
            with contextlib.redirect_stdout(io.StringIO()):
                r = run_design.design_for(pid, Trace(), seed=s)
            v, f = r["verify"], r["flow"]
            plans.append(" → ".join(f["steps"]))
            menus.append(v["metrics"].get("메뉴"))
            courses.append(v["metrics"].get("세척 코스"))
            touches.append(v["user_touches"])
            beats.append(v["beats_met"] / v["beats_total"])
            if "가열 시간(분)" in v["metrics"]:
                cooks.append(v["metrics"]["가열 시간(분)"])

        label = personas.get(pid)["label"]
        print(f"\n[B] {label} — 시드 {n}회")
        det = {"계획 순서": plans, "선택 메뉴": menus, "세척 코스": courses}
        row = {}
        for k, vals in det.items():
            u = Counter(vals)
            mark = "불변" if len(u) == 1 else f"**{len(u)}가지로 갈림**"
            print(f"  {k:10} {mark}  {dict(u) if len(u) > 1 else list(u)[0]}")
            row[k] = {"unique": len(u), "values": dict(u)}
        row["touches"] = summary("사용자 개입", touches, "회")
        row["beats"] = summary("장면 달성률", beats)
        if cooks:
            row["cook_min"] = summary("가열 시간", cooks, "분")
            print("  가열 시간 분포:", dict(sorted(Counter(cooks).items())))
        out[pid] = row
    return out


def main():
    na = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    nb = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    print("=" * 78)
    print(f"흔들림 측정 — converge {na}회 · 설계 에이전트 {nb}회 × {len(personas.ids())}상황")
    print("=" * 78)
    res = {"n_converge": na, "n_agent": nb,
           "converge": measure_converge(na), "agent": measure_agent(nb)}
    with open("variance.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print("\nvariance.json 저장")


if __name__ == "__main__":
    main()
