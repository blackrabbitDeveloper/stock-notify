미국 장 시작 전 자동으로 관심 종목을 계산해 Discord Webhook으로 전송.

# Setup (GitHub Actions 전용)
## Secrets & Variables
레포지토리 → Settings → Secrets and variables → Actions 에서 다음을 추가:

### * Secrets
DISCORD_WEBHOOK_URL : 디스코드 웹훅 URL

GOOGLE_API_KEY : Gemini API 키

FINNHUB_TOKEN : (https://finnhub.io/) API Key

### * Variables (없으면 기본값 사용)
GEMINI_MODEL_NAME = gemini-2.5-flash (또는 gemini-2.5-pro)

GENAI_TRANSPORT = rest 

MAX_TICKERS = 5

AI_EXPLAINER_MAX_TOKENS = 1024
