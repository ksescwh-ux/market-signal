"""
config.py — 설정 한곳 모음 (지표 메타데이터 + 임계값 + 옵션)
================================================================
[이 파일이 왜 중요한가]
지표를 추가/삭제하거나 위험 기준(임계값)을 바꾸고 싶을 때,
코드 여기저기를 뒤질 필요 없이 "이 파일 하나만" 고치면 됩니다.

각 지표 1개 = 아래 INDICATORS 리스트의 딕셔너리({...}) 1개.

[판정 방향 direction 의 뜻]
  - "high_bad"  : 숫자가 높을수록 위험 (예: VIX, 하이일드 스프레드)
  - "low_bad"   : 숫자가 낮을수록 위험 (예: 수익률곡선 2s10s — 마이너스가 위험)
  - "trend"     : 추세로 판단 (예: HYG/KRE 20일선, CPI/PCE 상승추세) — signals.py에서 처리

[thresholds 임계값의 뜻]
  high_bad 예) {"yellow":4.0, "orange":5.0, "red":6.0}
    → 4.0 미만이면 green, 4.0~5.0 yellow, 5.0~6.0 orange, 6.0 이상 red
  low_bad 예) {"green":0.2, "red":0.0}
    → 0.2 초과 green, 0~0.2 yellow, 0 미만(역전) red

[stale_days 신선도]
  데이터 관측 날짜가 이 일수보다 오래되면 '오래된 데이터'로 표시.
  (일별 FRED 지표는 5일, 월별 CPI/PCE는 45일 등)

[fallback 폴백]
  1순위 소스 실패 시 대신 쓸 소스. 없으면 None. (WTI만 사용)
"""

# ─────────────────────────────────────────────────────────────
# 지표 메타데이터 테이블 — 지표를 바꾸려면 여기만 수정!
# ─────────────────────────────────────────────────────────────
INDICATORS = [
    # ===== Tier 1 : 신용시장 (가장 중요, "왕 지표") =====
    {
        "name": "하이일드 스프레드",
        "source": "fred",
        "code": "BAMLH0A0HYM2",
        "tier": 1,
        "direction": "high_bad",
        "thresholds": {"yellow": 4.0, "orange": 5.0, "red": 6.0},
        "unit": "%p",
        "stale_days": 5,
        "fallback": None,
    },
    {
        "name": "투자등급 스프레드",
        "source": "fred",
        "code": "BAMLC0A0CM",
        "tier": 1,
        "direction": "high_bad",
        "thresholds": {"yellow": 1.5, "orange": 2.0, "red": 2.5},
        "unit": "%p",
        "stale_days": 5,
        "fallback": None,
    },
    {
        "name": "하이일드 ETF (HYG)",
        "source": "yahoo",
        "code": "HYG",
        "tier": 1,
        "direction": "trend",
        "trend_type": "ma20",   # 20일 이동평균선 기준
        # near_pct: 20일선 ±1% 이내면 '근처'(yellow), drop_pct: 하루 5%+ 하락이면 red
        "thresholds": {"near_pct": 0.01, "drop_pct": 0.05},
        "unit": "$",
        "stale_days": 5,
        "fallback": None,
    },
    {
        "name": "지역은행 ETF (KRE)",
        "source": "yahoo",
        "code": "KRE",
        "tier": 1,
        "direction": "trend",
        "trend_type": "ma20",
        "thresholds": {"near_pct": 0.01, "drop_pct": 0.05},
        "unit": "$",
        "stale_days": 5,
        "fallback": None,
    },

    # ===== Tier 2 : 매크로 (방향 결정) =====
    {
        "name": "근원 CPI(전년비)",
        "source": "fred",
        "code": "CPILFESL",     # 지수값 → signals.py에서 전월대비 상승/하락 단순판정
        "tier": 2,
        "direction": "trend",
        "trend_type": "mom",    # 직전값 대비 상승/하락 (단순화)
        "thresholds": {},
        "unit": "지수",
        "stale_days": 90,   # 월간 지표: 날짜가 '그 달 1일'로 찍히고 발표는 1~2달 늦어, 최신값도 60~85일 '옛날'로 보임 → 90일로 넉넉히
        "fallback": None,
    },
    {
        "name": "근원 PCE(전년비)",
        "source": "fred",
        "code": "PCEPILFE",
        "tier": 2,
        "direction": "trend",
        "trend_type": "mom",
        "thresholds": {},
        "unit": "지수",
        "stale_days": 90,   # 월간 지표: 날짜가 '그 달 1일'로 찍히고 발표는 1~2달 늦어, 최신값도 60~85일 '옛날'로 보임 → 90일로 넉넉히
        "fallback": None,
    },
    {
        "name": "실업률",
        "source": "fred",
        "code": "UNRATE",
        "tier": 2,
        "direction": "trend",   # 상승 시작/급등 판정 (signals.py)
        "trend_type": "mom",
        "thresholds": {},
        "unit": "%",
        "stale_days": 90,   # 월간 지표: 날짜가 '그 달 1일'로 찍히고 발표는 1~2달 늦어, 최신값도 60~85일 '옛날'로 보임 → 90일로 넉넉히
        "fallback": None,
    },
    {
        "name": "수익률곡선 2s10s",
        "source": "fred",
        "code": "T10Y2Y",
        "tier": 2,
        "direction": "low_bad",  # 마이너스(역전)가 위험
        "thresholds": {"green": 0.2, "red": 0.0},
        "unit": "%p",
        "stale_days": 5,
        "fallback": None,
    },

    # ===== Tier 3 : 시장·심리 (단기 트리거) =====
    {
        "name": "VIX 공포지수",
        "source": "fred",
        "code": "VIXCLS",
        "tier": 3,
        "direction": "high_bad",
        "thresholds": {"yellow": 18, "orange": 25, "red": 30},
        "unit": "",
        "stale_days": 5,
        "fallback": None,
    },
    {
        "name": "WTI 유가",
        "source": "fred",
        "code": "DCOILWTICO",   # 1순위: FRED (며칠 지연 가능)
        "tier": 3,
        "direction": "high_bad",
        "thresholds": {"yellow": 85, "orange": 100, "red": 110},
        "unit": "$",
        "stale_days": 5,
        # 2순위: FRED 값이 오래됐거나 실패하면 Yahoo CL=F 로 폴백 (8단계)
        "fallback": {"source": "yahoo", "code": "CL=F"},
    },
    {
        "name": "미 10년물 금리",
        "source": "fred",
        "code": "DGS10",
        "tier": 3,
        "direction": "high_bad",
        "thresholds": {"yellow": 4.3, "orange": 4.7, "red": 5.0},
        "unit": "%",
        "stale_days": 5,
        "fallback": None,
    },
]


# ─────────────────────────────────────────────────────────────
# 종합 판정 옵션 (3절 로직에서 사용 — 4단계에서 구현)
# ─────────────────────────────────────────────────────────────
MAX_MISSING = 0.30                       # 결측 비율 임계 (초과 시 '판정 보류 — 회색')
YELLOW_WARN = {"on": True, "threshold": 5}   # yellow 5개 이상이면 보조경고 한 줄
TIER3_PANIC = {"on": False}              # Tier3만 위험 2개일 때 경계로 격상 (기본 OFF)

# ★ 거짓 경보 감소 (고도화 2단계)
#  on=True 면, orange(주황) 위험 신호는 '어제도 위험이었을 때'만 위험 등급에 카운트.
#  하루만 깜빡인 단발성 신호는 '관찰 중'으로만 표시(등급 안 올림). red(빨강)는 즉시 인정.
CONFIRM = {"on": True}
HEARTBEAT_MODE = "weekly"                # off | daily | weekly (정상작동 통지 주기)
HEARTBEAT_WEEKDAY = 0                    # weekly 일 때 보낼 요일 (0=월요일 ... 6=일요일)

# ─────────────────────────────────────────────────────────────
# 알림 설정
# ─────────────────────────────────────────────────────────────
# 어디로 알림을 보낼지: "slack" | "telegram" | "both" | "off"
NOTIFY_PROVIDER = "slack"

# 웹 대시보드 주소 (11단계에서 GitHub Pages 켜면 채움). 비어 있으면 메시지에서 생략.
DASHBOARD_URL = ""

# 대시보드 추이 차트에 그릴 지표 이름들 (config.INDICATORS 의 name 과 일치해야 함)
CHART_INDICATORS = ["하이일드 스프레드", "VIX 공포지수", "WTI 유가"]

# 추이에 보여줄 최근 일수 (약 3년치 거래일 ≈ 800). 백필 데이터를 다 보여줌.
HISTORY_DAYS = 800

# backfill.py 가 과거를 몇 년치 채울지 (1회성 도구 기본값)
BACKFILL_YEARS = 3


# ─────────────────────────────────────────────────────────────
# 종합 판정별 표시(이모지·제목)와 '행동 가이드' 문구
#   알림·대시보드가 공통으로 이걸 씁니다. 문구를 바꾸려면 여기만 수정.
# ─────────────────────────────────────────────────────────────
VERDICTS = {
    "gray": {
        "emoji": "⚪",
        "title": "판정 보류 — 데이터 부족",
        "actions": [
            "데이터 소스 점검 필요 (FRED/Yahoo 결측 확인)",
            "이전 판정을 참고하고, 자동판정은 신뢰 보류",
        ],
    },
    "red": {
        "emoji": "🔴",
        "title": "최악 진입 — 전면 방어",
        "actions": [
            "위험자산 최소화 / 현금·단기국채 최대",
            "반등 추격매수 금지",
            "신용시장 안정될 때까지 대기",
        ],
    },
    "orange": {
        "emoji": "🟠",
        "title": "위험 — 2차 방어",
        "actions": [
            "위험자산 절반 축소 / 현금 40~50%",
            "★빚(신용거래·미수) 즉시 청산★",
            "AI 집중 포지션 분산",
        ],
    },
    "yellow": {
        "emoji": "🟡",
        "title": "경계 — 1차 방어",
        "actions": [
            "신규매수 중단 / 현금 30%",
            "비싼 종목부터 일부 차익실현",
            "헷지 소량",
        ],
    },
    "green": {
        "emoji": "🟢",
        "title": "평상시 — 유지",
        "actions": [
            "정상 보유 / 현금 10~20%",
        ],
    },
}

# 자동화하지 않고 '수동 확인'으로만 표시할 정성 항목
MANUAL_CHECK_ITEMS = [
    "트럼프 내러티브 톤 / 발언",
    "이란·중동 등 지정학 헤드라인",
]


# ═════════════════════════════════════════════════════════════
# ★ 주린이(초보 투자자)용 쉬운 설명 — 고도화 1단계
# ═════════════════════════════════════════════════════════════

# 지표별 쉬운 설명 (지표코드 → {what: 이게 뭐냐, why: 왜 중요하냐})
PLAIN = {
    "BAMLH0A0HYM2": {
        "what": "신용도 낮은 기업이 돈 빌릴 때 무는 '위험 가산 이자폭'이에요.",
        "why": "이 값이 치솟으면 = 시장이 '기업들이 빚 못 갚을라'며 겁먹는다는 뜻. 폭락보다 먼저 움직이는 대표 선행지표예요.",
    },
    "BAMLC0A0CM": {
        "what": "우량(투자등급) 기업이 돈 빌릴 때 무는 위험 가산폭이에요.",
        "why": "우량 기업 이자까지 오르면 = 신용 경색이 넓게 번지는 신호.",
    },
    "HYG": {
        "what": "위험한 회사채를 모아둔 ETF(상장펀드) 가격이에요.",
        "why": "20일 평균선 아래로 빠지면 = 위험한 채권을 사람들이 던지는 중이라는 뜻.",
    },
    "KRE": {
        "what": "미국 지역은행들을 모아둔 ETF 가격이에요.",
        "why": "지역은행이 흔들리면 금융위기 불씨가 되곤 해요(2023년 SVB 사태처럼).",
    },
    "CPILFESL": {
        "what": "변동 큰 항목(식품·에너지)을 뺀 '진짜 물가(근원 CPI)'예요.",
        "why": "물가가 다시 오르면 = 금리 인하가 늦어져 증시에 부담.",
    },
    "PCEPILFE": {
        "what": "미국 중앙은행(연준)이 가장 중시하는 물가 지표(근원 PCE)예요.",
        "why": "이게 재가속하면 = 연준이 긴축을 더 오래 끌어 증시를 누름.",
    },
    "UNRATE": {
        "what": "일자리 없는 사람의 비율(실업률)이에요.",
        "why": "갑자기 급등하면 = 경기 침체가 다가온다는 신호.",
    },
    "T10Y2Y": {
        "what": "장기금리(10년) − 단기금리(2년) 차이예요(수익률곡선).",
        "why": "마이너스(역전)가 되면 = 역사적으로 경기침체 1~2년 전 경고였어요.",
    },
    "VIXCLS": {
        "what": "시장의 '공포 온도계'(VIX)예요.",
        "why": "치솟으면 = 투자자들이 급히 보험을 산다는 뜻 = 패닉 분위기.",
    },
    "DCOILWTICO": {
        "what": "국제 원유(기름) 가격(WTI)이에요.",
        "why": "급등하면 = 물가·기업 비용이 올라 증시에 부담. 지정학 위기 신호이기도.",
    },
    "DGS10": {
        "what": "미국 국채 10년물의 이자율이에요.",
        "why": "급등하면 = 주식의 상대 매력이 떨어지고, 특히 성장주에 타격.",
    },
}

# 종합 판정별 '주린이 한 줄' (등급 → 쉬운 말 해설)
VERDICT_PLAIN = {
    "green": "지금은 평소처럼 투자해도 무난한 구간이에요. 큰 위험 신호는 안 보여요.",
    "yellow": "살짝 조심할 구간이에요. 무리한 신규 매수는 잠깐 멈춰도 좋아요.",
    "orange": "위험 신호가 켜졌어요. 욕심내기보다 '지키는' 쪽이 나은 구간이에요.",
    "red": "여러 위험이 동시에 켜졌어요. 아주 보수적으로 봐야 할 구간이에요.",
    "gray": "데이터가 부족해 판단을 잠시 미뤘어요. 자동 판정을 너무 믿지 마세요.",
}

# 신호등 색 → '지금 의미' 한 줄 (지표 카드에서 사용)
SIGNAL_MEANING = {
    "green": "지금은 안정적이에요",
    "yellow": "약간 주의가 필요해요",
    "orange": "경고 구간이에요",
    "red": "위험 구간이에요",
    None: "데이터가 없어 확인이 필요해요",
}

# 대시보드 맨 위 '이 신호등이 뭐예요?' 온보딩 설명
ONBOARDING = [
    "이 신호등은 미국 증시가 '폭락·조정 위험'에 가까운지를 11개 지표로 자동 점검해요.",
    "🟢평상시 → 🟡경계 → 🟠위험 → 🔴최악 순으로 위험이 커져요. ⚪는 '데이터 부족(판단 보류)'.",
    "각 지표 카드의 'ⓘ 쉬운 설명'을 누르면 그게 무슨 뜻인지 풀어서 보여줘요.",
    "⚠️ 이건 투자 '조언'이 아니라 '참고용 온도계'예요. 최종 판단·책임은 본인에게 있어요.",
]
