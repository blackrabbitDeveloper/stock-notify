# src/ranker.py
import pandas as pd
from collections import Counter
from .fetch_news import fetch_company_news
from .news_scorer import score_news_items

def rank_with_news(df, tickers, use_news=True, min_bars=5):
    rows=[]
    skips = Counter()

    if df is None or df.empty or not tickers:
        print("[DEBUG] ranker: empty input df or no tickers")
        return pd.DataFrame(columns=["ticker","day_ret","vol_x","news_n","news_bonus","score","top_news"])

    for t in tickers:
        g = df[df["ticker"]==t].sort_values("Date")
        if len(g) < max(2, min_bars):
            skips["len<min_bars"] += 1
            continue

        last, prev = g.iloc[-1], g.iloc[-2]
        if pd.isna(last["Close"]) or pd.isna(prev["Close"]) or prev["Close"] == 0:
            skips["bad_close"] += 1
            continue

        day_ret = (last["Close"]/prev["Close"] - 1)*100.0

        vol_mean20 = g["Volume"].tail(20).mean()
        if pd.isna(vol_mean20) or vol_mean20 <= 0:
            vol_mean5 = g["Volume"].tail(5).mean()
            if pd.isna(vol_mean5) or vol_mean5 <= 0:
                skips["bad_volume"] += 1
                continue
            vol_x = last["Volume"]/vol_mean5
        else:
            vol_x = last["Volume"]/vol_mean20

        news_bonus, news_n, top_news = 0.0, 0, []
        if use_news:
            raw = fetch_company_news(t, hours_back=48)
            news_bonus = score_news_items(raw) if raw else 0.0
            news_n = len(raw) if raw else 0
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for it in (raw or [])[:3]:
                h = max(0,(now - it["datetime"]).total_seconds()/3600.0)
                top_news.append({
                    "title": it["headline"], "summary": (it.get("summary") or "")[:200],
                    "source": it.get("source",""), "url": it.get("url",""), "hours_ago": round(h,1)
                })

        score = (day_ret/2.5) + ((vol_x-1.0)*4.0) + news_bonus
        rows.append({
            "ticker":t,"day_ret":day_ret,"vol_x":vol_x,"news_n":news_n,
            "news_bonus":news_bonus,"score":score,"top_news":top_news
        })

    out = pd.DataFrame(rows, columns=["ticker","day_ret","vol_x","news_n","news_bonus","score","top_news"])
    if out.empty:
        print("[DEBUG] ranker skips:", dict(skips))
        return out
    return out.sort_values("score", ascending=False).head(10)
