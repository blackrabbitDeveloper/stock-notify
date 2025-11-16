# src/ranker.py
import pandas as pd
from collections import Counter
from .fetch_news import fetch_company_news
from .news_scorer import score_news_items
from .technical_analyzer import analyze_stock_technical, calculate_technical_score

def rank_with_news(df, tickers, use_news=True, min_bars=5, tech_filter_count=30):
    """
    2단계 필터링으로 개선된 종목 랭킹
    
    1단계: 전체 종목 기술적 분석 (빠름)
    2단계: 상위 N개만 뉴스 분석 (느린 API 호출 최소화)
    
    Args:
        tech_filter_count: 뉴스 분석할 상위 종목 개수 (기본 30개)
    
    점수 구성:
    - 기술적 분석 점수: 0~10점 (가중치 85%)
    - 뉴스 보너스: 0~4점 (가중치 15%)
    """
    rows=[]
    skips = Counter()

    if df is None or df.empty or not tickers:
        print("[DEBUG] ranker: empty input df or no tickers")
        return pd.DataFrame(columns=[
            "ticker","day_ret","vol_x","news_n","news_bonus",
            "tech_score","combined_score","top_news","technical_analysis"
        ])

    print(f"[DEBUG] === 1단계: {len(tickers)}개 종목 기술적 분석 ===")
    
    # === 1단계: 전체 종목 기술적 분석만 수행 ===
    tech_results = []
    
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

        # 기술적 분석 수행
        tech_analysis = analyze_stock_technical(g)
        tech_score = 0.0
        
        if tech_analysis:
            tech_score = calculate_technical_score(tech_analysis)
        else:
            skips["tech_analysis_failed"] += 1
            continue  # 기술적 분석 실패시 제외

        tech_results.append({
            "ticker": t,
            "day_ret": day_ret,
            "vol_x": vol_x,
            "tech_score": tech_score,
            "technical_analysis": tech_analysis,
            "df_group": g  # 나중에 사용할 수 있도록 저장
        })
    
    if not tech_results:
        print("[DEBUG] 기술적 분석 결과 없음")
        print(f"[DEBUG] skips: {dict(skips)}")
        return pd.DataFrame(columns=[
            "ticker","day_ret","vol_x","news_n","news_bonus",
            "tech_score","combined_score","top_news","technical_analysis"
        ])
    
    # 기술적 점수로 정렬
    tech_results.sort(key=lambda x: x["tech_score"], reverse=True)
    
    # 상위 N개만 선택
    top_tech = tech_results[:tech_filter_count]
    print(f"[DEBUG] 기술적 점수 상위 {len(top_tech)}개 선별 완료")
    print(f"[DEBUG] 점수 범위: {top_tech[0]['tech_score']:.2f} ~ {top_tech[-1]['tech_score']:.2f}")
    
    # === 2단계: 상위 N개만 뉴스 분석 ===
    print(f"[DEBUG] === 2단계: 상위 {len(top_tech)}개 뉴스 분석 ===")
    
    for idx, item in enumerate(top_tech, 1):
        t = item["ticker"]
        
        # 뉴스 분석
        news_bonus, news_n, top_news = 0.0, 0, []
        if use_news:
            print(f"[DEBUG] [{idx}/{len(top_tech)}] {t} 뉴스 분석 중...", end=" ")
            raw = fetch_company_news(t, hours_back=48)
            news_bonus = score_news_items(raw) if raw else 0.0
            news_n = len(raw) if raw else 0
            print(f"뉴스 {news_n}개, 점수 {news_bonus:.2f}")
            
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

        # 최종 점수 계산
        # tech_score: 0~10 → 0~8.5
        # news_bonus: 0~4 → 0~1.0 (4 * 0.25)
        combined_score = (item["tech_score"] * 0.85) + (news_bonus * 0.25)
        
        rows.append({
            "ticker": t,
            "day_ret": item["day_ret"],
            "vol_x": item["vol_x"],
            "news_n": news_n,
            "news_bonus": news_bonus,
            "tech_score": item["tech_score"],
            "combined_score": combined_score,
            "top_news": top_news,
            "technical_analysis": item["technical_analysis"]
        })

    out = pd.DataFrame(rows, columns=[
        "ticker","day_ret","vol_x","news_n","news_bonus",
        "tech_score","combined_score","top_news","technical_analysis"
    ])
    
    if out.empty:
        print("[DEBUG] ranker skips:", dict(skips))
        return out
    
    # combined_score 기준으로 정렬
    result = out.sort_values("combined_score", ascending=False).head(10)
    
    print(f"[DEBUG] === 최종 10개 선별 완료 ===")
    print(f"[DEBUG] 최종 점수 범위: {result.iloc[0]['combined_score']:.2f} ~ {result.iloc[-1]['combined_score']:.2f}")
    
    return result
