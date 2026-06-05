"""
commentary.py — AI 시황 코멘터리 (Claude API)
================================================================
insight 엔진이 계산한 '분석 재료'(위험점수·주도지표·과거대비 백분위·괴리 등)와
지표 스냅샷을 Claude 에 넘겨, 초보 투자자도 읽기 쉬운 한국어 시황 해설을 생성합니다.

[설계 원칙]
  - 사실 근거(grounding): 제공된 숫자만 쓰게 하고, 예측·매매조언을 금지(가드레일).
  - 비용: 하루 1회 호출. 시스템 프롬프트에 prompt caching 적용.
  - 견고함: ANTHROPIC_API_KEY 가 없거나 호출이 실패하면 자동으로
            insight 의 '자동 작문 해설'(narrative)로 폴백 → 시스템이 절대 안 깨짐.

[면책] 이 해설은 정보 제공이며 투자 조언이 아닙니다.
"""

import os
import json

import util

log = util.get_logger()

# 사용할 모델 (가장 똑똑한 모델. 하루 1회라 비용은 미미함)
MODEL = "claude-opus-4-8"

# 시스템 프롬프트 — '변하지 않는' 지시문. (prompt caching 대상)
SYSTEM_PROMPT = """\
당신은 거시경제·금융시장 데이터를 쉬운 말로 풀어주는 한국어 시장 해설가입니다.
독자는 '주린이'(초보 개인투자자)입니다. 아래 [데이터]만 근거로 3~5문장 해설을 쓰세요.

[반드시 지킬 규칙]
1. 오직 제공된 숫자만 사용하세요. 새로운 수치·뉴스·종목·사건을 절대 지어내지 마세요.
2. 미래 예측 금지: "오를 것", "내릴 것", "전망", "할 가능성이 높다" 같은 단정적 미래 단언을 쓰지 마세요.
3. 투자·매매 조언 금지: "사라/팔아라/현금 비중을 늘려라/지금이 기회" 같은 행동 지시를 쓰지 마세요.
4. 현재 상태를 묘사하세요: "~한 상태예요", "~에 가까워요", "역사적으로 ~한 편이에요" 처럼.
5. 월가 애널리스트 수준의 통찰(주도 요인, 층위 간 괴리, 과거 대비 위치, '한 끗' 근접)을
   일반인이 이해할 수 있는 쉬운 한국어로 풀어 쓰세요. 어려운 용어는 짧게 설명을 덧붙이세요.
6. 출력은 해설 본문만. 머리말("다음은…")·목록·과한 이모지·면책문구를 넣지 마세요.
   자연스러운 한 단락(또는 2단락)으로 쓰세요. 존댓말(~예요/~이에요)을 사용하세요.
"""


def _build_facts(analysis: dict, judged: list) -> str:
    """모델에 넘길 '사실 묶음'을 만든다. (제공된 숫자 외엔 못 쓰게)"""
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
    return (
        "[데이터]\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
        + "\n\n위 데이터만 근거로, 규칙을 지켜 3~5문장 한국어 시황 해설을 써주세요."
    )


def generate_commentary(analysis: dict, judged: list) -> dict:
    """
    AI 시황 해설을 생성합니다.
    돌려주는 값: {"text": "...", "source": "ai" | "engine"}
      - 키 없음/실패 시 engine(자동 작문 해설)로 폴백.
    """
    # 폴백 텍스트 = insight 엔진의 자동 작문 해설을 한 단락으로
    fallback = " ".join(analysis.get("narrative", [])) or "오늘의 해설을 준비하지 못했어요."

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.info("ANTHROPIC_API_KEY 없음 → 엔진 해설(자동 작문) 사용")
        return {"text": fallback, "source": "engine"}

    try:
        import anthropic

        client = anthropic.Anthropic()  # 환경변수 ANTHROPIC_API_KEY 자동 사용
        resp = client.messages.create(
            model=MODEL,
            max_tokens=700,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # 시스템 프롬프트 캐싱
            }],
            messages=[{"role": "user", "content": _build_facts(analysis, judged)}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        if not text:
            raise ValueError("빈 응답")

        # 캐시 적중 여부 로깅(참고용)
        u = resp.usage
        log.info(
            f"AI 코멘터리 생성 성공 (입력 {u.input_tokens} / 출력 {u.output_tokens} "
            f"/ 캐시읽기 {getattr(u, 'cache_read_input_tokens', 0)})"
        )
        return {"text": text, "source": "ai"}

    except Exception as e:
        log.warning(f"AI 코멘터리 실패({e}) → 엔진 해설로 폴백")
        return {"text": fallback, "source": "engine"}


if __name__ == "__main__":
    # 단독 실행 시: 오늘 데이터로 코멘터리 1회 생성해 출력
    from dotenv import load_dotenv
    import fetch, signals, storage, insight

    load_dotenv()
    key = os.getenv("FRED_API_KEY")
    run_dt = util.now_kst()
    prev = storage.get_previous_state(run_dt.strftime("%Y-%m-%d"))
    r = fetch.collect_all(key)
    j = signals.judge_all(r)
    c = signals.compute_composite(j, prev)
    a = insight.build_analysis(j, c)
    out = generate_commentary(a, j)
    log.info(f"=== 시황 코멘터리 (source={out['source']}) ===")
    log.info(out["text"])
