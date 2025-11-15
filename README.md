# 📈 Stock Notify Bot - 기술적 분석 기반 단기 매매 추천

미국 장 시작 전 **기술적 분석**을 통해 단기 매매에 유리한 종목을 자동으로 선별하여 Discord Webhook으로 전송하는 봇입니다.

## 🎯 주요 기능

### 1. 기술적 분석 (Technical Analysis)
- **이동평균선 분석**
  - 골든크로스/데드크로스 감지 (5일/20일 MA)
  - 이평선 정배열 확인 (5일 > 10일 > 20일)
  - 현재가와 MA 간 괴리율 계산

- **모멘텀 지표**
  - RSI (Relative Strength Index) - 과매수/과매도 구간 감지
  - MACD (Moving Average Convergence Divergence) - 매매 신호
  - 볼린저 밴드 - 가격 위치 및 변동성 분석

- **거래량 분석**
  - 거래량 급증 감지 (20일 평균 대비)
  - 가격 상승 + 거래량 증가 동반 확인
  - 거래대금 기준 필터링

- **추세 및 변동성**
  - ATR (Average True Range) - 변동성 측정
  - ADX (Average Directional Index) - 추세 강도 측정

### 2. 종합 스코어링 시스템
- **기술적 분석 점수**: 0~10점 (가중치 70%)
  - 골든크로스: +2.5점
  - 이평선 정배열: +1.5점
  - MACD 상향돌파: +1.8점
  - 거래량 동반 상승: +2.0점
  - RSI 적정 구간: +1.2점
  - 기타 신호들...

- **뉴스 감성 분석**: 0~6점 (가중치 30%)
  - VADER 감성 분석
  - 카테고리별 가중치 (실적, M&A, FDA 등)
  - 시간 감쇠 (최근 뉴스 우대)

### 3. AI 기반 종합 분석 (Gemini)
- 기술적 지표와 뉴스를 종합한 추천 사유 생성
- 단기 매매 관점의 핵심 포인트 요약

## 🚀 Setup

### GitHub Actions 전용 설정

레포지토리 → Settings → Secrets and variables → Actions 에서 다음을 추가:

#### Secrets
- `DISCORD_WEBHOOK_URL` : 디스코드 웹훅 URL
- `GOOGLE_API_KEY` : Gemini API 키
- `FINNHUB_TOKEN` : (https://finnhub.io/) API Key

#### Variables (선택사항)
- `GEMINI_MODEL_NAME` = gemini-2.5-flash (또는 gemini-2.5-pro)
- `GENAI_TRANSPORT` = rest
- `MAX_TICKERS` = 5
- `AI_EXPLAINER_MAX_TOKENS` = 1024

## 📦 로컬 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# .env 파일 설정
DISCORD_WEBHOOK_URL=your_webhook_url
GOOGLE_API_KEY=your_gemini_api_key
FINNHUB_TOKEN=your_finnhub_token

# 실행
python -m src.main
```

## 🎨 Discord 출력 형식

각 추천 종목에 대해 다음 정보를 제공합니다:

```
🎯 AAPL · Score 8.5 (Tech 7.2)
💵 가격: 175.50 (전일 172.30, 🟢 +1.86%)
📊 수익률 +1.86% · 거래량 2.3x · 뉴스 3개 (+1.2)

📈 기술적 분석 신호
🟢 골든크로스(5/20)
✅ 이평선 정배열
RSI 45.2
🟢 MACD 상향돌파
💪 거래량 동반 상승

💡 AI 추천 사유
5일 이평선이 20일선을 상향돌파하는 골든크로스가 발생했으며, 
MACD도 함께 상승 전환하여 강한 매수 신호를 보이고 있습니다. 
거래량도 평균의 2.3배로 급증하며 상승 추세를 뒷받침하고 있습니다.
(confidence 0.65)

📰 뉴스 하이라이트
- [Reuters] Apple announces new product line (2.3h)
- [Bloomberg] Strong Q4 earnings beat estimates (5.1h)

⚠️ 주의사항
투자 자문 아님. 단기 매매 전략이므로 손절 필수.
```

## 📊 점수 체계

### 기술적 분석 점수 (0~10점)
- 골든크로스: +2.5
- 이평선 정배열: +1.5
- MACD 상향돌파: +1.8
- 거래량 동반 상승: +2.0
- RSI 과매도 탈출: +1.2
- 볼린저밴드 하단 반등: +1.0
- 강한 추세(ADX>25): +0.8
- 기타 신호들...

### 최종 점수
```
최종 점수 = (기술적 분석 점수 × 0.7) + (뉴스 점수 × 0.5)
```

## 🔧 설정 파일

### config/universe.yaml
```yaml
mode: auto               # auto | static
static_list: []

auto:
  pool: nasdaq100        # sp500 | nasdaq100
  min_price: 5           # 최소 가격 필터
  top_by_dollar_vol: 200 # 거래대금 상위 N개
  max_final_universe: 150
  use_news_bonus: true

ai_explainer:
  enabled: true
  model_name: gemini-2.5-flash
```

## 📈 거래 전략 권장사항

이 봇은 다음과 같은 **단기 매매 신호**를 찾습니다:

1. **진입 조건**
   - 골든크로스 발생
   - 거래량 급증 동반
   - RSI 30~50 구간 (과매도 탈출)
   - MACD 상향돌파

2. **주의 사항**
   - 데드크로스 발생 시 회피
   - RSI 70 이상 (과매수) 주의
   - 거래량 없는 급등 주의
   - 반드시 손절가 설정 필수

3. **추천 보유 기간**: 1~5일 (단기)

## ⚠️ 면책 조항

이 봇은 교육 및 정보 제공 목적으로만 사용됩니다.
- 투자 자문이 아닙니다
- 모든 투자 결정은 본인의 책임입니다
- 손실 가능성을 항상 고려하세요
- 반드시 손절매를 설정하세요

## 📝 라이선스

MIT License
