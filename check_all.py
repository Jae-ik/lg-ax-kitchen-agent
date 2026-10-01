# -*- coding: utf-8 -*-
"""검사를 전부 돌리고, **실패하면 무엇부터 봐야 하는지 알려준다.**

왜 이 파일이 필요한가:
  검사를 11종 만들어 놓고도 한 번에 돌리는 진입점이 없었다. 매번
  손으로 `for f in check_*.py` 를 돌렸다는 뜻이고, 그러면 "검사가
  지킨다" 는 말은 **내가 기억할 때만 참**이다.

  더 큰 구멍은 그다음이었다. 검사가 깨졌을 때 **무엇을 할지가 어디에도
  적혀 있지 않았다.** 하루 동안 실패 네 건에 서로 다른 대처를 했는데
  그 선택 기준이 코드에 없었다.

      검사 식이 틀렸다        -> 검사를 고쳤다      (physics · skim 41건)
      시험 설계가 틀렸다      -> 시험을 다시 짰다   (cooking · 익힘 포화)
      문서가 낡았다           -> 문서를 갱신했다    (consistency · 수치)
      가정값이 결과를 지배한다 -> **기준을 완화했다** (sensitivity)

  마지막이 위험하다. 가장 쉬운 길이기 때문이다. 아무 안내가 없으면
  다음 사람은 늘 그 길로 간다 — 그러면 검사는 통과하지만 아무것도
  지키지 않는다. 그래서 대처 순서를 검사 옆에 붙여 둔다.

쓰는 법
    python check_all.py           전부 돌린다
    python check_all.py --fast    오래 걸리는 것(분산 측정)은 건너뛴다
"""
from __future__ import annotations
import ast
import os
import subprocess
import sys
import time

# 순서에 뜻이 있다. 산출물을 먼저 새로 만들고, 그다음 그것을 읽는 검사를
# 돌린다. 거꾸로 하면 "낡았다" 는 실패가 먼저 나서 진짜 문제를 가린다.
REGEN = [
    ("run_design.py", "4상황을 실행해 scenarios.json·trace_design.json 을 만든다"),
    ("measure_variance.py", "시드를 바꿔 200회씩 돌려 variance.json 을 만든다"),
    # README 의 '믿는 재고가 틀리면' 표를 check_consistency 가 이 파일로 대조한다
    ("belief_gap.py", "믿는 재고와 실제가 다를 때를 재 belief_gap.json 을 만든다"),
    ("delivery_gap.py", "주문 사고(결제 실패·취소·지연)를 재 delivery_gap.json 을 만든다"),
    ("days.py", "장부를 잇는 사흘과 장부 없는 사흘을 재 days.json 을 만든다"),
]

# (스크립트, 무엇을 지키는가, **깨졌을 때 무엇부터 보는가**)
CHECKS = [
    ("check_regression.py", "4상황의 수치가 그대로인가", [
        "산출물이 방금 만든 것인지 먼저 본다 — 아니면 run_design.py 부터",
        "값이 달라졌으면 **무엇을 고쳐서 달라졌는지** 커밋을 짚는다",
        "기대값을 고치는 것은 마지막이다. 고칠 때는 왜 달라져도 되는지 적는다",
    ]),
    ("check_consistency.py", "선언=구현, 문서=실행 결과, 산출물 신선도", [
        "문서 수치 불일치면 **실행값이 맞는지 먼저** 확인한다",
        "실행값이 맞으면 문서를 갱신한다 (문서가 따라가는 쪽이다)",
        "선언=구현 불일치면 선언을 고친다 — 구현이 진짜다",
    ]),
    ("check_planner.py", "순서를 정말 계산하는가", [
        "planner.py 를 고쳤는가? 계획 결과가 달라졌으면 그 변경이 의도였는지 본다",
        "'같은 스킬 두 번' 같은 한계 항목은 설계 경계다 — 고치려면 구조를 바꿔야 한다",
    ]),
    ("check_physics.py", "질량·에너지·온도가 스스로 모순되지 않는가", [
        "**검사 식을 먼저 의심한다.** 실제로 질량 수지 식을 틀리게 써서"
        " 멀쩡한 skim 을 41건 위반으로 읽은 적이 있다",
        "식이 맞으면 모델을 본다. 상수를 바꿨다면 그 근거도 다시 잰다",
    ]),
    ("check_cooking.py", "요리로서 말이 되는가", [
        "**시험 입력이 계약을 지키는지 먼저** 본다 (weigh 반환 키 등)",
        "포화·경계에서 재고 있지 않은지 본다 (익힘이 1.0 이면 내려갈 곳이 없다)",
        "그다음 코드를 본다",
    ]),
    ("check_skills.py", "스킬이 단독으로도 성립하는가", [
        "계약 위반 항목이면 input_schema 를 읽고 시험 입력을 맞춘다",
        "순수성 실패면 스킬이 전역 상태나 난수를 쓰는지 본다",
    ]),
    ("check_interlock.py", "각 단계가 뒤에 실제로 영향을 주는가", [
        "'죽은 단계' 가 나오면 **교란 함수가 실제 출력 키를 건드리는지 먼저** 본다",
        "(없는 키를 건드려 아무 효과가 없던 적이 있다 — 그래서 _assert_changed 가 있다)",
    ]),
    ("check_generalize.py", "설계하지 않은 상황에서도 이유를 남기는가", [
        "ok=False 자체는 실패가 아니다. **이유가 안 남는 것**이 실패다",
        "중단됐는데 절감이 집계되면 그게 문제다",
    ]),
    ("check_reuse.py", "reusable_for 가 말뿐인지", [
        "스킬 파일 해시가 바뀌었다고 나오면 검사 중 스킬을 고친 것이다 — 결과 무효",
        "주입만 바꿔서 안 되면 그 스킬은 도메인에 묶여 있다. 선언을 고치거나 코드를 고친다",
    ]),
    ("check_sensitivity.py", "가정값이 결과를 지배하지 않는가", [
        "**기준 완화는 마지막 수단이다.**",
        "새 항목이 커졌으면 (1) 상수를 실측으로 바꿀 수 있는지,"
        " (2) 그 지표의 허용 폭이 맞는지 먼저 본다",
        "KNOWN_RISKY 에 넣으려면 **무엇이 뒤집히는지 수치로** 적는다."
        " 적을 수 없으면 아직 이해하지 못한 것이다",
    ]),
    ("check_thinq.py", "자연어 다리 — 키 없이도 확인한다", [
        "안전 항목(기피 재료가 재고로 들어감)이면 **그것부터** 고친다",
        "범주어(갑각류 등)가 새면 recipe_parse.CATEGORY 를 본다 — 넓힐 때는 "
        "걸리는 재료 덩어리를 전수 출력해 오탐(얇게→게)을 확인한다",
        "LLM 값 검증 실패면 _validate 의 범위를 본다 — 넓히기 전에 "
        "그 값이 정말 허용될 값인지 묻는다",
        "LLM 장면 제안(d 항목)이 깨지면 llm_design._reject_reason 을 본다 — "
        "버린 목록을 읽고 **옳은 장면까지 버리지 않았는지** 먼저 확인한다",
        "'제어 결과가 달라진다' 가 뜨면 LLM 이 제어 루프에 샌 것이다 — "
        "이 설계의 전제가 깨진 것이므로 최우선이다",
    ]),
    ("audit_recipes.py", "공개 레시피 100건 전수 — 영양·파싱·조리법·중복", [
        "원본 영양값 오류는 고칠 수 없다 — nutrition_ok 로 표시해 저염 가구에서 빼는지 본다",
        "파싱 누락이면 원문(parts_raw)을 출력해 어느 표기가 빠졌는지 본다(물·괄호 이름이 그랬다)",
        "검사 규칙이 틀렸을 수 있다 — 한 글자 재료·'국수' 처럼 규칙의 오탐부터 의심한다",
    ]),
    ("audit_physics.py", "물리 극단값 격자 — 늘 성립해야 할 성질", [
        "질량·온도가 NaN·음수면 입구 검사(_qty_ok·_set_temp)가 빠진 경로를 찾는다",
        "화력이 상한 밖이면 start/set_power 중 어느 쪽이 묶지 않는지 본다",
        "같은 물리를 하는 함수가 둘이면 한쪽만 고쳤는지 본다(물 붓기·재료 넣기)",
    ]),
    ("stress_test.py", "예외 없이 끝나는가", [
        "예외가 난 입력을 그대로 재현해 본다",
        "'예외 0건' 은 결과가 옳다는 뜻이 아니다 — 값은 따로 읽는다",
    ]),
]

SLOW = {"measure_variance.py"}


def _run(script: str) -> tuple[int, str, float]:
    t0 = time.time()
    p = subprocess.run([sys.executable, "-X", "utf8", script],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0


def main() -> int:
    fast = "--fast" in sys.argv
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print("=" * 74)
    print("전체 검사" + (" (--fast: 분산 측정 건너뜀)" if fast else ""))
    print("=" * 74)

    print("\n[1] 산출물을 새로 만든다")
    for script, why in REGEN:
        if fast and script in SLOW:
            print(f"  -- {script:24s} 건너뜀")
            continue
        code, out, sec = _run(script)
        mark = "OK" if code == 0 else "!!"
        print(f"  {mark} {script:24s} {sec:5.1f}초  {why}")
        if code != 0:
            print(f"      만들지 못했다 — 아래 검사 결과는 믿을 수 없다")
            print("      " + out.strip().splitlines()[-1][:80])
            return 1

    print("\n[2] 검사")
    failed = []
    for script, what, _ in CHECKS:
        code, out, sec = _run(script)
        # **같은 이름의 검사 함수가 두 번 있으면 앞의 것은 조용히 사라진다.**
        # 2026-09-30 에 새 검사를 c5b 로 넣었는데 이미 c5b 가 있어, 뒤의 것이
        # 앞의 것을 덮어 새 검사가 한 번도 돌지 않았다(통과로 보였다).
        # (전역 이름으로 검사를 찾는 check_consistency 에서만 실제로 사라진다.
        #  데코레이터가 목록에 넣는 파일은 둘 다 돈다 — check_cooking 의 c18 이
        #  그랬다. 그래도 이름은 하나로 맞춘다: 어느 방식인지 매번 따지지 않도록.)
        with open(script, encoding="utf-8") as f:
            tops = [d.name for d in ast.parse(f.read()).body
                    if isinstance(d, ast.FunctionDef)]
        dup = sorted({t for t in tops if tops.count(t) > 1})
        if dup:
            code = 1
            out += f"\n!! 같은 이름의 함수가 두 번 있다 — 앞의 것이 덮인다: {dup}"
        # **아무것도 안 하고 끝난 검사는 통과가 아니다.** 패치로 파일 끝의
        # `if __name__ == "__main__":` 이 지워져 check_generalize 가 아무것도
        # 돌리지 않고 종료 코드 0 으로 끝났는데, 여기서 통과로 셌다.
        if code == 0 and len([l for l in out.splitlines() if l.strip()]) < 3:
            code = 1
            out += "\n!! 출력 없이 끝났다 — 검사가 실행되지 않았을 수 있다"
        tail = next((l for l in reversed(out.strip().splitlines())
                     if l.strip()), "")
        mark = "OK" if code == 0 else "!!"
        print(f"  {mark} {script:24s} {sec:5.1f}초  {what}")
        if code != 0:
            failed.append((script, out))
            for l in out.strip().splitlines():
                if l.lstrip().startswith("!!"):
                    print(f"      {l.strip()[:96]}")

    print()
    print("=" * 74)
    if not failed:
        print(f"  {len(CHECKS)}종 전부 통과")
        print("=" * 74)
        return 0

    print(f"  {len(failed)}종 실패 — 무엇부터 볼지 아래 순서를 따른다")
    print("=" * 74)
    for script, _ in failed:
        steps = next(s for f, _, s in CHECKS if f == script)
        print(f"\n[{script}]")
        for i, s in enumerate(steps, 1):
            print(f"   {i}. {s}")
    print()
    print("  공통 원칙")
    print("   · 기대값·기준을 고치는 것은 **마지막 수단**이다.")
    print("   · 고치기 전에 '내 검사가 틀렸는가' 를 먼저 묻는다 —")
    print("     하루에 세 번 그랬다(질량 수지 식·익힘 포화·물만 넣은 눌어붙음).")
    print("   · 기준을 바꿨으면 **왜 바꿔도 되는지 주석에 남긴다.**")
    print("     남길 수 없으면 아직 원인을 모르는 것이다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
