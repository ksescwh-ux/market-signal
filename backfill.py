"""
backfill.py — 과거 데이터 채우기 (1회성 도구)
================================================================
FRED·Yahoo 에 이미 있는 과거 시계열을 끌어와, '매일의 신호등 판정'을
거꾸로 재구성해 logs/signal_log.csv 를 한 번에 채웁니다.

이렇게 하면 차트가 오늘부터가 아니라 '과거 N년'부터 그려집니다.

실행:
    python backfill.py            # config.BACKFILL_YEARS(기본 3년) 만큼
    python backfill.py 1          # 1년만

주의: 한 번 돌리면 기존 signal_log.csv 를 덮어씁니다(과거 재구성본으로).
"""

import os
import sys
import csv
import bisect

import requests

import config
import util
import signals
import storage

log = util.get_logger()

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
YAHOO_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ─────────────────────────────────────────────────────────────
# 1. 과거 시계열 통째로 받기
# ─────────────────────────────────────────────────────────────
def fred_series(code: str, api_key: str, start: str) -> tuple:
    """FRED 시리즈 전체를 받아 (날짜리스트, 값리스트) 로. 날짜 오름차순."""
    params = {
        "series_id": code, "api_key": api_key, "file_type": "json",
        "observation_start": start, "sort_order": "asc",
    }
    resp = requests.get(FRED_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    dates, vals = [], []
    for obs in resp.json().get("observations", []):
        raw = obs.get("value", ".")
        if raw not in (".", ""):
            dates.append(obs["date"])
            vals.append(float(raw))
    log.info(f"[FRED:{code}] 과거 {len(vals)}개 수신")
    return dates, vals


def yahoo_series(code: str, start: str) -> tuple:
    """Yahoo 종가 전체를 받아 (날짜리스트, 종가리스트) 로. 날짜 오름차순."""
    import yfinance as yf
    try:
        session = requests.Session()
        session.headers["User-Agent"] = YAHOO_UA
        hist = yf.Ticker(code, session=session).history(start=start, auto_adjust=True)
    except Exception:
        hist = yf.Ticker(code).history(start=start, auto_adjust=True)
    closes = hist["Close"].dropna()
    dates = [d.strftime("%Y-%m-%d") for d in closes.index]
    vals = [round(float(v), 2) for v in closes.values]
    log.info(f"[Yahoo:{code}] 과거 {len(vals)}개 수신")
    return dates, vals


# ─────────────────────────────────────────────────────────────
# 2. '특정 날짜 시점의 값' 조회 (과거를 거슬러 재현)
# ─────────────────────────────────────────────────────────────
def _idx_as_of(dates: list, d: str) -> int:
    """dates(오름차순)에서 d 이하인 가장 마지막 인덱스. 없으면 -1."""
    i = bisect.bisect_right(dates, d) - 1
    return i


# ─────────────────────────────────────────────────────────────
# 3. 백필 본체
# ─────────────────────────────────────────────────────────────
def run_backfill(years: int, api_key: str) -> int:
    today = util.now_kst().strftime("%Y-%m-%d")
    start_year = int(today[:4]) - years
    start = f"{start_year}{today[4:]}"  # N년 전 같은 날
    log.info(f"=== 백필 시작: {start} ~ {today} ({years}년) ===")

    # (1) 모든 지표의 과거 시계열 수집 (소스별)
    raw = {}  # code -> (dates, vals)
    eth_extra = {}  # ETF code -> (ma20_list, change_list)  사전계산
    for ind in config.INDICATORS:
        code = ind["code"]
        try:
            if ind["source"] == "fred":
                raw[code] = fred_series(code, api_key, start)
            else:  # yahoo (HYG/KRE)
                dts, vs = yahoo_series(code, start)
                raw[code] = (dts, vs)
                # 20일 이동평균·하루 변동률 사전계산
                ma20, chg = [], []
                for i in range(len(vs)):
                    window = vs[max(0, i - 19): i + 1]
                    ma20.append(round(sum(window) / len(window), 2))
                    chg.append(round(vs[i] / vs[i - 1] - 1.0, 4) if i > 0 else None)
                eth_extra[code] = (ma20, chg)
        except Exception as e:
            log.warning(f"[{ind['name']}] 과거 수집 실패: {e} → 이 지표는 결측 처리")
            raw[code] = ([], [])

    # (2) 날짜 척추(spine): HYG 거래일을 미국 장 영업일 기준으로 사용
    spine_dates = raw.get("HYG", ([], []))[0]
    if not spine_dates:
        # HYG 실패 시 VIX(일별 FRED)로 대체
        spine_dates = raw.get("VIXCLS", ([], []))[0]
    spine_dates = [d for d in spine_dates if d >= start]
    log.info(f"재구성할 날짜 수: {len(spine_dates)}일")

    # (3) 하루씩 거슬러 판정 재구성
    all_rows = []
    prev_state = None  # 어제 {grade, signals}
    for d in spine_dates:
        judged = []
        for ind in config.INDICATORS:
            code = ind["code"]
            dts, vs = raw[code]
            i = _idx_as_of(dts, d)
            if i < 0:
                r = {**ind, "value": None, "date": None, "prev_value": None,
                     "ma20": None, "change_1d": None, "stale": False,
                     "missing": True, "skipped": False}
            else:
                r = {**ind, "value": vs[i], "date": dts[i],
                     "prev_value": vs[i - 1] if i > 0 else None,
                     "stale": False, "missing": False, "skipped": False}
                if code in eth_extra:
                    ma20, chg = eth_extra[code]
                    r["ma20"] = ma20[i]
                    r["change_1d"] = chg[i]
            judged.append({**r, "signal": signals.judge_indicator(r)})

        composite = signals.compute_composite(judged, prev_state)
        all_rows.append(storage._build_row(judged, composite, d))
        # 다음 날을 위한 prev_state 갱신
        prev_state = {
            "grade": composite["grade"],
            "signals": {r["name"]: r["signal"] for r in judged if not r.get("skipped")},
        }

    # (4) CSV 한 번에 쓰기 (덮어쓰기)
    os.makedirs(storage.LOGS_DIR, exist_ok=True)
    columns = storage._columns_for(all_rows[-1])
    with open(storage.CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    log.info(f"CSV 재구성 완료: {len(all_rows)}일치 → {storage.CSV_PATH}")
    return len(all_rows)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    key = os.getenv("FRED_API_KEY")
    if not key:
        log.error("FRED_API_KEY 가 없습니다. .env 확인.")
        sys.exit(1)
    yrs = int(sys.argv[1]) if len(sys.argv) > 1 else getattr(config, "BACKFILL_YEARS", 3)
    n = run_backfill(yrs, key)
    log.info(f"백필 끝 ✅ ({n}일). 이제 'python main.py' 를 한 번 돌려 대시보드 데이터를 갱신하세요.")
