"""
test_signals.py — 판정 로직 자동 테스트
================================================================
이 파일은 signals.py 의 판정이 '정확히 경계값에서' 올바른지 검사합니다.
인터넷 연결이 필요 없습니다(가짜 데이터로 로직만 시험).

실행 방법 (프로젝트 폴더에서):
    python -m pytest -v

[검사 항목]
  1) high_bad 임계 경계 (정확히 4.0 / 5.0 / 6.0 일 때)
  2) low_bad(수익률곡선) 경계
  3) trend(추세) 판정
  4) 종합 판정 — 판정 보류(회색) / 최악 / 위험 / 경계 / 평상시
"""

import os
import sys

# 상위 폴더(프로젝트 루트)를 import 경로에 추가 — tests/ 안에서 실행해도 모듈을 찾게.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signals


# ─────────────────────────────────────────────────────────────
# 1. high_bad 임계 경계 테스트
#    thresholds = {yellow:4.0, orange:5.0, red:6.0}
# ─────────────────────────────────────────────────────────────
HIGH_T = {"yellow": 4.0, "orange": 5.0, "red": 6.0}


def test_high_bad_green():
    # 4.0 '미만'은 green
    assert signals.judge_high_bad(3.99, HIGH_T) == signals.GREEN


def test_high_bad_yellow_boundary():
    # 정확히 4.0 이면 yellow (경계 포함)
    assert signals.judge_high_bad(4.0, HIGH_T) == signals.YELLOW


def test_high_bad_orange_boundary():
    # 정확히 5.0 이면 orange
    assert signals.judge_high_bad(5.0, HIGH_T) == signals.ORANGE


def test_high_bad_red_boundary():
    # 정확히 6.0 이면 red (가장 중요한 경계!)
    assert signals.judge_high_bad(6.0, HIGH_T) == signals.RED
    assert signals.judge_high_bad(6.5, HIGH_T) == signals.RED


# ─────────────────────────────────────────────────────────────
# 2. low_bad(수익률곡선 2s10s) 경계 테스트
#    thresholds = {green:0.2, red:0.0}
# ─────────────────────────────────────────────────────────────
LOW_T = {"green": 0.2, "red": 0.0}


def test_low_bad_green():
    assert signals.judge_low_bad(0.41, LOW_T) == signals.GREEN


def test_low_bad_yellow_zone():
    # 0 ~ 0.2 사이는 yellow
    assert signals.judge_low_bad(0.2, LOW_T) == signals.YELLOW
    assert signals.judge_low_bad(0.0, LOW_T) == signals.YELLOW


def test_low_bad_red_inversion():
    # 마이너스(역전)는 red
    assert signals.judge_low_bad(-0.01, LOW_T) == signals.RED


# ─────────────────────────────────────────────────────────────
# 3. trend(추세) 판정 테스트
# ─────────────────────────────────────────────────────────────
def test_trend_rising_is_yellow():
    assert signals.judge_trend(value=3.5, prev_value=3.2) == signals.YELLOW


def test_trend_falling_is_green():
    assert signals.judge_trend(value=3.0, prev_value=3.2) == signals.GREEN


def test_trend_no_prev_is_none():
    assert signals.judge_trend(value=3.0, prev_value=None) is None


# ─────────────────────────────────────────────────────────────
# 4. 종합 판정 테스트 (가짜 지표로 구성)
# ─────────────────────────────────────────────────────────────
def _ind(tier, signal, name="지표", skipped=False):
    """테스트용 가짜 지표 하나를 만듭니다."""
    return {
        "tier": tier,
        "signal": signal,
        "name": name,
        "value": 1.0,
        "unit": "",
        "date": "2026-06-02",
        "skipped": skipped,
    }


def test_composite_green_when_all_calm():
    # 전부 green/yellow면 평상시
    judged = [
        _ind(1, signals.GREEN), _ind(1, signals.GREEN),
        _ind(2, signals.GREEN), _ind(2, signals.YELLOW),
        _ind(3, signals.GREEN), _ind(3, signals.GREEN),
    ]
    c = signals.compute_composite(judged)
    assert c["grade"] == "green"


def test_composite_orange_when_tier1_danger():
    # Tier1 위험 점등 1개 → 위험(orange)
    judged = [
        _ind(1, signals.RED), _ind(1, signals.GREEN),
        _ind(2, signals.GREEN), _ind(2, signals.GREEN),
        _ind(3, signals.GREEN), _ind(3, signals.GREEN),
    ]
    c = signals.compute_composite(judged)
    assert c["grade"] == "orange"


def test_composite_red_when_tier1_and_tier2_danger():
    # Tier1 + Tier2 동시 위험 → 최악(red)
    judged = [
        _ind(1, signals.ORANGE), _ind(1, signals.GREEN),
        _ind(2, signals.RED), _ind(2, signals.GREEN),
        _ind(3, signals.GREEN), _ind(3, signals.GREEN),
    ]
    c = signals.compute_composite(judged)
    assert c["grade"] == "red"


def test_composite_yellow_when_tier2_3_sum_two():
    # Tier2+Tier3 위험 합계 2개 → 경계(yellow)
    judged = [
        _ind(1, signals.GREEN), _ind(1, signals.GREEN),
        _ind(2, signals.ORANGE),
        _ind(3, signals.ORANGE),
        _ind(3, signals.GREEN),
    ]
    c = signals.compute_composite(judged)
    assert c["grade"] == "yellow"


def test_composite_gray_when_tier1_missing():
    # Tier1 두 지표가 모두 결측(None) → 판정 보류(회색)
    judged = [
        _ind(1, None), _ind(1, None),
        _ind(2, signals.GREEN), _ind(2, signals.GREEN),
        _ind(3, signals.GREEN), _ind(3, signals.GREEN),
    ]
    c = signals.compute_composite(judged)
    assert c["grade"] == "gray"


def test_unconfirmed_orange_becomes_watch():
    # 어제 정상(green)이던 지표가 오늘 orange → 하루짜리라 '관찰 중'(등급 안 올림)
    judged = [
        _ind(1, signals.ORANGE, name="KRE"),
        _ind(1, signals.GREEN, name="HYG"),
        _ind(2, signals.GREEN, name="실업"),
        _ind(3, signals.GREEN, name="VIX"),
    ]
    prev = {"grade": "green", "signals": {"KRE": "green"}}
    c = signals.compute_composite(judged, prev)
    assert c["grade"] == "green"                 # 위험으로 안 올라감
    assert c["tier_danger"][1] == 0              # 확정 위험 0
    assert any(w["name"] == "KRE" for w in c["watch"])  # 관찰 중에 포함


def test_confirmed_orange_counts():
    # 어제도 orange 였던 지표가 오늘도 orange → 확정 위험 → 등급 상승
    judged = [
        _ind(1, signals.ORANGE, name="KRE"),
        _ind(1, signals.GREEN, name="HYG"),
        _ind(2, signals.GREEN, name="실업"),
    ]
    prev = {"grade": "orange", "signals": {"KRE": "orange"}}
    c = signals.compute_composite(judged, prev)
    assert c["grade"] == "orange"
    assert c["tier_danger"][1] == 1


def test_red_counts_immediately_without_prev():
    # red(빨강)는 어제가 정상이어도 즉시 위험으로 카운트(심각하므로)
    judged = [
        _ind(1, signals.RED, name="하이일드"),
        _ind(1, signals.GREEN, name="HYG"),
        _ind(2, signals.GREEN, name="실업"),
    ]
    prev = {"grade": "green", "signals": {"하이일드": "green"}}
    c = signals.compute_composite(judged, prev)
    assert c["grade"] == "orange"      # Tier1 위험 1개 → 위험
    assert c["tier_danger"][1] == 1
    assert c["watch"] == []


def test_direction_better_when_grade_improves():
    # 어제 orange → 오늘 green 이면 방향이 '나아짐(better)'
    judged = [_ind(1, signals.GREEN, name="x"), _ind(2, signals.GREEN, name="y")]
    prev = {"grade": "orange", "signals": {}}
    c = signals.compute_composite(judged, prev)
    assert c["direction"]["state"] == "better"


def test_composite_skipped_not_counted_as_missing():
    # 미구현(skipped) 지표는 결측으로 세지 않아야 함 (판정보류 오작동 방지)
    judged = [
        _ind(1, signals.GREEN), _ind(1, signals.GREEN),
        _ind(1, None, skipped=True), _ind(1, None, skipped=True),  # HYG/KRE 자리
        _ind(2, signals.GREEN), _ind(3, signals.GREEN),
    ]
    c = signals.compute_composite(judged)
    # skipped 2개를 제외하면 결측 0 → 회색이 아니라 평상시여야 함
    assert c["grade"] == "green"
    assert c["missing"] == 0
