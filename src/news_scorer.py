import math
import re
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urlparse

from .sentiment import sentiment_score

# 주요 소스 가중치 (축소)
MAJOR_SOURCES = {
    "reuters.com": 1.1,      # 1.2 → 1.1
    "bloomberg.com": 1.1,    # 1.2 → 1.1
    "wsj.com": 1.08,         # 1.15 → 1.08
    "ft.com": 1.08,          # 1.15 → 1.08
    "seekingalpha.com": 1.03, # 1.05 → 1.03
}

# 카테고리 룰 (간단 키워드 기반)
CAT_RULES = {
    "earnings_up": r"(beats|tops|above|surpass).*(estimates|forecast|guidance)|raises guidance",
    "earnings_down": r"(misses|below).*(estimates|forecast|guidance)|cuts guidance",
    "mna": r"(acquire|buy|merger|takeover|acquisition)",
    "contract": r"(wins|secures).*(contract|order|deal|partnership)",
    "fda": r"(fda|phase\s*(ii|iii)|approval|trial|pdufa)",
    "analyst_up": r"(upgrade|raises price target|initiates.*buy|overweight)",
    "analyst_down": r"(downgrade|cuts price target|initiates.*sell|underweight)",
    "legal": r"(lawsuit|probe|investigation|settlement|antitrust|regulator|sec)",
}

# 카테고리 가중치 (축소)
CAT_WEIGHT = {
    "earnings_up": 1.4,      # 1.6 → 1.4
    "earnings_down": 1.4,    # 1.6 → 1.4
    "mna": 1.5,              # 1.8 → 1.5
    "contract": 1.2,         # 1.4 → 1.2
    "fda": 1.4,              # 1.7 → 1.4
    "analyst_up": 1.1,       # 1.2 → 1.1
    "analyst_down": 1.1,     # 1.2 → 1.1
    "legal": 1.05,           # 1.1 → 1.05
}

def _classify_category(headline: str, summary: str = "") -> List[str]:
    text = f"{headline} {summary}".lower()
    hits = []
    for k, pat in CAT_RULES.items():
        if re.search(pat, text):
            hits.append(k)
    return hits or ["uncat"]

def _recency_decay(published_dt: datetime, now_dt: datetime) -> float:
    """시간 감쇠 함수 (더 빠르게 감소)"""
    hours = max(0.0, (now_dt - published_dt).total_seconds() / 3600.0)
    # 12시간 반감으로 변경 (기존 24시간)
    return math.exp(-hours / 12.0)

def _source_weight(url: str) -> float:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        host = ""
    for dom, w in MAJOR_SOURCES.items():
        if dom in host:
            return w
    return 1.0

def score_news_items(items: List[Dict]) -> float:
    """
    items: fetch_news에서 받은 기사 리스트
    전체 보너스(0~4) 반환 (기존 0~6에서 축소)
    부정 감성은 0으로 클립(가점만).
    """
    if not items:
        return 0.0

    now = datetime.now(timezone.utc)
    total = 0.0
    for it in items:
        title = (it.get("headline") or "").strip()
        summ = (it.get("summary") or "").strip()
        url = (it.get("url") or "").strip()
        dt = it.get("datetime") or now

        senti = sentiment_score(f"{title} {summ}")  # -1..+1
        if senti <= 0:
            continue  # 가점만 반영

        cats = _classify_category(title, summ)
        w_cat = max(CAT_WEIGHT.get(c, 1.0) for c in cats)
        w_time = _recency_decay(dt, now)
        w_src = _source_weight(url)

        # 뉴스 점수 누적 (가중치 전체적으로 낮춤)
        total += senti * w_cat * w_time * w_src * 0.7  # 추가 감소 계수

    # 상한 클립 (6 → 4로 축소)
    return min(4.0, total)
