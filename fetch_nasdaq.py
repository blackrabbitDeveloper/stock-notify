# save as fetch_sp500.py
import os, re, sys, traceback
import requests, pandas as pd

URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
UA  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

FALLBACK = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","BRK-B","AVGO","JPM",
    "XOM","LLY","V","UNH","WMT","MA","PG","JNJ","COST","HD"
]  # 네트워크 막힐 때 최소 폴백

def clean(sym: str) -> str:
    s = (sym or "").strip().upper().replace(" ", "").replace(".", "-")
    return re.sub(r"[^A-Z0-9\-]", "", s)

def read_symbols_from_html(html: str) -> list[str]:
    tables = pd.read_html(html)  # requires: pandas, lxml/html5lib
    for df in tables:
        for col in df.columns:
            if "symbol" in str(col).lower() or "ticker" in str(col).lower():
                syms = [clean(x) for x in df[col].dropna().astype(str).tolist()]
                seen, out = set(), []
                for s in syms:
                    if s and s != "—" and s not in seen:
                        seen.add(s); out.append(s)
                return out
    return []

def fetch_nasdaq() -> list[str]:
    try:
        r = requests.get(URL, headers=UA, timeout=20)
        r.raise_for_status()
        syms = read_symbols_from_html(r.text)
        if syms:
            print(f"[INFO] fetched from Wikipedia: {len(syms)} tickers")
            return syms
        else:
            print("[WARN] table parse returned 0; using fallback")
    except Exception as e:
        print(f"[WARN] fetch failed: {e}")
        traceback.print_exc()
    return FALLBACK

def main():
    syms = fetch_nasdaq()
    # 저장
    pd.Series(syms, name="Symbol").to_csv("nasdaq100.txt", index=False, header=False)
    pd.DataFrame({"Symbol": syms}).to_csv("nasdaq100.csv", index=False)
    # 콘솔에도 일부 표시
    print(f"[DONE] wrote nasdaq100.txt & nasdaq100.csv (count={len(syms)})")
    print("Sample:", syms[:30])

if __name__ == "__main__":
    main()
