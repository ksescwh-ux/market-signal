"""
fetch.py — 데이터 수집 (FRED 우선 / Yahoo 보조)
================================================================
인터넷에서 경제·시장 지표를 받아오는 일을 담당합니다.

설계 원칙:
  - 소스(FRED/Yahoo)별로 함수를 나누고, 각각 독립된 try/except 로 감쌉니다.
    → 한 소스가 실패해도 다른 소스 결과를 날리지 않게.
  - 데이터를 못 가져오면 프로그램이 죽지 않고 '결측(missing)'으로 표시합니다.
  - 휴일·주말엔 값이 없으므로, 항상 '최신 유효값(last valid observation)'을
    그 관측 날짜와 함께 받아옵니다.

이 단계(2단계)에서는 FRED 수집 함수만 만듭니다.
Yahoo 수집은 8단계에서 추가합니다.
"""

import time

import requests

import util
import config

log = util.get_logger()

# FRED 데이터 조회 주소 (공식 API)
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Yahoo 호출 시 보낼 브라우저 흉내 User-Agent (차단 완화용)
YAHOO_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch_fred(code: str, api_key: str, stale_days: int = 5) -> dict:
    """
    FRED 에서 지표 하나의 '최신 유효값'을 받아옵니다.

    매개변수
      code       : FRED 시리즈 코드 (예: "BAMLH0A0HYM2")
      api_key    : FRED 무료 API 키
      stale_days : 며칠 넘으면 '오래된 데이터'로 표시할지

    돌려주는 값 (딕셔너리)
      성공: {"value": 4.32, "date": "2026-06-02", "source": "fred",
             "stale": False, "missing": False}
      실패: {"value": None, "date": None, "source": "fred",
             "stale": False, "missing": True, "error": "사유"}
    """
    result = {
        "value": None,
        "date": None,
        "prev_value": None,   # 직전 유효값 (추세 판정용 — CPI/PCE/실업률)
        "source": "fred",
        "stale": False,
        "missing": True,
    }

    try:
        # 최신순(desc)으로 받아서, 값이 있는 첫 관측치를 고릅니다.
        # FRED 는 값이 없는 날을 "." 으로 표시하므로 그건 건너뜁니다.
        params = {
            "series_id": code,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",   # 최신 날짜부터
            "limit": 30,            # 최근 30개만 보면 충분 (휴일 연속 대비)
        }
        resp = requests.get(FRED_BASE_URL, params=params, timeout=20)
        resp.raise_for_status()    # HTTP 오류(401/404 등)면 여기서 예외 발생
        data = resp.json()

        observations = data.get("observations", [])
        if not observations:
            log.warning(f"[FRED:{code}] 관측치가 비어 있습니다.")
            result["error"] = "관측치 없음"
            return result

        # 값이 "." (결측)이 아닌 유효값들만 최신순으로 추립니다.
        #   valid[0] = 가장 최신값,  valid[1] = 그 직전값(추세 판정용)
        valid = []
        for obs in observations:
            raw = obs.get("value", ".")
            if raw != "." and raw != "":
                valid.append((float(raw), obs["date"]))
            if len(valid) >= 2:
                break  # 최신값 + 직전값 두 개면 충분

        if not valid:
            # 30개가 전부 "." 이면 결측
            log.warning(f"[FRED:{code}] 최근 값이 모두 결측(.)입니다.")
            result["error"] = "최근 값이 모두 결측"
            return result

        value, date_str = valid[0]
        prev_value = valid[1][0] if len(valid) >= 2 else None
        obs_date = util.parse_date(date_str)
        stale = util.is_stale(obs_date, stale_days)

        result.update({
            "value": value,
            "date": date_str,
            "prev_value": prev_value,
            "stale": stale,
            "missing": False,
        })
        if stale:
            log.warning(
                f"[FRED:{code}] 값 {value} ({date_str}) — "
                f"{stale_days}일보다 오래됨(오래된 데이터)"
            )
        else:
            log.info(f"[FRED:{code}] 값 {value} ({date_str}) 수집 성공")
        return result

    except requests.exceptions.HTTPError as e:
        # 키가 틀렸거나(401) 코드가 틀렸을(404) 때 주로 발생
        log.error(f"[FRED:{code}] HTTP 오류: {e}")
        result["error"] = f"HTTP 오류: {e}"
        return result
    except requests.exceptions.RequestException as e:
        # 인터넷 연결 문제, 시간 초과 등
        log.error(f"[FRED:{code}] 네트워크 오류: {e}")
        result["error"] = f"네트워크 오류: {e}"
        return result
    except (ValueError, KeyError) as e:
        # 받은 데이터 형식이 예상과 다를 때
        log.error(f"[FRED:{code}] 데이터 처리 오류: {e}")
        result["error"] = f"데이터 처리 오류: {e}"
        return result


def fetch_yahoo(code: str, stale_days: int = 5, retries: int = 3) -> dict:
    """
    Yahoo Finance 에서 시세를 받아옵니다 (HYG/KRE 추세, WTI 폴백용).

    - 실패하면 잠깐 기다렸다 다시 시도(재시도 + 지수 백오프).
    - 모두 실패해도 프로그램이 죽지 않고 '결측(missing)'으로 돌려줍니다.
    - 20일 이동평균선(ma20)·하루 변동률(change_1d)도 함께 계산(HYG/KRE 추세 판정용).

    돌려주는 값 예:
      {"value": 79.68, "date": "2026-06-03", "prev_value": 79.90,
       "ma20": 79.5, "change_1d": -0.0027, "source": "yahoo",
       "stale": False, "missing": False}
    """
    result = {
        "value": None, "date": None, "prev_value": None,
        "ma20": None, "change_1d": None,
        "source": "yahoo", "stale": False, "missing": True,
    }

    # yfinance 는 curl_cffi 기반이라 기본적으로 브라우저를 흉내 내 UA 를 보냅니다.
    # 추가로 UA 를 명시 시도하되, 호환이 안 되면 기본 방식으로 자동 대체합니다.
    import yfinance as yf

    for attempt in range(retries):
        try:
            try:
                session = requests.Session()
                session.headers["User-Agent"] = YAHOO_UA
                hist = yf.Ticker(code, session=session).history(
                    period="3mo", auto_adjust=True
                )
            except Exception:
                # 세션 주입이 안 되는 버전이면 기본 방식으로
                hist = yf.Ticker(code).history(period="3mo", auto_adjust=True)

            closes = hist["Close"].dropna() if not hist.empty else hist
            if hist.empty or len(closes) == 0:
                raise ValueError("빈 데이터(차단 가능성)")

            value = round(float(closes.iloc[-1]), 2)
            date_str = closes.index[-1].strftime("%Y-%m-%d")
            prev_value = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else None
            ma20 = round(float(closes.tail(20).mean()), 2) if len(closes) >= 20 else None
            change_1d = (
                round(value / prev_value - 1.0, 4) if prev_value not in (None, 0) else None
            )
            stale = util.is_stale(util.parse_date(date_str), stale_days)

            result.update({
                "value": value, "date": date_str, "prev_value": prev_value,
                "ma20": ma20, "change_1d": change_1d,
                "stale": stale, "missing": False,
            })
            mark = " ⚠️오래됨" if stale else ""
            log.info(f"[Yahoo:{code}] 값 {value:.2f} ({date_str}){mark} 수집 성공")
            return result

        except Exception as e:
            wait = 2 ** attempt  # 1초 → 2초 → 4초 (지수 백오프)
            if attempt < retries - 1:
                log.warning(
                    f"[Yahoo:{code}] 시도 {attempt+1}/{retries} 실패: {e} "
                    f"→ {wait}초 후 재시도"
                )
                time.sleep(wait)
            else:
                log.error(f"[Yahoo:{code}] {retries}회 모두 실패: {e} → 결측 처리")
                result["error"] = str(e)

    return result


def collect_all(api_key: str) -> list:
    """
    config.INDICATORS 표 전체를 수집합니다 (FRED + Yahoo + WTI 폴백).

    동작:
      - source == "fred"  : fetch_fred 로 수집.
                            fallback 이 있고(WTI) FRED 값이 결측이거나 오래됐으면
                            Yahoo 폴백(CL=F)을 시도해 신선하면 그걸 채택.
      - source == "yahoo" : fetch_yahoo 로 수집(HYG/KRE).
    소스별 독립 처리라, 한 지표 실패가 다른 지표 결과를 망치지 않습니다.
    """
    results = []
    for ind in config.INDICATORS:
        if ind["source"] == "fred":
            r = fetch_fred(ind["code"], api_key, stale_days=ind["stale_days"])

            # WTI 폴백: FRED 가 결측이거나 오래됐으면 Yahoo CL=F 시도
            fb = ind.get("fallback")
            if fb and fb.get("source") == "yahoo" and (r["missing"] or r["stale"]):
                log.info(f"[{ind['name']}] FRED 값이 부실 → Yahoo 폴백 시도")
                y = fetch_yahoo(fb["code"], stale_days=ind["stale_days"])
                if not y["missing"] and not y["stale"]:
                    log.info(f"[{ind['name']}] Yahoo 폴백 채택")
                    r = {**y, "used_fallback": True}

        elif ind["source"] == "yahoo":
            r = fetch_yahoo(ind["code"], stale_days=ind["stale_days"])

        else:
            log.warning(f"[{ind['name']}] 알 수 없는 source: {ind['source']}")
            r = {"value": None, "date": None, "missing": True, "source": ind["source"]}

        results.append({**ind, **r, "skipped": False})
    return results


def collect_fred_indicators(api_key: str) -> list:
    """
    config.INDICATORS 표를 읽어, source 가 'fred' 인 지표를 전부 수집합니다.
    (Yahoo 지표 HYG/KRE 와 WTI 폴백은 8단계에서 추가)

    돌려주는 값: 지표별 결과 딕셔너리들의 리스트.
      각 항목 = config 메타데이터(name/tier/unit/code...) +
                수집 결과(value/date/stale/missing) 를 합친 것.
    """
    results = []
    for ind in config.INDICATORS:
        if ind["source"] != "fred":
            # Yahoo 지표는 이번 단계에서 건너뜀(8단계 예정). 자리만 표시.
            results.append({
                **ind,
                "value": None,
                "date": None,
                "stale": False,
                "missing": True,
                "skipped": True,   # '아직 미구현'이라 결측과 구분
            })
            continue

        r = fetch_fred(ind["code"], api_key, stale_days=ind["stale_days"])
        results.append({**ind, **r, "skipped": False})
    return results


def print_indicator_table(results: list) -> None:
    """수집 결과를 사람이 보기 좋은 표로 출력합니다."""
    log.info("=" * 64)
    log.info(f"{'지표':<18}{'Tier':<6}{'값':<12}{'기준일':<13}{'상태'}")
    log.info("-" * 64)
    for r in results:
        name = r["name"]
        tier = f"T{r['tier']}"
        if r.get("skipped"):
            value, date, status = "-", "-", "⏭️ 8단계예정(Yahoo)"
        elif r["missing"]:
            value, date, status = "-", "-", "❌ 결측"
        else:
            unit = r.get("unit", "")
            value = f"{r['value']}{unit}"
            date = r["date"]
            status = "⚠️ 오래됨" if r["stale"] else "✅ 정상"
        # 한글 폭 보정을 위해 단순 정렬 (완벽하진 않아도 읽기 충분)
        log.info(f"{name:<16}  {tier:<5} {str(value):<11} {str(date):<12} {status}")
    log.info("=" * 64)


# ─────────────────────────────────────────────────────────────
# 이 파일을 직접 실행하면 FRED 지표 전부를 수집해 표로 보여줍니다.
#   python fetch.py
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv  # .env 파일에서 키를 읽어오는 도구

    load_dotenv()  # 같은 폴더의 .env 파일을 읽어 환경변수로 올림
    api_key = os.getenv("FRED_API_KEY")

    if not api_key or api_key == "여기에_키_붙여넣기":
        log.error("FRED_API_KEY 가 설정되지 않았습니다!")
        log.error(".env 파일을 만들고 FRED_API_KEY=발급받은키 를 적어주세요.")
    else:
        log.info("=== 3단계: FRED 지표 전체 수집 ===")
        results = collect_fred_indicators(api_key)
        print_indicator_table(results)

        # 요약: 성공/오래됨/결측 개수
        fred_results = [r for r in results if not r.get("skipped")]
        ok = sum(1 for r in fred_results if not r["missing"] and not r["stale"])
        stale = sum(1 for r in fred_results if not r["missing"] and r["stale"])
        missing = sum(1 for r in fred_results if r["missing"])
        log.info(f"FRED 수집 결과 — 정상 {ok} / 오래됨 {stale} / 결측 {missing}")
        if missing == 0:
            log.info("3단계 수집 성공! ✅ (FRED 지표 전부 수신)")
        else:
            log.warning("일부 지표가 결측입니다. 위 표의 ❌ 항목을 확인하세요.")
