# 📈 Stock Notify Bot

기술적 분석 + 자기학습 기반 미국 주식 단기 매매 추천 봇

매일 미국 장 마감 후 기술적 분석, 뉴스 감성 분석, AI 분석을 종합하여 유망 종목을 선별하고 Discord로 알림을 보냅니다. 백테스팅 엔진과 자기학습 시스템이 전략을 자동으로 개선하며, GitHub Pages 대시보드에서 성과를 실시간으로 확인할 수 있습니다.

**🌐 라이브 대시보드**: [https://blackrabbitdeveloper.github.io/stock-notify/](https://blackrabbitdeveloper.github.io/stock-notify/)

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    GitHub Actions (자동화)                     │
├──────────┬──────────────┬───────────────┬───────────────────┤
│ 매일     │ 매주 토요일    │ 매주 일요일     │ 매일              │
│ 21:30UTC │ 12:00 UTC    │ 10:00 UTC     │ (봇 실행 후)       │
├──────────┼──────────────┼───────────────┼───────────────────┤
│ 봇 실행   │ 백테스트      │ 자기학습       │ 대시보드 생성      │
│ schedule │ backtest     │ self-tuning   │ generate_dashboard │
└────┬─────┴──────┬───────┴───────┬───────┴─────────┬─────────┘
     │            │               │                 │
     ▼            ▼               ▼                 ▼
 Discord      data/backtest/  config/strategy   docs/index.html
 알림          결과 JSON       _state.json       GitHub Pages
```

---

## 🎯 주요 기능

### 1. 기술적 분석 (v2)
| 카테고리 | 신호 | 설명 |
|---------|------|------|
| 이동평균 | 골든크로스 / 데드크로스 | 5일/20일 MA 교차 |
| | 이평선 정배열 | 5일 > 10일 > 20일 MA |
| | MA 괴리율 | 현재가와 MA 간 거리 |
| 모멘텀 | RSI | 과매수/과매도 감지 (14일) |
| | MACD | 시그널 라인 교차 |
| | 스토캐스틱 | %K, %D 교차 |
| 변동성 | 볼린저 밴드 | 가격 위치 및 밴드 폭 |
| | ATR | 평균 진정 범위 (손절/익절 산출) |
| | ADX | 추세 강도 (25 이상 = 강한 추세) |
| 거래량 | 거래량 급증 | 20일 평균 대비 비율 |
| | 가격+거래량 동반 | 상승 + 거래량 증가 |

### 2. 뉴스 감성 분석
- VADER 감성 분석기 (NLTK)
- 카테고리별 가중치 (실적, M&A, FDA 승인 등)
- 시간 감쇠 (최근 뉴스 우대)
- Finnhub API 활용

### 3. AI 종합 분석 (Gemini)
- 기술적 지표 + 뉴스를 종합한 추천 사유 생성
- 단기 매매 관점 핵심 포인트 요약
- 신뢰도 점수 제공

### 4. 포지션 관리
- ATR 기반 자동 손절/익절 계산
- 일일 가격 추적 및 자동 청산
- 포지션 이력 저장 (`data/positions.json`, `data/history.json`)

### 5. 백테스팅 엔진
- 과거 데이터 기반 전략 검증 (기본 90거래일)
- 신호별/점수 구간별 성과 분석
- 파라미터 최적화 (그리드 서치)
- 월별 수익률, Profit Factor, 샤프 비율 산출
- 15개 테스트 케이스 통과

### 6. 자기학습 시스템
- **시장 레짐 감지**: 상승장(bullish) / 하락장(bearish) / 횡보장(sideways) 자동 분류
- **파라미터 최적화**: ATR 배수, 최소 기술점수, 보유일수 등 자동 조정
- **신호 가중치 조정**: 신호별 성과 기반 가중치 증감
- **안전장치**: 극단적 변경 방지, 최소/최대 범위 제한
- 17개 검증 테스트 통과

### 7. 웹 대시보드
- **GitHub Pages** 자동 배포 (매일 갱신)
- **5개 탭**: 시장 현황 / 포지션 / 성과 / 백테스트 / 자기학습
- **시장 현황**: S&P 500, NASDAQ 100, USD/KRW, Gold 실시간 차트
  - Yahoo Finance API 직접 호출 (CORS 프록시 폴백)
  - 1개월 / 3개월 / 6개월 / 1년 기간 전환
- 다크 테마, Chart.js 4.4, 반응형 레이아웃

---

## 📁 프로젝트 구조

```
stock-notify-bot/
├── src/
│   ├── main.py                 # 봇 메인 실행
│   ├── technical_analyzer.py   # 기술적 분석 v2
│   ├── fetch_prices.py         # 가격 데이터 수집 (yfinance)
│   ├── fetch_news.py           # 뉴스 수집 (Finnhub)
│   ├── news_scorer.py          # 뉴스 스코어링
│   ├── sentiment.py            # VADER 감성 분석
│   ├── ai_explainer.py         # Gemini AI 분석
│   ├── ranker.py               # 종합 스코어링 & 랭킹
│   ├── universe_builder.py     # 종목 유니버스 구성
│   ├── position_tracker.py     # 포지션 관리 & 추적
│   ├── market_regime.py        # 시장 레짐 감지
│   ├── backtester.py           # 백테스팅 엔진 (1019줄)
│   ├── backtest_utils.py       # 백테스트 유틸
│   ├── self_tuning.py          # 자기학습 엔진 (989줄)
│   ├── strategy_tuner.py       # 전략 파라미터 튜너
│   ├── send_discord.py         # Discord 알림
│   ├── config.py               # 설정 관리
│   └── logger.py               # 로깅
│
├── config/
│   └── universe.yaml           # 종목 유니버스 설정
│
├── data/
│   ├── positions.json          # 현재 포지션
│   ├── history.json            # 청산 이력
│   ├── pools/                  # 종목 풀 캐시
│   └── backtest/               # 백테스트 결과
│
├── docs/
│   └── index.html              # GitHub Pages 대시보드
│
├── .github/workflows/
│   ├── schedule.yml            # 매일 봇 실행 + 대시보드
│   ├── backtest.yml            # 주간 백테스트
│   ├── self-tuning.yml         # 주간 자기학습
│   └── autotune.yml            # 레거시 자동 튜닝
│
├── generate_dashboard.py       # 대시보드 HTML 생성기
├── run_backtest.py             # 백테스트 CLI
├── run_self_tuning.py          # 자기학습 CLI
├── test_backtest.py            # 백테스트 테스트 (15개)
├── test_self_tuning.py         # 자기학습 테스트 (17개)
├── requirements.txt            # Python 의존성
└── README.md
```

---

## 🚀 설치 & 실행

### 필요 조건
- Python 3.12+
- GitHub 계정 (Actions 자동화)
- API 키: Discord Webhook, Finnhub, Google Gemini

### 로컬 설치

```bash
# 1. 클론
git clone https://github.com/blackrabbitDeveloper/stock-notify.git
cd stock-notify

# 2. 의존성 설치
pip install -r requirements.txt

# 3. NLTK 데이터 다운로드
python -c "import nltk; nltk.download('vader_lexicon')"

# 4. 환경변수 설정 (.env 파일)
cat > .env << EOF
DISCORD_WEBHOOK_URL=your_discord_webhook_url
GOOGLE_API_KEY=your_gemini_api_key
FINNHUB_TOKEN=your_finnhub_api_key
EOF

# 5. 실행
python -m src.main
```

### GitHub Actions 설정

레포지토리 → **Settings** → **Secrets and variables** → **Actions**:

| 종류 | 이름 | 설명 |
|------|------|------|
| Secret | `DISCORD_WEBHOOK_URL` | Discord 웹훅 URL |
| Secret | `GOOGLE_API_KEY` | Gemini API 키 |
| Secret | `FINNHUB_TOKEN` | Finnhub API 키 |
| Variable | `GEMINI_MODEL_NAME` | `gemini-2.5-flash` (기본값) |
| Variable | `GENAI_TRANSPORT` | `rest` |
| Variable | `MAX_TICKERS` | `5` (추천 종목 수) |
| Variable | `AI_EXPLAINER_MAX_TOKENS` | `1024` |

### GitHub Pages 활성화

레포지토리 → **Settings** → **Pages** → Source: **Deploy from a branch** → Branch: `main`, Folder: `/docs`

---

## 📊 CLI 명령어

### 봇 실행
```bash
python -m src.main
```

### 백테스트
```bash
# 기본 90거래일 백테스트
python run_backtest.py

# 180거래일, 상위 3종목
python run_backtest.py --days 180 --top 3

# S&P 500 풀로 1년 + 결과 내보내기
python run_backtest.py --pool sp500 --days 252 --export

# Discord로 결과 전송
python run_backtest.py --discord

# 파라미터 최적화 (빠른 모드: 32개 조합)
python run_backtest.py --optimize --quick

# 전체 최적화 (243개 조합)
python run_backtest.py --optimize
```

### 자기학습
```bash
# 기본 60거래일 기반 학습
python run_self_tuning.py

# 90거래일, DRY RUN (변경 미적용)
python run_self_tuning.py --days 90 --dry-run

# Discord로 결과 전송
python run_self_tuning.py --discord
```

### 대시보드 생성
```bash
python generate_dashboard.py
# → docs/index.html 생성
```

### 테스트
```bash
# 백테스트 엔진 테스트 (15개)
python -m pytest test_backtest.py -v

# 자기학습 엔진 테스트 (17개)
python -m pytest test_self_tuning.py -v
```

---

## ⚙️ GitHub Actions 자동화

| 워크플로우 | 스케줄 | 설명 |
|-----------|--------|------|
| `schedule.yml` | 매일 21:30 UTC (월~금) | 봇 실행 → 대시보드 생성 → 커밋 |
| `backtest.yml` | 매주 토요일 12:00 UTC | 백테스트 실행 → 결과 커밋 |
| `self-tuning.yml` | 매주 일요일 10:00 UTC | 자기학습 → 전략 파라미터 갱신 |

모든 워크플로우는 `workflow_dispatch`를 지원하여 수동 실행도 가능합니다.

---

## 🎨 Discord 알림 예시

```
🎯 AAPL · Score 8.5 (Tech 7.2)
💵 가격: 175.50 (전일 172.30, 🟢 +1.86%)
📊 수익률 +1.86% · 거래량 2.3x · 뉴스 3개 (+1.2)

📈 기술적 분석 신호
🟢 골든크로스(5/20)  ✅ 이평선 정배열
RSI 45.2  🟢 MACD 상향돌파  💪 거래량 동반 상승

💡 AI 추천 사유
골든크로스와 MACD 상승 전환이 동시 발생. 거래량도 평균의
2.3배로 급증하며 강한 매수 신호. (confidence 0.65)

🎯 손절: $171.20 (-2.45%) | 익절: $182.80 (+4.16%)
```

---

## 📈 스코어링 체계

### 기술적 분석 점수 (0~10점)
각 신호별 가중치가 자기학습 시스템에 의해 자동 조정됩니다.

| 신호 | 기본 점수 | 설명 |
|------|----------|------|
| 골든크로스 | +2.5 | 5일/20일 MA 상향 돌파 |
| 이평선 정배열 | +1.5 | 단기 > 중기 > 장기 |
| MACD 상향돌파 | +1.8 | 시그널 라인 교차 |
| 거래량 동반 상승 | +2.0 | 가격 상승 + 거래량 증가 |
| RSI 적정 구간 | +1.2 | 30~50 구간 |
| 볼린저 하단 반등 | +1.0 | 하단 밴드 터치 후 반등 |
| 강한 추세 | +0.8 | ADX > 25 |

### 최종 점수
```
최종 점수 = (기술적 분석 점수 × 0.7) + (뉴스 감성 점수 × 0.5)
```

---

## 🧠 자기학습 파이프라인

```
1. 시장 레짐 감지
   └─ S&P 500 기반 bullish / bearish / sideways 판별

2. 백테스트 실행
   └─ 현재 파라미터로 최근 N일 성과 평가

3. 파라미터 최적화
   └─ ATR 배수, 최소 기술점수, 보유일수 등 그리드 서치

4. 신호 가중치 조정
   └─ 신호별 성과(승률, 수익) 기반 가중치 증감

5. 안전장치 적용
   └─ 극단적 변경 방지, 범위 제한, 이전값과 블렌딩

6. 결과 저장 & Discord 리포트
   └─ config/strategy_state.json, config/signal_weights.json
```

---

## ⚠️ 면책 조항

이 봇은 **교육 및 정보 제공 목적**으로만 사용됩니다.
- 투자 자문이 아닙니다
- 모든 투자 결정은 본인의 책임입니다
- 과거 성과가 미래 수익을 보장하지 않습니다
- 반드시 손절매를 설정하세요

---

## 📝 라이선스

MIT License