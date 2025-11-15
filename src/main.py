import yaml, os
import pandas as pd
from dotenv import load_dotenv

from .fetch_prices import get_history
from .universe_builder import build_auto_universe
from .ranker import rank_with_news
from .ai_explainer import explain_reason
from .send_discord import send_discord_with_reasons

load_dotenv()

def load_cfg():
    with open("config/universe.yaml","r",encoding="utf-8") as f:
        return yaml.safe_load(f)

def resolve_universe(cfg):
    if (cfg.get("mode","static")).lower()=="auto":
        u = build_auto_universe(cfg.get("auto",{}))
        return u if u else cfg.get("static_list",[])
    return cfg.get("static_list",[])

def run_once():
    cfg = load_cfg()
    tickers = resolve_universe(cfg)
    prices = get_history(tickers, days=40)
    use_news = bool(cfg.get("auto",{}).get("use_news_bonus", True))
    topn = rank_with_news(prices, tickers, use_news=use_news, min_bars=5)
    if topn.empty:
        send_discord_with_reasons([], "US Pre-Open Watchlist (Technical Analysis)")
        print("no recommendations – dataset too thin"); return

    top_symbols = topn["ticker"].tolist()
    from src.fetch_prices import get_latest_quotes
    q = get_latest_quotes(top_symbols, prepost=True)
    # topn에 병합
    topn = topn.merge(q, on="ticker", how="left")

    rows=[]
    ai_on = bool(cfg.get("ai_explainer",{}).get("enabled", True))
    
    def _num(x):
        return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

    def _ts(x):
        # pandas.Timestamp → ISO 문자열
        try:
            return x.isoformat() if pd.notna(x) else None
        except Exception:
            return None

    for _, r in topn.iterrows():
        # 기술적 분석 데이터 가져오기
        tech_analysis = r.get("technical_analysis", {})
        
        reason_obj = {"reason": "규칙 기반 선별 결과.", "confidence": 0.4, "caveat": "투자 자문 아님"}
        if ai_on:
            reason_obj = explain_reason(
                r["ticker"],
                {
                    "day_ret": float(r["day_ret"]), 
                    "vol_x": float(r["vol_x"]),
                    "tech_score": float(r.get("tech_score", 0)),
                    "technical_signals": tech_analysis
                },
                r.get("top_news", []),
            )

        rows.append({
            "ticker":     r["ticker"],
            "day_ret":    float(r["day_ret"]),
            "vol_x":      float(r["vol_x"]),
            "news_n":     int(r.get("news_n", 0)),
            "news_bonus": float(r.get("news_bonus", 0.0)),
            "tech_score": float(r.get("tech_score", 0.0)),
            "score":      float(r.get("combined_score", 0.0)),
            "top_news":   r.get("top_news", []),
            "technical_analysis": tech_analysis,
            "reason_obj": reason_obj,

            # ⬇️ 현재가/전일종가/시각 추가
            "last_price": _num(r.get("last_price")),
            "prev_close": _num(r.get("prev_close")),
            "last_time":  _ts(r.get("last_time")),
        })

    send_discord_with_reasons(rows, "US Pre-Open Watchlist (Technical Analysis)")
    print("done")

if __name__ == "__main__":
    run_once()
