"""
기술적 분석 모듈
단기 매매를 위한 각종 지표 계산
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional

def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """단순 이동평균"""
    return series.rolling(window=period, min_periods=period).mean()

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """지수 이동평균"""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()

def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Relative Strength Index)"""
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(close: pd.Series, fast=12, slow=26, signal=9) -> Dict[str, pd.Series]:
    """MACD 지표"""
    ema_fast = calculate_ema(close, fast)
    ema_slow = calculate_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    
    return {
        'macd': macd_line,
        'signal': signal_line,
        'histogram': histogram
    }

def calculate_bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> Dict[str, pd.Series]:
    """볼린저 밴드"""
    sma = calculate_sma(close, period)
    std = close.rolling(window=period).std()
    
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    
    return {
        'upper': upper,
        'middle': sma,
        'lower': lower
    }

def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (변동성 지표)"""
    prev_close = close.shift(1)
    
    tr1 = high - low
    tr2 = abs(high - prev_close)
    tr3 = abs(low - prev_close)
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    return atr

def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ADX (Average Directional Index) - 추세 강도"""
    # +DM, -DM 계산
    high_diff = high.diff()
    low_diff = -low.diff()
    
    pos_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
    neg_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
    
    # ATR
    atr = calculate_atr(high, low, close, period)
    
    # +DI, -DI
    pos_di = 100 * (pos_dm.rolling(window=period).mean() / atr)
    neg_di = 100 * (neg_dm.rolling(window=period).mean() / atr)
    
    # DX, ADX
    dx = 100 * abs(pos_di - neg_di) / (pos_di + neg_di)
    adx = dx.rolling(window=period).mean()
    
    return adx

def analyze_stock_technical(df: pd.DataFrame) -> Optional[Dict]:
    """
    단일 종목의 기술적 분석 수행
    
    Args:
        df: Date, Close, High, Low, Volume 컬럼을 가진 DataFrame
    
    Returns:
        분석 결과 딕셔너리 또는 None
    """
    print(f"[DEBUG] analyze_stock_technical: df shape={df.shape}, columns={list(df.columns)}")
    print(f"[DEBUG] tail:\n{df.tail(60)}")
    if df is None or len(df) < 30:
        return None
    
    df = df.sort_values('Date').reset_index(drop=True)
    close = df['Close']
    high = df.get('High', close)
    low = df.get('Low', close)
    volume = df['Volume']
    
    try:
        # 이동평균선
        sma5 = calculate_sma(close, 5)
        sma10 = calculate_sma(close, 10)
        sma20 = calculate_sma(close, 20)
        sma50 = calculate_sma(close, 50) if len(df) >= 50 else None
        
        # 현재가 정보
        current_price = close.iloc[-1]
        prev_price = close.iloc[-2] if len(close) >= 2 else current_price
        
        # 이평선 괴리율
        ma5_deviation = ((current_price - sma5.iloc[-1]) / sma5.iloc[-1] * 100) if pd.notna(sma5.iloc[-1]) else 0
        ma20_deviation = ((current_price - sma20.iloc[-1]) / sma20.iloc[-1] * 100) if pd.notna(sma20.iloc[-1]) else 0
        
        # 골든/데드 크로스 체크 (5일선과 20일선)
        golden_cross = False
        dead_cross = False
        if len(df) >= 21:
            prev_5ma = sma5.iloc[-2]
            prev_20ma = sma20.iloc[-2]
            curr_5ma = sma5.iloc[-1]
            curr_20ma = sma20.iloc[-1]
            
            if pd.notna(prev_5ma) and pd.notna(prev_20ma):
                if prev_5ma <= prev_20ma and curr_5ma > curr_20ma:
                    golden_cross = True
                elif prev_5ma >= prev_20ma and curr_5ma < curr_20ma:
                    dead_cross = True
        
        # 이평선 정배열 확인 (5 > 10 > 20)
        ma_alignment = False
        if pd.notna(sma5.iloc[-1]) and pd.notna(sma10.iloc[-1]) and pd.notna(sma20.iloc[-1]):
            ma_alignment = (sma5.iloc[-1] > sma10.iloc[-1] > sma20.iloc[-1])
        
        # RSI
        rsi = calculate_rsi(close, 14)
        current_rsi = rsi.iloc[-1] if pd.notna(rsi.iloc[-1]) else 50
        
        # MACD
        macd_data = calculate_macd(close)
        macd_value = macd_data['macd'].iloc[-1] if pd.notna(macd_data['macd'].iloc[-1]) else 0
        signal_value = macd_data['signal'].iloc[-1] if pd.notna(macd_data['signal'].iloc[-1]) else 0
        histogram = macd_data['histogram'].iloc[-1] if pd.notna(macd_data['histogram'].iloc[-1]) else 0
        
        # MACD 크로스 체크
        macd_cross_up = False
        macd_cross_down = False
        if len(df) >= 27:
            prev_hist = macd_data['histogram'].iloc[-2]
            curr_hist = macd_data['histogram'].iloc[-1]
            if pd.notna(prev_hist) and pd.notna(curr_hist):
                if prev_hist < 0 and curr_hist > 0:
                    macd_cross_up = True
                elif prev_hist > 0 and curr_hist < 0:
                    macd_cross_down = True
        
        # 볼린저 밴드
        bb = calculate_bollinger_bands(close, 20)
        bb_upper = bb['upper'].iloc[-1] if pd.notna(bb['upper'].iloc[-1]) else current_price
        bb_middle = bb['middle'].iloc[-1] if pd.notna(bb['middle'].iloc[-1]) else current_price
        bb_lower = bb['lower'].iloc[-1] if pd.notna(bb['lower'].iloc[-1]) else current_price
        
        # 볼린저 밴드 위치 (0~1, 0.5가 중앙)
        bb_position = 0.5
        if bb_upper != bb_lower:
            bb_position = (current_price - bb_lower) / (bb_upper - bb_lower)
        
        # ATR (변동성)
        atr = calculate_atr(high, low, close, 14)
        current_atr = atr.iloc[-1] if pd.notna(atr.iloc[-1]) else 0
        atr_percent = (current_atr / current_price * 100) if current_price > 0 else 0
        
        # ADX (추세 강도)
        adx = calculate_adx(high, low, close, 14)
        current_adx = adx.iloc[-1] if pd.notna(adx.iloc[-1]) else 0
        
        # 거래량 분석
        vol_sma20 = volume.tail(20).mean()
        current_volume = volume.iloc[-1]
        volume_ratio = current_volume / vol_sma20 if vol_sma20 > 0 else 1
        
        # 가격 상승 + 거래량 증가 동반 확인
        price_up = current_price > prev_price
        volume_surge = volume_ratio > 1.5
        bullish_volume = price_up and volume_surge
        
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
            'ma5_deviation': float(ma5_deviation),
            'ma20_deviation': float(ma20_deviation),
            
            # 크로스 신호
            'golden_cross': golden_cross,
            'dead_cross': dead_cross,
            'ma_alignment': ma_alignment,
            
            # 모멘텀 지표
            'rsi': float(current_rsi),
            'rsi_oversold': current_rsi < 30,
            'rsi_overbought': current_rsi > 70,
            
            # MACD
            'macd': float(macd_value),
            'macd_signal': float(signal_value),
            'macd_histogram': float(histogram),
            'macd_cross_up': macd_cross_up,
            'macd_cross_down': macd_cross_down,
            
            # 볼린저 밴드
            'bb_upper': float(bb_upper),
            'bb_middle': float(bb_middle),
            'bb_lower': float(bb_lower),
            'bb_position': float(bb_position),
            'bb_squeeze': (bb_upper - bb_lower) / bb_middle < 0.1 if bb_middle > 0 else False,
            
            # 변동성 & 추세
            'atr': float(current_atr),
            'atr_percent': float(atr_percent),
            'adx': float(current_adx),
            'strong_trend': current_adx > 25,
            
            # 거래량
            'volume': float(current_volume),
            'volume_ratio': float(volume_ratio),
            'bullish_volume': bullish_volume,
        }
    
    except Exception as e:
        print(f"[ERROR] Technical analysis failed: {e}")
        return None


def calculate_technical_score(analysis: Dict) -> float:
    """
    기술적 분석 결과를 점수화
    단기 매매에 유리한 조건일수록 높은 점수
    
    Returns:
        0~10 범위의 점수
    """
    if not analysis:
        return 0.0
    
    score = 0.0
    
    # 1. 골든크로스 (+2.5점)
    if analysis.get('golden_cross', False):
        score += 2.5
    elif analysis.get('dead_cross', False):
        score -= 1.5
    
    # 2. 이평선 정배열 (+1.5점)
    if analysis.get('ma_alignment', False):
        score += 1.5
    
    # 3. RSI 상태 (과매도 구간에서 반등 신호)
    rsi = analysis.get('rsi', 50)
    if 30 < rsi < 50:  # 과매도 벗어나는 구간
        score += 1.2
    elif rsi > 70:  # 과매수 (단기 조정 위험)
        score -= 0.8
    
    # 4. MACD 크로스 (+1.8점)
    if analysis.get('macd_cross_up', False):
        score += 1.8
    elif analysis.get('macd_cross_down', False):
        score -= 1.0
    
    # 5. MACD 히스토그램 양수 (+0.5점)
    if analysis.get('macd_histogram', 0) > 0:
        score += 0.5
    
    # 6. 볼린저 밴드 (하단 근처에서 반등 신호)
    bb_pos = analysis.get('bb_position', 0.5)
    if 0.1 < bb_pos < 0.3:  # 하단 근처
        score += 1.0
    elif bb_pos > 0.9:  # 상단 근처 (과열)
        score -= 0.5
    
    # 7. 거래량 동반 상승 (+2.0점) - 중요!
    if analysis.get('bullish_volume', False):
        score += 2.0
    elif analysis.get('volume_ratio', 1) > 2.0:  # 거래량 급증
        score += 1.0
    
    # 8. ADX 추세 강도
    if analysis.get('strong_trend', False):
        score += 0.8
    
    # 9. 이평선 5일선 괴리율 (급등 후 조정 위험)
    ma5_dev = analysis.get('ma5_deviation', 0)
    if -3 < ma5_dev < 5:  # 적정 범위
        score += 0.5
    elif ma5_dev > 10:  # 과도한 상승
        score -= 0.5
    
    # 0~10 범위로 클리핑
    return max(0.0, min(10.0, score))
