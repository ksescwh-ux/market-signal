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
def compose_narrative(a: dict) -> list:
    s = []
    score, band = a["risk_score"], a["score_band"]

    # 1) 점수 + 과거 대비
    line = f"오늘 위험 점수는 {score}/100 — '{band}' 수준이에요."
    if a["score_pct"] is not None:
        loc = "낮은(차분한)" if a["score_pct"] <= 40 else ("높은(긴장된)" if a["score_pct"] >= 60 else "중간")
        line += f" 지난 1년 중 {a['score_pct']}% 지점으로 {loc} 편이고요."
    s.append(line)

    # 2) 주도 지표
    if a["drivers"]:
        names = ", ".join(f"{d['name']}({d['value']}{d['unit']})" for d in a["drivers"])
        s.append(f"지금 읽기를 끌어올리는 건 {names}예요.")
    else:
        s.append("위험을 끌어올리는 지표가 거의 없어 전반적으로 잔잔해요.")

    # 3) Tier별 + 괴리
    tr = a["tier_reads"]
    tline = f"층위별로는 신용시장 {tr[1]} · 매크로 {tr[2]} · 심리 {tr[3]} 상태예요."
    if a["divergence"]:
        tline += " 한쪽은 잔잔한데 한쪽은 달아오르는 '괴리'가 보여요 — 이런 엇갈림은 눈여겨볼 대목이에요."
    s.append(tline)

    # 4) 위험선 근접
    p = a["proximity"]
    if p:
        subj = _josa(p["name"], "이", "가")
        s.append(f"특히 {subj} {p['value']}{p['unit']}로 {p['line']}선까지 {p['gap']} 남아 '한 끗' 거리예요.")

    # 5) 모멘텀 + 연속
    mo = []
    if a["score_delta_5d"] is not None:
        if a["score_delta_5d"] > 3: mo.append(f"지난주보다 위험이 +{a['score_delta_5d']}점 올랐고")
        elif a["score_delta_5d"] < -3: mo.append(f"지난주보다 위험이 {a['score_delta_5d']}점 내렸고")
        else: mo.append("지난주와 비슷하고")
    if a["streak_calm"] >= 2:
        mo.append(f"{a['streak_calm']}일 연속 평상시예요")
    elif a["days_since_danger"] == 0:
        mo.append("오늘은 위험 구간이에요")
    if mo:
        s.append((" ".join(mo)).strip().rstrip(",") + ".")

    return s
