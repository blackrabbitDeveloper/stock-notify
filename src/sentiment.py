# 기본: VADER (가볍고 빠름)
# 선택: FinBERT (금융 특화, 무겁지만 정확도 ↑). 환경변수로 on/off.

import os
from typing import Optional

# --- VADER ---
from nltk import download as nltk_download
from nltk.sentiment import SentimentIntensityAnalyzer
_vader: Optional[SentimentIntensityAnalyzer] = None

def _get_vader():
    global _vader
    if _vader is None:
        try:
            _vader = SentimentIntensityAnalyzer()
        except LookupError:
            # 다운로드가 안되어 있으면 자동 설치 후 재시도
            nltk_download('vader_lexicon')
            _vader = SentimentIntensityAnalyzer()
    return _vader

def vader_score(text: str) -> float:
    """-1..+1 범위 점수 반환"""
    if not text:
        return 0.0
    sia = _get_vader()
    return max(-1.0, min(1.0, sia.polarity_scores(text).get("compound", 0.0)))


# --- FinBERT (옵션) ---
_USE_FINBERT = os.getenv("USE_FINBERT", "false").lower() in {"1","true","yes","on"}
_finbert_pipe = None

def _ensure_finbert():
    global _finbert_pipe
    if _finbert_pipe is None:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline
        tok = AutoTokenizer.from_pretrained("ProsusAI/finbert")
        mdl = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
        _finbert_pipe = pipeline("sentiment-analysis", model=mdl, tokenizer=tok)
    return _finbert_pipe

def finbert_score(text: str) -> float:
    """-1..+1 범위 점수 반환 (positive~+1, neutral~0, negative~-1) × 확신도"""
    if not text:
        return 0.0
    try:
        clf = _ensure_finbert()
        lab = clf(text[:512])[0]  # {'label': 'positive|neutral|negative', 'score': ...}
        base = {"positive": +1.0, "neutral": 0.0, "negative": -1.0}.get(lab["label"].lower(), 0.0)
        return float(base) * float(lab.get("score", 1.0))
    except Exception:
        return 0.0


def sentiment_score(text: str) -> float:
    """환경 설정에 따라 FinBERT 또는 VADER 선택"""
    if _USE_FINBERT:
        return finbert_score(text)
    return vader_score(text)
