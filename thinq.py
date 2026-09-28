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
_HANGUL_NUM = {"한": 1, "하나": 1, "혼자": 1, "둘": 2, "두": 2, "셋": 3,
               "세": 3, "넷": 4, "네": 4, "다섯": 5, "여섯": 6}
_DEFAULT = {
    "id": "thinq", "label": "대화로 받은 상황", "household_size": 1,
    "arrive_home": "19:00", "time_budget_min": 45, "next_morning_rush": False,
    "avoid": [], "dislike_noise_after": "23:00",
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
  fridge             [{name, qty_g, stored_days, shelf_life_days}]

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


def ask_claude_cli(prompt: str, timeout: int = 240) -> str:
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
def rule_understand(text: str) -> dict:
    """키 없이 읽는다. LLM 이 없을 때의 폴백이자, LLM 결과의 대조군이다.

    정규식이라 한계가 뚜렷하다 — 그래서 읽어낸 근거를 함께 남긴다.
    읽지 못한 항목은 기본값이 쓰이고, 그 사실이 `read` 에 안 적힌다.
    """
    got, why = {}, []

    m = re.search(r"(\d{1,2})\s*시\s*(반|(\d{1,2})\s*분)?", text)
    if m:
        h = int(m.group(1))
        mi = 30 if m.group(2) == "반" else int(m.group(3) or 0)
        if "저녁" in text or "밤" in text or "늦" in text or h < 12:
            h = h + 12 if h < 12 else h
        got["arrive_home"] = f"{h % 24:02d}:{mi:02d}"
        why.append(f"'{m.group(0)}' → 귀가 {got['arrive_home']}")

    m = re.search(r"(\d{1,3})\s*분", text)
    if m and "시" not in text[max(0, m.start() - 3):m.start()]:
        got["time_budget_min"] = int(m.group(1))
        why.append(f"'{m.group(0)}' → 쓸 수 있는 시간")

    m = re.search(r"(\d)\s*(명|인|식구)", text)
    if m:
        got["household_size"] = int(m.group(1))
        why.append(f"'{m.group(0)}' → {m.group(1)}인")
    else:
        # "넷이 먹을" 처럼 한글로 말하는 쪽이 더 흔하다
        m = re.search(r"(한|하나|혼자|둘|두|셋|세|넷|네|다섯|여섯)\s*"
                      r"(명|이서|이|식구|가족)", text)
        if m and m.group(1) in _HANGUL_NUM:
            got["household_size"] = _HANGUL_NUM[m.group(1)]
            why.append(f"'{m.group(0)}' → {_HANGUL_NUM[m.group(1)]}인")

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
        chunks = re.findall(r"[가-힣]+", text)
        found = []
        for f in _FOOD:
            if f in avoid:
                continue                    # 기피 재료는 재고로 넣지 않는다
            if any(c == f or (len(f) >= 2 and f in c) for c in chunks):
                found.append(f)
        if found:
            got["fridge"] = [{"name": f, "qty_g": 300, "stored_days": 3,
                              "shelf_life_days": 7} for f in found]
            why.append(f"재료로 읽음: {', '.join(found)} (양·보관일은 기본값)")

    if "아침" in text and ("바쁘" in text or "일찍" in text):
        got["next_morning_rush"] = True
        why.append("아침에 여유가 없다고 읽음")

    got["goal_hint"] = text.strip()[:40]
    got["friction_reported"] = _friction_from(text)
    return {"fields": got, "read": why, "by": "규칙"}


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


def understand(text: str, ask=None) -> dict:
    """자연어를 상황 dict 로 바꾼다.

    ask 를 주면 그것으로 읽고, 안 주면 규칙으로 읽는다. **어느 쪽이든
    결과는 검사를 거친다** — LLM 이 준 값을 그대로 쓰지 않는다.
    """
    if ask is None:
        got = rule_understand(text)
    else:
        raw = ask(PROMPT.replace("{text}", text))
        fields, why = _parse_json(raw)
        got = {"fields": fields, "read": why, "by": "LLM"}

    clean, rejected = _validate(got["fields"])
    persona = {**_DEFAULT, **clean}
    confirm = [k for k in NEEDS_CONFIRM if k in clean]
    return {"persona": persona, "read": got["read"], "by": got["by"],
            "rejected": rejected, "needs_confirm": confirm}


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
        "arrive_home": (str, lambda v: re.fullmatch(r"\d{1,2}:\d{2}", v)),
        "dislike_noise_after": (str, lambda v: re.fullmatch(r"\d{1,2}:\d{2}", v)),
        "time_budget_min": (int, lambda v: 1 <= v <= 600),
        "household_size": (int, lambda v: 1 <= v <= 12),
        "max_sodium_mg": ((int, float), lambda v: 0 < v <= 5000),
        "next_morning_rush": (bool, lambda v: True),
        "goal_hint": (str, lambda v: len(v) <= 200),
        "avoid": (list, lambda v: all(isinstance(x, str) for x in v)),
        "friction_reported": (list, lambda v: all(isinstance(x, str) for x in v)),
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

    # 재고 항목의 빠진 칸을 채운다. 여기서 안 채우면 뒤에서 깨진다.
    if "fridge" in ok:
        filled = []
        for x in ok["fridge"]:
            filled.append({"name": x["name"],
                           "qty_g": x.get("qty_g") or 300,
                           "stored_days": x.get("stored_days", 3),
                           "shelf_life_days": x.get("shelf_life_days") or 7})
        missing = sum(1 for x in ok["fridge"]
                      if not all(k in x for k in
                                 ("qty_g", "stored_days", "shelf_life_days")))
        if missing:
            bad.append(f"fridge: {missing}개 항목의 양·보관일이 없어 "
                       f"기본값(300g·3일·7일)으로 채웠다")
        ok["fridge"] = filled
    return ok, bad


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
    if v.get("user_touches"):
        ask = m.get("확인 요청")
        bits.append(f"확인이 필요한 것이 {v['user_touches']}건 있습니다"
                    + (f" ({'; '.join(ask)})" if ask else ""))
    else:
        bits.append("따로 물을 것은 없습니다")
    if not v.get("verified"):
        bits.append("**아직 시나리오를 다 채우지는 못했습니다**")
    return " · ".join(bits)


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
    text = (ask(EXPLAIN_PROMPT.replace("{data}", data)) or "").strip()
    invented = _invented_numbers(text, m)
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
def run(text: str, ask=None, seed: int = 7, approve=None) -> dict:
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

    u = understand(text, ask=ask)
    avoid = u["persona"].get("avoid", [])
    expanded, notes, unresolved = expand_avoid(avoid)
    ask_user = {"items": u["needs_confirm"], "avoid": avoid,
                "expanded_to": expanded, "expansion": notes,
                "unresolved": unresolved}

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
        return {"understood": u, "result": None, "stopped": True,
                "confirm": ask_user,
                "explained": {"text": f"실행 전에 확인이 필요합니다 — {what}. "
                                      f"맞으면 승인해 주세요.",
                              "by": "규칙", "invented": []}}

    pid = u["persona"]["id"]
    personas.PERSONAS[pid] = u["persona"]

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        # LLM 이 있으면 시나리오 장면도 제안받는다(llm_design). 제어는 그대로다.
        factory = None
        if ask is not None:
            from llm_design import make_llm_beats
            from kitchen_domain import kitchen_beats, kitchen_capabilities
            factory = (lambda fo: make_llm_beats(ask, kitchen_capabilities,
                                                 kitchen_beats, fo))
        result = run_design.design_for(pid, Trace(), seed=seed,
                                       beats_factory=factory)
    e = explain(result, ask=ask)
    return {"understood": u, "result": result, "explained": e,
            "trace": buf.getvalue(), "stopped": False, "confirm": ask_user}


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--yes"]
    yes = "--yes" in sys.argv
    text = " ".join(args) or \
        "오늘 늦어. 9시 반쯤 들어가는데 냉장고에 배추랑 두부 있어. 30분 안에 먹고 싶어"
    ask = best_ask()
    print("=" * 74)
    print("씽큐 클로 식 진입점 — 자연어로 받고, 판단은 측정이 한다")
    print("=" * 74)
    print(f"\n사용자: {text}")
    print(f"LLM: {how()}")

    out = run(text, ask=ask, approve=(lambda c: True) if yes else None)
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
