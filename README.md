미국 장 시작 전 자동으로 관심 종목을 계산해 Discord Webhook으로 전송.

Setup (GitHub Actions 전용)
1) Secrets & Variables

레포지토리 → Settings → Secrets and variables → Actions 에서 다음을 추가:

Secrets

DISCORD_WEBHOOK_URL : 디스코드 웹훅 URL

GOOGLE_API_KEY : (선택) Gemini API 키

FINNHUB_TOKEN : (선택) 뉴스 사용 시

Variables (없으면 기본값 사용)

GEMINI_MODEL_NAME = gemini-1.5-pro (또는 gemini-1.5-flash)

GENAI_TRANSPORT = rest (회사망/방화벽 회피)

MAX_TICKERS = 5

AI_EXPLAINER_MAX_TOKENS = 768

DRY_RUN = 1 (테스트 시 콘솔 출력만, 운영은 0)