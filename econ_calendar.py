"""
econ_calendar.py — 경제 지표 발표 일정 (다가오는 발표 + 한국시간)
================================================================
우리가 추적하는 '월간 지표'(CPI·PCE·실업률)의 다음 발표 예정일을
FRED 발표일정 API 에서 가져와, 미 동부시 오전 8:30 발표 기준으로
한국시간(KST)으로 환산해 알려줍니다.

(VIX·금리·유가·ETF 같은 일별 시장지표는 '발표 시각'이 따로 없고
 미국장 마감 후 갱신되므로 캘린더엔 월간 지표만 표시합니다.)
"""

from datetime import datetime

import requests

import util

log = util.get_logger()

# FRED 코드 → (보기 좋은 이름). 월간·정기발표 지표만.
_MONTHLY = {
    "CPILFESL": "근원 CPI (소비자물가)",
    "PCEPILFE": "근원 PCE (연준 선호 물가)",
    "UNRATE": "실업률 (고용보고서)",
}

# 미국 주요 지표 발표 시각: 동부시(ET) 오전 8:30 (CPI·PCE·고용 공통)
_RELEASE_ET_HOUR, _RELEASE_ET_MIN = 8, 30

_FRED = "https://api.stlouisfed.org/fred/"


def _release_id(code: str, api_key: str):
    """지표가 속한 FRED release id 를 찾음."""
    r = requests.get(_FRED + "series/release",
                     params={"series_id": code, "api_key": api_key, "file_type": "json"},
                     timeout=15)
    r.raise_for_status()
    return r.json()["releases"][0]["id"]


def _next_release_date(release_id: int, api_key: str, today: str):
    """오늘 이후의 가장 가까운 발표 예정일(YYYY-MM-DD)."""
    r = requests.get(_FRED + "release/dates",
                     params={"release_id": release_id, "api_key": api_key,
                             "file_type": "json", "include_release_dates_with_no_data": "true",
                             "sort_order": "asc", "realtime_start": today},
                     timeout=15)
    r.raise_for_status()
    for d in r.json().get("release_dates", []):
        if d["date"] >= today:
            return d["date"]
    return None


def _et_release_to_kst(date_str: str) -> datetime:
    """그 날짜의 'ET 오전 8:30' 을 한국시간으로 환산(서머타임 자동 처리)."""
    naive = datetime.strptime(date_str, "%Y-%m-%d")
    et = naive.replace(hour=_RELEASE_ET_HOUR, minute=_RELEASE_ET_MIN, tzinfo=util.ET)
    return et.astimezone(util.KST)


def upcoming_releases(api_key: str) -> list:
    """
    월간 지표들의 다음 발표 예정을 한국시간과 함께 돌려줍니다.
    [{"name","date","kst","days_away"}], 가까운 순. 실패한 지표는 건너뜀.
    """
    today = util.now_kst().strftime("%Y-%m-%d")
    out = []
    for code, name in _MONTHLY.items():
        try:
            rid = _release_id(code, api_key)
            d = _next_release_date(rid, api_key, today)
            if not d:
                continue
            kst = _et_release_to_kst(d)
            days = (kst.date() - util.now_kst().date()).days
            out.append({
                "name": name,
                "date": d,
                "kst": kst.strftime("%m/%d(%a) %H:%M"),
                "kst_full": util.fmt_kst(kst),
                "days_away": days,
            })
        except Exception as e:
            log.warning(f"[캘린더:{code}] 발표일 조회 실패: {e}")
    out.sort(key=lambda x: x["date"])
    return out


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    for r in upcoming_releases(os.getenv("FRED_API_KEY")):
        log.info(f"{r['name']}: {r['date']} (한국시간 {r['kst']}, D-{r['days_away']})")
