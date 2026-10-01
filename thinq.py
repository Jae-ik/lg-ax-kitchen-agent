# -*- coding: utf-8 -*-
"""자연어와 파이프라인을 잇는 다리 — 씽큐 클로 같은 대화형 진입점.

왜 필요한가:
  LG 가 IFA 2026 에서 공개한 씽큐 클로는 카톡·텔레그램으로 대화하고,
  사용자 루틴에 맞춰 가전 동작을 **먼저 제안**하며, **실행은 사람이
  승인**한다. 하는 일을 뜯어보면 이렇다.

      자연어 → 제안 → 사람 승인 → 가전 실행

  우리 파이프라인은 이렇다.

      상황 dict → 계획 계산 → 실행 → 지표

  가운데는 이미 있고 **양 끝이 비어 있었다.** 이 파일이 그 두 끝이다.

어디에 LLM 을 두는가 — 그리고 어디에 두지 않는가:
      [LLM]    자연어 → 상황 dict
      [결정적] situation_read → … → converge → aftercare   ← 손대지 않는다
      [LLM]    지표 → 사람이 읽는 문장

  제어 루프에 LLM 을 넣으면 "같은 입력이면 같은 출력" 이 깨진다. 200회
  반복 측정도, 회귀 검사 4상황도, 계획 결정성(60회 뒤섞기)도 전부 그
  전제 위에 서 있다. 그래서 **판단은 측정이 하고 LLM 은 말만 한다.**

키가 없어도 돌아간다:
  `ask` 는 (프롬프트) -> 문자열 함수다. **주입받는다.**
      키 있음  → Claude 를 부른다 (ask_claude)
      키 없음  → 규칙 기반으로 읽는다 (rule_understand)
      검사     → 가짜 ask 를 넣어 결정적으로 시험한다
  스킬이 관측·조작 함수를 주입받는 것과 같은 방식이고, 그래서 키 없이도
  이 경로를 검증할 수 있다.

안전:
  LLM 이 읽은 것을 **그대로 믿지 않는다.** 받은 값은 형식·범위를 검사하고,
  알레르기처럼 틀리면 위험한 항목은 읽었다는 사실만 남기고 **사람에게
  확인을 요청**한다. 씽큐 클로의 "실행은 사람이 승인" 과 같은 자리다.

    python thinq.py "오늘 늦어. 9시 반쯤 들어가는데 냉장고에 배추랑 두부 있어"
"""
from __future__ import annotations
import json
import os
import re
import shutil
import sys
import threading

# ── 1 동시 처리 (2026-10-01) ─────────────────────────────────────────
# 냉장고·조리기·가구 표가 모듈 전역이라(시뮬레이터 설계) 한 프로세스에서 두 요청이
# 겹치면 섞인다. 서버로 쓰려면 가구별 상태 분리가 필요하다(README 설계). 그 전까지는
# 한 번에 하나씩 돌린다.
_RUN_LOCK = threading.Lock()

# 안전 확인이 필요한 항목 — LLM 이 읽었어도 사람이 확인한다
NEEDS_CONFIRM = ("avoid", "max_sodium_mg")

# 규칙 기반으로 읽을 때 쓰는 표
#
# 한 글자 이름("무")을 단순 부분 일치로 찾으면 "아무것도" 에서도 걸린다.
# 그래서 **한글 덩어리 단위**로 본다 — recipe_parse 에서 배와 배추를
# 가른 것과 같은 방법이다.
_FOOD = ("배추", "두부", "된장", "애호박", "닭고기", "간장", "대파", "미나리",
         "찹쌀", "계란", "달걀", "양파", "무", "버섯", "고기", "우유", "새우")
_NO_STOCK = ("아무것도 없", "텅 비", "하나도 없", "다 떨어")
# 오늘의 주문 방식을 알아보는 말. 순서가 우선순위다(직접 > 묻기 > 자동).
_MODE_CUES = (
    ("self", r"(내가|제가|직접)\s*[^.,]{0,8}?(사\s*갈|사\s*올|사서|살게|사갈|들를|들러)"
             r"|(장|마트)[은는을를]?\s*(내가|제가)"),
    ("ask", r"(물어보고|묻고|확인하고|확인받고)\s*[^.,]{0,4}?(사|시켜|주문)"
            r"|(시키기|사기|주문하기)\s*전에\s*[^.,]{0,8}?(물어|확인)"),
    ("auto", r"알아서\s*[^.,]{0,4}?(시켜|주문|사|장)"),
)
_HANGUL_NUM = {"한": 1, "하나": 1, "혼자": 1, "둘": 2, "두": 2, "셋": 3,
               "세": 3, "넷": 4, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7,
               "여덟": 8, "아홉": 9}
# 말로 한 양. "두부 반 모", "닭고기 200g", "계란 3개" — 전엔 모두 300g 으로 가정했다.
# 단위 무게는 **대표값 가정**이다.
UNIT_G = {("두부", "모"): 300, ("배추", "포기"): 2000, ("배추", "통"): 2000,
          ("무", "개"): 1500, ("양파", "개"): 200, ("계란", "개"): 60, ("달걀", "개"): 60,
          ("계란", "알"): 60, ("달걀", "알"): 60, ("애호박", "개"): 300, ("대파", "단"): 500}
_QTY_WORD = {"반": 0.5, "한": 1, "하나": 1, "두": 2, "세": 3, "네": 4}
_DEFAULT = {
    "id": "thinq", "label": "대화로 받은 상황", "household_size": 1,
    "arrive_home": "19:00", "time_budget_min": 45, "next_morning_rush": False,
    # 말하지 않았으면 층간소음 규칙의 야간 시작(22:00)을 쓴다 — 전에는 근거 없는 23:00
    "avoid": [], "dislike_noise_after": "22:00",
    "goal_hint": "", "friction_reported": [], "fridge": [],
}

# CLI 를 부를 때 기본 시스템 프롬프트를 이걸로 덮는다. 덮지 않으면
# 사용자의 CLAUDE.md 를 따라 조언하고 확인을 요청한다 — 우리가 원하는
# 것은 변환 한 번이다.
CONVERTER_SYSTEM = (
    "너는 문자열 변환기다. 요청된 형식만 그대로 출력한다. "
    "설명·조언·확인 요청·메타 언급을 하지 않는다. "
    "주어지지 않은 값은 지어내지 않는다.")

PROMPT = """사용자가 한국어로 말한 상황에서 아래 항목을 뽑아 JSON 으로만 답하라.
모르는 값은 넣지 마라 — 지어내지 마라.

  arrive_home        "HH:MM"  집에 도착하는 시각
  time_budget_min    int      식사까지 쓸 수 있는 분
  household_size     int      먹는 사람 수
  avoid              [str]    알레르기·기피 재료
  dislike_noise_after "HH:MM" 이 시각 이후 소음 회피
  next_morning_rush  bool     내일 아침에 여유가 없는가
  goal_hint          str      한 줄 요약
  friction_reported  [str]    사용자가 말한 불편
  fridge             [{name, qty_g, stored_days, shelf_life_days}]  지금 **있는** 것만.
                     없다·다 썼다·떨어졌다고 한 재료는 넣지 마라
  order_mode         "auto"|"ask"|"self"  오늘 장보기를 어떻게 할지 — 알아서
                     주문(auto) · 묻고 사기(ask) · 직접 사 가기(self). 말하지
                     않았으면 넣지 마라
  leave_office       "HH:MM"  회사를 나서는(나선) 시각. "지금 퇴근" 이면 아래
                     지금 시각. 지금 시각이 "모름" 이면 넣지 마라
  commute_min        int      집까지 걸리는 분 ("40분 걸려")

지금 시각: {now}
사용자 말: {text}"""


# ── LLM 을 부르는 쪽 ────────────────────────────────────────────────────
def ask_claude(prompt: str) -> str:
    """진짜 Claude 를 부른다. 키가 없으면 RuntimeError."""
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError("ANTHROPIC_API_KEY 가 없다")
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-opus-5",
        max_tokens=2000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def ask_claude_cli(prompt: str, timeout: int = 90) -> str:
    # (240초였다 — 한 번 실행에 LLM 을 4번 부르므로 퇴근길 메시지 하나가 최악
    #  16분이 걸릴 수 있었다. 90초를 넘기면 규칙·틀로 돌아간다.)
    """설치된 `claude` 명령으로 부른다 — **API 키가 없어도 된다.**

    이 자리를 처음엔 비워 두고 "키가 없어 검증 못 함" 이라고 적었는데,
    환경을 실제로 보니 API 키는 없지만 claude CLI 가 깔려 있었다.
    **없다고 적기 전에 찾아봤어야 했다.**

    두 가지를 조심한다. 처음엔 둘 다 놓쳐서 엉뚱한 답이 왔다.

      1. 프롬프트를 **stdin 으로** 넘긴다. 명령줄 인자로 주면 Windows
         shell 의 인용 규칙 때문에 개행·따옴표에서 잘린다 — 실제로
         "'아래는' 에서 끊겨 들어왔습니다" 라는 답이 돌아왔다.
      2. **빈 작업 디렉토리에서, 시스템 프롬프트를 덮어써서** 부른다.
         그냥 부르면 CLI 가 프로젝트와 사용자의 CLAUDE.md 를 읽고
         그것에 대해 답한다 — 실제로 "CLAUDE.md 규칙대로 #local 채널에
         알림을 보내려 했지만" 같은 문장과, 이번 실행에 없는 숫자
         (25·27·48)가 답에 섞여 왔다. 우리가 원하는 것은 문장 하나를
         JSON 으로 바꾸는 **순수한 변환**이다.
    """
    import subprocess
    import tempfile
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude 명령을 찾지 못했다")
    with tempfile.TemporaryDirectory(prefix="thinq_") as empty:
        r = subprocess.run(
            [exe, "-p", "--system-prompt", CONVERTER_SYSTEM,
             "--strict-mcp-config"],
            input=prompt, cwd=empty, shell=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude 호출 실패 rc={r.returncode}: "
                           f"{(r.stderr or '')[:200]}")
    return r.stdout or ""


def best_ask():
    """지금 쓸 수 있는 가장 나은 호출 방법. 없으면 None(규칙으로 간다)."""
    if os.environ.get("ANTHROPIC_API_KEY") or             os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            import anthropic          # noqa: F401
            return ask_claude
        except ImportError:
            pass
    if shutil.which("claude"):
        return ask_claude_cli
    return None


def available() -> bool:
    """지금 진짜 LLM 을 부를 수 있는가."""
    return best_ask() is not None


def how() -> str:
    a = best_ask()
    return {None: "키도 CLI 도 없어 규칙으로 읽는다",
            ask_claude: "anthropic SDK 로 부른다",
            ask_claude_cli: "claude CLI 로 부른다 (API 키 불필요)"}[a]


# ── 자연어 → 상황 ──────────────────────────────────────────────────────
def _hour_cands(h: int, before: str) -> list:
    """'N시' 의 N 이 될 수 있는 24시간 값들. 첫째가 기본값이다.

    바로 앞의 말(before)이 있으면 하나로 정해진다. 없으면 둘이다 — 저녁
    에이전트라 오후를 먼저 둔다(3시 → [15, 3]). 전에는 12 미만을 **무조건**
    오후로 읽어 "새벽 1시 도착" 이 13:00, "밤 12시 반" 이 12:30 이 됐다.
    """
    if h > 12:
        return [h]
    if any(k in before for k in ("새벽", "아침", "오전")):
        return [0 if h == 12 else h]
    if any(k in before for k in ("오후", "저녁")):
        return [h if h == 12 else h + 12]
    if h == 12 and any(k in before for k in ("밤", "자정")):
        return [0]
    if "밤" in before:                  # 밤 11시 → 23시, 밤 1시 → 01시(자정 뒤)
        return [h] if h <= 4 else [h + 12]
    return [h + 12, h] if h < 12 else [12, 0]


def rule_understand(text: str, now: str | None = None) -> dict:
    """키 없이 읽는다. LLM 이 없을 때의 폴백이자, LLM 결과의 대조군이다.

    정규식이라 한계가 뚜렷하다 — 그래서 읽어낸 근거를 함께 남긴다.
    읽지 못한 항목은 기본값이 쓰이고, 그 사실이 `read` 에 안 적힌다.
    """
    got, why = {}, []

    # 시각: 뒤에 '퇴근' 이 오면 퇴근 시각, 아니면 귀가 시각이다.
    # ("6시 반에 퇴근" 을 귀가로 읽으면 이동 시간이 0 이 된다)
    # 시각마다 **후보**를 둔다 — "1시" 는 13시일 수도 01시일 수도 있다.
    # 고르는 순서: 앞의 말(새벽·밤) → 지금 시각에서 가까운 앞쪽 → 퇴근·귀가가
    # 앞뒤로 맞는 조합 → 그래도 모르면 저녁으로 읽고 **애매했다고 남긴다.**
    nowm = (int(now[:2]) * 60 + int(now[3:])) if now and _is_clock(now) else None
    clocks = {}
    for m in re.finditer(r"(\d{1,2})\s*시\s*(반|(\d{1,2})\s*분)?", text):
        h = int(m.group(1))
        mi = 30 if m.group(2) == "반" else int(m.group(3) or 0)
        cands = [c * 60 + mi for c in _hour_cands(h, text[max(0, m.start() - 4):m.start()])]
        if nowm is not None and len(cands) > 1:
            cands.sort(key=lambda x: (x - (nowm - 60)) % 1440)
        kind = ("leave_office" if re.match(r"\s*(에|쯤|쯤에)?\s*퇴근", text[m.end():])
                else "arrive_home")
        if kind not in clocks:
            clocks[kind] = (m.group(0).strip(), cands)
    pick = {k: v[1][0] for k, v in clocks.items()}
    if len(clocks) == 2:
        for lv_ in clocks["leave_office"][1]:
            ar_ = next((a for a in clocks["arrive_home"][1]
                        if 0 < (a - lv_) % 1440 <= 180 and a != lv_), None)
            if ar_ is not None:
                pick = {"leave_office": lv_, "arrive_home": ar_}
                break
    for k, (said_, cands) in clocks.items():
        hhmm = f"{(pick[k] // 60) % 24:02d}:{pick[k] % 60:02d}"
        got[k] = hhmm
        label = "퇴근" if k == "leave_office" else "귀가"
        why.append(f"'{said_}' → {label} {hhmm}"
                   + ("" if len(cands) < 2 else
                      " (지금 시각에 가까운 쪽)" if nowm is not None else
                      f" (낮·밤이 애매해 {hhmm} 로 읽었다 — 틀리면 '새벽'·'밤' 을 붙여 달라)"))

    # "N분" 은 여럿이다 — 걸리는 시간(40분 걸려)·퇴근까지(30분 뒤 퇴근)는
    # 쓸 수 있는 시간이 아니다. 전에는 첫 "N분" 을 무조건 예산으로 읽었다.
    for m in re.finditer(r"(\d{1,3})\s*분", text):
        if "시" in text[max(0, m.start() - 3):m.start()]:
            continue
        if re.match(r"\s*(정도\s*)?(걸|뒤|후|거리)", text[m.end():]):
            continue
        got["time_budget_min"] = int(m.group(1))
        why.append(f"'{m.group(0)}' → 쓸 수 있는 시간")
        break

    # 퇴근: "지금 퇴근해" 는 **지금이 몇 시인지** 알아야 시각이 된다.
    # 모르면 지어내지 않는다(now 는 호출자가 준다 — 실제 기기라면 휴대폰 시계).
    m = re.search(r"(\d{1,3})\s*분\s*(정도\s*)?(걸려|걸림|걸리|거리)", text)
    if m:
        got["commute_min"] = int(m.group(1))
        why.append(f"'{m.group(0)}' → 집까지 {m.group(1)}분")
    m = re.search(r"(\d{1,3})\s*분\s*(뒤|후)(에)?\s*퇴근", text)
    m_now = re.search(r"(지금|방금|이제)\s*퇴근|퇴근\s*(해|했|하는\s*중|중이|길이)",
                      text)
    if "leave_office" not in got and (m or m_now):
        if now and _is_clock(now):
            base = int(now[:2]) * 60 + int(now[3:])
            t = base + (int(m.group(1)) if m else 0)
            got["leave_office"] = f"{(t // 60) % 24:02d}:{t % 60:02d}"
            why.append(f"'{(m or m_now).group(0)}' (지금 {now}) → 퇴근 "
                       f"{got['leave_office']}")
        else:
            why.append(f"'{(m or m_now).group(0)}' — 지금이 몇 시인지 몰라 "
                       f"퇴근 시각을 정하지 않았다")

    # 두 자리도 읽는다 — "12명" 을 "2명" 으로, "열두 명" 을 "두 명" 으로 읽었다
    # (2026-10-01). 숫자 앞이 숫자면 잘린 것이다.
    m = re.search(r"(?<!\d)(\d{1,2})\s*(명|식구|인(?!분))", text)
    if m:
        got["household_size"] = int(m.group(1))
        why.append(f"'{m.group(0)}' → {m.group(1)}인")
    else:
        # "넷이 먹을" 처럼 한글로 말하는 쪽이 더 흔하다. '열' 이 붙으면 십 단위다.
        m = next((x for x in re.finditer(
            r"(열\s*)?(한|하나|혼자|둘|두|셋|세|넷|네|다섯|여섯|일곱|여덟|아홉)?\s*"
            r"(명|이서|이|식구|가족)", text) if x.group(1) or x.group(2)), None)
        if m and (m.group(2) in _HANGUL_NUM or m.group(2) is None):
            n = (10 if m.group(1) else 0) + (_HANGUL_NUM.get(m.group(2), 0) if m.group(2) else 0)
            if n:
                got["household_size"] = n
                why.append(f"'{m.group(0).strip()}' → {n}인")
        if "household_size" not in got and re.search(r"혼자", text):
            got["household_size"] = 1                 # "혼자 먹어" — 뒤에 명·이가 없다
            why.append("'혼자' → 1인")

    # 기피 재료를 **먼저** 읽는다. 그래야 그것이 재고로 들어가지 않는다.
    avoid = []
    # "못 드셔" 도 읽는다 — 어른 얘기를 할 때 흔하다. 처음엔 '먹' 만 봐서
    # "갑각류를 못 드셔" 를 통째로 놓쳤다.
    for mm in re.finditer(r"([가-힣]{2,6}?)\s*(알레르기|알러지|(?:못|안)\s*(?:먹|드))",
                          text):
        name = mm.group(1)
        # "새우를 못 먹어" 의 '를' 을 뗀다. 안 떼면 '새우를' 이 기피어가 되어
        # 레시피의 '새우' 에 걸리지 않는다.
        name = re.sub(r"(을|를|은|는|이|가|도|랑|하고)$", "", name) or name
        # "있는 새우 알레르기" 처럼 앞말이 붙으면 아는 재료로 잘라 낸다
        for f in _FOOD:
            if name.endswith(f):
                name = f
                break
        avoid.append(name)
        why.append(f"'{mm.group(0)}' → 기피 {name}")
    if avoid:
        got["avoid"] = avoid

    # 재고: **덩어리 단위**로 본다. "아무것도" 에서 '무' 를 빼지 않기 위해서다.
    if any(k in text for k in _NO_STOCK):
        got["fridge"] = []
        why.append("재고가 비었다고 읽음")
    else:
        found, gone = _foods_in(text, avoid)
        if gone:
            why.append(f"없다고 읽음: {', '.join(gone)}")
        if found:
            got["fridge"] = []
            for f in found:
                q = _qty_after(text, f)
                got["fridge"].append({"name": f, **({"qty_g": q} if q else {})})
            said = [f"{x['name']} {x['qty_g']}g" for x in got["fridge"] if "qty_g" in x]
            why.append(f"재료로 읽음: {', '.join(found)}"
                       + (f" (말한 양: {', '.join(said)})" if said else "")
                       + " (나머지 양은 가정, 보관일은 모름)")

    # 오늘의 주문 방식. **직접 사겠다** 를 먼저 본다 — "알아서 시키지 말고
    # 내가 사 갈게" 처럼 둘이 섞이면 사람이 하겠다는 쪽이 이긴다.
    for mode, pat in _MODE_CUES:
        mm = re.search(pat, text)
        # "알아서 사지 마" 는 자동 주문이 아니라 그 반대다. 부정이 붙으면
        # 그 방식으로 읽지 않는다(읽지 못하면 기본값 ask — 묻는 쪽).
        if mm and re.match(r"[^.,]{0,3}?(지|진)(는|도)?\s*(마|말|않)",
                           text[mm.end():]):
            why.append(f"'{mm.group(0)}…' 에 부정이 붙어 주문 방식 {mode} 로 읽지 않음")
            continue
        if mm:
            got["order_mode"] = mode
            why.append(f"'{mm.group(0)}' → 주문 방식 {mode}")
            break

    if "아침" in text and ("바쁘" in text or "일찍" in text):
        got["next_morning_rush"] = True
        why.append("아침에 여유가 없다고 읽음")

    got["goal_hint"] = text.strip()[:40]
    got["friction_reported"] = _friction_from(text)
    return {"fields": got, "read": why, "by": "규칙"}


_NEG = r"없|다\s*(썼|먹었|떨어)|떨어졌|안\s*남|버렸|못\s*샀"
_JOIN = r"^\s*(랑|이랑|하고|과|와|,)?\s*$"


def _foods_in(text: str, avoid: list) -> tuple:
    """말에서 **있는** 재료와 **없다고 한** 재료를 가른다.

    전에는 이름이 나오기만 하면 있다고 읽었다 — "두부 없어 배추만 있어" 에서
    두부가, "새우는 없어" 에서 새우가 재고가 됐다(2026-10-01). 재료마다 바로 뒤
    (다음 재료나 '있' 이 나오기 전까지)에 없음·다 씀·떨어짐이 있으면 없는 것이다.
    "배추랑 두부 없어" 처럼 '랑' 으로만 이어지면 뒤 재료의 부정을 같이 받는다.
    """
    hits = []
    for f in _FOOD:
        if f in avoid:
            continue                    # 기피 재료는 재고로 넣지 않는다
        longer = [g for g in _FOOD if g != f and f in g]
        for m in re.finditer(re.escape(f), text):
            # 더 긴 재료 이름의 일부면 따로 읽지 않는다 — "닭고기" 안의 "고기"
            if any(mm.start() <= m.start() and m.end() <= mm.end()
                   for g in longer for mm in re.finditer(re.escape(g), text)):
                continue
            # 한 글자 이름(무)은 낱말일 때만 — "무척"·"나무" 가 아니다
            if len(f) == 1:
                before = text[m.start() - 1] if m.start() else " "
                after = text[m.end():m.end() + 2]
                if re.match(r"[가-힣]", before) or not re.match(
                        r"(랑|이랑|하고|는|도|가|를|\s|,|\.|$)", after or "$"):
                    continue
            hits.append((m.start(), m.end(), f))
    hits.sort()
    neg = {}
    for i in range(len(hits) - 1, -1, -1):          # 뒤에서부터 — '랑' 이 뒤를 따른다
        st, en, f = hits[i]
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        seg = re.split(r"[.!?]", text[en:nxt])[0]
        seg = seg.split("있")[0]
        if re.search(_NEG, seg):
            neg[i] = True
        elif i + 1 < len(hits) and re.match(_JOIN, text[en:nxt]):
            neg[i] = neg.get(i + 1, False)
        else:
            neg[i] = False
    found, gone = [], []
    for i, (_, _, f) in enumerate(hits):
        (gone if neg[i] else found).append(f)
    found = [f for f in dict.fromkeys(found) if f not in gone]
    return found, list(dict.fromkeys(gone))


def _qty_after(text: str, food: str):
    """재료 바로 뒤의 양을 읽는다. 못 읽으면 None(가정은 호출자가 한다)."""
    i = text.find(food)
    if i < 0:
        return None
    rest = text[i + len(food):i + len(food) + 10]
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(kg|킬로|g|그램)", rest)
    if m:
        v = float(m.group(1))
        return round(v * 1000 if m.group(2) in ("kg", "킬로") else v)
    m = re.match(r"\s*(\d+|반|한|하나|두|세|네)\s*(모|개|알|포기|통|단)", rest)
    if m and (food, m.group(2)) in UNIT_G:
        n = float(m.group(1)) if m.group(1).isdigit() else _QTY_WORD[m.group(1)]
        return round(n * UNIT_G[(food, m.group(2))])
    return None


def _friction_from(text: str) -> list:
    """말속에서 '수고' 를 집는다. 하나도 못 집으면 빈 목록이다 —
    situation_read 가 그 경우 성립으로 세지 않는다."""
    out = []
    RULES = [
        (("뭐", "뭘", "무엇"), "냉장고를 열어 뭐가 남았는지 확인하는 일"),
        (("지키", "봐야", "지켜"), "조리 중 냄비 앞을 떠나지 못하는 일"),
        (("설거지", "세척"), "먹고 나서 설거지를 미루는 일"),
        (("장보", "장 보", "사야"), "누가 장을 볼지 매번 정하는 일"),
        (("버리", "상해", "상함"), "재료가 남아 버리는 일"),
    ]
    for keys, label in RULES:
        if any(k in text for k in keys):
            out.append(label)
    return out


# 가구가 **한 번 정해 두는 선호**. 그날 말이 이것을 덮는다.
# 안전 항목(기피·나트륨)은 여기 두지 않는다 — 그것은 말할 때마다 승인을 거친다.
PROFILE_KEYS = ("order_mode", "auto_limit_krw",
                # 퇴근을 알아보는 데 쓰는 가구 사실·선호 (위치는 동의해야 쓴다)
                "commute_min", "location_consent", "leave_window",
                "location_dwell_min",
                # 근거를 못 찾은 가정값 — 가구가 정하면 그것을 쓴다(prefs 로 넘어간다)
                "eat_min", "shop_detour_min", "shop_trip_min",
                "order_cap_krw", "location_auto_order",
                "avoid", "max_sodium_mg",
                # 3 외부 LLM 전송 동의 · 4 결제 권한자 (2026-10-01)
                "llm_consent", "payers",
                # 가구의 구매 이력(장부) — "처음 사는 품목" 판단에 쓴다. 말로는 못 바꾼다
                "purchase_history")
PREF_KEYS = ("eat_min", "shop_detour_min", "shop_trip_min",
             "order_cap_krw", "location_auto_order")
# **안전 항목도 가구가 저장한다**(2026-10-01). 전에는 "말할 때마다 승인" 이라며
# 저장을 막아, 하루 말하지 않으면 그날은 새우가 걸러지지 않았다. 저장한 것은
# 이미 확인한 것이고, 그날 말은 **더할 수만** 있다 — 말하지 않았다고 풀리지 않는다.
SAFETY_KEYS = ("avoid", "max_sodium_mg")
# 돈·동의·가정값 설정은 **말로 바꾸지 않는다.** LLM 경로로 "이십만원까지 사"
# 가 자동 주문 상한을, "위치 써도 돼" 가 위치 동의를 바꿨다(2026-10-01) —
# 아이 말·잘못 들은 말·끼어든 문장으로 돈과 동의가 바뀐다. 설정에서만 바꾼다.
SETTINGS_ONLY = ("llm_consent", "payers", "purchase_history",
                 "auto_limit_krw", "location_consent", "leave_window",
                 "location_dwell_min", "eat_min", "shop_detour_min", "shop_trip_min",
                 "order_cap_krw", "location_auto_order")


def understand(text: str, ask=None, profile: dict | None = None,
               now: str | None = None, location: list | None = None) -> dict:
    """자연어를 상황 dict 로 바꾼다.

    ask 를 주면 그것으로 읽고, 안 주면 규칙으로 읽는다. **어느 쪽이든
    결과는 검사를 거친다** — LLM 이 준 값을 그대로 쓰지 않는다.

    profile: 가구가 정해 둔 선호(PROFILE_KEYS). 기본값 < 선호 < 그날 말 순서로
    덮는다. 전에는 선호를 받는 곳이 없어 "한 번 정해 두고 그날 말로 바꾼다" 가
    README 에만 있었다 — 그날 말이 없으면 늘 기본값(ask)이었다.
    """
    if ask is None:
        got = rule_understand(text, now=now)
    else:
        # 외부 LLM 에는 **이 일에 필요 없는 개인정보를 가려** 보낸다(전화·주소·
        # 이메일·주민·카드번호). 전에는 말을 그대로 보냈다(2026-10-01).
        try:
            raw = ask(PROMPT.replace("{text}", redact(text)).replace("{now}", now or "모름"))
            fields, why = _parse_json(raw)
        except Exception as e:                     # LLM 이 죽어도 멈추지 않는다
            fields, why = {}, [f"LLM 호출 실패({type(e).__name__})"]
        if fields:
            got = {"fields": fields, "read": why, "by": "LLM"}
        else:
            # 엉뚱한 답·실패면 **규칙으로 읽는다**. 전엔 기본값(19:00·재고 없음)
            # 으로 조용히 진행했다.
            got = rule_understand(text, now=now)
            got["read"] = why + ["LLM 답을 쓸 수 없어 규칙으로 읽었다"] + got["read"]
            got["by"] = "규칙(LLM 실패)"

    clean, rejected = _validate(got["fields"])
    for k in SETTINGS_ONLY:
        if k in clean:
            clean.pop(k)
            rejected.append(f"{k}: 설정은 말로 바꾸지 않는다 — 앱 설정에서 바꾼다")
    pref, pref_bad = _validate({k: v for k, v in (profile or {}).items()
                                if k in PROFILE_KEYS})
    rejected += [f"선호 {b}" for b in pref_bad]
    rejected += [f"선호 {k}: 가구 선호로 두지 않는 항목이라 버렸다"
                 for k in (profile or {}) if k not in PROFILE_KEYS]
    persona = {**_DEFAULT, **pref, **clean}
    # 안전 항목: 저장한 것 ∪ 그날 말. 말하지 않았다고 풀리지 않는다.
    if pref.get("avoid") or clean.get("avoid"):
        persona["avoid"] = list(dict.fromkeys((pref.get("avoid") or [])
                                              + (clean.get("avoid") or [])))
    if pref.get("max_sodium_mg") and clean.get("max_sodium_mg"):
        persona["max_sodium_mg"] = min(pref["max_sodium_mg"], clean["max_sodium_mg"])
    persona["goal_hint"] = redact(persona.get("goal_hint") or "")
    _leave_from(persona, clean, location, got["read"])
    prefs = {k: persona.pop(k) for k in PREF_KEYS if k in persona}
    if prefs:
        persona["prefs"] = prefs
    for k in PROFILE_KEYS:
        if k in clean and k in pref and clean[k] != pref[k]:
            got["read"].append(f"{k}: 정해 둔 {pref[k]} → 오늘은 {clean[k]}")
        elif k in pref and k not in clean:
            got["read"].append(f"{k}: 정해 둔 선호 {pref[k]} 를 쓴다")
    confirm = [k for k in NEEDS_CONFIRM if k in clean]
    return {"persona": persona, "read": got["read"], "by": got["by"],
            "rejected": rejected, "needs_confirm": confirm}


def _leave_from(persona: dict, said: dict, location, read: list) -> None:
    """퇴근을 무엇으로 알았는지 정하고 귀가 시각을 맞춘다.

    믿는 순서: **그날 말(확정) > 위치(추정) > 없음.** 위치는 점심·외근도
    '회사를 나섬' 으로 보이므로 말이 있으면 말을 따른다.
    이동 시간만 알고 퇴근 시각을 모르면 선제 주문을 하지 않는다 —
    전에는 commute_min 만 있어도 선제 주문 가구로 판단될 수 있었다.
    """
    import location as L
    if "leave_office" in said:
        persona["leave_source"] = "message"
        if location:
            read.append("위치: 그날 말(퇴근)이 있어 위치는 보지 않는다")
    elif location is not None:
        found, why = L.detect_leave(
            location, consent=bool(persona.get("location_consent")),
            commute_min=persona.get("commute_min"),
            window=persona.get("leave_window"),
            dwell_min=persona.get("location_dwell_min", L.DWELL_MIN))
        read.extend(f"위치: {w}" for w in why)
        if found and "arrive_home" in said:
            # **말한 귀가 시각이 위치 추정보다 확실하다.** 전에는 위치가 계산한
            # 귀가로 말한 것을 덮었다. 집 밖 시간은 두 시각의 차로 본다.
            away = (L._m(said["arrive_home"]) - L._m(found["leave_office"])) % 1440
            if not 0 < away <= 180:
                read.append(f"위치: 퇴근 추정 {found['leave_office']} 이 말한 귀가 "
                            f"{said['arrive_home']} 와 맞지 않아 위치를 쓰지 않는다")
                found = None
            else:
                found = dict(found, commute_min=away, arrive_home=said["arrive_home"])
        if found:
            persona.update({k: v for k, v in found.items() if k != "left_at"})
            return
    if not persona.get("leave_office"):
        if persona.pop("commute_min", None) is not None:
            read.append("집까지 걸리는 시간은 알지만 퇴근을 몰라 퇴근길에 할 일을 "
                        "정하지 않았다")
        return
    lv = L._m(persona["leave_office"])
    commute = persona.get("commute_min")
    if "arrive_home" in said and commute is not None:
        # 퇴근·이동·귀가를 다 말했는데 **서로 맞지 않으면** 알린다(들렀다 오는
        # 날일 수 있다). 말한 귀가를 따르고 집 밖 시간을 두 시각의 차로 본다.
        away = (L._m(said["arrive_home"]) - lv) % 1440
        if away != commute:
            read.append(f"퇴근 {persona['leave_office']} + {commute}분 = "
                        f"{L._hhmm(lv + commute)} 이 말한 귀가 {said['arrive_home']} "
                        f"와 맞지 않는다 — 귀가를 따르고 집 밖 시간을 {away}분으로 본다")
            if 0 < away <= 180:
                persona["commute_min"] = commute = away
            else:
                read.append("집 밖 시간이 말이 되지 않아 퇴근길에 할 일을 정하지 않았다")
                persona.pop("leave_office", None)
                persona.pop("leave_source", None)
                persona.pop("commute_min", None)
                return
    if "arrive_home" in said and commute is None:
        commute = (L._m(said["arrive_home"]) - lv) % (24 * 60)
        if 0 < commute <= 180:
            persona["commute_min"] = commute
            read.append(f"퇴근 {persona['leave_office']} · 귀가 {said['arrive_home']} "
                        f"→ 집까지 {commute}분")
    if persona.get("commute_min") is None:
        read.append("퇴근은 알지만 집까지 걸리는 시간을 몰라 퇴근길에 할 일을 "
                    "정하지 않았다")
        persona.pop("leave_office", None)
        persona.pop("leave_source", None)
        return
    if "arrive_home" not in said:
        persona["arrive_home"] = L._hhmm(lv + persona["commute_min"])
        read.append(f"퇴근 {persona['leave_office']} + {persona['commute_min']}분 → "
                    f"귀가 {persona['arrive_home']}")


_PII = [
    (r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)", "[전화번호]"),
    (r"(?<!\d)0\d{1,2}[-\s.]\d{3,4}[-\s.]\d{4}(?!\d)", "[전화번호]"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "[이메일]"),
    (r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)", "[주민번호]"),
    (r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)", "[카드번호]"),
    (r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
     r"\S*\s+\S+(구|군|시)\s+\S+(로|길|동)\s*\d[\d-]*(번길\s*\d+)?", "[주소]"),
    (r"(이름은|성함은|이름이)\s*\S+", r"\1 [이름]"),
    # 도시 없이 말하는 주소("마포구 월드컵로 12")·동호수 — 10/1 점검에서 새어 나갔다
    (r"\S+(구|군)\s+\S+(로|길)\s*\d[\d-]*(번길\s*\d+)?", "[주소]"),
    (r"\d{1,4}\s*동\s*\d{1,5}\s*호", "[동호수]"),
]


def redact(text: str) -> str:
    """외부 LLM 으로 보내기 전에 이 일에 필요 없는 개인정보를 가린다.

    시간·재료·인원은 남긴다. 이름은 문맥 없이 알아보기 어려워 "이름은 ○○"
    꼴만 가린다 — 완전하지 않다(남는 위험으로 README 에 적는다).
    """
    for pat, rep in _PII:
        text = re.sub(pat, rep, text)
    return text


def _parse_json(raw: str):
    """LLM 답에서 JSON 을 꺼낸다. 앞뒤에 말이 붙어 와도 된다."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}, ["LLM 답에서 JSON 을 찾지 못했다 — 기본값으로 간다"]
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {}, [f"JSON 을 읽지 못했다({e}) — 기본값으로 간다"]
    return d, [f"LLM 이 {len(d)}개 항목을 읽었다: {', '.join(sorted(d))}"]


def _validate(fields: dict):
    """**읽은 값을 그대로 믿지 않는다.** 형식·범위를 보고 거른다.

    LLM 은 그럴듯한 값을 지어낼 수 있고, 시간이나 인원이 터무니없으면
    뒤의 계획이 조용히 이상해진다. 거른 항목은 이유와 함께 돌려준다.
    """
    ok, bad = {}, []
    CHECK = {
        # "19:75"·"25:00" 도 형식은 맞다. 시·분 범위까지 본다.
        "arrive_home": (str, _is_clock),
        "dislike_noise_after": (str, _is_clock),
        "time_budget_min": (int, lambda v: 1 <= v <= 600),
        "household_size": (int, lambda v: 1 <= v <= 12),
        "max_sodium_mg": ((int, float), lambda v: 0 < v <= 5000),
        "next_morning_rush": (bool, lambda v: True),
        "goal_hint": (str, lambda v: len(v) <= 200),
        "avoid": (list, lambda v: all(isinstance(x, str) for x in v)),
        "friction_reported": (list, lambda v: all(isinstance(x, str) for x in v)),
        # 오늘의 주문 방식. 세 값 밖이면 버린다 — 버리면 기본값(ask, 묻기)이다.
        "order_mode": (str, lambda v: v in ("auto", "ask", "self")),
        # 자동 주문 1회 상한(원). 0 이면 사실상 자동 주문을 끈다.
        "auto_limit_krw": (int, lambda v: 0 <= v <= 200000),
        "leave_office": (str, _is_clock),
        "commute_min": (int, lambda v: 1 <= v <= 180),
        "location_consent": (bool, lambda v: True),
        "location_dwell_min": (int, lambda v: 0 <= v <= 30),
        "eat_min": (int, lambda v: 5 <= v <= 120),
        "order_cap_krw": (int, lambda v: 0 <= v <= 500000),
        "llm_consent": (bool, lambda v: True),
        "payers": (list, lambda v: all(isinstance(x, str) and x for x in v)),
        "purchase_history": (list, lambda v: all(isinstance(x, str) and x for x in v)),
        "location_auto_order": (bool, lambda v: True),
        "shop_detour_min": (int, lambda v: 0 <= v <= 90),
        "shop_trip_min": (int, lambda v: 0 <= v <= 120),
        "leave_window": (list, lambda v: len(v) == 2
                         and all(isinstance(x, str) and _is_clock(x) for x in v)),
        # 이름만 있으면 받되 **나머지는 우리가 채운다.** 사용자는
        # "배추 있어" 라고만 말하고 보관일을 말하지 않는다. LLM 도
        # 그렇게 준다 — 실제로 stored_days 가 빠진 항목이 와서
        # inventory 가 KeyError 로 깨졌다. 받는 쪽에서 메꾼다.
        "fridge": (list, lambda v: all(isinstance(x, dict) and x.get("name")
                                       for x in v)),
    }
    for k, v in (fields or {}).items():
        rule = CHECK.get(k)
        if rule is None:
            bad.append(f"{k}: 모르는 항목이라 버렸다")
            continue
        typ, pred = rule
        if not isinstance(v, typ) or isinstance(v, bool) != (typ is bool):
            bad.append(f"{k}: 형식이 아니라 버렸다 ({v!r})")
            continue
        try:
            if not pred(v):
                bad.append(f"{k}: 범위를 벗어나 버렸다 ({v!r})")
                continue
        except Exception:
            bad.append(f"{k}: 검사 중 오류라 버렸다 ({v!r})")
            continue
        ok[k] = v

    if "fridge" in ok:
        ok["fridge"], notes = _clean_fridge(ok["fridge"])
        bad.extend(notes)
    return ok, bad


def _is_clock(v: str) -> bool:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", v or "")
    return bool(m) and int(m.group(1)) < 24 and int(m.group(2)) < 60


def _num(v):
    """'300g'·'300'·300.0 을 수로. 못 읽으면 None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(g|그램)?\s*", str(v))
    return float(m.group(1)) if m else None


# 이름에 붙어 오는 수량·어림말. '한·두·반' 은 **앞에 공백이 있을 때만**
# 뗀다 — 없으면 호두가 '호', 완두가 '완' 이 됐다(실제로 그렇게 잘렸다).
# "두부 한 모" 의 '한 모' 를 떼지 않으면
# 메뉴 단계는 '두부' 가 있다고 보고(부분 일치), 계량 단계는 '두부' 를
# 못 찾는다(정확 일치) — 실제로 그렇게 계량이 실패했다.
_AMOUNT_WORDS = re.compile(
    r"(?:\s+(?:한|두|세|네|반|몇)|\s*\d+(?:\.\d+)?)\s*"
    r"(?:개|모|통|단|봉|팩|마리|송이|포기|근|줌|쪽|알|컵|인분|g|그램|kg)?\s*$"
    r"|\s*(?:조금|약간|좀|많이|남은 거|남은것|반쯤)\s*$")


def _clean_fridge(items: list):
    """LLM·규칙이 읽은 재고를 계량·물리에 넣을 수 있는 형태로.

    · 이름에서 수량·어림말을 뗀다(두부 한 모 → 두부)
    · 양은 수로 읽고 **0 이하면 버린다** — 음수 질량이 냄비에 들어갔다
    · 같은 재료는 합친다
    · **보관일·수명을 모르면 지어내지 않는다(None).** 전에는 3일·7일을
      채웠는데, 보관일은 임박 판단과 계량 때 채소에서 나오는 물(하루 2%)
      까지 정한다 — 사용자가 말하지 않은 물 6% 를 만들고 있었다.
    · 양을 모르면 300g 으로 둔다. 조리가 돌려면 양이 있어야 해서다
      (**가정**이며 그렇게 적는다).
    """
    from recipe_parse import clean
    merged, notes, guessed = {}, [], []
    for x in items:
        raw = str(x["name"]).strip()
        name = clean(_AMOUNT_WORDS.sub("", raw).strip()) or raw
        if name != raw:
            notes.append(f"fridge: '{raw}' 를 '{name}' 로 읽었다")
        q = _num(x.get("qty_g"))
        if x.get("qty_g") is not None and q is None:
            notes.append(f"fridge: {name} 의 양 {x.get('qty_g')!r} 를 읽지 못했다")
        if q is not None and q <= 0:
            notes.append(f"fridge: {name} 의 양 {q} 는 물리적으로 불가능해 버렸다")
            continue
        sd = _num(x.get("stored_days"))
        sd = int(sd) if sd is not None and sd >= 0 else None
        sl = _num(x.get("shelf_life_days"))
        sl = int(sl) if sl is not None and sl > 0 else None
        if q is None:
            guessed.append(name)
        if name in merged:
            m = merged[name]
            m["qty_g"] = (m["qty_g"] or 300) + (q or 300)
            notes.append(f"fridge: {name} 가 두 번 와서 양을 합쳤다")
            continue
        merged[name] = {"name": name, "qty_g": q if q is not None else 300,
                        "stored_days": sd, "shelf_life_days": sl,
                        # 말로 받은 재고 — 양까지 모르면 가정이다
                        "source": "told" if q is not None else "assumed"}
    if guessed:
        notes.append(f"fridge: 양을 몰라 300g 으로 가정 — {', '.join(guessed)}")
    unknown = [n for n, v in merged.items() if v["stored_days"] is None]
    if unknown:
        notes.append(f"fridge: 보관일을 몰라 임박 판단에서 뺀다 — {', '.join(unknown)}")
    return list(merged.values()), notes


# ── 지표 → 사람 말 ─────────────────────────────────────────────────────
def rule_explain(result: dict) -> str:
    """키 없이 설명한다. **수치는 전부 실행 결과에서 온다.**

    못 한 경우를 먼저 말한다. "-로 정했습니다" 처럼 빈 값을 그대로
    읽어 주면 사용자는 무슨 일이 있었는지 알 수 없다 — 실제로 한 번
    그렇게 나갔다.
    """
    m = result["verify"]["metrics"]
    v = result["verify"]

    # ── 못 한 경우를 먼저 ──
    if not m.get("메뉴") or m.get("메뉴") == "-":
        why = m.get("메뉴 없음") or m.get("중단") or "이유를 찾지 못했습니다"
        return f"이번에는 만들 수 있는 것이 없었습니다. {why}"

    bits = [f"{m['메뉴']}로 정했습니다"]
    # 다시 계획했다는 사실을 숨기지 않는다 — 사용자는 "왜 장을 봤지?" 를 묻는다
    if m.get("재계획"):
        n = m["재계획"].count("→") + 1
        bits.append(f"막힌 곳이 있어 {n}번 다시 계획했습니다"
                    + (f"({m['고른 안']})" if m.get("고른 안") else ""))
    if m.get("가열 시간(분)"):
        bits.append(f"가열은 {m['가열 시간(분)']}분")
    if m.get("손이 가는 일"):
        bits.append(f"손이 가는 일은 {m['손이 가는 일']}")
    if m.get("시간 예산"):
        over = "초과" in str(m["시간 예산"])
        bits.append(("시간이 모자랍니다 — " if over else "시간은 ")
                    + str(m["시간 예산"]))
    if m.get("조달 대기"):
        bits.append(str(m["조달 대기"]))
    if m.get("세척 코스"):
        bits.append(f"세척은 {m['세척 코스']}")
    asked = asked_list(m)
    if v.get("user_touches"):
        # 개입 수와 목록이 같아야 한다 — 전엔 구매 확인만 나열해 "4건(3개 나열)" 이었다
        bits.append(f"물어본 것이 {v['user_touches']}건 있습니다 ({'; '.join(asked)})"
                    if len(asked) == v["user_touches"] else
                    f"물어본 것이 {v['user_touches']}건 있습니다")
    else:
        bits.append("따로 물을 것은 없습니다")
    if not v.get("verified"):
        bits.append("**아직 시나리오를 다 채우지는 못했습니다**")
    notes = must_tell(m)
    return " · ".join(bits) + (" · 꼭 알릴 것: " + " / ".join(notes) if notes else "")


def asked_list(m: dict) -> list:
    """사람에게 물은 것 — 개입 수를 이루는 것 전부."""
    out = list(m.get("확인 요청") or [])
    if "물음" in str(m.get("재고 확인", "")):
        out.append("냉장고에 있는지 확인")
    if "씻은 기록이 없다" in str(m.get("도구 확인", "")):
        out.append("냄비를 씻었는지 확인")
    out += [i for i in (m.get("주문 사고") or []) if "물어" in i]
    return out


def must_tell(m: dict) -> list:
    """설명이 무엇이든 **반드시** 사용자에게 가야 하는 것 — 안전·돈·계획 변경.

    10/1 점검: 넣은 날 모름(냄새·색 확인)·씻지 않은 냄비·버린 재료·주문 사고·집에서
    다시 짬은 결과에만 남고 메시지로는 나가지 않았다. LLM 설명도 빠뜨릴 수 있다.
    """
    out = []
    if m.get("넣은 날 모름"):
        out.append(str(m["넣은 날 모름"]))
    if "씻은 기록이 없다" in str(m.get("도구 확인", "")):
        out.append(str(m["도구 확인"]))
    for k in ("버리고 바꿈", "폐기 대상", "주문 사고", "오늘 못 받음", "앞선 계획에서 이미 주문한 것"):
        if m.get(k):
            v = m[k]
            out.append(f"{k}: " + ("; ".join(map(str, v)) if isinstance(v, list) else str(v)))
    for k in ("다시 짠 곳", "주문 방식 조정"):
        if m.get(k):
            out.append(str(m[k]))
    if "시연" in str(m.get("승인", "")):
        out.append("승인은 시연용 가정이다 — 실제로는 묻고 기다린다")
    return out


EXPLAIN_PROMPT = """아래는 주방 에이전트가 실제로 실행한 결과다.
사용자에게 **두세 문장으로만** 알려라. 목록·제목·주석을 쓰지 마라.
**아래 값에 없는 수를 쓰지 마라.** 아직 하지 않은 일을 한 것처럼 말하지 마라.

{data}"""


def explain(result: dict, ask=None) -> dict:
    """실행 결과를 사람이 읽는 문장으로.

    LLM 을 쓰더라도 **수치는 실행 결과에서만** 온다. 답에 없는 숫자가
    섞이면 그 사실을 표시한다 — 지어낸 값이 사용자에게 가면 안 된다.
    """
    m = result["verify"]["metrics"]
    if ask is None:
        return {"text": rule_explain(result), "by": "규칙", "invented": []}

    data = json.dumps({k: m[k] for k in sorted(m)}, ensure_ascii=False,
                      indent=1)
    try:
        text = (ask(EXPLAIN_PROMPT.replace("{data}", data)) or "").strip()
    except Exception as e:
        # 설명 단계에서 LLM 이 죽어도 실행 결과를 버리지 않는다 — 규칙으로 설명한다.
        # 전엔 여기서 예외가 올라가 다 끝난 실행이 통째로 사라졌다(10/1).
        return {"text": rule_explain(result), "by": f"규칙(LLM 실패: {type(e).__name__})",
                "invented": []}
    if not text:
        return {"text": rule_explain(result), "by": "규칙(LLM 빈 답)", "invented": []}
    invented = _invented_numbers(text, m)
    # LLM 이 무엇을 쓰든 안전·돈·계획 변경은 **덧붙여** 반드시 알린다
    notes = must_tell(m)
    if notes:
        text += " · 꼭 알릴 것: " + " / ".join(notes)
    return {"text": text, "by": "LLM", "invented": invented}


def _invented_numbers(text: str, metrics: dict) -> list:
    """설명에 있는데 실행 결과에는 없는 수를 찾는다."""
    have = set()
    for v in metrics.values():
        for n in re.findall(r"\d+(?:\.\d+)?", str(v)):
            have.add(n)
            have.add(n.rstrip("0").rstrip("."))
    said = re.findall(r"\d+(?:\.\d+)?", text)
    return [n for n in said
            if n not in have and n.rstrip("0").rstrip(".") not in have]


# ── 전체 ───────────────────────────────────────────────────────────────
def run(*a, **kw) -> dict:
    """자연어 한 줄에서 실행 결과와 설명까지 — 한 번에 하나씩(_RUN_LOCK)."""
    with _RUN_LOCK:
        return _run(*a, **kw)


def _run(text: str, ask=None, seed: int = 7, approve=None,
        profile: dict | None = None, now: str | None = None,
        location: list | None = None) -> dict:
    """자연어 한 줄에서 실행 결과와 설명까지.

    **안전 항목(기피·나트륨)을 읽었으면 승인 없이 실행하지 않는다.**
    처음엔 `needs_confirm` 을 결과에 적기만 하고 그대로 실행했다 —
    경고는 아무것도 막지 않는다. 씽큐 클로의 "실행은 사람이 승인" 이
    바로 이 자리다.

    approve 는 (확인할 내용 dict) -> bool 이다. 주입받는다. 없으면 멈춘다.
    """
    import contextlib
    import io as _io

    import personas
    import run_design
    from orchestrator import Trace
    from recipe_parse import expand_avoid

    # 3 외부 LLM 전송 동의 — 가구가 동의하지 않았으면 LLM 을 **부르지 않는다**.
    # 가려도 재료·시간·가족 수는 외부로 간다. 동의를 끄면 규칙으로만 읽고, 장면도
    # 틀로, 설명도 규칙으로 한다(2026-10-01).
    llm_note = None
    if ask is not None and (profile or {}).get("llm_consent") is False:
        ask, llm_note = None, "외부 LLM 전송에 동의하지 않아 규칙으로 읽었다"
    u = understand(text, ask=ask, profile=profile, now=now, location=location)
    if llm_note:
        u["read"].insert(0, llm_note)
    avoid = u["persona"].get("avoid", [])
    expanded, notes, unresolved = expand_avoid(avoid)
    no_hit = _no_hit(avoid)
    ask_user = {"items": u["needs_confirm"], "avoid": avoid,
                "expanded_to": expanded, "expansion": notes,
                "unresolved": unresolved, "no_hit": no_hit}

    # 풀지 못한 범주어는 승인이 있어도 멈춘다. '곡류' 라는 글자는 레시피에
    # 없으므로 아무것도 걸러지지 않는데, 사용자는 걸러졌다고 믿는다.
    if unresolved:
        return {"understood": u, "result": None, "stopped": True,
                "confirm": ask_user,
                "explained": {"text": (f"'{', '.join(unresolved)}' 가 어떤 재료들인지 "
                                       f"몰라 거를 수 없습니다. 구체적인 재료 이름으로 "
                                       f"알려 주세요."),
                              "by": "규칙", "invented": []}}
    if u["needs_confirm"] and not (approve and approve(ask_user)):
        what = (f"못 드시는 것: {', '.join(avoid)}"
                + (f" ({'; '.join(notes)})" if notes else ""))
        # 승인하면 걸러졌다고 믿는다. 이 말로는 **아무것도 안 걸리면** 그렇게
        # 말해야 한다 — '매운 것' 은 재료 이름이 아니라 0건이다.
        if no_hit:
            what += (f". 단 '{', '.join(no_hit)}' 가 들어간 자료는 0건이라 "
                     f"이 말로는 아무것도 거르지 않습니다 — 재료 이름이 아니면 "
                     f"구체적인 재료로 알려 주세요")
        return {"understood": u, "result": None, "stopped": True,
                "confirm": ask_user,
                "explained": {"text": f"실행 전에 확인이 필요합니다 — {what}. "
                                      f"맞으면 승인해 주세요.",
                              "by": "규칙", "invented": []}}

    pid = u["persona"]["id"]
    personas.PERSONAS[pid] = u["persona"]
    # 처음 사는 것 등 확인이 필요한 주문은 **사람에게 묻는다.** approve 가 없으면
    # 사지 않는다(승인 대기). 시연용 "동의 가정" 은 run_design 에만 있다.
    # 4 결제 권한자 — 가구가 정해 두면 **그 사람의 승인만** 돈을 쓴다. 승인 함수는
    # True/False 또는 {"ok": bool, "by": 이름} 을 돌려준다. 권한자가 정해져 있는데
    # 누가 승인했는지 모르면 사지 않는다(아이가 누른 승인 등). 안전 승인(알레르기
    # 확인)은 누구나 할 수 있다 — 막는 쪽이라 위험이 없다.
    payers = (profile or {}).get("payers")

    def approve_purchase(items):
        if not approve:
            return False
        res = approve({"purchase": list(items), "payers": payers})
        ok, by = (bool(res.get("ok")), res.get("by")) if isinstance(res, dict) else (bool(res), None)
        if payers and by not in payers:
            u["read"].append(f"승인한 사람({by or '모름'})이 결제 권한자({', '.join(payers)})가 "
                             f"아니라 사지 않았다")
            return False
        return ok

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        # LLM 이 있으면 시나리오 장면도 제안받는다(llm_design). 제어는 그대로다.
        factory = None
        if ask is not None:
            from llm_design import make_llm_beats
            import kitchen_domain as KD
            factory = (lambda fo: make_llm_beats(
                ask, KD.kitchen_capabilities, KD.kitchen_beats, fo,
                human_only=KD.HUMAN_ONLY, claims=KD.CLAIMS,
                conditional=KD.CONDITIONAL))
        try:
            result = run_design.design_for(pid, Trace(), seed=seed,
                                           beats_factory=factory,
                                           approve_purchase=approve_purchase)
        finally:
            # 전역 표에 남기지 않는다 — 다음 실행·다른 가구와 섞이지 않게
            personas.PERSONAS.pop(pid, None)
    e = explain(result, ask=ask)
    return {"understood": u, "result": result, "explained": e,
            "trace": buf.getvalue(), "stopped": False, "confirm": ask_user}


def _no_hit(avoid) -> list:
    """풀어도 자료(공개 레시피·내 기록) 어디에도 걸리지 않는 기피어."""
    import kitchen as K
    from kitchen_domain import load_recipes
    from recipe_parse import contains_any, expand_avoid
    texts = [r.get("parts_raw") or "" for r in load_recipes()["recipes"]]
    texts += [" ".join(i["name"] for i in r.get("ingredients", []))
              for r in K.RECORDS.values()]
    out = []
    for a in avoid or []:
        keys, _, _ = expand_avoid([a])
        if not any(contains_any(t, keys) for t in texts):
            out.append(a)
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if a not in ("--yes", "--llm-ok")]
    yes = "--yes" in sys.argv
    text = " ".join(args) or \
        "오늘 늦어. 9시 반쯤 들어가는데 냉장고에 배추랑 두부 있어. 30분 안에 먹고 싶어"
    # 외부 LLM 은 동의해야 쓴다 — 명령줄에서는 --llm-ok 로 동의한다
    llm_ok = "--llm-ok" in sys.argv
    ask = best_ask() if llm_ok else None
    if not llm_ok:
        print("(외부 LLM 을 쓰지 않는다 — 쓰려면 --llm-ok. 보내는 것: 가린 말(전화·주소 등 "
              "제외)·재료·시간·가족 수)")
    print("=" * 74)
    print("씽큐 클로 식 진입점 — 자연어로 받고, 판단은 측정이 한다")
    print("=" * 74)
    print(f"\n사용자: {text}")
    print(f"LLM: {how()}")

    # "지금 퇴근해" 는 지금 시각이 있어야 읽힌다 — 명령줄에서는 이 컴퓨터의
    # 시계를 쓴다(실제 기기라면 휴대폰 시계). 전에는 넘기지 않아 못 읽었다.
    import datetime
    now = datetime.datetime.now().strftime("%H:%M")
    out = run(text, ask=ask, approve=(lambda c: True) if yes else None, now=now)
    u = out["understood"]
    print(f"\n[읽은 것] ({u['by']})")
    for w in u["read"]:
        print(f"   · {w}")
    for b in u["rejected"]:
        print(f"   ! {b}")
    if u["needs_confirm"]:
        print(f"   ⚠ 사람 확인 필요: {', '.join(u['needs_confirm'])} "
              f"— 틀리면 위험한 항목이라 그대로 쓰지 않는다")

    p = u["persona"]
    print(f"\n[상황] 귀가 {p['arrive_home']} · 예산 {p['time_budget_min']}분 "
          f"· {p['household_size']}인 · 재고 {len(p['fridge'])}종 "
          f"· 수고 {len(p['friction_reported'])}건")

    if out["stopped"]:
        print(f"\n[멈춤] {out['explained']['text']}")
        if not out["confirm"]["unresolved"]:
            print("   승인하려면 --yes 를 붙여 다시 실행한다")
        print()
        return 0

    print(f"\n[설명] ({out['explained']['by']})")
    print(f"   {out['explained']['text']}")
    if out["explained"]["invented"]:
        print(f"   ! 실행 결과에 없는 수가 섞였다: "
              f"{out['explained']['invented']} — 그대로 쓰면 안 된다")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
