"""
멀티 타임프레임(MTF) 분석기.

Triple Screen 전략 기반:
  월봉 → 시장 방향 (상승장/하락장)
  주봉 → 추세 확인 (상승/하락/횡보)
  일봉 → 진입 타이밍 (기존 기술적 분석)

3개 타임프레임이 정렬될 때만 진입 → 승률 극대화.
"""
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  타임프레임별 분석
# ══════════════════════════════════════════════════════

def _resample_ohlcv(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """일봉 → 주봉/월봉으로 리샘플링."""
    df = df.sort_values("Date").copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")

    agg = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }

    # 존재하는 컬럼만
    agg = {k: v for k, v in agg.items() if k in df.columns}
    resampled = df.resample(freq).agg(agg).dropna(subset=["Close"])
    resampled = resampled.reset_index()
    return resampled


def _calc_trend(close: pd.Series, periods: List[int] = None) -> Dict:
    """
    추세 판단.
    SMA 기울기 + 가격 위치로 상승/하락/횡보 결정.
    """
    if periods is None:
        periods = [5, 10, 20]

    result = {"trend": "sideways", "strength": 0.0, "details": {}}

    if len(close) < max(periods) + 2:
        return result

    current = close.iloc[-1]
    signals = []

    for p in periods:
        sma = close.rolling(p).mean()
        if pd.isna(sma.iloc[-1]) or pd.isna(sma.iloc[-2]):
            continue

        # 가격 vs SMA 위치
        above = current > sma.iloc[-1]

        # SMA 기울기 (최근 3개 기간)
        recent = sma.dropna().tail(3)
        if len(recent) >= 2:
            slope = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] * 100
        else:
            slope = 0

        signals.append({
            "period": p,
            "above_sma": above,
            "slope": slope,
        })
        result["details"][f"sma{p}"] = {
            "value": round(sma.iloc[-1], 2),
            "above": above,
            "slope": round(slope, 3),
        }

    if not signals:
        return result

    # 종합 판단
    bullish_count = sum(1 for s in signals if s["above_sma"] and s["slope"] > 0.5)
    bearish_count = sum(1 for s in signals if not s["above_sma"] and s["slope"] < -0.5)
    total = len(signals)

    if bullish_count >= total * 0.6:
        result["trend"] = "bullish"
        result["strength"] = bullish_count / total
    elif bearish_count >= total * 0.6:
        result["trend"] = "bearish"
        result["strength"] = bearish_count / total
    else:
        result["trend"] = "sideways"
        result["strength"] = 0.3

    return result


def analyze_weekly(df_daily: pd.DataFrame) -> Dict:
    """
    주봉 분석.
    최소 60 거래일(12주) 필요.
    """
    if len(df_daily) < 60:
        return {"trend": "unknown", "strength": 0, "available": False}

    weekly = _resample_ohlcv(df_daily, "W")
    if len(weekly) < 10:
        return {"trend": "unknown", "strength": 0, "available": False}

    trend = _calc_trend(weekly["Close"], periods=[5, 10, 20])

    # 추가: 주봉 RSI
    close = weekly["Close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    weekly_rsi = rsi.iloc[-1] if pd.notna(rsi.iloc[-1]) else 50

    # 주봉 MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()
    macd_hist = macd - macd_signal
    weekly_macd_bullish = (macd_hist.iloc[-1] > 0) if pd.notna(macd_hist.iloc[-1]) else False

    trend["available"] = True
    trend["rsi"] = round(weekly_rsi, 1)
    trend["macd_bullish"] = weekly_macd_bullish
    return trend


def analyze_monthly(df_daily: pd.DataFrame) -> Dict:
    """
    월봉 분석.
    최소 250 거래일(12개월) 필요.
    """
    if len(df_daily) < 250:
        return {"trend": "unknown", "strength": 0, "available": False}

    monthly = _resample_ohlcv(df_daily, "ME")
    if len(monthly) < 6:
        return {"trend": "unknown", "strength": 0, "available": False}

    trend = _calc_trend(monthly["Close"], periods=[3, 6, 12])

    # 월봉에서는 장기 SMA(10개월 ≈ 200일) 위/아래 판단
    sma10 = monthly["Close"].rolling(10).mean()
    if pd.notna(sma10.iloc[-1]):
        trend["above_200d"] = monthly["Close"].iloc[-1] > sma10.iloc[-1]
    else:
        trend["above_200d"] = None

    trend["available"] = True
    return trend


# ══════════════════════════════════════════════════════
#  멀티 타임프레임 통합 점수
# ══════════════════════════════════════════════════════

def calculate_mtf_score(df_daily: pd.DataFrame) -> Dict:
    """
    멀티 타임프레임 종합 분석.

    Returns:
        {
            "mtf_score": float,      # -3.0 ~ +3.0 (기술 점수에 가감)
            "alignment": str,        # "strong_bull" / "bull" / "neutral" / "bear" / "strong_bear"
            "weekly": {...},
            "monthly": {...},
            "should_trade": bool,    # 진입 허용 여부
        }
    """
    weekly = analyze_weekly(df_daily)
    monthly = analyze_monthly(df_daily)

    score = 0.0
    reasons = []

    # ── 월봉 (방향, ±1.5) ──
    if monthly.get("available"):
        if monthly["trend"] == "bullish":
            score += 1.5 * monthly["strength"]
            reasons.append("월봉↑")
        elif monthly["trend"] == "bearish":
            score -= 1.5 * monthly["strength"]
            reasons.append("월봉↓")

        # 200일선 위/아래
        if monthly.get("above_200d") is True:
            score += 0.5
            reasons.append("200일선↑")
        elif monthly.get("above_200d") is False:
            score -= 0.5
            reasons.append("200일선↓")

    # ── 주봉 (추세, ±1.5) ──
    if weekly.get("available"):
        if weekly["trend"] == "bullish":
            score += 1.0 * weekly["strength"]
            reasons.append("주봉↑")
        elif weekly["trend"] == "bearish":
            score -= 1.0 * weekly["strength"]
            reasons.append("주봉↓")

        # 주봉 RSI 확인
        w_rsi = weekly.get("rsi", 50)
        if w_rsi > 70:
            score -= 0.5
            reasons.append("주봉RSI과열")
        elif w_rsi < 30:
            score += 0.5
            reasons.append("주봉RSI과매도")

        # 주봉 MACD
        if weekly.get("macd_bullish"):
            score += 0.3
        else:
            score -= 0.3

    # ── 정렬 상태 판단 ──
    w_trend = weekly.get("trend", "unknown")
    m_trend = monthly.get("trend", "unknown")

    if m_trend == "bullish" and w_trend == "bullish":
        alignment = "strong_bull"
        should_trade = True
    elif m_trend == "bullish" or w_trend == "bullish":
        alignment = "bull"
        should_trade = True
    elif m_trend == "bearish" and w_trend == "bearish":
        alignment = "strong_bear"
        should_trade = False  # 월봉+주봉 모두 하락 → 진입 금지
    elif m_trend == "bearish" or w_trend == "bearish":
        alignment = "bear"
        should_trade = True   # 한쪽만 하락이면 허용 (감점으로 처리)
    else:
        alignment = "neutral"
        should_trade = True

    # 점수 클램프
    score = round(max(-3.0, min(3.0, score)), 3)

    return {
        "mtf_score": score,
        "alignment": alignment,
        "should_trade": should_trade,
        "reasons": reasons,
        "weekly": weekly,
        "monthly": monthly,
    }


def calculate_mtf_score_from_cache(
    daily_data: pd.DataFrame,
    ticker: str,
) -> Dict:
    """
    전체 데이터에서 특정 종목의 MTF 점수 계산.
    backtester/ranker에서 호출.
    """
    ticker_data = daily_data[daily_data["ticker"] == ticker].copy()
    if len(ticker_data) < 60:
        return {
            "mtf_score": 0.0,
            "alignment": "unknown",
            "should_trade": True,
            "reasons": ["데이터 부족"],
            "weekly": {"available": False},
            "monthly": {"available": False},
        }

    return calculate_mtf_score(ticker_data)
