import math
import re
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urlparse

from .sentiment import sentiment_score

# 주요 소스 가중치 (선택)
MAJOR_SOURCES = {
    "reuters.com": 1.2,
    "bloomberg.com": 1.2,
    "wsj.com": 1.15,
    "ft.com": 1.15,
    "seekingalpha.com": 1.05,
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
CAT_WEIGHT = {
    "earnings_up": 1.6,
    "earnings_down": 1.6,
    "mna": 1.8,
    "contract": 1.4,
    "fda": 1.7,
    "analyst_up": 1.2,
    "analyst_down": 1.2,
    "legal": 1.1,
}

def _classify_category(headline: str, summary: str = "") -> List[str]:
    text = f"{headline} {summary}".lower()
    hits = []
    for k, pat in CAT_RULES.items():
        if re.search(pat, text):
            hits.append(k)
    return hits or ["uncat"]

def _recency_decay(published_dt: datetime, now_dt: datetime) -> float:
    hours = max(0.0, (now_dt - published_dt).total_seconds() / 3600.0)
    return math.exp(-hours / 24.0)  # 24h 반감

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
    전체 보너스(0~6) 반환. 부정 감성은 0으로 클립(가점만).
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
            continue  # 가점만 반영 (감점 정책을 쓰고 싶다면 여기서 반대로 처리)

        cats = _classify_category(title, summ)
        w_cat = max(CAT_WEIGHT.get(c, 1.0) for c in cats)
        w_time = _recency_decay(dt, now)
        w_src = _source_weight(url)

        total += senti * w_cat * w_time * w_src

    # 상한 클립
    return min(6.0, total)
