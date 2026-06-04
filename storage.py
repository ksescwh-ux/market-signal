"""
storage.py — 판정 결과 저장 (CSV + JSON)
================================================================
[하는 일]
  1) logs/signal_log.csv 에 매 실행 결과를 '한 줄'씩 누적(append).
     단, 같은 날짜에 또 실행하면 그 날 줄을 '덮어쓰기'.
  2) logs/latest.json 에 가장 최근 결과를 통째로 저장(웹 대시보드 연동용).
  3) 각 지표의 관측 날짜·신선도(오래됨) 플래그도 함께 저장.

CSV 는 엑셀에서 한글이 안 깨지도록 'utf-8-sig' 로 저장합니다.
"""

import os
import csv
import json

import config
import util

log = util.get_logger()

# 저장 위치 (프로젝트 루트 기준)
_ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(_ROOT, "logs")
CSV_PATH = os.path.join(LOGS_DIR, "signal_log.csv")
LATEST_JSON_PATH = os.path.join(LOGS_DIR, "latest.json")

# 웹 대시보드(docs/data) 저장 위치
DOCS_DATA_DIR = os.path.join(_ROOT, "docs", "data")
DOCS_LATEST_PATH = os.path.join(DOCS_DATA_DIR, "latest.json")
DOCS_HISTORY_PATH = os.path.join(DOCS_DATA_DIR, "history.json")

# CSV 고정 컬럼 (지표 컬럼은 뒤에 자동으로 붙음)
_FIXED_COLUMNS = [
    "날짜", "데이터기준날짜", "종합판정",
    "Tier1점등수", "Tier2점등수", "Tier3점등수", "결측수",
]


def _data_date(judged: list) -> str:
    """판정에 쓰인 지표들 중 '가장 최근 관측 날짜'를 대표 기준일로 돌려줍니다."""
    dates = [r["date"] for r in judged if not r.get("skipped") and r.get("date")]
    return max(dates) if dates else ""


def _build_row(judged: list, composite: dict, run_date: str) -> dict:
    """CSV 한 줄(딕셔너리)을 만듭니다."""
    row = {
        "날짜": run_date,
        "데이터기준날짜": _data_date(judged),
        "종합판정": f"{composite['emoji']} {composite['title']}",
        "Tier1점등수": composite["tier_danger"][1],
        "Tier2점등수": composite["tier_danger"][2],
        "Tier3점등수": composite["tier_danger"][3],
        "결측수": composite["missing"],
    }
    # 지표별 값·신호등 컬럼 추가
    for r in judged:
        if r.get("skipped"):
            continue
        name = r["name"]
        row[f"{name}_값"] = "" if r["missing"] else r["value"]
        row[f"{name}_신호등"] = "" if r["signal"] is None else r["signal"]
    return row


def _columns_for(row: dict) -> list:
    """고정 컬럼 + (이번 행에 등장한) 지표 컬럼 순서로 헤더를 만듭니다."""
    indicator_cols = [k for k in row.keys() if k not in _FIXED_COLUMNS]
    return _FIXED_COLUMNS + indicator_cols


def save_to_csv(judged: list, composite: dict, run_date: str) -> str:
    """
    결과를 signal_log.csv 에 저장. 같은 run_date 줄이 있으면 덮어씁니다.
    run_date 형식: 'YYYY-MM-DD' (한국시간 기준 날짜)
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    new_row = _build_row(judged, composite, run_date)

    # 기존 줄들을 읽어옵니다 (있으면)
    existing = []
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))

    # 같은 날짜 줄은 빼고(덮어쓰기 위해), 나머지 + 새 줄
    existing = [r for r in existing if r.get("날짜") != run_date]
    all_rows = existing + [new_row]
    all_rows.sort(key=lambda r: r.get("날짜", ""))  # 날짜순 정렬

    columns = _columns_for(new_row)
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        # extrasaction='ignore' : 옛 줄에 없는 컬럼이 있어도 에러 안 나게
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    log.info(f"CSV 저장 완료: {CSV_PATH} (총 {len(all_rows)}줄)")
    return CSV_PATH


def build_latest_dict(judged: list, composite: dict, run_dt) -> dict:
    """
    latest.json / 대시보드가 읽을 딕셔너리를 만듭니다.
    run_dt : 실행 시각 (시간대 붙은 datetime)
    """
    indicators = []
    for r in judged:
        indicators.append({
            "name": r["name"],
            "tier": r["tier"],
            "source": r["source"],
            "code": r["code"],
            "value": None if r["missing"] else r["value"],
            "unit": r.get("unit", ""),
            "date": r.get("date"),
            "signal": r["signal"],          # green/yellow/orange/red 또는 null
            "stale": r.get("stale", False),  # 오래된 데이터 플래그
            "missing": r["missing"],
            "skipped": r.get("skipped", False),
        })

    return {
        "updated_kst": util.fmt_kst(run_dt),
        "updated_iso": run_dt.isoformat(),
        "data_date": _data_date(judged),
        "composite": {
            "grade": composite["grade"],
            "emoji": composite["emoji"],
            "title": composite["title"],
            "actions": composite["actions"],
            "tier_danger": composite["tier_danger"],
            "missing": composite["missing"],
            "active": composite["active"],
            "yellow_count": composite["yellow_count"],
            "warnings": composite["warnings"],
            "lit": composite["lit"],
        },
        "indicators": indicators,
        "manual_check": __import__("config").MANUAL_CHECK_ITEMS,
        "disclaimer": (
            "이 시스템은 투자 판단을 돕는 보조 모니터링 도구이며, 투자 조언이나 "
            "매매 신호가 아닙니다. 모든 투자 결정과 결과의 책임은 사용자 본인에게 있습니다."
        ),
    }


def save_latest_json(judged: list, composite: dict, run_dt) -> str:
    """가장 최근 결과를 logs/latest.json 에 저장."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    data = build_latest_dict(judged, composite, run_dt)
    with open(LATEST_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"latest.json 저장 완료: {LATEST_JSON_PATH}")
    return LATEST_JSON_PATH


def _to_float(s):
    """CSV 에서 읽은 문자열 값을 숫자로. 비었거나 변환 실패면 None."""
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def build_history() -> dict:
    """
    signal_log.csv 를 읽어 추이 차트용 history.json 데이터를 만듭니다.
    config.CHART_INDICATORS 에 적힌 지표들의 날짜별 값을 모읍니다.
    """
    chart_names = config.CHART_INDICATORS
    dates, grades = [], []
    series = {name: [] for name in chart_names}

    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                dates.append(row.get("날짜", ""))
                grades.append(row.get("종합판정", ""))
                for name in chart_names:
                    series[name].append(_to_float(row.get(f"{name}_값")))

    return {"dates": dates, "grades": grades, "series": series}


def save_dashboard_data(judged: list, composite: dict, run_dt) -> None:
    """
    웹 대시보드(docs/data)용 latest.json + history.json 을 저장합니다.
    GitHub Pages 가 docs/ 를 배포하면 대시보드가 이 파일들을 읽습니다.
    """
    os.makedirs(DOCS_DATA_DIR, exist_ok=True)

    # 1) 현재 상태 (logs/latest.json 과 동일 내용)
    latest = build_latest_dict(judged, composite, run_dt)
    with open(DOCS_LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)

    # 2) 추이 데이터 (CSV 가공)
    history = build_history()
    with open(DOCS_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    log.info(f"대시보드 데이터 저장 완료: {DOCS_DATA_DIR} (추이 {len(history['dates'])}일치)")


def save_all(judged: list, composite: dict, run_dt) -> None:
    """CSV + latest.json + 대시보드 데이터(docs/data) 를 한 번에 저장."""
    run_date = run_dt.strftime("%Y-%m-%d")  # 한국시간 날짜
    save_to_csv(judged, composite, run_date)   # CSV 먼저 저장(history 가 이걸 읽음)
    save_latest_json(judged, composite, run_dt)
    save_dashboard_data(judged, composite, run_dt)
