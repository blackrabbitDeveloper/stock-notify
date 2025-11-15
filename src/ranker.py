# src/ranker.py
import pandas as pd
from collections import Counter
from .fetch_news import fetch_company_news
from .news_scorer import score_news_items
from .technical_analyzer import analyze_stock_technical, calculate_technical_score

def rank_with_news(df, tickers, use_news=True, min_bars=5):
    """
    기술적 분석 + 뉴스 감성을 결합한 종목 랭킹
    
    점수 구성:
    - 기술적 분석 점수: 0~10점 (가중치 70%)
    - 뉴스 보너스: 0~6점 (가중치 30%)
    """
    rows=[]
    skips = Counter()

    if df is None or df.empty or not tickers:
        print("[DEBUG] ranker: empty input df or no tickers")
        return pd.DataFrame(columns=[
            "ticker","day_ret","vol_x","news_n","news_bonus",
            "tech_score","combined_score","top_news","technical_analysis"
        ])

    for t in tickers:
        g = df[df["ticker"]==t].sort_values("Date")
        if len(g) < max(2, min_bars):
            skips["len<min_bars"] += 1
            continue

        last, prev = g.iloc[-1], g.iloc[-2]
        if pd.isna(last["Close"]) or pd.isna(prev["Close"]) or prev["Close"] == 0:
            skips["bad_close"] += 1
            continue

        # 기본 수익률
        day_ret = (last["Close"]/prev["Close"] - 1)*100.0

        # 거래량 배수
        vol_mean20 = g["Volume"].tail(20).mean()
        if pd.isna(vol_mean20) or vol_mean20 <= 0:
            vol_mean5 = g["Volume"].tail(5).mean()
            if pd.isna(vol_mean5) or vol_mean5 <= 0:
                skips["bad_volume"] += 1
                continue
            vol_x = last["Volume"]/vol_mean5
        else:
            vol_x = last["Volume"]/vol_mean20

        # === 기술적 분석 수행 ===
        tech_analysis = analyze_stock_technical(g)
        tech_score = 0.0
        
        if tech_analysis:
            tech_score = calculate_technical_score(tech_analysis)
        else:
            skips["tech_analysis_failed"] += 1
            # 기술적 분석 실패 시에도 계속 진행 (점수만 0)

        # === 뉴스 분석 ===
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
                    "title": it["headline"], 
                    "summary": (it.get("summary") or "")[:200],
                    "source": it.get("source",""), 
                    "url": it.get("url",""), 
                    "hours_ago": round(h,1)
                })

        # === 최종 점수 계산 ===
        # 기술적 분석 70%, 뉴스 30% 가중치
        combined_score = (tech_score * 0.7) + (news_bonus * 0.5)  # news_bonus는 0~6이므로 *0.5하면 0~3
        
        rows.append({
            "ticker": t,
            "day_ret": day_ret,
            "vol_x": vol_x,
            "news_n": news_n,
            "news_bonus": news_bonus,
            "tech_score": tech_score,
            "combined_score": combined_score,
            "top_news": top_news,
            "technical_analysis": tech_analysis or {}
        })

    out = pd.DataFrame(rows, columns=[
        "ticker","day_ret","vol_x","news_n","news_bonus",
        "tech_score","combined_score","top_news","technical_analysis"
    ])
    
    if out.empty:
        print("[DEBUG] ranker skips:", dict(skips))
        return out
    
    # combined_score 기준으로 정렬
    return out.sort_values("combined_score", ascending=False).head(10)
