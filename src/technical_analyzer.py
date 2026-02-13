"""
기술적 분석 모듈 v2
- 후행 지표 의존 축소
- 진입 타이밍 중심 분석
- 리스크/리워드 비율 계산
- 다중 시간프레임 고려
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple


# ──────────────────────────────────────────────
# 기본 지표 계산 함수
# ──────────────────────────────────────────────

def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Smoothing 방식 RSI"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))

    # 첫 번째 평균은 SMA, 이후는 Wilder's smoothing
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    for i in range(period, len(close)):
        if pd.notna(avg_gain.iloc[i - 1]):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(close: pd.Series, fast=12, slow=26, signal=9) -> Dict[str, pd.Series]:
    ema_fast = calculate_ema(close, fast)
    ema_slow = calculate_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {'macd': macd_line, 'signal': signal_line, 'histogram': histogram}


def calculate_bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> Dict[str, pd.Series]:
    sma = calculate_sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    return {'upper': sma + (std * std_dev), 'middle': sma, 'lower': sma - (std * std_dev)}


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    high_diff = high.diff()
    low_diff = -low.diff()
    pos_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
    neg_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
    atr = calculate_atr(high, low, close, period)
    pos_di = 100 * (pos_dm.rolling(window=period).mean() / atr)
    neg_di = 100 * (neg_dm.rolling(window=period).mean() / atr)
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di)
    return dx.rolling(window=period).mean()


def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                         k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """스토캐스틱 %K, %D"""
    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()
    denom = highest_high - lowest_low
    k = 100 * (close - lowest_low) / denom.replace(0, np.nan)
    d = k.rolling(window=d_period).mean()
    return k, d


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """OBV (On-Balance Volume) — 거래량 흐름 확인"""
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (volume * direction).cumsum()


def calculate_vwap_ratio(high: pd.Series, low: pd.Series, close: pd.Series,
                         volume: pd.Series, period: int = 20) -> pd.Series:
    """현재가 / VWAP 비율 (최근 N일 근사)"""
    typical = (high + low + close) / 3
    cum_tp_vol = (typical * volume).rolling(window=period, min_periods=period).sum()
    cum_vol = volume.rolling(window=period, min_periods=period).sum()
    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return close / vwap


# ──────────────────────────────────────────────
# 핵심 분석 함수
# ──────────────────────────────────────────────

def _detect_pullback_to_support(close: pd.Series, sma20: pd.Series, sma50: pd.Series,
                                bb_lower: pd.Series) -> Dict:
    """
    눌림목 매수 감지 (pullback to support)
    — 상승 추세 중 지지선 근처까지 내려온 뒤 반등 시작점
    — MA 위/아래 모두 허용 (±범위)
    """
    result = {'pullback_to_ma20': False, 'pullback_to_ma50': False,
              'pullback_to_bb_lower': False, 'pullback_score': 0.0}
    if len(close) < 5:
        return result

    cur = close.iloc[-1]
    prev = close.iloc[-2]
    is_bouncing = cur > prev  # 반등 중인지

    # 20일선 근처 (±4%) + 반등 중
    if pd.notna(sma20.iloc[-1]):
        ma20 = sma20.iloc[-1]
        pct_from_ma20 = (cur - ma20) / ma20 * 100
        if -4.0 <= pct_from_ma20 <= 3.0 and is_bouncing:
            result['pullback_to_ma20'] = True
            # MA 아래에서 반등하면 더 높은 점수 (진정한 눌림목)
            result['pullback_score'] += 2.0 if pct_from_ma20 < 0 else 1.5

    # 50일선 근처 (±3%) + 반등 (더 강한 지지)
    if sma50 is not None and len(sma50) > 0 and pd.notna(sma50.iloc[-1]):
        ma50 = sma50.iloc[-1]
        pct_from_ma50 = (cur - ma50) / ma50 * 100
        if -3.0 <= pct_from_ma50 <= 3.0 and is_bouncing:
            result['pullback_to_ma50'] = True
            result['pullback_score'] += 2.5

    # 볼린저 하단 근처 (±4%) + 반등
    if pd.notna(bb_lower.iloc[-1]):
        bl = bb_lower.iloc[-1]
        pct_from_bl = (cur - bl) / bl * 100
        if -2.0 <= pct_from_bl <= 5.0 and is_bouncing:
            result['pullback_to_bb_lower'] = True
            result['pullback_score'] += 1.5

    return result


def _detect_volume_breakout(close: pd.Series, volume: pd.Series, high: pd.Series) -> Dict:
    """
    거래량 돌파 감지
    — 최근 N일 고점 돌파 + 거래량 급증
    """
    result = {'breakout_detected': False, 'breakout_type': None, 'breakout_score': 0.0}
    if len(close) < 20:
        return result

    cur_close = close.iloc[-1]
    cur_vol = volume.iloc[-1]
    vol_avg20 = volume.iloc[-20:].mean()

    # 20일 최고가 돌파 확인
    high_20d = high.iloc[-21:-1].max()  # 오늘 제외 최근 20일 고가
    if pd.notna(high_20d) and cur_close > high_20d:
        vol_ratio = cur_vol / vol_avg20 if vol_avg20 > 0 else 1
        if vol_ratio >= 1.5:
            result['breakout_detected'] = True
            result['breakout_type'] = '20d_high_breakout'
            result['breakout_score'] = min(3.0, 1.5 + (vol_ratio - 1.5) * 0.5)

    # 10일 최고가 돌파 (약한 신호)
    elif len(high) >= 11:
        high_10d = high.iloc[-11:-1].max()
        if pd.notna(high_10d) and cur_close > high_10d:
            vol_ratio = cur_vol / vol_avg20 if vol_avg20 > 0 else 1
            if vol_ratio >= 1.3:
                result['breakout_detected'] = True
                result['breakout_type'] = '10d_high_breakout'
                result['breakout_score'] = min(2.0, 1.0 + (vol_ratio - 1.3) * 0.4)

    return result


def _detect_rsi_divergence(close: pd.Series, rsi: pd.Series, lookback: int = 10) -> Dict:
    """
    RSI 다이버전스 감지
    — 가격 신저점 but RSI 높아지면 → 강세 다이버전스 (매수 신호)
    — 가격 신고점 but RSI 낮아지면 → 약세 다이버전스 (매도 신호)
    """
    result = {'bullish_divergence': False, 'bearish_divergence': False, 'divergence_score': 0.0}
    if len(close) < lookback + 5 or len(rsi) < lookback + 5:
        return result

    recent_close = close.iloc[-lookback:]
    recent_rsi = rsi.iloc[-lookback:]

    # 유효한 데이터인지 확인
    if recent_close.isna().any() or recent_rsi.isna().any():
        return result

    # 최근 구간을 전반부/후반부로 분할
    half = lookback // 2
    close_first_half_min = recent_close.iloc[:half].min()
    close_second_half_min = recent_close.iloc[half:].min()
    rsi_first_half_min = recent_rsi.iloc[:half].min()
    rsi_second_half_min = recent_rsi.iloc[half:].min()

    # 강세 다이버전스: 가격 하락인데 RSI 상승
    if close_second_half_min < close_first_half_min and rsi_second_half_min > rsi_first_half_min:
        result['bullish_divergence'] = True
        result['divergence_score'] = 2.0

    close_first_half_max = recent_close.iloc[:half].max()
    close_second_half_max = recent_close.iloc[half:].max()
    rsi_first_half_max = recent_rsi.iloc[:half].max()
    rsi_second_half_max = recent_rsi.iloc[half:].max()

    # 약세 다이버전스: 가격 상승인데 RSI 하락
    if close_second_half_max > close_first_half_max and rsi_second_half_max < rsi_first_half_max:
        result['bearish_divergence'] = True
        result['divergence_score'] = -1.5

    return result


def _calculate_risk_reward(close: pd.Series, atr: float, sma20_val: float,
                           bb_lower_val: float) -> Dict:
    """
    리스크/리워드 계산
    — 손절가, 목표가, R:R 비율 제시
    """
    cur = close.iloc[-1]
    result = {'stop_loss': None, 'target_price': None, 'risk_reward_ratio': 0.0}

    if cur <= 0 or atr <= 0:
        return result

    # 손절가: ATR 1.5배 아래 또는 지지선 아래
    atr_stop = cur - atr * 1.5
    support_candidates = [v for v in [sma20_val, bb_lower_val] if v is not None and pd.notna(v) and v < cur]
    if support_candidates:
        support_stop = max(support_candidates) * 0.99  # 지지선 1% 아래
        stop_loss = max(atr_stop, support_stop)  # 더 높은(덜 위험한) 쪽 선택
    else:
        stop_loss = atr_stop

    # 목표가: ATR 2~3배 위
    target_price = cur + atr * 2.5

    risk = cur - stop_loss
    reward = target_price - cur

    if risk > 0:
        result['stop_loss'] = round(stop_loss, 2)
        result['target_price'] = round(target_price, 2)
        result['risk_reward_ratio'] = round(reward / risk, 2)

    return result


# ──────────────────────────────────────────────
# 메인 분석 함수
# ──────────────────────────────────────────────

def analyze_stock_technical(df: pd.DataFrame) -> Optional[Dict]:
    """
    종목 기술적 분석 v2

    개선 사항:
    - 눌림목/지지선 반등 감지 (진입 타이밍 중시)
    - 거래량 돌파 패턴
    - RSI 다이버전스
    - 리스크/리워드 비율
    - 스토캐스틱 추가
    - OBV, VWAP 비율
    """
    if df is None or len(df) < 30:
        return None

    df = df.sort_values('Date').reset_index(drop=True)
    close = df['Close']
    high = df.get('High', close)
    low = df.get('Low', close)
    volume = df['Volume']

    try:
        # ── 기본 지표 ──
        sma5 = calculate_sma(close, 5)
        sma10 = calculate_sma(close, 10)
        sma20 = calculate_sma(close, 20)
        sma50 = calculate_sma(close, 50) if len(df) >= 50 else None

        current_price = close.iloc[-1]
        prev_price = close.iloc[-2] if len(close) >= 2 else current_price

        # 이평선 괴리율
        ma5_dev = ((current_price - sma5.iloc[-1]) / sma5.iloc[-1] * 100) if pd.notna(sma5.iloc[-1]) else 0
        ma20_dev = ((current_price - sma20.iloc[-1]) / sma20.iloc[-1] * 100) if pd.notna(sma20.iloc[-1]) else 0

        # 크로스 체크
        golden_cross = dead_cross = False
        if len(df) >= 21 and pd.notna(sma5.iloc[-2]) and pd.notna(sma20.iloc[-2]):
            if sma5.iloc[-2] <= sma20.iloc[-2] and sma5.iloc[-1] > sma20.iloc[-1]:
                golden_cross = True
            elif sma5.iloc[-2] >= sma20.iloc[-2] and sma5.iloc[-1] < sma20.iloc[-1]:
                dead_cross = True

        # 이평선 정배열
        ma_alignment = False
        if pd.notna(sma5.iloc[-1]) and pd.notna(sma10.iloc[-1]) and pd.notna(sma20.iloc[-1]):
            ma_alignment = (sma5.iloc[-1] > sma10.iloc[-1] > sma20.iloc[-1])

        # RSI
        rsi = calculate_rsi(close, 14)
        current_rsi = rsi.iloc[-1] if pd.notna(rsi.iloc[-1]) else 50

        # MACD
        macd_data = calculate_macd(close)
        macd_val = macd_data['macd'].iloc[-1] if pd.notna(macd_data['macd'].iloc[-1]) else 0
        signal_val = macd_data['signal'].iloc[-1] if pd.notna(macd_data['signal'].iloc[-1]) else 0
        hist_val = macd_data['histogram'].iloc[-1] if pd.notna(macd_data['histogram'].iloc[-1]) else 0

        macd_cross_up = macd_cross_down = False
        if len(df) >= 27:
            prev_hist = macd_data['histogram'].iloc[-2]
            curr_hist = macd_data['histogram'].iloc[-1]
            if pd.notna(prev_hist) and pd.notna(curr_hist):
                macd_cross_up = prev_hist < 0 and curr_hist > 0
                macd_cross_down = prev_hist > 0 and curr_hist < 0

        # 볼린저 밴드
        bb = calculate_bollinger_bands(close, 20)
        bb_upper = bb['upper'].iloc[-1] if pd.notna(bb['upper'].iloc[-1]) else current_price
        bb_middle = bb['middle'].iloc[-1] if pd.notna(bb['middle'].iloc[-1]) else current_price
        bb_lower = bb['lower'].iloc[-1] if pd.notna(bb['lower'].iloc[-1]) else current_price
        bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5

        bb_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0
        bb_squeeze = bb_width < 0.08  # 스퀴즈 기준 강화

        # ATR
        atr = calculate_atr(high, low, close, 14)
        current_atr = atr.iloc[-1] if pd.notna(atr.iloc[-1]) else 0
        atr_percent = (current_atr / current_price * 100) if current_price > 0 else 0

        # ADX
        adx = calculate_adx(high, low, close, 14)
        current_adx = adx.iloc[-1] if pd.notna(adx.iloc[-1]) else 0

        # 스토캐스틱
        stoch_k, stoch_d = calculate_stochastic(high, low, close)
        current_stoch_k = stoch_k.iloc[-1] if pd.notna(stoch_k.iloc[-1]) else 50
        current_stoch_d = stoch_d.iloc[-1] if pd.notna(stoch_d.iloc[-1]) else 50
        stoch_oversold = current_stoch_k < 20 and current_stoch_d < 20
        stoch_cross_up = False
        if len(stoch_k) >= 2 and pd.notna(stoch_k.iloc[-2]) and pd.notna(stoch_d.iloc[-2]):
            stoch_cross_up = stoch_k.iloc[-2] < stoch_d.iloc[-2] and stoch_k.iloc[-1] > stoch_d.iloc[-1]

        # OBV 추세
        obv = calculate_obv(close, volume)
        obv_rising = False
        if len(obv) >= 5:
            obv_sma5 = obv.iloc[-5:].mean()
            obv_sma10 = obv.iloc[-10:].mean() if len(obv) >= 10 else obv_sma5
            obv_rising = obv_sma5 > obv_sma10

        # VWAP 비율
        vwap_ratio_s = calculate_vwap_ratio(high, low, close, volume, 20)
        current_vwap_ratio = vwap_ratio_s.iloc[-1] if pd.notna(vwap_ratio_s.iloc[-1]) else 1.0

        # 거래량 분석
        vol_avg20 = volume.tail(20).mean()
        vol_ratio = volume.iloc[-1] / vol_avg20 if vol_avg20 > 0 else 1
        price_up = current_price > prev_price
        bullish_volume = price_up and vol_ratio > 1.5

        # ── 고급 패턴 분석 ──
        sma50_series = sma50 if sma50 is not None else pd.Series([np.nan] * len(close))
        pullback = _detect_pullback_to_support(close, sma20, sma50_series, bb['lower'])
        breakout = _detect_volume_breakout(close, volume, high)
        divergence = _detect_rsi_divergence(close, rsi)
        risk_reward = _calculate_risk_reward(
            close, current_atr,
            sma20.iloc[-1] if pd.notna(sma20.iloc[-1]) else None,
            bb_lower
        )

        # ── 연속 상승/하락일 계산 ──
        consecutive_up = 0
        for i in range(len(close) - 1, 0, -1):
            if close.iloc[i] > close.iloc[i - 1]:
                consecutive_up += 1
            else:
                break

        return {
            # 가격 정보
            'current_price': float(current_price),
            'prev_price': float(prev_price),
            'price_change_pct': float((current_price - prev_price) / prev_price * 100),

            # 이동평균선
            'sma5': float(sma5.iloc[-1]) if pd.notna(sma5.iloc[-1]) else None,
            'sma10': float(sma10.iloc[-1]) if pd.notna(sma10.iloc[-1]) else None,
            'sma20': float(sma20.iloc[-1]) if pd.notna(sma20.iloc[-1]) else None,
            'sma50': float(sma50.iloc[-1]) if sma50 is not None and pd.notna(sma50.iloc[-1]) else None,
            'ma5_deviation': float(ma5_dev),
            'ma20_deviation': float(ma20_dev),
            'golden_cross': golden_cross,
            'dead_cross': dead_cross,
            'ma_alignment': ma_alignment,

            # RSI
            'rsi': float(current_rsi),
            'rsi_oversold': current_rsi < 30,
            'rsi_overbought': current_rsi > 70,

            # MACD
            'macd': float(macd_val),
            'macd_signal': float(signal_val),
            'macd_histogram': float(hist_val),
            'macd_cross_up': macd_cross_up,
            'macd_cross_down': macd_cross_down,

            # 볼린저 밴드
            'bb_upper': float(bb_upper),
            'bb_middle': float(bb_middle),
            'bb_lower': float(bb_lower),
            'bb_position': float(bb_position),
            'bb_squeeze': bb_squeeze,
            'bb_width': float(bb_width),

            # 변동성 & 추세
            'atr': float(current_atr),
            'atr_percent': float(atr_percent),
            'adx': float(current_adx),
            'strong_trend': current_adx > 25,

            # 스토캐스틱
            'stoch_k': float(current_stoch_k),
            'stoch_d': float(current_stoch_d),
            'stoch_oversold': stoch_oversold,
            'stoch_cross_up': stoch_cross_up,

            # OBV / VWAP
            'obv_rising': obv_rising,
            'vwap_ratio': float(current_vwap_ratio),

            # 거래량
            'volume': float(volume.iloc[-1]),
            'volume_ratio': float(vol_ratio),
            'bullish_volume': bullish_volume,

            # 연속 상승일
            'consecutive_up': consecutive_up,

            # ★ 진입 타이밍 패턴 (v2 신규)
            'pullback': pullback,
            'breakout': breakout,
            'divergence': divergence,

            # ★ 리스크/리워드 (v2 신규)
            'risk_reward': risk_reward,
        }

    except Exception as e:
        return None


# ──────────────────────────────────────────────
# 리스크 점수
# ──────────────────────────────────────────────

def calculate_risk_score(analysis: Dict) -> float:
    """위험도 점수 (0~10, 높을수록 위험)"""
    if not analysis:
        return 5.0

    risk = 0.0
    is_breakout = analysis.get('breakout', {}).get('breakout_detected', False)

    # 과매수 (RSI) — 돌파 시 완화
    rsi = analysis.get('rsi', 50)
    if rsi > 80:
        risk += 1.5 if is_breakout else 3.0
    elif rsi > 70:
        risk += 0.5 if is_breakout else 1.5

    # 스토캐스틱 과매수
    if analysis.get('stoch_k', 50) > 80 and analysis.get('stoch_d', 50) > 80:
        risk += 0.5 if is_breakout else 1.5

    # 데드크로스
    if analysis.get('dead_cross', False):
        risk += 2.0

    # MACD 하향
    if analysis.get('macd_cross_down', False):
        risk += 1.5

    # 약세 다이버전스
    if analysis.get('divergence', {}).get('bearish_divergence', False):
        risk += 2.0

    # 볼린저밴드 상단 과열
    bb_pos = analysis.get('bb_position', 0.5)
    if bb_pos > 0.95:
        risk += 2.0
    elif bb_pos > 0.85:
        risk += 1.0

    # 거래량 없는 상승
    if analysis.get('price_change_pct', 0) > 2 and analysis.get('volume_ratio', 1) < 0.8:
        risk += 2.0

    # 과도한 이평선 괴리
    ma5_dev = abs(analysis.get('ma5_deviation', 0))
    if ma5_dev > 15:
        risk += 2.0
    elif ma5_dev > 10:
        risk += 1.0

    # 연속 상승 과열
    if analysis.get('consecutive_up', 0) >= 7:
        risk += 2.5
    elif analysis.get('consecutive_up', 0) >= 5:
        risk += 1.5

    # OBV 하락 중 가격 상승 (약세 신호)
    if not analysis.get('obv_rising', True) and analysis.get('price_change_pct', 0) > 0:
        risk += 1.0

    # VWAP 위로 과도하게 이격
    vwap_r = analysis.get('vwap_ratio', 1.0)
    if vwap_r > 1.08:
        risk += 1.0

    return min(10.0, risk)


# ──────────────────────────────────────────────
# 확증 지표
# ──────────────────────────────────────────────

def calculate_confirmation_score(analysis: Dict) -> int:
    """긍정 신호 개수 (0~10)"""
    if not analysis:
        return 0

    c = 0
    if analysis.get('golden_cross', False):
        c += 1
    if analysis.get('ma_alignment', False):
        c += 1
    if analysis.get('macd_cross_up', False):
        c += 1
    rsi = analysis.get('rsi', 50)
    if 30 < rsi < 65:
        c += 1
    if analysis.get('bullish_volume', False):
        c += 1
    if analysis.get('strong_trend', False):
        c += 1
    bb_pos = analysis.get('bb_position', 0.5)
    if 0.2 < bb_pos < 0.7:
        c += 1
    if analysis.get('stoch_cross_up', False):
        c += 1
    if analysis.get('obv_rising', False):
        c += 1
    if analysis.get('divergence', {}).get('bullish_divergence', False):
        c += 1
    return c


# ──────────────────────────────────────────────
# 종합 기술 점수 v2
# ──────────────────────────────────────────────

def _load_signal_weights() -> Dict:
    """config/signal_weights.json에서 가중치 로드. 없으면 기본값 1.0."""
    import json
    from pathlib import Path
    path = Path("config/signal_weights.json")
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def calculate_technical_score(analysis: Dict) -> float:
    """
    기술적 분석 점수 v3 (0~10)

    개선 핵심:
    ① 진입 타이밍 패턴 (눌림목·돌파·다이버전스) 가중치 ↑
    ② 후행 지표 (골든크로스·정배열) 가중치 ↓
    ③ 과열 종목 강력 페널티
    ④ R:R 비율 반영
    ⑤ signal_weights.json 기반 동적 가중치 적용 (자동 튜닝)
    """
    if not analysis:
        return 0.0

    sw = _load_signal_weights()  # 동적 가중치
    def w(key: str) -> float:
        return sw.get(key, 1.0)

    score = 0.0

    # ──── A. 진입 타이밍 신호 (최대 +5.0, 핵심!) ────

    # A1. 눌림목 매수 (pullback to support)
    pullback = analysis.get('pullback', {})
    score += pullback.get('pullback_score', 0.0) * w('pullback_score')

    # A2. 거래량 돌파
    breakout = analysis.get('breakout', {})
    score += breakout.get('breakout_score', 0.0) * w('breakout_score')

    # A3. RSI 강세 다이버전스
    divergence = analysis.get('divergence', {})
    score += divergence.get('divergence_score', 0.0) * w('divergence_score')

    # A4. 스토캐스틱 과매도 반등
    if analysis.get('stoch_oversold', False) and analysis.get('stoch_cross_up', False):
        score += 1.5 * w('stoch_cross_up')
    elif analysis.get('stoch_cross_up', False):
        score += 0.5 * w('stoch_cross_up')

    # ──── B. 추세 확인 신호 (최대 +3.0, 보조) ────

    # B1. 골든크로스 / 데드크로스
    if analysis.get('golden_cross', False):
        score += 1.0 * w('golden_cross')
    elif analysis.get('dead_cross', False):
        score -= 2.0

    # B2. 이평선 정배열 (추세 확인용)
    if analysis.get('ma_alignment', False):
        score += 0.8 * w('ma_alignment')

    # B3. MACD
    if analysis.get('macd_cross_up', False):
        score += 1.0 * w('macd_cross_up')
    elif analysis.get('macd_cross_down', False):
        score -= 1.5

    if analysis.get('macd_histogram', 0) > 0:
        score += 0.3

    # ──── C. 거래량 확인 (최대 +2.0) ────

    if analysis.get('bullish_volume', False):
        score += 1.5 * w('bullish_volume')
    elif analysis.get('volume_ratio', 1) > 2.0:
        score += 0.5

    # OBV 상승 (거래량 흐름 긍정)
    if analysis.get('obv_rising', False):
        score += 0.5 * w('obv_rising')

    # ──── D. 과열 페널티 (최대 -5.0) ────
    # 돌파 감지 시 RSI/BB 과열 페널티 완화 (돌파 시 RSI가 높은 건 자연스러움)
    is_breakout = analysis.get('breakout', {}).get('breakout_detected', False)

    rsi = analysis.get('rsi', 50)
    if rsi > 80:
        score -= 1.0 if is_breakout else 2.5  # 돌파 시 페널티 완화
    elif rsi > 70:
        score -= 0.5 if is_breakout else 1.5
    elif 30 < rsi < 50:  # 과매도 탈출 구간 (반등 유리)
        score += 0.8 * w('rsi_oversold_bounce')
    elif 50 <= rsi < 60:  # 중립 회복 구간
        score += 0.3

    # 볼린저 밴드
    bb_pos = analysis.get('bb_position', 0.5)
    if bb_pos > 0.95:
        score -= 0.5 if is_breakout else 2.0  # 돌파 시 상단 이탈은 자연스러움
    elif bb_pos > 0.85:
        score -= 0.3 if is_breakout else 1.0

    # 연속 상승 과열 (기준 완화: 5일부터 페널티)
    consec = analysis.get('consecutive_up', 0)
    if consec >= 7:
        score -= 2.5
    elif consec >= 5:
        score -= 1.5

    # 과도한 이평선 괴리 (이미 너무 올랐음)
    ma5_dev = analysis.get('ma5_deviation', 0)
    if ma5_dev > 10:
        score -= 1.5
    elif ma5_dev > 7:
        score -= 0.5

    # VWAP 대비 과도 이격
    if analysis.get('vwap_ratio', 1.0) > 1.05:
        score -= 0.5

    # ──── E. 추세 강도 & 볼린저 스퀴즈 ────

    if analysis.get('strong_trend', False):
        score += 0.5 * w('strong_trend')

    # 볼린저 스퀴즈 후 돌파 (폭발 직전)
    if analysis.get('bb_squeeze', False) and analysis.get('breakout', {}).get('breakout_detected', False):
        score += 1.5 * w('bb_squeeze_breakout')

    # ──── F. R:R 비율 보너스 ────
    rr = analysis.get('risk_reward', {}).get('risk_reward_ratio', 0)
    if rr >= 3.0:
        score += 1.0 * w('rr_bonus')
    elif rr >= 2.0:
        score += 0.5 * w('rr_bonus')
    elif 0 < rr < 1.0:
        score -= 0.5

    # ──── G. 리스크 필터링 ────
    risk = calculate_risk_score(analysis)
    if risk > 7:
        score -= 3.0
    elif risk > 5:
        score -= 1.5

    # ──── H. 확증 보너스 ────
    confirmations = calculate_confirmation_score(analysis)
    if confirmations >= 6:
        score += 1.0
    elif confirmations >= 5:
        score += 0.5
    elif confirmations <= 1:
        score -= 0.5

    # 0~10 클리핑
    final = max(0.0, min(10.0, score))

    # 메타 정보 추가
    analysis['risk_score'] = risk
    analysis['confirmation_count'] = confirmations
    analysis['final_tech_score'] = final

    return final
