import yaml, os
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv

from .fetch_prices import get_history, get_latest_quotes
from .universe_builder import build_auto_universe
from .ranker import rank_with_news
from .ai_explainer import explain_reason
from .send_discord import send_discord_with_reasons, send_discord_position_report
from .position_tracker import update_positions, register_positions, get_summary

load_dotenv()

def load_cfg():
    with open("config/universe.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 자기 학습된 파라미터 오버라이드 (strategy_state.json)
    import json
    from pathlib import Path
    state_path = Path("config/strategy_state.json")
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            tuned = state.get("current_params", {})
            auto = cfg.get("auto", {})
            for key in ["min_tech_score", "atr_stop_mult", "atr_tp_mult", "max_hold_days", "top_n"]:
                if key in tuned:
                    auto[key] = tuned[key]
            cfg["auto"] = auto
            regime = state.get("current_regime", "unknown")
            print(f"  🧠 자기 학습 파라미터 적용 (레짐: {regime})")
        except Exception as e:
            print(f"  ⚠️ strategy_state.json 로드 실패: {e}")

    return cfg

def resolve_universe(cfg):
    if (cfg.get("mode", "static")).lower() == "auto":
        u = build_auto_universe(cfg.get("auto", {}))
        return u if u else cfg.get("static_list", [])
    return cfg.get("static_list", [])

def run_once():
    cfg = load_cfg()
    auto = cfg.get("auto", {})

    # 자동 튜닝된 파라미터 적용 (기본값 폴백)
    atr_stop_mult  = float(auto.get("atr_stop_mult", 2.0))
    atr_tp_mult    = float(auto.get("atr_tp_mult", 4.0))
    max_hold_days  = int(auto.get("max_hold_days", 7))
    top_n_override = int(auto.get("top_n", 5))

    # ─────────────────────────────────────────────────────────
    # STEP 1: 기존 포지션 업데이트 (장 마감 후 전일 종가 기준)
    # ─────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("STEP 1: 포지션 업데이트")
    print("="*60)
    still_open, newly_closed = update_positions()

    # 포지션 현황 Discord 전송
    summary = get_summary()
    send_discord_position_report(summary, newly_closed)

    # ─────────────────────────────────────────────────────────
    # STEP 2: 신규 종목 추천 (기존 로직)
    # ─────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("STEP 2: 신규 종목 추천")
    print("="*60)
    tickers = resolve_universe(cfg)
    prices  = get_history(tickers, days=60)

    use_news         = bool(cfg.get("auto", {}).get("use_news_bonus", True))
    tech_filter_count = int(cfg.get("auto", {}).get("tech_filter_count", 30))
    topn = rank_with_news(
        prices, tickers, use_news=use_news,
        min_bars=5, tech_filter_count=tech_filter_count
    )

    if topn.empty:
        send_discord_with_reasons([], "US Pre-Open Watchlist (Technical Analysis)")
        print("no recommendations – dataset too thin")
        return

    top_symbols = topn["ticker"].tolist()
    q = get_latest_quotes(top_symbols, prepost=True)
    topn = topn.merge(q, on="ticker", how="left")

    rows  = []
    ai_on = bool(cfg.get("ai_explainer", {}).get("enabled", True))
    today = datetime.now(timezone.utc).date().isoformat()

    def _num(x):
        return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

    def _ts(x):
        try:
            return x.isoformat() if pd.notna(x) else None
        except Exception:
            return None

    for _, r in topn.iterrows():
        tech_analysis = r.get("technical_analysis", {})

        reason_obj = {"reason": "규칙 기반 선별 결과.", "confidence": 0.4, "caveat": "투자 자문 아님"}
        if ai_on:
            reason_obj = explain_reason(
                r["ticker"],
                {
                    "day_ret":           float(r["day_ret"]),
                    "vol_x":             float(r["vol_x"]),
                    "tech_score":        float(r.get("tech_score", 0)),
                    "technical_signals": tech_analysis,
                },
                r.get("top_news", []),
            )

        rows.append({
            "ticker":             r["ticker"],
            "day_ret":            float(r["day_ret"]),
            "vol_x":              float(r["vol_x"]),
            "news_n":             int(r.get("news_n", 0)),
            "news_bonus":         float(r.get("news_bonus", 0.0)),
            "tech_score":         float(r.get("tech_score", 0.0)),
            "score":              float(r.get("combined_score", 0.0)),
            "top_news":           r.get("top_news", []),
            "technical_analysis": tech_analysis,
            "reason_obj":         reason_obj,
            "last_price":         _num(r.get("last_price")),
            "prev_close":         _num(r.get("prev_close")),
            "last_time":          _ts(r.get("last_time")),
            "fundamentals":       r.get("fundamentals"),
            "fundamental_score":  float(r.get("fundamental_score", 0)),
            "mtf_alignment":     r.get("mtf_alignment", ""),
            "mtf_score":         float(r.get("mtf_score", 0)),
            "timing_details":    r.get("timing_details", ""),
            "sector":            r.get("sector", ""),
        })

    # 추천 Discord 전송
    send_discord_with_reasons(rows, "US Pre-Open Watchlist (Technical Analysis)")

    # ─────────────────────────────────────────────────────────
    # STEP 3: 신규 포지션 등록 → positions.json 저장
    # ─────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("STEP 3: 포지션 등록")
    print("="*60)
    register_positions(rows, today)

    print("\ndone")

if __name__ == "__main__":
    run_once()