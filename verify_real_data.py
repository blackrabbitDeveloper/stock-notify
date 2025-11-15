"""
데이터가 실제인지 확인하는 간단한 스크립트
"""
import yfinance as yf
from datetime import datetime

ticker = "AAPL"
print(f"\n{'='*60}")
print(f"📊 {ticker} 실제 데이터 확인")
print(f"{'='*60}\n")

# 최근 5일 데이터 가져오기
stock = yf.Ticker(ticker)
df = stock.history(period="5d")

if df.empty:
    print("❌ 데이터를 가져올 수 없습니다.")
else:
    print(f"✅ Yahoo Finance에서 실제 데이터 다운로드 완료\n")
    print(f"최근 5일 거래일:\n")
    print(df[['Close', 'Volume']].to_string())
    
    print(f"\n{'─'*60}")
    print(f"📈 현재 정보:")
    print(f"{'─'*60}")
    print(f"종목명: {stock.info.get('longName', 'N/A')}")
    print(f"현재가: ${df['Close'].iloc[-1]:.2f}")
    print(f"전일가: ${df['Close'].iloc[-2]:.2f}")
    print(f"등락: {((df['Close'].iloc[-1]/df['Close'].iloc[-2])-1)*100:+.2f}%")
    print(f"거래량: {df['Volume'].iloc[-1]:,.0f}")
    
    print(f"\n이 데이터를 다음 사이트와 비교해보세요:")
    print(f"🔗 https://finance.yahoo.com/quote/{ticker}")
    print(f"\n💡 가격이 일치하면 실제 데이터입니다!\n")
