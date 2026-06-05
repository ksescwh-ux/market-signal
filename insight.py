"""
insight.py — 인사이트 엔진 (월가식 분석 재료를 계산)
================================================================
종합 등급(5칸)만으론 단조롭습니다. 이 모듈은 '시장의 텍스처'를 수치로 뽑아냅니다:

  - risk_score   : 0~100 연속 위험 점수 (매일 움직임)
  - drivers      : 지금 위험을 끌어올린 주도 지표
  - tier_reads   : 신용/매크로/심리 층위별 상태
  - proximity    : 위험선에 가장 근접한 지표 ('한 끗' 경고)
  - percentile   : 과거 대비 현재 위치 (3년 백필 데이터 활용)
  - momentum     : 어제·지난주 대비 변화
  - divergence   : 층위 간 괴리 (한쪽은 잔잔, 한쪽은 과열)
  - streak       : 평상시/위험 연속 일수
  - narrative    : 위 재료로 자동 작문한 사람용 해설 문장들

이 재료들은 (1) 대시보드에 바로 쓰이고 (2) 다음 단계에서 AI 코멘터리의 입력이 됩니다.
"""

import os
import csv

import config
import storage

# 신호 심각도 점수, Tier 가중치 (Tier1 신용시장이 가장 중요)
_SEV = {"green": 0, "yellow": 1, "orange": 2, "red": 3, None: 0}
_TIER_W = {1: 3.0, 2: 2.0, 3: 1.5}
_TIER_NAME = {1: "신용시장", 2: "매크로", 3: "심리"}


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _has_batchim(word: str) -> bool:
    """한글 단어 끝에 받침이 있는지(조사 선택용)."""
    if not word:
        return False
    last = word[-1]
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28 != 0
    return False  # 숫자·영문·괄호 등으로 끝나면 받침 없는 것으로 간주


def _josa(word: str, batchim: str, no_batchim: str) -> str:
    """받침 유무에 맞는 조사를 붙임. 예: _josa('유가','이','가') → '유가가'"""
    return word + (batchim if _has_batchim(word) else no_batchim)


# ─────────────────────────────────────────────────────────────
# 위험 점수 (0~100)
# ─────────────────────────────────────────────────────────────
def risk_score_from_signals(signal_by_tier: list) -> int:
    """[(tier, signal), ...] 로부터 0~100 점수. (결측은 분모에서 제외)"""
    num = den = 0.0
    for tier, sig in signal_by_tier:
        if sig is None:
            continue
        w = _TIER_W.get(tier, 1.0)
        num += w * _SEV.get(sig, 0)
        den += w * 3
    return round(100 * num / den) if den else 0


def risk_score(judged: list) -> int:
    pairs = [(r["tier"], r["signal"]) for r in judged if not r.get("skipped")]
    return risk_score_from_signals(pairs)


def _row_score(row: dict) -> int:
    """CSV 한 줄(과거 기록)의 신호 컬럼으로 점수 재계산."""
    pairs = []
    for ind in config.INDICATORS:
        sig = row.get(f"{ind['name']}_신호등") or None
        pairs.append((ind["tier"], sig))
    return risk_score_from_signals(pairs)


def _band(score: int) -> str:
    if score < 20: return "매우 차분"
    if score < 35: return "차분"
    if score < 50: return "보통"
    if score < 65: return "주의"
    if score < 80: return "위험"
    return "심각"


# ─────────────────────────────────────────────────────────────
# 과거 기록 로드 + 통계 (백분위·모멘텀·연속일수)
# ─────────────────────────────────────────────────────────────
def _load_rows() -> list:
    if not os.path.exists(storage.CSV_PATH):
        return []
    with open(storage.CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _percentile(value, past_values) -> int:
    """value 가 past_values 중 몇 % 지점인지(낮을수록 차분)."""
    vals = [v for v in past_values if v is not None]
    if not vals or value is None:
        return None
    below = sum(1 for v in vals if v <= value)
    return round(100 * below / len(vals))


def _tier_read(judged: list, tier: int) -> str:
    sigs = [r["signal"] for r in judged
            if r["tier"] == tier and not r.get("skipped") and r["signal"]]
    if not sigs:
        return "데이터부족"
    m = max(_SEV[s] for s in sigs)
    return {0: "안정", 1: "중립", 2: "경계", 3: "경고"}[m]


def _proximity(judged: list):
    """위험선(다음 임계값)에 상대적으로 가장 가까운 비(非)green 지표."""
    best = None
    for r in judged:
        if r.get("skipped") or r["missing"] or r["direction"] != "high_bad":
            continue
        v = r["value"]
        t = r.get("thresholds", {})
        ups = sorted(x for x in (t.get("yellow"), t.get("orange"), t.get("red"))
                     if x is not None and x > v)
        if not ups or v <= 0:
            continue
        nearest = ups[0]
        rel = (nearest - v) / v
        if best is None or rel < best["rel"]:
            best = {"name": r["name"], "value": v, "unit": r.get("unit", ""),
                    "line": nearest, "gap": round(nearest - v, 2), "rel": rel}
    if best and best["rel"] <= 0.12:   # 12% 이내로 근접할 때만 의미
        return best
    return None


# ─────────────────────────────────────────────────────────────
# 분석 종합
# ─────────────────────────────────────────────────────────────
def build_analysis(judged: list, composite: dict) -> dict:
    rows = _load_rows()               # 과거 기록(오늘 줄은 아직 없음)
    score = risk_score(judged)

    # 점수 백분위 + 평균 (과거 1년)
    win = 252
    past_scores = [_row_score(r) for r in rows[-win:]]
    score_pct = _percentile(score, past_scores)
    score_avg = round(sum(past_scores) / len(past_scores)) if past_scores else None

    # 모멘텀: 5거래일 전 점수와 비교
    score_5d_ago = _row_score(rows[-5]) if len(rows) >= 5 else None
    score_delta_5d = (score - score_5d_ago) if score_5d_ago is not None else None

    # 주도 지표 (yellow 이상, 가중·심각도 순)
    drivers = sorted(
        [r for r in judged if not r.get("skipped") and _SEV.get(r["signal"], 0) >= 1],
        key=lambda r: _TIER_W.get(r["tier"], 1) * _SEV.get(r["signal"], 0),
        reverse=True,
    )
    driver_list = [{"name": r["name"], "signal": r["signal"],
                    "value": r["value"], "unit": r.get("unit", "")} for r in drivers[:3]]

    # Tier별 상태 + 괴리
    tier_reads = {t: _tier_read(judged, t) for t in (1, 2, 3)}
    sev_map = {"안정": 0, "중립": 1, "경계": 2, "경고": 3, "데이터부족": 0}
    sev_vals = [sev_map[v] for v in tier_reads.values()]
    divergence = (max(sev_vals) - min(sev_vals)) >= 2  # 층위 간 2단계 이상 벌어지면 괴리

    # 위험선 근접
    prox = _proximity(judged)

    # 핵심 지표 과거 대비 위치 (차트 지표 기준)
    pctl = {}
    for name in config.CHART_INDICATORS:
        cur = next((r["value"] for r in judged if r["name"] == name and not r["missing"]), None)
        past = [_f(r.get(f"{name}_값")) for r in rows[-win:]]
        p = _percentile(cur, past)
        if p is not None:
            pctl[name] = p

    # 연속 일수 (과거 등급 + 오늘 등급)
    grades = [r.get("등급") or "" for r in rows] + [composite["grade"]]
    streak_calm = 0
    for g in reversed(grades):
        if g == "green":
            streak_calm += 1
        else:
            break
    days_since_danger = None
    for i, g in enumerate(reversed(grades)):
        if g in ("orange", "red"):
            days_since_danger = i
            break

    analysis = {
        "risk_score": score,
        "score_band": _band(score),
        "score_pct": score_pct,         # 과거 1년 중 백분위
        "score_avg": score_avg,
        "score_delta_5d": score_delta_5d,
        "drivers": driver_list,
        "tier_reads": tier_reads,
        "divergence": divergence,
        "proximity": prox,
        "percentile": pctl,
        "streak_calm": streak_calm,
        "days_since_danger": days_since_danger,
    }
    analysis["narrative"] = compose_narrative(analysis)
    return analysis


# ─────────────────────────────────────────────────────────────
# 자동 작문 (재료 → 사람용 문장)
# ─────────────────────────────────────────────────────────────
# 점수 등급 → 헤드라인용 표현
_BAND_PHRASE = {
    "매우 차분": "잔잔한", "차분": "비교적 차분한", "보통": "중립적인",
    "주의": "슬슬 경계해야 할", "위험": "위험 신호가 켜진", "심각": "매우 위험한",
}


def compose_narrative(a: dict) -> list:
    """
    AI 해설처럼 '헤드라인 → 핵심 통찰 → 체크포인트 → 마무리' 흐름으로
    자동 작문합니다. (제공된 수치만 사용, 예측·조언 없음)
    """
    s = []
    score, band = a["risk_score"], a["score_band"]
    pctl = a.get("percentile", {})

    # 1) 헤드라인 — 한마디 + 점수의 역사적 위치
    head = f"지금 시장은 한마디로 '{_BAND_PHRASE.get(band, band)}' 상태예요. 위험 점수는 100점 만점에 {score}점"
    pct = a.get("score_pct")
    calm_band = band in ("매우 차분", "차분", "보통")
    if pct is None:
        head += "이에요."
    elif pct >= 60 and calm_band:
        # 절대값은 낮지만 '잠잠했던 1년' 기준으론 높은, 미묘한 상황
        head += f"이에요. 절대적으론 낮지만, 워낙 잠잠했던 지난 1년 기준으로 보면 {pct}% 지점으로 살짝 올라온 편이고요."
    elif pct <= 40:
        head += "으로, 지난 1년 기준으로도 낮은 편이에요."
    elif pct >= 60:
        head += f"으로, 지난 1년 중 {pct}% 지점까지 올라온 다소 높은 수준이에요."
    else:
        head += f"으로, 지난 1년 평균({a['score_avg']}점) 부근이에요."
    s.append(head)

    # 2) 핵심 통찰 — '왕 지표' 하이일드 스프레드의 역사적 위치를 쉬운 말로
    hy = pctl.get("하이일드 스프레드")
    if hy is not None:
        if hy <= 30:
            s.append(
                f"특히 시장이 가장 먼저 겁먹는 신용시장(하이일드 스프레드)이 "
                f"지난 1년 중 하위 {hy}% 수준이라, '기업이 빚을 못 갚을 걱정'은 거의 없는 편안한 자리예요."
            )
        elif hy >= 70:
            s.append(
                f"특히 신용시장(하이일드 스프레드)이 지난 1년 중 상위 {100 - hy}%까지 올라, "
                f"기업 부도를 걱정하는 분위기가 평소보다 커졌어요 — 폭락 전 가장 중요한 경고 신호예요."
            )
        else:
            s.append("신용시장(하이일드 스프레드)은 1년 평균 부근으로, 아직 뚜렷한 긴장은 없어요.")

    # 3) 체크포인트 — 위험선 근접 + 역사적으로 높은 지표 (같은 지표는 한 문장으로 합침)
    clauses = []
    p = a.get("proximity")
    prox_name = p["name"] if p else None
    if p:
        subj = _josa(p["name"], "이", "가")
        note = ""
        pp = pctl.get(p["name"])
        if pp is not None and pp >= 70:
            note = f" — 게다가 1년 기준으로도 높은 축(상위 {100 - pp}%)"
        clauses.append(
            f"{subj} {p['value']}{p['unit']}로 심리적 분기점인 {p['line']}선을 {p['gap']} 앞두고 있다는 점{note}"
        )
    for name, pc in pctl.items():
        if name == "하이일드 스프레드" or name == prox_name or pc is None:
            continue
        if pc >= 70:
            clauses.append(f"{_josa(name, '이', '가')} 1년 기준 이미 높은 축(상위 {100 - pc}%)이라는 점")
    if a.get("divergence"):
        clauses.append("층위 간 온도차(괴리)가 보인다는 점")
    if clauses:
        s.append("다만 체크포인트도 있어요 — " + ", 그리고 ".join(clauses) + "이에요.")

    # 4) 확산 여부 — 노란불이 번졌는지
    if a["drivers"]:
        names = ", ".join(d["name"] for d in a["drivers"][:2])
        tr = a["tier_reads"]
        calm = [nm for t, nm in {1: "신용시장", 3: "투자심리"}.items() if tr.get(t) in ("안정", "중립")]
        tail = ("아직 " + "·".join(calm) + " 쪽으로는 번지지 않았어요.") if calm else "확산 여부를 지켜볼 단계예요."
        s.append(f"{names} 등에 '노란불'이 켜졌지만, {tail}")

    # 5) 마무리 — 연속 일수 + 모멘텀 (자연스러운 순서, 거짓 인과 없이)
    close = []
    if a["streak_calm"] >= 2:
        close.append(f"최근 {a['streak_calm']}일째 '평상시'가 이어지고 있어요")
    elif a["days_since_danger"] == 0:
        close.append("오늘은 위험 구간에 들어선 상태예요")
    d5 = a.get("score_delta_5d")
    if d5 is not None and abs(d5) > 3:
        close.append("지난주보다 위험 점수는 " + (f"{d5}점 올랐어요" if d5 > 0 else f"{abs(d5)}점 낮아졌어요"))
    if close:
        s.append(". ".join(close) + ".")

    return s
