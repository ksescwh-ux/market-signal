# 🚦 시장 신호등 — 미국 증시 위험 자동 모니터링

미국 주식시장의 폭락/조정 위험을 조기에 감지하는 **자동 모니터링 시스템**입니다.
경제·시장 지표를 자동 수집 → 신호등(🟢🟡🟠🔴) 판정 → 위험하면 **슬랙 알림** → 결과를 기록하고 **웹 대시보드**로 보여줍니다.

> ⚠️ **면책:** 이 시스템은 투자 판단을 돕는 **보조 모니터링 도구**이며, 투자 조언이나 매매 신호가 아닙니다. 모든 투자 결정과 결과의 책임은 사용자 본인에게 있습니다. 데이터는 외부 무료 API에 의존하므로 지연·오류·결측 가능성이 있으며, 자동 판정이 시장을 완전히 대표하지 않습니다.

---

## 📂 구성 파일

| 파일 | 역할 |
|------|------|
| `main.py` | 메인 실행 (수집 → 판정 → 저장 → 알림 → 대시보드 데이터) |
| `fetch.py` | 데이터 수집 (FRED + Yahoo + WTI 폴백, 재시도/결측 처리) |
| `signals.py` | 신호등 판정 + 종합 판정 |
| `notify.py` | 알림 (슬랙/텔레그램 + heartbeat + 실패 알림) |
| `storage.py` | 결과 저장 (CSV / JSON / 대시보드 데이터) |
| `config.py` | **★ 지표·임계값·옵션 전부 여기** |
| `util.py` | 시간대(KST/ET/UTC)·로깅·신선도 검증 |
| `docs/` | 웹 대시보드 (GitHub Pages) |
| `tests/` | 자동 테스트 |
| `.github/workflows/monitor.yml` | GitHub 자동 실행 설정 |

---

## ▶️ 로컬에서 실행하기

```powershell
# 1) 라이브러리 설치 (최초 1회)
pip install -r requirements.txt

# 2) 비밀키 설정 — .env.example 을 복사해 .env 를 만들고 키를 채움
#    FRED_API_KEY=...        (https://fredaccount.stlouisfed.org/apikeys)
#    SLACK_WEBHOOK_URL=...    (https://api.slack.com/apps → Incoming Webhooks)

# 3) 실행
python main.py

# 4) 테스트
python -m pytest -v
```

---

## 🔧 지표·기준 바꾸는 법 (코드 몰라도 OK)

**전부 `config.py` 한 곳에서** 바꿉니다.

- **위험 기준값 바꾸기:** `INDICATORS` 안에서 해당 지표의 `thresholds` 숫자 수정
  예) VIX 빨강 기준을 30→28로: `"thresholds": {"yellow": 18, "orange": 25, "red": 28}`
- **지표 추가/삭제:** `INDICATORS` 리스트에 딕셔너리 한 칸을 추가하거나 지움
- **신선도(오래됨) 민감도:** 각 지표의 `stale_days` 수정 (일별=5, 월별=45 등)
- **종합 판정 옵션:**
  - `MAX_MISSING` — 결측이 이 비율 넘으면 ⚪판정보류 (기본 0.30)
  - `YELLOW_WARN` — yellow 누적 경고 (기본 ON, 5개)
  - `TIER3_PANIC` — 단기 패닉(VIX·유가) 격상 (기본 OFF)
  - `HEARTBEAT_MODE` — 정상작동 통지 주기 `off`/`daily`/`weekly`
- **알림 대상 바꾸기:** `NOTIFY_PROVIDER` = `"slack"` / `"telegram"` / `"both"` / `"off"`

---

## ⏰ 자동 실행 시간 (KST ↔ UTC 환산)

GitHub Actions의 cron은 **UTC 기준**입니다. `.github/workflows/monitor.yml`의 cron을 바꿀 때 참고:

| 원하는 한국시간(KST) | cron(UTC) |
|---|---|
| KST 07:00 | `0 22 * * *` |
| KST 08:00 | `0 23 * * *` ← 기본값 |
| KST 09:00 | `0 0 * * *` |

- **DST 주의:** 미국 서머타임에 장 마감 시각이 1시간 움직입니다. "장 마감 후 여유 있게"(KST 08시 권장) 잡으면 됩니다.
- **cron 함정:** (a) 정시보다 15~60분 늦게 도는 게 흔함 (b) 60일간 repo에 커밋이 없으면 스케줄 자동 비활성화 — 이 시스템은 매 실행 로그를 커밋하므로 자연히 방지됨 (c) 지연/스킵 대비 heartbeat로 "이번 주 한 번도 안 왔다"를 인지.

---

## 🩺 고장 진단 체크리스트 (알림이 안 올 때)

1. GitHub **Actions 탭** → 최근 실행이 초록 체크(성공)인가, 빨강 X(실패)인가?
2. 실패면 → 어느 스텝에서? (체크아웃/설치/실행/커밋 중)
3. 실행은 됐는데 알림이 없다 → 판정이 🟢평상시라 안 보낸 것일 수 있음 (heartbeat 마지막 수신 확인).
4. **Yahoo 결측인가 FRED 결측인가?** 대시보드의 "오래됨/결측" 플래그 확인 — Yahoo면 일시 차단 가능성(잠시 후 자동 복구).
5. 슬랙 Webhook 만료/삭제? → `python notify.py`로 테스트 메시지 보내보기.
6. cron이 안 돈다 → 60일 무커밋 비활성화 여부 확인(Actions 탭 상단 경고), 수동 "Run workflow"로 재가동.

---

## ⚠️ 알려진 한계

- **추세 단순화:** 근원 CPI/PCE/실업률의 "3개월 연속 상승"·"0.5%p 급등" 같은 정밀 추세는 **'직전값 대비 상승/하락'으로 단순화**했습니다. 이 때문에 이 지표들은 red(빨강)가 거의 뜨지 않습니다(올랐으면 yellow). 개선 여지로 남겨둠.
- **정성 지표 미자동화:** 트럼프 발언 톤·지정학 헤드라인은 자동화하지 않고 대시보드/알림에 "수동 확인 필요"로만 표시.
- **무료 API 의존:** FRED·Yahoo의 지연·결측 가능. Yahoo는 클라우드 IP에서 가끔 차단(그래도 FRED 9개로 시스템은 계속 작동).
