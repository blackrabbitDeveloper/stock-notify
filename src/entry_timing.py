"""
진입 타이밍 정밀화 모듈.

1. 볼린저밴드 스퀴즈 → 확장 패턴 감지
2. 거래량 클라이맥스 / 축소 후 폭발
3. 캔들스틱 패턴 (망치형, 장악형)
4. 섹터 로테이션 (강세 섹터 보너스)
"""
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  1. 볼린저밴드 스퀴즈 → 확장 패턴
# ══════════════════════════════════════════════════════

def detect_bb_squeeze_expansion(df: pd.DataFrame) -> Dict:
    """
    볼린저밴드 스퀴즈 후 상방 확장 감지.
    스퀴즈(밴드 축소) 후 가격이 상단 밴드 돌파 시 강한 매수 신호.

    Returns:
        {
            "squeeze_expansion": bool,
            "expansion_direction": "up" | "down" | None,
            "squeeze_bars": int,      # 스퀴즈 지속 기간
            "expansion_score": float, # 0 ~ 2.0
        }
    """
    result = {
        "squeeze_expansion": False,
        "expansion_direction": None,
        "squeeze_bars": 0,
        "expansion_score": 0.0,
    }

    if len(df) < 30:
        return result

    close = df["Close"]
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    width = (upper - lower) / sma20

    if width.isna().all():
        return result

    # 최근 20일간 밴드폭 분석
    recent_width = width.tail(20).dropna()
    if len(recent_width) < 10:
        return result

    # 스퀴즈 판단: 최근 밴드폭이 20일 평균의 70% 이하
    avg_width = recent_width.mean()
    min_width = recent_width.min()

    # 최근 5일간 스퀴즈였는지 확인
    squeeze_days = 0
    for w in recent_width.iloc[-10:-1]:  # 오늘 제외 최근 10일
        if w < avg_width * 0.75:
            squeeze_days += 1

    result["squeeze_bars"] = squeeze_days

    if squeeze_days < 3:
        return result  # 충분한 스퀴즈 아님

    # 오늘 확장 감지
    today_width = width.iloc[-1]
    yesterday_width = width.iloc[-2] if len(width) >= 2 else today_width
    current = close.iloc[-1]
    upper_today = upper.iloc[-1]
    lower_today = lower.iloc[-1]

    if pd.isna(today_width) or pd.isna(upper_today):
        return result

    # 밴드 확장 시작 (오늘 밴드폭 > 어제)
    expanding = today_width > yesterday_width * 1.1

    if expanding:
        if current > upper_today * 0.98:
            result["squeeze_expansion"] = True
            result["expansion_direction"] = "up"
            # 스퀴즈가 길수록 + 상단 돌파가 강할수록 높은 점수
            result["expansion_score"] = min(2.0, 0.5 + squeeze_days * 0.15 +
                                            (current - upper_today) / upper_today * 50)
        elif current < lower_today * 1.02:
            result["squeeze_expansion"] = True
            result["expansion_direction"] = "down"
            result["expansion_score"] = 0.0  # 하방 확장은 매도 신호

    return result


# ══════════════════════════════════════════════════════
#  2. 거래량 패턴 정밀 분석
# ══════════════════════════════════════════════════════

def detect_volume_pattern(df: pd.DataFrame) -> Dict:
    """
    거래량 패턴 감지:
    - 축소 후 폭발 (accumulation → breakout)
    - 가격 상승 + 거래량 증가 확인 (건전한 상승)
    - 드라이업(dry up) 후 반등

    Returns:
        {
            "volume_explosion": bool,
            "dry_up_reversal": bool,
            "healthy_rise": bool,
            "volume_score": float,  # 0 ~ 1.5
        }
    """
    result = {
        "volume_explosion": False,
        "dry_up_reversal": False,
        "healthy_rise": False,
        "volume_score": 0.0,
    }

    if len(df) < 20:
        return result

    close = df["Close"]
    volume = df["Volume"]

    vol_avg20 = volume.tail(20).mean()
    vol_avg5 = volume.tail(5).mean()
    today_vol = volume.iloc[-1]
    today_close = close.iloc[-1]
    yesterday_close = close.iloc[-2] if len(close) >= 2 else today_close

    if vol_avg20 <= 0:
        return result

    vol_ratio = today_vol / vol_avg20

    # 축소 후 폭발: 최근 5일 평균 거래량 < 20일 평균의 60% → 오늘 200% 이상
    if vol_avg5 < vol_avg20 * 0.6 and vol_ratio >= 2.0:
        if today_close > yesterday_close:
            result["volume_explosion"] = True
            result["volume_score"] += min(1.0, (vol_ratio - 2.0) * 0.3 + 0.5)

    # 드라이업 후 반등: 3일간 거래량 감소 → 오늘 반등
    if len(volume) >= 5:
        recent_vols = volume.tail(5).values
        declining = all(recent_vols[i] < recent_vols[i - 1] for i in range(1, 4))
        if declining and vol_ratio >= 1.3 and today_close > yesterday_close:
            result["dry_up_reversal"] = True
            result["volume_score"] += 0.5

    # 건전한 상승: 가격↑ + 거래량↑ (최근 3일)
    if len(df) >= 4:
        recent = df.tail(4)
        price_up = all(recent["Close"].iloc[i] > recent["Close"].iloc[i - 1] for i in range(1, 4))
        vol_up = all(recent["Volume"].iloc[i] > recent["Volume"].iloc[i - 1] for i in range(1, 4))
        if price_up and vol_up:
            result["healthy_rise"] = True
            result["volume_score"] += 0.5

    result["volume_score"] = round(min(1.5, result["volume_score"]), 3)
    return result


# ══════════════════════════════════════════════════════
#  3. 캔들스틱 패턴
# ══════════════════════════════════════════════════════

def detect_candle_patterns(df: pd.DataFrame) -> Dict:
    """
    핵심 캔들스틱 패턴 감지.

    Returns:
        {
            "hammer": bool,           # 망치형 (하락 후 반전)
            "bullish_engulfing": bool, # 강세 장악형
            "morning_star": bool,      # 모닝스타
            "candle_score": float,     # 0 ~ 1.5
        }
    """
    result = {
        "hammer": False,
        "bullish_engulfing": False,
        "morning_star": False,
        "candle_score": 0.0,
    }

    if len(df) < 5:
        return result

    o = df["Open"].values if "Open" in df.columns else df["Close"].values
    h = df["High"].values if "High" in df.columns else df["Close"].values
    l = df["Low"].values if "Low" in df.columns else df["Close"].values
    c = df["Close"].values

    # 최근 캔들
    o1, h1, l1, c1 = o[-1], h[-1], l[-1], c[-1]
    o2, h2, l2, c2 = o[-2], h[-2], l[-2], c[-2]

    body1 = abs(c1 - o1)
    body2 = abs(c2 - o2)
    range1 = h1 - l1 if h1 > l1 else 0.001

    # 망치형: 하방 그림자가 몸통의 2배 이상, 상방 그림자 짧음
    lower_shadow = min(o1, c1) - l1
    upper_shadow = h1 - max(o1, c1)
    if lower_shadow > body1 * 2 and upper_shadow < body1 * 0.5 and c1 > o1:
        # 이전 2일이 하락이면 반전 신호
        if c[-3] > c[-2]:
            result["hammer"] = True
            result["candle_score"] += 1.0

    # 강세 장악형: 어제 음봉, 오늘 양봉이 어제를 완전히 감쌈
    if c2 < o2 and c1 > o1:  # 어제 음봉, 오늘 양봉
        if o1 <= c2 and c1 >= o2:  # 오늘이 어제를 감쌈
            result["bullish_engulfing"] = True
            result["candle_score"] += 1.0

    # 모닝스타: 3일 패턴 (큰 음봉 → 작은 캔들 → 큰 양봉)
    if len(df) >= 4:
        o3, c3 = o[-3], c[-3]
        body3 = abs(c3 - o3)
        if c3 < o3 and body3 > body2 * 2:  # 3일 전 큰 음봉
            if body2 < body3 * 0.3:  # 2일 전 작은 캔들
                if c1 > o1 and body1 > body2 * 2:  # 오늘 큰 양봉
                    result["morning_star"] = True
                    result["candle_score"] += 1.5

    result["candle_score"] = round(min(1.5, result["candle_score"]), 3)
    return result


# ══════════════════════════════════════════════════════
#  4. 섹터 로테이션
# ══════════════════════════════════════════════════════

# 섹터 분류 (yfinance sector 기준)
SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
}


def calculate_sector_momentum(period_days: int = 20) -> Dict[str, float]:
    """
    섹터별 모멘텀 계산.
    Returns: {sector: momentum_score}
    """
    import yfinance as yf

    results = {}
    try:
        etf_tickers = list(SECTOR_ETFS.values())
        data = yf.download(etf_tickers, period=f"{period_days + 5}d",
                           group_by="ticker", progress=False)

        for sector, etf in SECTOR_ETFS.items():
            try:
                if len(etf_tickers) > 1:
                    closes = data[etf]["Close"].dropna()
                else:
                    closes = data["Close"].dropna()

                if len(closes) < 10:
                    continue

                # 모멘텀: 기간 수익률 + 추세 강도
                ret = (closes.iloc[-1] / closes.iloc[0] - 1) * 100
                # 최근 5일 vs 전체 기간
                ret_5d = (closes.iloc[-1] / closes.iloc[-6] - 1) * 100 if len(closes) >= 6 else ret

                # 가속도 보너스
                momentum = ret * 0.6 + ret_5d * 0.4
                results[sector] = round(momentum, 2)
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"섹터 모멘텀 계산 실패: {e}")

    return results


def get_sector_score(ticker_sector: str, sector_momentum: Dict[str, float]) -> float:
    """
    종목의 섹터 모멘텀에 따른 보너스 점수.
    범위: -1.0 ~ +1.0
    """
    if not sector_momentum or not ticker_sector:
        return 0.0

    momentum = sector_momentum.get(ticker_sector)
    if momentum is None:
        return 0.0

    # 전체 섹터 대비 상대 강도
    all_values = list(sector_momentum.values())
    if not all_values:
        return 0.0

    avg = np.mean(all_values)
    std = np.std(all_values) if len(all_values) > 1 else 1.0
    if std == 0:
        std = 1.0

    # Z-score → 점수 변환
    z = (momentum - avg) / std
    score = max(-1.0, min(1.0, z * 0.5))
    return round(score, 3)


# ══════════════════════════════════════════════════════
#  5. 통합 진입 타이밍 점수
# ══════════════════════════════════════════════════════

def calculate_entry_timing_score(df: pd.DataFrame) -> Dict:
    """
    진입 타이밍 종합 점수.
    기존 기술적 점수에 가감.

    Returns:
        {
            "timing_score": float,       # -2.0 ~ +5.0
            "bb_squeeze": {...},
            "volume_pattern": {...},
            "candle_pattern": {...},
            "details": str,
        }
    """
    bb = detect_bb_squeeze_expansion(df)
    vol = detect_volume_pattern(df)
    candle = detect_candle_patterns(df)

    score = 0.0
    details = []

    # 볼린저 스퀴즈 확장
    if bb["squeeze_expansion"] and bb["expansion_direction"] == "up":
        score += bb["expansion_score"]
        details.append(f"BB확장↑({bb['squeeze_bars']}일)")
    elif bb["squeeze_expansion"] and bb["expansion_direction"] == "down":
        score -= 1.0
        details.append("BB확장↓")

    # 거래량 패턴
    score += vol["volume_score"]
    if vol["volume_explosion"]:
        details.append("거래량폭발")
    if vol["dry_up_reversal"]:
        details.append("드라이업반전")
    if vol["healthy_rise"]:
        details.append("건전상승")

    # 캔들 패턴
    score += candle["candle_score"]
    if candle["hammer"]:
        details.append("망치형")
    if candle["bullish_engulfing"]:
        details.append("장악형")
    if candle["morning_star"]:
        details.append("모닝스타")

    return {
        "timing_score": round(max(-2.0, min(5.0, score)), 3),
        "bb_squeeze": bb,
        "volume_pattern": vol,
        "candle_pattern": candle,
        "details": ", ".join(details) if details else "패턴 없음",
    }
