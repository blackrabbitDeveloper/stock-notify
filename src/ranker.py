# src/ranker.py
"""
개선된 랭킹 시스템 v2
- 시장 컨텍스트 반영
- 진입 타이밍 중심 스코어링
- 과열 종목 제거 강화
- 섹터 분산
"""
import pandas as pd
import time
from collections import Counter
from typing import List, Dict, Optional
from .logger import logger
from .fetch_news import fetch_company_news
from .news_scorer import score_news_items
from .technical_analyzer import analyze_stock_technical, calculate_technical_score


# ─── 시장 컨텍스트 ───────────────────────────

def _assess_market_context(prices: pd.DataFrame) -> Dict:
    """
    시장 전체 상황 평가
    - 시장 평균 수익률/변동성
    - 상승 종목 비율
    - 시장 과열/침체 판단
    """
    context = {
        'market_avg_ret': 0.0,
        'up_ratio': 0.5,
        'market_volatility': 0.0,
        'market_condition': 'neutral',  # bullish / neutral / bearish / overheated
        'score_adjustment': 0.0,
    }
    
    if prices is None or prices.empty:
        return context
    
    try:
        # 종목별 최근 수익률 계산
        rets = []
        for t in prices['ticker'].unique():
            g = prices[prices['ticker'] == t].sort_values('Date')
            if len(g) >= 2:
                ret = (g['Close'].iloc[-1] / g['Close'].iloc[-2] - 1) * 100
                rets.append(ret)
        
        if not rets:
            return context
        
        rets_s = pd.Series(rets)
        context['market_avg_ret'] = float(rets_s.mean())
        context['up_ratio'] = float((rets_s > 0).mean())
        context['market_volatility'] = float(rets_s.std())
        
        # 시장 상태 판단
        avg_ret = context['market_avg_ret']
        up_ratio = context['up_ratio']
        
        if avg_ret > 1.5 and up_ratio > 0.75:
            context['market_condition'] = 'overheated'
            context['score_adjustment'] = -1.5  # 과열 시 전체적으로 보수적
        elif avg_ret > 0.5 and up_ratio > 0.6:
            context['market_condition'] = 'bullish'
            context['score_adjustment'] = 0.0
        elif avg_ret < -0.5 and up_ratio < 0.4:
            context['market_condition'] = 'bearish'
            context['score_adjustment'] = -1.0  # 하락장에서는 보수적
        else:
            context['market_condition'] = 'neutral'
            context['score_adjustment'] = 0.0
        
        logger.info(
            f"시장 컨텍스트: {context['market_condition']} "
            f"(평균수익률 {avg_ret:.2f}%, 상승비율 {up_ratio:.1%}, "
            f"변동성 {context['market_volatility']:.2f}%)"
        )
    except Exception as e:
        logger.warning(f"시장 컨텍스트 분석 실패: {e}")
    
    return context


# ─── 과열 필터 ───────────────────────────────

def _is_overheated(tech: Dict, day_ret: float) -> bool:
    """
    과열 종목 판별 — 이미 크게 오른 종목은 진입 부적합
    """
    reasons = []
    
    # RSI 과매수
    if tech.get('rsi', 50) > 75:
        reasons.append('rsi_overbought')
    
    # 연속 상승 과열
    if tech.get('consecutive_up', 0) >= 5:
        reasons.append('consecutive_up_5d+')
    
    # 볼린저 상단 이탈
    if tech.get('bb_position', 0.5) > 0.95:
        reasons.append('bb_upper_breakout')
    
    # 이평선 과도 괴리
    if tech.get('ma5_deviation', 0) > 12:
        reasons.append('ma5_deviation_extreme')
    
    # 당일 급등 (5% 이상) + 거래량 폭증은 추격 매수 위험
    if day_ret > 5 and tech.get('volume_ratio', 1) > 3:
        reasons.append('spike_day')
    
    # 약세 다이버전스 발생
    if tech.get('divergence', {}).get('bearish_divergence', False):
        reasons.append('bearish_divergence')
    
    # 3개 이상 과열 신호 → 과열 판정
    return len(reasons) >= 2


def rank_with_news(
    df: pd.DataFrame,
    tickers: List[str],
    use_news: bool = True,
    min_bars: int = 5,
    tech_filter_count: int = 30,
    min_tech_score: float = 4.0  # 기준 하향 (v2 점수 체계에 맞춤)
) -> pd.DataFrame:
    """
    개선된 종목 랭킹 v2

    변경 사항:
    ① 시장 컨텍스트 반영 (과열장 필터)
    ② 과열 종목 강제 제거
    ③ 진입 타이밍 점수 중심
    ④ 뉴스 보너스 가중치 정규화
    ⑤ 섹터 분산 (동일 섹터 과집중 방지) — 향후 확장
    """
    RESULT_COLS = [
        "ticker", "day_ret", "vol_x", "news_n", "news_bonus",
        "tech_score", "combined_score", "top_news", "technical_analysis"
    ]
    
    rows = []
    skips = Counter()
    
    if df is None or df.empty or not tickers:
        logger.warning("입력 데이터가 비어있습니다.")
        return pd.DataFrame(columns=RESULT_COLS)
    
    # ── 0단계: 시장 컨텍스트 분석 ──
    market_ctx = _assess_market_context(df)
    market_adj = market_ctx['score_adjustment']
    
    logger.info(f"{'=' * 60}")
    logger.info(f"1단계: {len(tickers)}개 종목 기술적 분석 (v2)")
    logger.info(f"{'=' * 60}")
    
    # ── 1단계: 기술적 분석 ──
    tech_results = []
    
    for t in tickers:
        g = df[df["ticker"] == t].sort_values("Date")
        
        if len(g) < max(2, min_bars):
            skips["len<min_bars"] += 1
            continue
        
        last, prev = g.iloc[-1], g.iloc[-2]
        
        if pd.isna(last["Close"]) or pd.isna(prev["Close"]) or prev["Close"] == 0:
            skips["bad_close"] += 1
            continue
        
        day_ret = (last["Close"] / prev["Close"] - 1) * 100.0
        
        # 거래량 배수
        vol_mean20 = g["Volume"].tail(20).mean()
        if pd.isna(vol_mean20) or vol_mean20 <= 0:
            vol_mean5 = g["Volume"].tail(5).mean()
            if pd.isna(vol_mean5) or vol_mean5 <= 0:
                skips["bad_volume"] += 1
                continue
            vol_x = last["Volume"] / vol_mean5
        else:
            vol_x = last["Volume"] / vol_mean20
        
        # 기술적 분석
        tech_analysis = analyze_stock_technical(g)
        if not tech_analysis:
            skips["tech_analysis_failed"] += 1
            continue
        
        tech_score = calculate_technical_score(tech_analysis)
        
        # 시장 컨텍스트 조정 적용
        adjusted_score = tech_score + market_adj
        
        # 과열 종목 사전 제거 (v2 신규)
        if _is_overheated(tech_analysis, day_ret):
            skips["overheated"] += 1
            continue
        
        # 최소 점수 필터링
        if adjusted_score < min_tech_score:
            skips["below_min_score"] += 1
            continue
        
        tech_results.append({
            "ticker": t,
            "day_ret": day_ret,
            "vol_x": vol_x,
            "tech_score": tech_score,
            "adjusted_score": adjusted_score,
            "technical_analysis": tech_analysis,
        })
    
    if not tech_results:
        logger.warning("기술적 분석 통과 종목 없음")
        logger.info(f"스킵 사유: {dict(skips)}")
        return pd.DataFrame(columns=RESULT_COLS)
    
    # 조정된 점수로 정렬
    tech_results.sort(key=lambda x: x["adjusted_score"], reverse=True)
    
    logger.info(f"기술적 분석 통과: {len(tech_results)}개 (과열 제거: {skips.get('overheated', 0)}개)")
    logger.info(f"점수 범위: {tech_results[0]['adjusted_score']:.2f} ~ {tech_results[-1]['adjusted_score']:.2f}")
    
    # 상위 N개 선택
    top_tech = tech_results[:tech_filter_count]
    
    # ── 2단계: 뉴스 분석 ──
    logger.info(f"{'=' * 60}")
    logger.info(f"2단계: 상위 {len(top_tech)}개 뉴스 분석")
    logger.info(f"{'=' * 60}")
    
    for idx, item in enumerate(top_tech, 1):
        t = item["ticker"]
        tech_score = item["tech_score"]
        
        news_bonus, news_n, top_news = 0.0, 0, []
        
        if use_news and tech_score >= 5.0:
            try:
                raw = fetch_company_news(t, hours_back=48)
                news_bonus = score_news_items(raw) if raw else 0.0
                news_n = len(raw) if raw else 0
                
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                for it in (raw or [])[:3]:
                    h = max(0, (now - it["datetime"]).total_seconds() / 3600.0)
                    top_news.append({
                        "title": it["headline"],
                        "summary": (it.get("summary") or "")[:200],
                        "source": it.get("source", ""),
                        "url": it.get("url", ""),
                        "hours_ago": round(h, 1)
                    })
                
                time.sleep(1.2)
            except Exception as e:
                logger.warning(f"뉴스 가져오기 실패 ({t}): {e}")
        
        # ── 최종 점수 계산 (v2: 정규화된 가중치) ──
        # tech_score: 0~10 → 85%
        # news_bonus: 0~4  → 15% (4점 만점을 10점 스케일로 정규화)
        news_normalized = (news_bonus / 4.0) * 10.0 if news_bonus > 0 else 0.0
        combined_score = (tech_score * 0.85) + (news_normalized * 0.15) + market_adj
        
        # R:R 비율 보너스 (최종 점수에 직접 반영)
        rr = item['technical_analysis'].get('risk_reward', {}).get('risk_reward_ratio', 0)
        if rr >= 2.5:
            combined_score += 0.5
        
        rows.append({
            "ticker": t,
            "day_ret": item["day_ret"],
            "vol_x": item["vol_x"],
            "news_n": news_n,
            "news_bonus": news_bonus,
            "tech_score": tech_score,
            "combined_score": combined_score,
            "top_news": top_news,
            "technical_analysis": item["technical_analysis"]
        })
    
    out = pd.DataFrame(rows, columns=RESULT_COLS)
    
    if out.empty:
        logger.warning(f"최종 결과 없음. 스킵 사유: {dict(skips)}")
        return out
    
    # combined_score 정렬 → 상위 10개
    result = out.sort_values("combined_score", ascending=False).head(10)
    
    logger.info(f"{'=' * 60}")
    logger.info(f"3단계: 최종 {len(result)}개 선별 완료")
    logger.info(f"시장 상태: {market_ctx['market_condition']} (조정: {market_adj:+.1f})")
    logger.info(f"{'=' * 60}")
    
    for _, row in result.iterrows():
        tech = row['technical_analysis']
        entry_signals = []
        if tech.get('pullback', {}).get('pullback_score', 0) > 0:
            entry_signals.append('눌림목')
        if tech.get('breakout', {}).get('breakout_detected', False):
            entry_signals.append('돌파')
        if tech.get('divergence', {}).get('bullish_divergence', False):
            entry_signals.append('다이버전스')
        
        rr = tech.get('risk_reward', {})
        rr_str = f"R:R {rr.get('risk_reward_ratio', 0):.1f}" if rr.get('risk_reward_ratio') else "R:R N/A"
        
        logger.info(
            f"{row['ticker']}: 종합 {row['combined_score']:.2f} "
            f"(기술 {row['tech_score']:.2f}, 뉴스 +{row['news_bonus']:.2f}) "
            f"진입: [{', '.join(entry_signals) or '없음'}] {rr_str} "
            f"위험 {tech.get('risk_score', 0):.1f}"
        )
    
    logger.info(f"스킵: {sum(skips.values())}개 {dict(skips)}")
    
    return result
