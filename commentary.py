"""
commentary.py — AI 애널리스트 (Claude API)
================================================================
하루 1번 호출로 세 가지를 한 번에 만듭니다(비용 절약):
  1) briefing  : 오늘의 지표 + 오늘의 뉴스를 '연결'한 데일리 브리핑
  2) news      : 헤드라인 한국어 번역 + 한 줄 요약 + 주제 + 중요도(큐레이션)
  (3) 슬랙 위험 알림은 이 briefing 을 재활용 — notify.py 가 머리글로 사용)

[견고함] ANTHROPIC_API_KEY 가 없거나 호출이 실패하면 insight 의 자동 해설(narrative)과
         영어 헤드라인으로 자동 폴백 → 시스템은 절대 안 깨짐.

[가드레일] 제공된 숫자·헤드라인만 사용, 예측·매매조언 금지.
"""

import os
import json

import util

log = util.get_logger()

# AI 모델 — 비용 절감을 위해 Haiku 사용 (사용자 지정). Opus 미사용.
# 바꾸려면 이 한 줄만 수정: claude-haiku-4-5 / claude-sonnet-4-6 / claude-opus-4-8
MODEL = "claude-haiku-4-5"

DAILY_SYSTEM = """\
당신은 한국 개인투자자(주린이)를 위한 시장 애널리스트입니다.
아래 [지표 데이터]와 [오늘의 뉴스 헤드라인]을 함께 보고 두 가지를 만드세요.

1) briefing — '인사이트 있는' 데일리 브리핑(4~6문장). 단순 요약·나열은 금지.
   반드시 아래 셋을 엮으세요(이게 핵심입니다):
   * 연결(메커니즘): 오늘 뉴스가 우리가 추적하는 지표(유가·VIX·하이일드 스프레드·금리·물가 등)
     중 '무엇'을 '어떤 경로'로 건드리는지. (예: 중동 이슈 → 유가 → 물가·기업비용)
   * 민감도: 그 지표가 지금 위험선에 가깝거나(제공된 '위험선_근접'), 역사적으로 높은
     위치인지(제공된 '백분위')를 보고 '지금 특히 민감/둔감한지'.
   * 지켜볼 트리거: 무엇이 더 진행되면 신호가 악화될지 한 가지.
   가장 중요한 것부터. 어려운 용어는 괄호로 짧게. (단순히 "노란불 몇 개"만 세지 마세요.)

2) news — 각 헤드라인마다:
   * ko_title : 자연스러운 한국어 번역(신문 제목처럼 간결).
   * summary  : 무슨 얘기인지 한 문장으로 쉽게.
   * theme    : 다음 중 하나 — "Fed", "지정학", "실적", "신용", "거시", "기타".
   * important: 시장 위험 관점에서 진짜 중요하면 true, 단순 가십·개별 종목이면 false.

[반드시 지킬 규칙]
- 오직 제공된 숫자와 헤드라인 내용만 사용. 없는 사실·수치·배경을 지어내지 마세요.
- 미래 예측("오를 것/내릴 것")과 매매 조언("사라/팔아라/비중") 금지.
- 존댓말(~예요/~이에요), 쉽고 담백하게. briefing 에 머리말·면책문구를 넣지 마세요.
- news 는 입력과 '같은 개수, 같은 순서'로 돌려주세요.
"""

DAILY_SCHEMA = {
    "type": "object",
    "properties": {
        "briefing": {"type": "string"},
        "news": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ko_title": {"type": "string"},
                    "summary": {"type": "string"},
                    "theme": {"type": "string"},
                    "important": {"type": "boolean"},
                },
                "required": ["ko_title", "summary", "theme", "important"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["briefing", "news"],
    "additionalProperties": False,
}


def _build_facts(analysis: dict, judged: list) -> str:
    """모델에 넘길 '지표 사실 묶음'. (제공된 숫자 외엔 못 쓰게)"""
    indicators = [
        {
            "지표": r["name"],
            "값": (None if r.get("missing") else r.get("value")),
            "단위": r.get("unit", ""),
            "신호등": r.get("signal"),
            "tier": r.get("tier"),
        }
        for r in judged if not r.get("skipped")
    ]
    facts = {
        "위험점수_0to100": analysis.get("risk_score"),
        "점수_등급": analysis.get("score_band"),
        "점수_과거1년_백분위": analysis.get("score_pct"),
        "점수_과거1년_평균": analysis.get("score_avg"),
        "점수_5거래일_변화": analysis.get("score_delta_5d"),
        "주도지표": analysis.get("drivers"),
        "Tier별_상태": analysis.get("tier_reads"),
        "층위_괴리_있음": analysis.get("divergence"),
        "위험선_근접": analysis.get("proximity"),
        "핵심지표_과거1년_백분위": analysis.get("percentile"),
        "평상시_연속일수": analysis.get("streak_calm"),
        "마지막_위험_이후_일수": analysis.get("days_since_danger"),
        "지표_스냅샷": indicators,
    }
    return "[지표 데이터]\n" + json.dumps(facts, ensure_ascii=False, indent=2)


def ai_daily(analysis: dict, judged: list, news_list: list) -> dict:
    """
    데일리 브리핑 + 뉴스 번역·큐레이션을 한 번의 호출로 생성.
    돌려주는 값: {"source": "ai"|"engine", "briefing": str, "news": [...]}
      - 키 없음/실패 시 engine(자동 해설) + 영어 헤드라인으로 폴백.
    """
    fallback_brief = " ".join(analysis.get("narrative", [])) or "오늘의 해설을 준비하지 못했어요."
    fallback = {"source": "engine", "briefing": fallback_brief, "news": news_list}

    if not os.getenv("ANTHROPIC_API_KEY"):
        log.info("ANTHROPIC_API_KEY 없음 → 엔진 해설 + 영어 헤드라인 사용")
        return fallback

    try:
        import anthropic

        client = anthropic.Anthropic()
        headlines = "\n".join(f"{i+1}. {n['title']}" for i, n in enumerate(news_list))
        user = _build_facts(analysis, judged) + "\n\n[오늘의 뉴스 헤드라인]\n" + (headlines or "(없음)")

        resp = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=[{"type": "text", "text": DAILY_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": DAILY_SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)

        # 뉴스 번역 결과를 원본(링크·출처·시각·위험표시)에 합치기
        merged = []
        for n, it in zip(news_list, data.get("news", [])):
            m = dict(n)
            m["ko_title"] = it.get("ko_title")
            m["summary"] = it.get("summary")
            m["theme"] = it.get("theme", "기타")
            m["important"] = bool(it.get("important"))
            merged.append(m)
        # 모델이 개수를 덜 돌려줬으면 나머지는 원본 유지
        if len(merged) < len(news_list):
            merged += news_list[len(merged):]

        u = resp.usage
        log.info(
            f"AI 데일리 생성 성공 (입력 {u.input_tokens} / 출력 {u.output_tokens} "
            f"/ 캐시읽기 {getattr(u, 'cache_read_input_tokens', 0)})"
        )
        return {"source": "ai", "briefing": data.get("briefing") or fallback_brief, "news": merged}

    except Exception as e:
        log.warning(f"AI 데일리 실패({e}) → 엔진 해설 + 영어 헤드라인으로 폴백")
        return fallback


# ── 지정학·정책 브리핑 (웹 검색) ───────────────────────────────
GEO_SYSTEM = """\
당신은 지정학·정책 리스크를 한국 개인투자자에게 '인사이트' 있게 풀어주는 애널리스트입니다.

먼저 web_search 로 '최근(오늘~며칠)' 시장에 영향 줄 만한 이슈를 찾으세요:
트럼프 발언·정책 톤, 이란·이스라엘·중동 정세, 관세·제재·수출통제 등.

그다음 아래 형식의 한국어 '참고 브리핑'을 쓰세요.

중간 설명("검색하겠습니다" 등) 없이, 곧바로 '톤:'으로 시작하는 브리핑만 출력하세요.

첫 줄: 톤: <고조|완화|혼재|특이사항 없음>
본문(3~5문장):
  - 핵심 이슈 1~2개를 짧게.
  - ★연결(메커니즘): 그 이슈가 우리가 추적하는 지표(유가 WTI, VIX, 하이일드 스프레드, 미 10년물 금리)
    중 무엇을 어떤 경로로 건드리는지.
  - ★민감도: 아래 '현재 지표 상황'을 보고 그 지표가 지금 특히 민감한지(예: 유가가 위험선 코앞).
  - ★지켜볼 트리거: 무엇이 더 진행되면 위험이 커질지 한 가지.
마지막 줄들: 출처: 제목 - URL  (2~3개)

[규칙]
- 검색으로 확인된 내용만 쓰세요. 별일 없으면 솔직히 '특이사항 없음'.
- 예측("오를 것")·매매조언("사라/팔아라") 금지. 정성 판단이라 '참고용'임을 의식해 단정 회피.
- 존댓말, 쉽게.
"""


def _geo_snapshot(judged: list) -> str:
    """지정학 브리핑이 '민감도' 판단에 쓸 현재 지표 상황 요약."""
    want = {"WTI 유가", "VIX 공포지수", "하이일드 스프레드", "미 10년물 금리"}
    rows = [
        f"- {r['name']}: {r['value']}{r.get('unit','')} ({r['signal']})"
        for r in judged if r["name"] in want and not r.get("missing")
    ]
    return "현재 지표 상황:\n" + "\n".join(rows)


def geo_brief(judged: list) -> dict:
    """
    웹 검색으로 지정학/정책 이슈를 찾아 '우리 지표와 엮은' 참고 브리핑을 생성.
    돌려주는 값: {"tone": str, "text": str} (출처 포함). 키 없음/실패면 None → 정적 '수동 확인' 유지.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        client = anthropic.Anthropic()
        user = (
            "오늘 시장에 영향 줄 지정학/정책 이슈를 웹에서 찾아, 아래 우리 지표 상황과 엮어 "
            "한국어 참고 브리핑을 써줘.\n\n" + _geo_snapshot(judged)
        )
        messages = [{"role": "user", "content": user}]
        resp = None
        for _ in range(4):  # 웹검색 서버툴이 pause_turn 내면 이어서 재개
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1600,
                # allowed_callers=["direct"] : Haiku 등 PTC 미지원 모델에서도 웹검색 쓰게
                tools=[{"type": "web_search_20260209", "name": "web_search",
                        "allowed_callers": ["direct"]}],
                system=[{"type": "text", "text": GEO_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break

        full = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        if not full:
            return None

        # 최종 브리핑만 추출: 마지막 '톤:' 이후 + 마크다운 군더더기 제거
        idx = full.rfind("톤:")
        if idx != -1:
            full = full[idx:]
        full = full.replace("**", "").replace("---", "").strip()

        # 첫 줄에서 톤 분리
        tone = "혼재"
        lines = [ln for ln in full.splitlines()]
        if lines and "톤:" in lines[0]:
            tone = lines[0].split("톤:", 1)[1].strip()
            full = "\n".join(lines[1:]).strip()
        log.info(f"지정학 브리핑 생성 성공 (톤: {tone})")
        return {"tone": tone, "text": full}

    except Exception as e:
        log.warning(f"지정학 브리핑 실패({e}) → 정적 '수동 확인' 유지")
        return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    import fetch, signals, storage, insight, news

    load_dotenv()
    k = os.getenv("FRED_API_KEY")
    run_dt = util.now_kst()
    prev = storage.get_previous_state(run_dt.strftime("%Y-%m-%d"))
    r = fetch.collect_all(k)
    j = signals.judge_all(r)
    c = signals.compute_composite(j, prev)
    a = insight.build_analysis(j, c)
    out = ai_daily(a, j, news.fetch_news())
    log.info(f"=== 데일리 브리핑 (source={out['source']}) ===")
    log.info(out["briefing"])
    log.info("=== 큐레이션된 뉴스(상위 3) ===")
    for n in out["news"][:3]:
        star = "★" if n.get("important") else " "
        log.info(f"{star}[{n.get('theme','')}] {n.get('ko_title') or n['title']}")
