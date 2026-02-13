"""
시장 레짐 감지 모듈

SPY/QQQ 데이터를 기반으로 현재 시장 상태를 자동 판별합니다.

레짐 종류:
  - bullish:    상승 추세 (MA 정배열 + RSI 50~70 + 상승 모멘텀)
  - bearish:    하락 추세 (MA 역배열 + RSI < 40 + 하락 모멘텀)
  - sideways:   횡보/박스권 (낮은 ADX + 좁은 볼린저 밴드)
  - volatile:   고변동성 (VIX 급등 또는 ATR 급등)

각 레짐에 맞는 전략 프로파일을 반환합니다.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
from .logger import logger

try:
    import yfinance as yf
except ImportError:
    yf = None


# ══════════════════════════════════════════════════════
#  레짐별 전략 프로파일
# ══════════════════════════════════════════════════════

REGIME_PROFILES = {
    "bullish": {
        "description": "상승 추세 — 모멘텀/돌파 전략 강화",
        "atr_stop_mult": 2.0,
        "atr_tp_mult": 5.0,        # 상승장에서 이익 더 키움
        "max_hold_days": 7,
        "min_tech_score": 4.0,
        "top_n": 5,
        "signal_weights": {
            "breakout_score": 1.3,      # 돌파 강화
            "pullback_score": 1.0,
            "golden_cross": 1.2,
            "macd_cross_up": 1.1,
            "ma_alignment": 1.2,
            "bullish_volume": 1.2,
            "stoch_cross_up": 0.8,
            "divergence_score": 0.7,    # 다이버전스 약화 (추세 추종)
            "rsi_oversold_bounce": 0.8,
            "bb_squeeze_breakout": 1.3,
        },
        "overheated_threshold": 2,      # 과열 기준 유지
        "news_weight": 0.15,
    },
    "bearish": {
        "description": "하락 추세 — 보수적, 반등 매매 중심",
        "atr_stop_mult": 1.5,       # 손절 타이트
        "atr_tp_mult": 3.0,         # 목표도 보수적
        "max_hold_days": 5,         # 빨리 청산
        "min_tech_score": 5.5,      # 높은 기준
        "top_n": 3,                 # 적은 종목
        "signal_weights": {
            "breakout_score": 0.5,      # 돌파 약화 (가짜 돌파 많음)
            "pullback_score": 1.5,      # 눌림목 강화
            "golden_cross": 0.5,
            "macd_cross_up": 0.8,
            "ma_alignment": 0.5,
            "bullish_volume": 1.0,
            "stoch_cross_up": 1.3,      # 과매도 반등 강화
            "divergence_score": 1.5,    # 다이버전스 강화 (반전 신호)
            "rsi_oversold_bounce": 1.5,
            "bb_squeeze_breakout": 0.7,
        },
        "overheated_threshold": 1,      # 과열 기준 강화
        "news_weight": 0.20,            # 뉴스 비중 높임
    },
    "sideways": {
        "description": "횡보장 — 평균회귀 전략, 밴드 매매",
        "atr_stop_mult": 1.5,
        "atr_tp_mult": 3.0,
        "max_hold_days": 5,
        "min_tech_score": 4.5,
        "top_n": 4,
        "signal_weights": {
            "breakout_score": 0.6,      # 돌파 약화 (가짜 돌파)
            "pullback_score": 1.3,
            "golden_cross": 0.7,
            "macd_cross_up": 0.9,
            "ma_alignment": 0.6,
            "bullish_volume": 1.0,
            "stoch_cross_up": 1.4,      # 과매도 반등 강화
            "divergence_score": 1.3,
            "rsi_oversold_bounce": 1.5,  # RSI 밴드 매매
            "bb_squeeze_breakout": 1.5,  # 스퀴즈 후 폭발
        },
        "overheated_threshold": 2,
        "news_weight": 0.15,
    },
    "volatile": {
        "description": "고변동성 — 매우 보수적, 최소 거래",
        "atr_stop_mult": 2.5,       # 넓은 손절 (변동성 감안)
        "atr_tp_mult": 3.5,
        "max_hold_days": 3,         # 초단기
        "min_tech_score": 6.0,      # 매우 높은 기준
        "top_n": 2,                 # 최소 종목
        "signal_weights": {
            "breakout_score": 0.3,
            "pullback_score": 1.0,
            "golden_cross": 0.3,
            "macd_cross_up": 0.5,
            "ma_alignment": 0.3,
            "bullish_volume": 1.2,
            "stoch_cross_up": 1.0,
            "divergence_score": 1.0,
            "rsi_oversold_bounce": 1.2,
            "bb_squeeze_breakout": 0.5,
        },
        "overheated_threshold": 1,
        "news_weight": 0.25,        # 뉴스 중요도 높음
    },
}


# ══════════════════════════════════════════════════════
#  시장 데이터 수집
# ══════════════════════════════════════════════════════

def _fetch_market_data(days: int = 60) -> Optional[Dict[str, pd.DataFrame]]:
    """SPY, QQQ, VIX 데이터 다운로드."""
    if yf is None:
        logger.warning("yfinance 미설치")
        return None

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days + 10)

    data = {}
    for ticker in ["SPY", "QQQ", "^VIX"]:
        try:
            df = yf.download(ticker, start=start, end=end,
                             interval="1d", progress=False, auto_adjust=False)
            if df is not None and not df.empty:
                df = df.reset_index()
                data[ticker] = df
        except Exception as e:
            logger.warning(f"시장 데이터 다운로드 실패 ({ticker}): {e}")

    return data if data else None


# ══════════════════════════════════════════════════════
#  레짐 감지 핵심 로직
# ══════════════════════════════════════════════════════

def _calc_regime_indicators(df: pd.DataFrame) -> Dict:
    """단일 지수(SPY/QQQ)의 레짐 지표 계산."""
    close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
    high = df["High"].squeeze() if isinstance(df["High"], pd.DataFrame) else df["High"]
    low = df["Low"].squeeze() if isinstance(df["Low"], pd.DataFrame) else df["Low"]
    volume = df["Volume"].squeeze() if isinstance(df["Volume"], pd.DataFrame) else df["Volume"]

    indicators = {}

    # SMA
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    indicators["price_vs_sma20"] = float((close.iloc[-1] / sma20.iloc[-1] - 1) * 100) if pd.notna(sma20.iloc[-1]) else 0
    indicators["price_vs_sma50"] = float((close.iloc[-1] / sma50.iloc[-1] - 1) * 100) if pd.notna(sma50.iloc[-1]) else 0
    indicators["sma20_above_sma50"] = bool(sma20.iloc[-1] > sma50.iloc[-1]) if pd.notna(sma20.iloc[-1]) and pd.notna(sma50.iloc[-1]) else False

    # SMA 기울기 (20일선이 상승/하락 중인지)
    if len(sma20.dropna()) >= 5:
        sma20_slope = (sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100
        indicators["sma20_slope_5d"] = float(sma20_slope)
    else:
        indicators["sma20_slope_5d"] = 0.0

    # RSI 14
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    indicators["rsi"] = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50

    # ADX (추세 강도)
    high_diff = high.diff()
    low_diff = -low.diff()
    pos_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
    neg_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    pos_di = 100 * (pos_dm.rolling(14).mean() / atr14)
    neg_di = 100 * (neg_dm.rolling(14).mean() / atr14)
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di)
    adx = dx.rolling(14).mean()
    indicators["adx"] = float(adx.iloc[-1]) if pd.notna(adx.iloc[-1]) else 20

    # ATR% (변동성)
    indicators["atr_pct"] = float(atr14.iloc[-1] / close.iloc[-1] * 100) if pd.notna(atr14.iloc[-1]) else 1.0

    # 볼린저 밴드 폭
    bb_std = close.rolling(20).std()
    bb_width = (2 * bb_std / sma20 * 100) if pd.notna(sma20.iloc[-1]) else pd.Series([4.0])
    indicators["bb_width"] = float(bb_width.iloc[-1]) if pd.notna(bb_width.iloc[-1]) else 4.0

    # 5일/20일 수익률
    indicators["ret_5d"] = float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) >= 6 else 0
    indicators["ret_20d"] = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) >= 21 else 0

    # 거래량 추세
    vol_avg5 = volume.tail(5).mean()
    vol_avg20 = volume.tail(20).mean()
    indicators["volume_trend"] = float(vol_avg5 / vol_avg20) if vol_avg20 > 0 else 1.0

    return indicators


def detect_market_regime(market_data: Optional[Dict] = None) -> Tuple[str, Dict]:
    """
    시장 레짐 감지.

    Returns:
        (regime_name, details_dict)
    """
    if market_data is None:
        market_data = _fetch_market_data(days=60)

    if not market_data:
        logger.warning("시장 데이터 없음 → neutral 폴백")
        return "bullish", {"reason": "데이터 없음, 기본값 사용", "confidence": 0.0}

    # SPY 기반 분석 (QQQ는 보조)
    spy = market_data.get("SPY")
    qqq = market_data.get("QQQ")
    vix = market_data.get("^VIX")

    if spy is None or len(spy) < 30:
        return "bullish", {"reason": "SPY 데이터 부족", "confidence": 0.0}

    ind = _calc_regime_indicators(spy)

    # QQQ 보조 지표
    qqq_ind = _calc_regime_indicators(qqq) if qqq is not None and len(qqq) >= 30 else {}

    # VIX
    vix_level = None
    if vix is not None and not vix.empty:
        vix_close = vix["Close"].squeeze() if isinstance(vix["Close"], pd.DataFrame) else vix["Close"]
        vix_level = float(vix_close.iloc[-1]) if pd.notna(vix_close.iloc[-1]) else None

    # ── 레짐 판별 로직 ──

    scores = {"bullish": 0, "bearish": 0, "sideways": 0, "volatile": 0}

    # VIX 기반 변동성 판단
    if vix_level is not None:
        if vix_level > 30:
            scores["volatile"] += 4
        elif vix_level > 25:
            scores["volatile"] += 2
        elif vix_level < 15:
            scores["bullish"] += 1

    # ATR 기반 변동성
    if ind["atr_pct"] > 2.5:
        scores["volatile"] += 2
    elif ind["atr_pct"] < 1.0:
        scores["sideways"] += 1

    # ADX 기반 추세 강도
    adx = ind["adx"]
    if adx < 20:
        scores["sideways"] += 3
    elif adx > 30:
        # 강한 추세 → 방향 판별
        if ind["sma20_above_sma50"] and ind["sma20_slope_5d"] > 0:
            scores["bullish"] += 2
        elif not ind["sma20_above_sma50"] and ind["sma20_slope_5d"] < 0:
            scores["bearish"] += 2

    # 가격 vs 이동평균선
    if ind["price_vs_sma20"] > 2 and ind["price_vs_sma50"] > 3:
        scores["bullish"] += 2
    elif ind["price_vs_sma20"] < -2 and ind["price_vs_sma50"] < -3:
        scores["bearish"] += 2
    elif abs(ind["price_vs_sma20"]) < 1.5:
        scores["sideways"] += 1

    # RSI
    rsi = ind["rsi"]
    if rsi > 60:
        scores["bullish"] += 1
    elif rsi < 40:
        scores["bearish"] += 1
    elif 45 < rsi < 55:
        scores["sideways"] += 1

    # 수익률 모멘텀
    if ind["ret_20d"] > 5:
        scores["bullish"] += 2
    elif ind["ret_20d"] < -5:
        scores["bearish"] += 2
    elif abs(ind["ret_20d"]) < 2:
        scores["sideways"] += 1

    if ind["ret_5d"] > 3:
        scores["bullish"] += 1
    elif ind["ret_5d"] < -3:
        scores["bearish"] += 1

    # 볼린저 밴드 폭
    if ind["bb_width"] < 3:
        scores["sideways"] += 1
    elif ind["bb_width"] > 6:
        scores["volatile"] += 1

    # QQQ 확인 (보조)
    if qqq_ind:
        if qqq_ind.get("ret_20d", 0) > 5:
            scores["bullish"] += 1
        elif qqq_ind.get("ret_20d", 0) < -5:
            scores["bearish"] += 1

    # 최종 결정
    regime = max(scores, key=scores.get)
    total_score = sum(scores.values())
    confidence = scores[regime] / total_score if total_score > 0 else 0

    details = {
        "regime": regime,
        "confidence": round(confidence, 3),
        "scores": scores,
        "indicators": {
            "spy_rsi": round(ind["rsi"], 1),
            "spy_adx": round(ind["adx"], 1),
            "spy_ret_5d": round(ind["ret_5d"], 2),
            "spy_ret_20d": round(ind["ret_20d"], 2),
            "spy_vs_sma20": round(ind["price_vs_sma20"], 2),
            "spy_atr_pct": round(ind["atr_pct"], 2),
            "spy_bb_width": round(ind["bb_width"], 2),
            "vix": round(vix_level, 1) if vix_level else None,
        },
        "reason": REGIME_PROFILES[regime]["description"],
    }

    logger.info(
        f"시장 레짐: {regime} (신뢰도 {confidence:.0%}) | "
        f"VIX={vix_level:.1f if vix_level else '?'} ADX={ind['adx']:.1f} "
        f"RSI={ind['rsi']:.1f} 20d={ind['ret_20d']:+.1f}%"
    )

    return regime, details


def get_regime_profile(regime: str) -> Dict:
    """레짐에 맞는 전략 프로파일 반환."""
    return REGIME_PROFILES.get(regime, REGIME_PROFILES["bullish"]).copy()
