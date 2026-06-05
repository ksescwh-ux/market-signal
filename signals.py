"""
signals.py — 신호등 판정 + 종합 판정
================================================================
[하는 일]
  1) 개별 지표 판정 : 숫자 하나를 보고 green/yellow/orange/red 결정
  2) 종합 판정       : 모든 신호등을 조합해 평상시/경계/위험/최악/판정보류 결정

[판정 방향(direction)]
  - high_bad : 숫자가 높을수록 위험 (VIX, 스프레드, 유가, 금리)
  - low_bad  : 숫자가 낮을수록 위험 (수익률곡선 2s10s — 마이너스가 위험)
  - trend    : 추세 (CPI/PCE/실업률은 '지난번보다 올랐나'로 단순화)
               ※ HYG/KRE 의 20일선 추세는 8단계(Yahoo)에서 처리

[★ 단순화 안내 — README 에도 명시]
  CPI/PCE/실업률의 '3개월 연속 상승', '0.5%p 급등' 같은 정밀 추세는
  현재 '직전값 대비 상승/하락'으로 단순화했습니다. 이 때문에 이 지표들은
  red(빨강)가 거의 뜨지 않습니다(올랐으면 yellow). 개선 여지로 남겨둡니다.

[면책] 이 시스템은 투자 보조 도구이며 매매 신호가 아닙니다.
"""

import config
import util

log = util.get_logger()

# 신호등 색 상수 (글자 오타 방지용)
GREEN = "green"
YELLOW = "yellow"
ORANGE = "orange"
RED = "red"
# 위험 '점등'으로 카운트하는 색 (orange, red 만. yellow 는 경계지만 미카운트)
DANGER_SIGNALS = (ORANGE, RED)


# ─────────────────────────────────────────────────────────────
# 1. 개별 지표 판정 함수들
# ─────────────────────────────────────────────────────────────
def judge_high_bad(value: float, t: dict) -> str:
    """높을수록 위험. t = {yellow, orange, red}."""
    if value >= t["red"]:
        return RED
    if value >= t["orange"]:
        return ORANGE
    if value >= t["yellow"]:
        return YELLOW
    return GREEN


def judge_low_bad(value: float, t: dict) -> str:
    """
    낮을수록 위험 (2s10s). t = {green, red}.
      value > green(0.2) → green
      red(0.0) ≤ value ≤ green → yellow
      value < red(0.0)   → red (역전)
    (이 지표는 orange 단계가 없어 3단계로 판정)
    """
    if value < t["red"]:
        return RED
    if value <= t["green"]:
        return YELLOW
    return GREEN


def judge_trend(value: float, prev_value) -> str:
    """
    추세(상승=위험) — '직전값 대비(mom)' 단순판정.
    직전값보다 올랐으면 yellow, 아니면 green. 직전값 없으면 None.
    (CPI/PCE/실업률에 사용)
    """
    if prev_value is None:
        return None
    return YELLOW if value > prev_value else GREEN


def judge_trend_ma20(close, ma20, change_1d, t: dict) -> str:
    """
    20일선 추세 판정 (HYG/KRE 에 사용).
      red    : 하루 5%+ 하락 (t["drop_pct"], 기본 0.05)
      yellow : 20일선 ±1% 이내 '근처' (t["near_pct"], 기본 0.01)
      orange : 20일선 아래
      green  : 20일선 위
    데이터 부족(close/ma20 없음)이면 None.
    """
    if close is None or ma20 is None or ma20 == 0:
        return None
    drop_pct = t.get("drop_pct", 0.05)
    near_pct = t.get("near_pct", 0.01)

    # 1) 급락 먼저 (하루 5%+ 하락)
    if change_1d is not None and change_1d <= -drop_pct:
        return RED
    # 2) 20일선 대비 위치
    gap = (close - ma20) / ma20
    if abs(gap) <= near_pct:
        return YELLOW   # 근처
    if gap < 0:
        return ORANGE   # 아래
    return GREEN        # 위


def judge_indicator(r: dict):
    """
    수집 결과 딕셔너리 하나를 받아 신호등 색을 돌려줍니다.
    판정 불가(결측/미구현/추세데이터 없음)면 None.
    """
    # 결측이거나 아직 미구현(Yahoo 등)이면 판정 불가
    if r.get("missing") or r.get("skipped"):
        return None

    value = r["value"]
    direction = r["direction"]

    if direction == "high_bad":
        return judge_high_bad(value, r["thresholds"])
    if direction == "low_bad":
        return judge_low_bad(value, r["thresholds"])
    if direction == "trend":
        # 추세 종류 구분: ma20(HYG/KRE) vs mom(CPI/PCE/실업률)
        trend_type = r.get("trend_type", "mom")
        if trend_type == "ma20":
            return judge_trend_ma20(
                value, r.get("ma20"), r.get("change_1d"), r.get("thresholds", {})
            )
        return judge_trend(value, r.get("prev_value"))

    log.warning(f"[{r['name']}] 알 수 없는 direction: {direction}")
    return None


def judge_all(results: list) -> list:
    """
    수집 결과 리스트 전체를 판정해, 각 항목에 'signal' 키를 추가합니다.
    signal 은 green/yellow/orange/red 중 하나거나, 판정 불가면 None.
    """
    judged = []
    for r in results:
        signal = judge_indicator(r)
        judged.append({**r, "signal": signal})
    return judged


# ─────────────────────────────────────────────────────────────
# 2. 종합 판정
# ─────────────────────────────────────────────────────────────
# 신호등 색의 '심각도 점수' (방향 비교·확정 판단용)
SEVERITY = {GREEN: 0, YELLOW: 1, ORANGE: 2, RED: 3}


def _sev(signal) -> int:
    """신호 심각도 점수. 모르는/결측이면 -1."""
    return SEVERITY.get(signal, -1)


def direction_arrow(today_sig, prev_sig) -> str:
    """
    오늘 신호 vs 어제 신호 비교.
      나빠짐(위험↑) → "↑",  나아짐(위험↓) → "↓",  같거나 비교불가 → ""
    """
    if today_sig is None or prev_sig is None:
        return ""
    t, p = _sev(today_sig), _sev(prev_sig)
    if t > p:
        return "↑"
    if t < p:
        return "↓"
    return ""


def _is_confirmed_danger(r: dict, prev_signals: dict) -> bool:
    """
    이 지표의 위험(orange/red)을 '확정 위험'으로 카운트할지 판단.
      - red          : 즉시 확정 (심각하므로 지연 없이 반영)
      - orange       : CONFIRM 옵션이 켜져 있으면 '어제도 위험'이었을 때만 확정
      - 그 외(green/yellow/None) : 위험 아님
    """
    sig = r["signal"]
    if sig == RED:
        return True
    if sig == ORANGE:
        if not config.CONFIRM.get("on", True):
            return True  # 옵션 끄면 즉시 카운트 (기존 동작)
        prev_sig = prev_signals.get(r["name"])
        if prev_sig is None:
            # 어제 신호 정보가 없으면(첫 실행·어제 결측) 확정/부정 불가 →
            # 위험을 숨기지 않도록 일단 카운트(안전 우선).
            return True
        return _sev(prev_sig) >= 2  # 어제도 orange/red 였으면 확정
    return False


def compute_composite(judged: list, prev: dict = None) -> dict:
    """
    개별 신호등들을 조합해 종합 판정을 계산합니다.

    prev : 어제 상태 {"grade": "...", "signals": {지표명: 신호}} (없으면 None)
           → 거짓경보 감소(2일 연속 확인)와 방향(어제 대비) 계산에 사용.

    돌려주는 값(딕셔너리) 주요 키:
      grade      : gray/red/orange/yellow/green
      emoji,title,actions : config.VERDICTS 에서 가져온 표시·행동문구
      tier_danger: {1: n, 2: n, 3: n}  각 Tier '확정 위험' 수
      watch      : '관찰 중'(어제는 정상이라 아직 미확정) 위험 지표 리스트
      confidence : 판정 확신도 ("높음"/"보통"/"-")
      direction  : 어제 대비 방향 {"state","text","arrow"}
      missing/active/yellow_count/warnings/lit : 기존과 동일
    """
    prev = prev or {}
    prev_signals = prev.get("signals", {})
    prev_grade = prev.get("grade")

    # '미구현(skipped)'은 아직 없는 지표로 보고 판정 대상에서 제외.
    active = [r for r in judged if not r.get("skipped")]
    missing = [r for r in active if r["signal"] is None]
    active_count = len(active)

    # ── 위험 신호를 '확정'과 '관찰 중(미확정)'으로 분리 ──────────
    tier_danger = {1: 0, 2: 0, 3: 0}   # 확정 위험만 카운트
    watch = []                          # 관찰 중(하루만 깜빡인 신호)
    for r in active:
        if r["signal"] not in DANGER_SIGNALS:
            continue
        if _is_confirmed_danger(r, prev_signals):
            tier_danger[r["tier"]] += 1
        else:
            watch.append({
                "name": r["name"], "tier": r["tier"], "signal": r["signal"],
                "value": r["value"], "unit": r.get("unit", ""),
            })

    yellow_count = sum(1 for r in active if r["signal"] == YELLOW)
    tier1_missing = sum(
        1 for r in active if r["tier"] == 1 and r["signal"] is None
    )

    warnings = []

    # ── 규칙 0: 판정 보류(회색) — 결측이 임계 초과 ──────────────
    missing_ratio = (len(missing) / active_count) if active_count else 1.0
    if missing_ratio > config.MAX_MISSING or tier1_missing >= 2:
        grade = "gray"
    else:
        # ── 규칙 1~4 (위에서부터 먼저 해당되는 것) ─────────────
        t1, t2, t3 = tier_danger[1], tier_danger[2], tier_danger[3]
        if t1 >= 1 and t2 >= 1:
            grade = "red"        # 최악
        elif t1 >= 1:
            grade = "orange"     # 위험
        elif (t2 + t3) >= 2:
            grade = "yellow"     # 경계
        else:
            grade = "green"      # 평상시

        # ── 보조 규칙: TIER3_PANIC (옵션) ─────────────────────
        # Tier3 만 위험 2개(예: VIX red + 유가 red)면 경계로 격상.
        if config.TIER3_PANIC.get("on") and grade == "green" and t3 >= 2:
            grade = "yellow"
            warnings.append("⚡ 단기 패닉 감지(Tier3 위험 2개) — 경계로 격상")

    # ── 보조 규칙: YELLOW_WARN (옵션) ─────────────────────────
    yw = config.YELLOW_WARN
    if yw.get("on") and yellow_count >= yw.get("threshold", 5):
        warnings.append(f"⚠️ 시장 전반 경계감 누적(yellow {yellow_count}개)")

    # ── 관찰 중(미확정 위험) 안내 ─────────────────────────────
    if watch:
        names = ", ".join(w["name"] for w in watch)
        warnings.append(
            f"👀 관찰 중: {names} — 어제는 정상이라 아직 위험 등급엔 반영 안 함(하루 더 확인)"
        )

    verdict = config.VERDICTS[grade]

    # ── 확신도 (확정 위험이 몇 개·얼마나 심각한가) ─────────────
    confirmed_total = sum(tier_danger.values())
    has_red = any(r["signal"] == RED and _is_confirmed_danger(r, prev_signals)
                  for r in active)
    if grade in ("green", "gray"):
        confidence = "-"
    elif has_red or confirmed_total >= 2:
        confidence = "높음"
    else:
        confidence = "보통"

    # ── 방향 (어제 등급 대비) ─────────────────────────────────
    grade_sev = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
    direction = {"state": "na", "text": "", "arrow": ""}
    if prev_grade in grade_sev and grade in grade_sev:
        cur, pre = grade_sev[grade], grade_sev[prev_grade]
        if cur > pre:
            direction = {"state": "worse", "text": "어제보다 위험이 커졌어요", "arrow": "🔺"}
        elif cur < pre:
            direction = {"state": "better", "text": "어제보다 나아졌어요", "arrow": "🔻"}
        else:
            direction = {"state": "same", "text": "어제와 같아요", "arrow": "➖"}

    # 점등(orange/red)된 지표 요약 (알림 메시지에 쓰임) — 확정/관찰 모두 포함
    lit = [
        {
            "name": r["name"],
            "signal": r["signal"],
            "value": r["value"],
            "unit": r.get("unit", ""),
            "date": r["date"],
        }
        for r in active if r["signal"] in DANGER_SIGNALS
    ]

    return {
        "grade": grade,
        "emoji": verdict["emoji"],
        "title": verdict["title"],
        "actions": verdict["actions"],
        "tier_danger": tier_danger,
        "watch": watch,
        "confidence": confidence,
        "direction": direction,
        "missing": len(missing),
        "active": active_count,
        "yellow_count": yellow_count,
        "warnings": warnings,
        "lit": lit,
    }


# ─────────────────────────────────────────────────────────────
# 3. 보기 좋은 출력 (콘솔용)
# ─────────────────────────────────────────────────────────────
_SIGNAL_EMOJI = {GREEN: "🟢", YELLOW: "🟡", ORANGE: "🟠", RED: "🔴", None: "⚪"}


def print_signals(judged: list, composite: dict) -> None:
    """개별 신호등 + 종합 판정을 콘솔에 보기 좋게 출력."""
    log.info("=" * 60)
    log.info("개별 신호등 판정")
    log.info("-" * 60)
    for r in judged:
        if r.get("skipped"):
            continue  # 미구현(Yahoo)은 생략
        emoji = _SIGNAL_EMOJI.get(r["signal"], "⚪")
        val = "결측" if r["signal"] is None else f"{r['value']}{r.get('unit','')}"
        log.info(f"  {emoji} {r['name']:<16} {val:<12} (T{r['tier']}, {r['date']})")

    log.info("=" * 60)
    c = composite
    dir_txt = ""
    if c.get("direction", {}).get("arrow"):
        dir_txt = f"  {c['direction']['arrow']} {c['direction']['text']}"
    log.info(f"종합 판정: {c['emoji']} {c['title']}{dir_txt}")
    conf = c.get("confidence", "-")
    log.info(
        f"  확정 위험 — Tier1:{c['tier_danger'][1]} "
        f"Tier2:{c['tier_danger'][2]} Tier3:{c['tier_danger'][3]} "
        f"| 확신도:{conf} | yellow:{c['yellow_count']} | 결측:{c['missing']}/{c['active']}"
    )
    for w in c["warnings"]:
        log.info(f"  {w}")
    log.info("  [행동 가이드]")
    for a in c["actions"]:
        log.info(f"    • {a}")
    log.info("=" * 60)


# ─────────────────────────────────────────────────────────────
# 4. 직접 실행하면 FRED 수집 → 판정 → 종합 판정까지 한 번에
#    python signals.py
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    import fetch

    load_dotenv()
    api_key = os.getenv("FRED_API_KEY")
    if not api_key or api_key == "여기에_키_붙여넣기":
        log.error("FRED_API_KEY 가 없습니다. .env 를 확인하세요.")
    else:
        log.info("=== 4단계: 수집 → 신호등 판정 → 종합 판정 ===")
        results = fetch.collect_fred_indicators(api_key)
        judged = judge_all(results)
        composite = compute_composite(judged)
        print_signals(judged, composite)
        log.info("4단계 판정 성공! ✅")
