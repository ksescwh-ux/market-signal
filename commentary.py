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

MODEL = "claude-opus-4-8"   # 하루 1회라 비용 미미

DAILY_SYSTEM = """\
당신은 한국 개인투자자(주린이)를 위한 시장 애널리스트입니다.
아래 [지표 데이터]와 [오늘의 뉴스 헤드라인]을 함께 보고 두 가지를 만드세요.

1) briefing — 오늘의 시장을 한 편의 브리핑으로(4~6문장).
   * 지표 상황과 뉴스를 '연결'해 설명하세요. (예: 어떤 지표가 노란불인데 관련 뉴스가 겹치는지,
     반대로 잠잠한 곳은 어디인지.)
   * 가장 중요한 것부터. 어려운 용어는 괄호로 짧게 풀어주세요.

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
