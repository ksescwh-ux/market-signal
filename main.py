"""
main.py — 메인 실행 파일
================================================================
이 파일 하나만 실행하면 전체 흐름이 돕니다:
    수집(fetch) → 판정(signals) → 저장(storage)
앞으로 단계가 진행되며 알림(notify)·대시보드 갱신이 여기에 추가됩니다.

실행:
    python main.py

[면책] 이 시스템은 투자 판단을 돕는 보조 모니터링 도구이며,
       투자 조언이나 매매 신호가 아닙니다. 모든 결정과 책임은 사용자 본인에게 있습니다.
"""

import os
import sys
from dotenv import load_dotenv

import util
import fetch
import signals
import storage
import notify
import insight
import econ_calendar

log = util.get_logger()


def run() -> int:
    """전체 모니터링 1회 실행. 성공 0, 실패 1 을 돌려줍니다."""
    load_dotenv()  # .env 의 키를 환경변수로 올림
    api_key = os.getenv("FRED_API_KEY")
    if not api_key or api_key == "여기에_키_붙여넣기":
        log.error("FRED_API_KEY 가 없습니다. .env 파일을 확인하세요.")
        return 1

    run_dt = util.now_kst()  # 실행 시각(한국시간)
    log.info("=" * 60)
    log.info(f"시장 신호등 모니터링 실행 — {util.fmt_kst(run_dt)}")
    log.info("=" * 60)

    # 1) 수집 (FRED + Yahoo + WTI 폴백)
    results = fetch.collect_all(api_key)

    # 2) 판정 (어제 상태를 읽어 '거짓경보 감소·방향'에 활용)
    prev = storage.get_previous_state(run_dt.strftime("%Y-%m-%d"))
    judged = signals.judge_all(results)
    composite = signals.compute_composite(judged, prev)

    # 2-1) 인사이트 엔진: 위험점수·주도지표·과거대비·자동해설 등을 계산해 붙임
    composite["analysis"] = insight.build_analysis(judged, composite)

    # 2-2) 다가오는 경제지표 발표 일정(한국시간). 실패해도 전체엔 영향 없음.
    try:
        composite["calendar"] = econ_calendar.upcoming_releases(api_key)
    except Exception as e:
        log.warning(f"경제 캘린더 조회 실패(무시): {e}")
        composite["calendar"] = []

    # 3) 콘솔 출력
    signals.print_signals(judged, composite)

    # 4) 저장 (CSV + latest.json)
    storage.save_all(judged, composite, run_dt)

    # 5) 알림 (위험/경계/보류면 발송, 평상시면 heartbeat 주기에만)
    notify.notify(composite, judged, run_dt)

    log.info("실행 완료 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(run())
