import os
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict

# Finnhub 회사뉴스 사용 (선택). FINNHUB_TOKEN 이 없으면 빈 리스트 반환.
# 반환 스키마: [{"headline","summary","source","url","datetime"}]

def fetch_company_news(ticker: str, hours_back: int = 48) -> List[Dict]:
    token = os.getenv("FINNHUB_TOKEN")
    if not token:
        return []

    now = datetime.now(timezone.utc)
    frm = (now - timedelta(hours=hours_back)).date().isoformat()
    to = now.date().isoformat()

    url = (
        "https://finnhub.io/api/v1/company-news"
        f"?symbol={ticker}&from={frm}&to={to}&token={token}"
    )
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return []
        items = r.json()[:100]  # 안전 상한
    except Exception:
        return []

    seen = set()
    out: List[Dict] = []
    for x in items:
        title = (x.get("headline") or "").strip()
        link = (x.get("url") or "").strip()
        key = (title[:120] + link)
        if not title or key in seen:
            continue
        seen.add(key)
        ts = x.get("datetime", 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else now
        out.append({
            "headline": title,
            "summary": (x.get("summary") or "").strip(),
            "source": (x.get("source") or "").strip(),
            "url": link,
            "datetime": dt,
        })
    return out
