"""
util.py — 공통 유틸리티 (시간대 처리 + 로깅 + 신선도 검증)
================================================================
이 파일은 시스템 전체가 공통으로 쓰는 '도구 상자'입니다.

왜 따로 빼냐면:
  - 시간(날짜) 처리를 여러 곳에서 제각각 하면 버그가 잘 납니다.
    그래서 '시간 관련 일'은 전부 여기 한 곳에 모읍니다.
  - 모든 시각에는 반드시 '시간대(타임존)'를 붙입니다.
    (예: 그냥 "오후 3시"가 아니라 "한국시간 오후 3시")
    시간대 없는 시각(=naive datetime)은 날짜 비교 버그의 원흉이라 금지합니다.

[면책] 이 시스템은 투자 판단을 돕는 보조 모니터링 도구이며,
       투자 조언이나 매매 신호가 아닙니다. 모든 결정과 책임은 사용자 본인에게 있습니다.
"""

import sys
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo  # 파이썬 표준 시간대 도구 (3.9+ 내장)


def _force_utf8_console() -> None:
    """
    윈도우 터미널은 기본 글자코드가 한글에 안 맞아 출력이 깨질 때가 있습니다.
    화면 출력을 UTF-8 로 강제해 한글이 안 깨지게 합니다. (한 번만 실행)
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # 파이썬 3.7+ 에서 지원
        except (AttributeError, ValueError):
            pass  # 환경에 따라 불가능하면 조용히 넘어감 (동작에는 지장 없음)

# ─────────────────────────────────────────────────────────────
# 1. 시간대(타임존) 정의
#    KST = 한국 표준시,  ET = 미국 동부시(뉴욕 증시),  UTC = 세계 표준시
# ─────────────────────────────────────────────────────────────
KST = ZoneInfo("Asia/Seoul")        # 한국시간
ET = ZoneInfo("America/New_York")   # 미국 동부시 (뉴욕 증시 기준)
UTC = timezone.utc                  # 세계 표준시 (GitHub Actions가 쓰는 기준)


# ─────────────────────────────────────────────────────────────
# 2. "지금 몇 시?" — 시간대가 붙은 현재 시각을 돌려주는 함수들
# ─────────────────────────────────────────────────────────────
def now_kst() -> datetime:
    """현재 시각을 '한국시간'으로 돌려줍니다."""
    return datetime.now(KST)


def now_et() -> datetime:
    """현재 시각을 '미국 동부시'로 돌려줍니다."""
    return datetime.now(ET)


def now_utc() -> datetime:
    """현재 시각을 '세계 표준시(UTC)'로 돌려줍니다."""
    return datetime.now(UTC)


# ─────────────────────────────────────────────────────────────
# 3. 시각을 보기 좋은 글자로 바꾸기 (사람이 읽기 쉽게)
# ─────────────────────────────────────────────────────────────
def fmt_kst(dt: datetime) -> str:
    """어떤 시각이든 '한국시간 글자'로 바꿔줍니다. 예: 2026-06-04 17:30 KST"""
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


# ─────────────────────────────────────────────────────────────
# 4. 신선도(freshness) 검증
#    "이 데이터가 며칠이나 묵은 것인가?"를 계산합니다.
#    예) FRED 일별 지표가 5일 넘게 안 바뀌었으면 '오래된 데이터'로 표시.
# ─────────────────────────────────────────────────────────────
def days_old(observation_date: datetime) -> int:
    """
    데이터의 관측 날짜가 '오늘로부터 며칠 전'인지 정수로 돌려줍니다.
    observation_date 는 반드시 시간대가 붙은 datetime 이어야 합니다.
    """
    if observation_date.tzinfo is None:
        # 시간대가 없으면 비교가 위험하므로 막습니다.
        raise ValueError("observation_date 에 시간대(tzinfo)가 없습니다. naive datetime 금지!")
    delta = now_utc() - observation_date.astimezone(UTC)
    return delta.days


def is_stale(observation_date: datetime, stale_days: int) -> bool:
    """
    데이터가 'stale_days' 일보다 오래됐으면 True(오래됨), 아니면 False.
    예) is_stale(관측날짜, 5)  →  5일 넘으면 True
    """
    return days_old(observation_date) > stale_days


def parse_date(date_str: str) -> datetime:
    """
    'YYYY-MM-DD' 형태의 날짜 글자를 시간대(UTC) 붙은 datetime 으로 바꿉니다.
    FRED 등이 돌려주는 날짜 문자열을 안전하게 다루기 위한 함수입니다.
    """
    naive = datetime.strptime(date_str, "%Y-%m-%d")
    # 날짜만 있는 값은 '그 날 자정 UTC'로 간주합니다.
    return naive.replace(tzinfo=UTC)


# ─────────────────────────────────────────────────────────────
# 5. 로깅(logging) 설정
#    print 대신 logging 을 쓰는 이유:
#      - 언제·어느 단계에서 무슨 일이 있었는지 시각과 함께 기록됩니다.
#      - GitHub Actions 로그에서 '어디서 실패했는지' 추적이 쉬워집니다.
# ─────────────────────────────────────────────────────────────
def get_logger(name: str = "market-signal") -> logging.Logger:
    """
    이 함수로 받은 logger 로 log.info(...) / log.error(...) 를 호출하면
    [시각] [수준] 메시지  형태로 깔끔하게 출력됩니다.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:  # 중복 설정 방지 (한 번만 세팅)
        _force_utf8_console()  # 한글 깨짐 방지
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ─────────────────────────────────────────────────────────────
# 6. 이 파일을 직접 실행했을 때 동작 확인용 (python util.py)
#    1단계 테스트에서 사용합니다.
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log = get_logger()
    log.info("util.py 동작 확인을 시작합니다.")
    log.info(f"현재 한국시간 : {fmt_kst(now_kst())}")
    log.info(f"현재 미국동부시: {now_et().strftime('%Y-%m-%d %H:%M ET')}")
    log.info(f"현재 세계표준시: {now_utc().strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("util.py 정상 작동! ✅")
