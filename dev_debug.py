# dev_debug.py
import yaml, pandas as pd
from src.universe_builder import build_auto_universe
from src.fetch_prices import get_history
from src.ranker import rank_with_news

def load_cfg():
    with open("config/universe.yaml","r",encoding="utf-8") as f:
        return yaml.safe_load(f)

cfg = load_cfg()

# 1) 유니버스 확인
if (cfg.get("mode","static")).lower()=="auto":
    tickers = build_auto_universe(cfg.get("auto",{}))
else:
    tickers = cfg.get("static_list",[])

print(f"[DEBUG] tickers: {len(tickers)} -> {tickers[:15]}")

# 2) 가격 데이터 수집 확인
df = get_history(tickers, days=40)
print(f"[DEBUG] price rows: {len(df)} cols:{list(df.columns)}")
if not df.empty:
    print("[DEBUG] sample:\n", df.head(10))
    print("[DEBUG] per-ticker counts:\n", df.groupby("ticker").size().sort_values(ascending=False).head(10))

# 3) 랭킹 직전까지
topn = rank_with_news(df, tickers, use_news=False, min_bars=5)  # 뉴스 끄고 최소 바 5
print(f"[DEBUG] rank rows: {len(topn)}")
if not topn.empty:
    print(topn[["ticker","day_ret","vol_x","score"]])
else:
    print("[DEBUG] rank empty → 위 단계 출력 참고")
