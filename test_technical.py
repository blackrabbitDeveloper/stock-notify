"""
기술적 분석 봇 테스트 스크립트
단일 종목에 대해 상세한 기술적 분석 결과를 출력합니다.
"""
import pandas as pd
from src.fetch_prices import get_history
from src.technical_analyzer import analyze_stock_technical, calculate_technical_score

def test_single_ticker(ticker: str = "AAPL"):
    """단일 종목 기술적 분석 테스트"""
    print(f"\n{'='*80}")
    print(f"📊 {ticker} 기술적 분석 테스트")
    print(f"{'='*80}\n")
    
    # 가격 데이터 가져오기
    print(f"📥 {ticker} 가격 데이터 다운로드 중...")
    df = get_history([ticker], days=60)
    
    if df.empty:
        print(f"❌ {ticker} 데이터를 가져올 수 없습니다.")
        return
    
    ticker_df = df[df['ticker'] == ticker].copy()
    print(f"✅ {len(ticker_df)}일치 데이터 수집 완료\n")
    
    # 기술적 분석 수행
    print("🔍 기술적 분석 수행 중...")
    analysis = analyze_stock_technical(ticker_df)
    
    if not analysis:
        print("❌ 기술적 분석 실패")
        return
    
    # 결과 출력
    print(f"\n{'─'*80}")
    print("💰 가격 정보")
    print(f"{'─'*80}")
    print(f"현재가: ${analysis['current_price']:.2f}")
    print(f"전일가: ${analysis['prev_price']:.2f}")
    print(f"등락률: {analysis['price_change_pct']:+.2f}%\n")
    
    print(f"{'─'*80}")
    print("📈 이동평균선")
    print(f"{'─'*80}")
    if analysis['sma5']:
        print(f"5일 MA:  ${analysis['sma5']:.2f} (괴리율: {analysis['ma5_deviation']:+.2f}%)")
    if analysis['sma10']:
        print(f"10일 MA: ${analysis['sma10']:.2f}")
    if analysis['sma20']:
        print(f"20일 MA: ${analysis['sma20']:.2f} (괴리율: {analysis['ma20_deviation']:+.2f}%)")
    if analysis['sma50']:
        print(f"50일 MA: ${analysis['sma50']:.2f}")
    
    print(f"\n{'─'*80}")
    print("🎯 매매 신호")
    print(f"{'─'*80}")
    
    signals = []
    if analysis['golden_cross']:
        signals.append("🟢 골든크로스 발생! (5일선이 20일선 돌파)")
    if analysis['dead_cross']:
        signals.append("🔴 데드크로스 발생 (5일선이 20일선 하향)")
    if analysis['ma_alignment']:
        signals.append("✅ 이평선 정배열 (5 > 10 > 20)")
    if analysis['macd_cross_up']:
        signals.append("🟢 MACD 상향돌파")
    if analysis['macd_cross_down']:
        signals.append("🔴 MACD 하향돌파")
    if analysis['bullish_volume']:
        signals.append("💪 가격 상승 + 거래량 급증")
    
    if signals:
        for sig in signals:
            print(f"  • {sig}")
    else:
        print("  특별한 신호 없음")
    
    print(f"\n{'─'*80}")
    print("📊 모멘텀 지표")
    print(f"{'─'*80}")
    print(f"RSI(14): {analysis['rsi']:.2f}", end="")
    if analysis['rsi_oversold']:
        print(" 📉 과매도 (< 30)")
    elif analysis['rsi_overbought']:
        print(" 📈 과매수 (> 70)")
    else:
        print(" (중립)")
    
    print(f"\nMACD:")
    print(f"  Line:   {analysis['macd']:+.3f}")
    print(f"  Signal: {analysis['macd_signal']:+.3f}")
    print(f"  Hist:   {analysis['macd_histogram']:+.3f}", end="")
    if analysis['macd_histogram'] > 0:
        print(" 🟢 양수 (상승세)")
    else:
        print(" 🔴 음수 (하락세)")
    
    print(f"\n{'─'*80}")
    print("📉 볼린저 밴드")
    print(f"{'─'*80}")
    print(f"상단: ${analysis['bb_upper']:.2f}")
    print(f"중간: ${analysis['bb_middle']:.2f}")
    print(f"하단: ${analysis['bb_lower']:.2f}")
    bb_pct = analysis['bb_position'] * 100
    print(f"위치: {bb_pct:.1f}%", end="")
    if bb_pct < 20:
        print(" (하단 근처 - 반등 구간)")
    elif bb_pct > 80:
        print(" (상단 근처 - 과열 구간)")
    else:
        print(" (중간 구간)")
    
    print(f"\n{'─'*80}")
    print("💎 변동성 & 추세")
    print(f"{'─'*80}")
    print(f"ATR(14): {analysis['atr']:.2f} ({analysis['atr_percent']:.2f}%)")
    print(f"ADX(14): {analysis['adx']:.2f}", end="")
    if analysis['strong_trend']:
        print(" 💎 강한 추세 (> 25)")
    else:
        print(" (약한 추세)")
    
    print(f"\n{'─'*80}")
    print("📊 거래량")
    print(f"{'─'*80}")
    print(f"현재 거래량: {analysis['volume']:,.0f}")
    print(f"20일 평균 대비: {analysis['volume_ratio']:.2f}x", end="")
    if analysis['volume_ratio'] > 2.0:
        print(" 📊 급증!")
    elif analysis['volume_ratio'] > 1.5:
        print(" 증가")
    else:
        print()
    
    # 점수 계산
    score = calculate_technical_score(analysis)
    
    print(f"\n{'='*80}")
    print(f"⭐ 기술적 분석 종합 점수: {score:.2f} / 10.0")
    print(f"{'='*80}\n")
    
    if score >= 7.0:
        print("💚 매수 고려 구간 (강한 신호)")
    elif score >= 5.0:
        print("💛 관심 구간 (중립적 신호)")
    elif score >= 3.0:
        print("🧡 주의 구간 (약한 신호)")
    else:
        print("❤️ 회피 구간 (부정적 신호)")
    
    print("\n⚠️  면책: 이는 기술적 분석 참고용이며 투자 자문이 아닙니다.")
    print()

def test_multiple_tickers():
    """여러 종목 비교 테스트"""
    tickers = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL"]
    print(f"\n{'='*80}")
    print(f"📊 다중 종목 기술적 분석 비교")
    print(f"{'='*80}\n")
    
    results = []
    
    for ticker in tickers:
        print(f"분석 중: {ticker}...", end=" ")
        df = get_history([ticker], days=60)
        
        if df.empty:
            print("❌ 데이터 없음")
            continue
        
        ticker_df = df[df['ticker'] == ticker].copy()
        analysis = analyze_stock_technical(ticker_df)
        
        if not analysis:
            print("❌ 분석 실패")
            continue
        
        score = calculate_technical_score(analysis)
        print(f"✅ 점수: {score:.2f}")
        
        results.append({
            'ticker': ticker,
            'score': score,
            'price': analysis['current_price'],
            'change': analysis['price_change_pct'],
            'rsi': analysis['rsi'],
            'volume_ratio': analysis['volume_ratio'],
            'golden_cross': analysis['golden_cross'],
            'macd_cross_up': analysis['macd_cross_up']
        })
    
    # 결과 출력
    if results:
        print(f"\n{'─'*80}")
        print("📈 종합 순위 (기술적 분석 점수 기준)")
        print(f"{'─'*80}\n")
        
        sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)
        
        for i, r in enumerate(sorted_results, 1):
            signals = []
            if r['golden_cross']:
                signals.append("GC")
            if r['macd_cross_up']:
                signals.append("MACD↑")
            if r['volume_ratio'] > 1.5:
                signals.append(f"Vol×{r['volume_ratio']:.1f}")
            
            signal_str = " | ".join(signals) if signals else "—"
            
            print(f"{i}. {r['ticker']:5s}  점수:{r['score']:5.2f}  "
                  f"가격:${r['price']:7.2f} ({r['change']:+5.2f}%)  "
                  f"RSI:{r['rsi']:5.1f}  [{signal_str}]")
    
    print()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # 커맨드라인 인자로 티커 지정
        ticker = sys.argv[1].upper()
        test_single_ticker(ticker)
    else:
        # 기본: 다중 종목 비교
        print("\n사용법:")
        print("  python test_technical.py         # 여러 종목 비교")
        print("  python test_technical.py AAPL    # 단일 종목 상세 분석\n")
        
        test_multiple_tickers()
