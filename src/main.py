import yaml, os
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
        send_discord_with_reasons([], "US Pre-Open Watchlist (Auto-Universe)")
        print("no recommendations – dataset too thin"); return

    rows=[]
    ai_on = bool(cfg.get("ai_explainer",{}).get("enabled", True))
    print(f"[DEBUG] ai_on: {ai_on}")
    for _, r in topn.iterrows():
        reason_obj = {"reason":"규칙 기반 선별 결과.", "confidence":0.4, "caveat":"투자 자문 아님"}
        if ai_on:
            reason_obj = explain_reason(
                r["ticker"],
                {"day_ret": r["day_ret"], "vol_x": r["vol_x"]},
                r["top_news"]
            )
        rows.append({**r.to_dict(), "reason_obj": reason_obj})

    send_discord_with_reasons(rows, "US Pre-Open Watchlist (Auto-Universe)")
    print("done")

if __name__ == "__main__":
    run_once()
