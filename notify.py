"""
notify.py — 알림 발송 (슬랙 / 텔레그램)
================================================================
[하는 일]
  1) 위험 알림 : 종합 판정이 위험하면 메시지를 만들어 보냄
  2) heartbeat : "시스템 정상 작동 중" 통지 (조용히 죽는 것 방지)
  3) 실패 알림 : main 실행 자체가 실패하면 별도 에러 메시지 (6단계 워크플로우에서 사용)

[알림 발송 정책]
  🔴최악 / 🟠위험 / ⚪판정보류 → 즉시 발송
  🟡경계                    → 발송 (cron 이 하루 1회라 자연히 '하루 1회')
  🟢평상시                  → 발송 안 함. 단 heartbeat 주기면 정상작동 통지만.

어디로 보낼지는 config.NOTIFY_PROVIDER ("slack"|"telegram"|"both"|"off") 로 결정.
토큰·주소는 .env / GitHub Secrets 에서 읽습니다 (코드에 직접 안 씀).
"""

import os

import requests

import config
import util

log = util.get_logger()

# 신호등 색 → 이모지
_EMOJI = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴", None: "⚪"}


# ─────────────────────────────────────────────────────────────
# 1. 실제 전송 (소스별)
# ─────────────────────────────────────────────────────────────
def send_slack(text: str, webhook_url: str) -> bool:
    """슬랙 Incoming Webhook 으로 메시지 전송. 성공 True."""
    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=15)
        resp.raise_for_status()
        log.info("슬랙 메시지 전송 성공")
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"슬랙 전송 실패: {e}")
        return False


def send_telegram(text: str, token: str, chat_id: str) -> bool:
    """텔레그램 봇으로 메시지 전송. 성공 True."""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
        log.info("텔레그램 메시지 전송 성공")
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"텔레그램 전송 실패: {e}")
        return False


def send(text: str) -> bool:
    """
    config.NOTIFY_PROVIDER 설정에 따라 알맞은 곳으로 메시지를 보냅니다.
    필요한 키는 .env / 환경변수에서 읽습니다. 하나라도 성공하면 True.
    """
    provider = config.NOTIFY_PROVIDER
    if provider == "off":
        log.info("알림이 'off' 로 설정되어 전송하지 않습니다.")
        return True

    ok = False
    if provider in ("slack", "both"):
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if webhook and webhook != "여기에_슬랙_웹훅주소_붙여넣기":
            ok = send_slack(text, webhook) or ok
        else:
            log.error("SLACK_WEBHOOK_URL 이 없습니다. .env 를 확인하세요.")

    if provider in ("telegram", "both"):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if token and chat_id:
            ok = send_telegram(text, token, chat_id) or ok
        else:
            log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없습니다.")

    return ok


# ─────────────────────────────────────────────────────────────
# 2. 메시지 만들기
# ─────────────────────────────────────────────────────────────
def _threshold_note(ind: dict) -> str:
    """점등 지표의 '기준값' 안내 문구. 예: (기준 6.0)"""
    t = ind.get("thresholds", {})
    sig = ind["signal"]
    if ind["direction"] == "high_bad" and sig in t:
        return f" (기준 {t[sig]})"
    if ind["direction"] == "low_bad" and sig == "red":
        return f" (역전 기준 {t.get('red', 0)})"
    return ""


def build_alert_message(composite: dict, judged: list, run_dt) -> str:
    """위험/경계/보류 상황의 알림 메시지(여러 줄 글자)를 만듭니다."""
    c = composite
    lines = []
    # 머리줄: 이모지 + 제목 + 데이터 기준일
    data_date = max(
        (r["date"] for r in judged if not r.get("skipped") and r.get("date")),
        default="-",
    )
    lines.append(f"{c['emoji']} {c['title']} (데이터 기준: {data_date})")

    # ★ AI 브리핑을 머리글로 (지표+뉴스를 엮은 한 단락). 엔진 폴백이어도 사용.
    brief = (c.get("analysis", {}) or {}).get("commentary", {}).get("text", "")
    if brief:
        lines.append("")
        lines.append(brief)

    # 방향(어제 대비) + 확신도 한 줄
    extra = []
    d = c.get("direction", {})
    if d.get("arrow"):
        extra.append(f"{d['arrow']} {d['text']}")
    if c.get("confidence") and c["confidence"] != "-":
        extra.append(f"확신도: {c['confidence']}")
    if extra:
        lines.append(" · ".join(extra))

    # 점등 지표 (orange/red). judged 에서 thresholds 를 찾아 기준값도 표기.
    judged_by_name = {r["name"]: r for r in judged}
    if c["lit"]:
        lines.append("")
        lines.append("[점등 지표]")
        for item in c["lit"]:
            ind = judged_by_name.get(item["name"], {})
            emoji = _EMOJI.get(item["signal"], "⚪")
            note = _threshold_note(ind) if ind else ""
            lines.append(
                f"{emoji} {item['name']}: {item['value']}{item['unit']}{note}"
            )

    # 보조경고 (yellow 누적 등)
    for w in c["warnings"]:
        lines.append(w)

    # 행동 가이드
    lines.append("")
    lines.append("[행동]")
    for a in c["actions"]:
        lines.append(f"• {a}")

    # 수동 확인 필요
    if config.MANUAL_CHECK_ITEMS:
        lines.append("")
        lines.append("[수동 확인 필요]")
        for m in config.MANUAL_CHECK_ITEMS:
            lines.append(f"• {m}")

    # 대시보드 링크 (있으면)
    if config.DASHBOARD_URL:
        lines.append("")
        lines.append(f"[대시보드] {config.DASHBOARD_URL}")

    return "\n".join(lines)


def build_heartbeat_message(composite: dict, run_dt) -> str:
    """정상 작동 통지(heartbeat) 한 줄 메시지."""
    return (
        f"✅ 시스템 정상 작동 중 — 현재 {composite['emoji']} {composite['title']}  "
        f"({util.fmt_kst(run_dt)})"
    )


# ─────────────────────────────────────────────────────────────
# 3. 발송 여부 판단 + 실행
# ─────────────────────────────────────────────────────────────
def _should_send_heartbeat(run_dt) -> bool:
    """HEARTBEAT_MODE 에 따라 이번 실행에서 heartbeat 를 보낼지 결정."""
    mode = config.HEARTBEAT_MODE
    if mode == "off":
        return False
    if mode == "daily":
        return True
    if mode == "weekly":
        # 지정 요일(기본 월요일)에만 발송
        return run_dt.weekday() == config.HEARTBEAT_WEEKDAY
    return False


def notify(composite: dict, judged: list, run_dt) -> None:
    """
    종합 판정에 따라 알림을 보냅니다(정책은 파일 상단 설명 참조).
    이 함수는 main.py 가 호출합니다.
    """
    grade = composite["grade"]

    # 위험/경계/보류 → 알림 메시지 발송
    if grade in ("red", "orange", "yellow", "gray"):
        msg = build_alert_message(composite, judged, run_dt)
        log.info(f"[{grade}] 알림 발송 시도")
        send(msg)
        return

    # 평상시(green) → heartbeat 주기면 정상작동 통지만
    if grade == "green":
        if _should_send_heartbeat(run_dt):
            log.info("heartbeat 발송 시도")
            send(build_heartbeat_message(composite, run_dt))
        else:
            log.info("평상시(green) + heartbeat 주기 아님 → 알림 없음")


def send_failure_alert(detail: str = "") -> bool:
    """
    실행 자체가 실패했을 때 보내는 에러 알림.
    (6단계 GitHub Actions 의 'if: failure()' 스텝에서 호출)
    """
    msg = (
        "🚨 시장 신호등 모니터링 실행 실패 — Actions 로그 확인 필요\n"
        f"시각: {util.fmt_kst(util.now_kst())}"
    )
    if detail:
        msg += f"\n사유: {detail}"
    return send(msg)


# ─────────────────────────────────────────────────────────────
# 4. 직접 실행하면 '테스트 메시지' 1회 발송
#    python notify.py
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    log.info(f"=== 7단계: 알림 테스트 발송 (provider={config.NOTIFY_PROVIDER}) ===")
    test_msg = (
        "🚦 시장 신호등 시스템 — 테스트 메시지입니다.\n"
        f"이 메시지가 보이면 알림 연결 성공! ✅ ({util.fmt_kst(util.now_kst())})"
    )
    if send(test_msg):
        log.info("테스트 메시지 발송 성공! 메신저를 확인하세요. ✅")
    else:
        log.error("테스트 메시지 발송 실패. 위 오류와 .env 설정을 확인하세요.")
