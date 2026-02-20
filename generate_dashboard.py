#!/usr/bin/env python3
"""
정적 대시보드 HTML 생성기

JSON 데이터 파일들을 읽어서 단일 HTML 파일로 변환.
GitHub Pages에 배포하여 브라우저에서 확인.

생성 대상:
  docs/index.html         — 메인 대시보드
  docs/data/dashboard.json — 대시보드 데이터 (HTML에 인라인)

데이터 소스:
  data/positions.json     — 열린 포지션 + 누적 통계
  data/history.json       — 청산 이력
  config/strategy_state.json — 자기 학습 상태
  config/signal_weights.json — 신호 가중치
  data/tuning_history.json   — 튜닝 이력
  data/backtest/             — 백테스트 결과

사용법:
  python generate_dashboard.py
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

# ── 경로 ──
DATA_DIR = Path("data")
CONFIG_DIR = Path("config")
DOCS_DIR = Path("docs")

POSITIONS_FILE = DATA_DIR / "positions.json"
HISTORY_FILE = DATA_DIR / "history.json"
STRATEGY_STATE_FILE = CONFIG_DIR / "strategy_state.json"
SIGNAL_WEIGHTS_FILE = CONFIG_DIR / "signal_weights.json"
TUNING_HISTORY_FILE = DATA_DIR / "tuning_history.json"
BACKTEST_DIR = DATA_DIR / "backtest"
UNIVERSE_FILE = CONFIG_DIR / "universe.yaml"


def load_json(path, default=None):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def fetch_market_indices() -> dict:
    """S&P500, 나스닥100, 원-달러 환율, 금 시세 데이터 수집 (최근 6개월)."""
    if not HAS_YFINANCE:
        print("  ⚠️ yfinance 미설치 — 시장 지표 스킵")
        print("    → pip install yfinance 로 설치하세요")
        return {}

    print(f"  yfinance 버전: {yf.__version__}")

    symbols = {
        "sp500":    {"ticker": "^GSPC",   "name": "S&P 500"},
        "nasdaq":   {"ticker": "^NDX",    "name": "NASDAQ 100"},
        "usd_krw":  {"ticker": "KRW=X",   "name": "USD/KRW"},
        "gold":     {"ticker": "GC=F",    "name": "Gold"},
    }

    result = {}

    # 방법 1: yf.download()로 한번에 다운로드 (더 안정적)
    tickers_str = " ".join(info["ticker"] for info in symbols.values())
    try:
        print(f"  📡 yf.download() 시도: {tickers_str}")
        df_all = yf.download(tickers_str, period="6mo", progress=False, auto_adjust=True)
        print(f"  📡 다운로드 결과: shape={df_all.shape if not df_all.empty else 'EMPTY'}")

        if not df_all.empty:
            for key, info in symbols.items():
                try:
                    ticker = info["ticker"]
                    # yf.download() 다중 티커일 때 컬럼이 MultiIndex
                    if isinstance(df_all.columns, __import__('pandas').MultiIndex):
                        if ("Close", ticker) in df_all.columns:
                            closes = df_all[("Close", ticker)].dropna()
                        else:
                            print(f"  ⚠️ {info['name']}: 컬럼 없음 — Ticker 방식 시도")
                            closes = _fetch_single_ticker(info)
                            if closes is None:
                                continue
                    else:
                        # 단일 티커인 경우
                        closes = df_all["Close"].dropna()

                    dates = [d.strftime("%Y-%m-%d") for d in closes.index]
                    values = [round(float(v), 2) for v in closes.values]

                    if not values:
                        print(f"  ⚠️ {info['name']}: 빈 데이터")
                        continue

                    current = values[-1]
                    prev = values[-2] if len(values) >= 2 else current
                    day_change = round((current - prev) / prev * 100, 2) if prev else 0

                    first = values[0]
                    period_change = round((current - first) / first * 100, 2) if first else 0

                    result[key] = {
                        "name": info["name"],
                        "current": current,
                        "day_change": day_change,
                        "period_change": period_change,
                        "dates": dates,
                        "values": values,
                    }
                    print(f"  ✅ {info['name']}: {current:,.2f} ({day_change:+.2f}%) [{len(values)}일]")
                except Exception as e:
                    print(f"  ⚠️ {info['name']} 파싱 실패: {e}")
    except Exception as e:
        print(f"  ⚠️ yf.download() 실패: {e}")
        print("  📡 개별 Ticker 방식으로 폴백...")

    # 방법 2: 실패한 항목에 대해 개별 Ticker 방식으로 재시도
    for key, info in symbols.items():
        if key in result:
            continue
        try:
            closes = _fetch_single_ticker(info)
            if closes is None:
                continue
            dates = [d.strftime("%Y-%m-%d") for d in closes.index]
            values = [round(float(v), 2) for v in closes.values]
            if not values:
                continue
            current = values[-1]
            prev = values[-2] if len(values) >= 2 else current
            day_change = round((current - prev) / prev * 100, 2) if prev else 0
            first = values[0]
            period_change = round((current - first) / first * 100, 2) if first else 0
            result[key] = {
                "name": info["name"],
                "current": current,
                "day_change": day_change,
                "period_change": period_change,
                "dates": dates,
                "values": values,
            }
            print(f"  ✅ {info['name']} (Ticker방식): {current:,.2f} ({day_change:+.2f}%)")
        except Exception as e:
            print(f"  ⚠️ {info['name']} Ticker방식도 실패: {e}")

    print(f"  📊 시장 지표 수집 완료: {len(result)}/4개")
    return result


def _fetch_single_ticker(info: dict):
    """개별 Ticker 방식으로 데이터 수집 (폴백용)."""
    import traceback
    try:
        tk = yf.Ticker(info["ticker"])
        hist = tk.history(period="6mo")
        if hist.empty:
            print(f"  ⚠️ {info['name']}: Ticker.history() 빈 결과")
            return None
        return hist["Close"].dropna()
    except Exception as e:
        print(f"  ⚠️ {info['name']} Ticker 실패: {e}")
        traceback.print_exc()
        return None


EARNINGS_FILE = DATA_DIR / "earnings_calendar.json"


def collect_dashboard_data() -> dict:
    """모든 데이터 소스를 하나의 dict로 수집."""

    # 1. 포지션
    pos_data = load_json(POSITIONS_FILE, {"positions": [], "stats": {}})
    positions = pos_data.get("positions", [])
    stats = pos_data.get("stats", {})

    # 2. 히스토리
    history = load_json(HISTORY_FILE, [])

    # 3. 자기 학습 상태
    strategy = load_json(STRATEGY_STATE_FILE, {})

    # 4. 신호 가중치
    weights = load_json(SIGNAL_WEIGHTS_FILE, {})

    # 5. 튜닝 이력
    tuning_history = load_json(TUNING_HISTORY_FILE, [])

    # 6. 최신 백테스트 결과
    backtest = {}
    if BACKTEST_DIR.exists():
        json_files = sorted(BACKTEST_DIR.glob("*.json"), reverse=True)
        if json_files:
            backtest = load_json(json_files[0], {})

    # 히스토리에서 일별 누적 PnL 계산
    daily_pnl = {}
    cumulative = 0.0
    sorted_history = sorted(history, key=lambda x: x.get("exit_date", ""))
    for h in sorted_history:
        d = h.get("exit_date", "")
        pnl = h.get("pnl_pct", 0) or 0
        cumulative += pnl
        daily_pnl[d] = round(cumulative, 2)

    # 히스토리에서 월별 성과
    monthly_perf = {}
    for h in sorted_history:
        d = h.get("exit_date", "")
        if len(d) >= 7:
            month = d[:7]
            if month not in monthly_perf:
                monthly_perf[month] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            monthly_perf[month]["trades"] += 1
            pnl = h.get("pnl_pct", 0) or 0
            monthly_perf[month]["total_pnl"] += pnl
            if pnl > 0:
                monthly_perf[month]["wins"] += 1

    for m in monthly_perf:
        t = monthly_perf[m]["trades"]
        monthly_perf[m]["win_rate"] = round(monthly_perf[m]["wins"] / t * 100, 1) if t > 0 else 0
        monthly_perf[m]["total_pnl"] = round(monthly_perf[m]["total_pnl"], 2)

    # 히스토리에서 청산 유형 비율
    exit_types = {"take_profit": 0, "stop_loss": 0, "expired": 0, "sell_signal": 0, "strategy_rebalance": 0, "trailing_stop": 0}
    for h in history:
        reason = h.get("close_reason", "")
        if reason in exit_types:
            exit_types[reason] += 1

    # 7. 시장 지표 수집
    market_indices = fetch_market_indices()

    # 8. 유니버스 설정 (universe.yaml)
    universe_cfg = {}
    if UNIVERSE_FILE.exists():
        try:
            import yaml
            with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
                universe_cfg = yaml.safe_load(f) or {}
        except Exception:
            pass

    # 포지션 비율 + 현금 비중 계산
    open_positions = [p for p in positions if p.get("status") == "open"]
    current_params = strategy.get("current_params", {})
    max_positions = current_params.get("max_positions", 10)
    current_regime = strategy.get("current_regime", "unknown")
    regime_cash = {
        "bullish": 10, "sideways": 30, "bearish": 50,
        "volatile": 60, "conservative": 40,
    }
    target_cash_pct = regime_cash.get(current_regime, 30)
    open_count = len(open_positions)
    usage_pct = round(open_count / max_positions * 100, 1) if max_positions > 0 else 0
    current_invest_pct = round(min(100, usage_pct * (100 - target_cash_pct) / 100), 1)
    current_cash_pct = round(100 - current_invest_pct, 1)

    portfolio = {
        "open_count": open_count,
        "max_positions": max_positions,
        "usage_pct": usage_pct,
        "target_cash_pct": target_cash_pct,
        "current_cash_pct": current_cash_pct,
        "current_invest_pct": current_invest_pct,
        "available_slots": max(0, max_positions - open_count),
        "regime": current_regime,
    }

    # 9. 어닝 캘린더 (data/earnings_calendar.json에서 읽기)
    #    수집은 run_earnings.py로 별도 실행
    earnings_calendar = []
    try:
        if EARNINGS_FILE.exists():
            with open(EARNINGS_FILE, "r", encoding="utf-8") as f:
                earn_data = json.load(f)
            earnings_calendar = earn_data.get("earnings", [])
            # 보유 종목 상태 실시간 갱신
            open_ticker_set = set(p.get("ticker") for p in open_positions)
            for e in earnings_calendar:
                e["is_holding"] = e["ticker"] in open_ticker_set
            print(f"[INFO] 어닝 캘린더 로드: {len(earnings_calendar)}건 "
                  f"(수집일: {earn_data.get('collected_at', 'N/A')})")
    except Exception as e:
        print(f"[WARN] 어닝 캘린더 로드 실패: {e}")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "positions": positions,
        "stats": stats,
        "history": history[-100:],
        "daily_cumulative_pnl": daily_pnl,
        "monthly_performance": monthly_perf,
        "exit_types": exit_types,
        "market_indices": market_indices,
        "portfolio": portfolio,
        "earnings_calendar": earnings_calendar,
        "strategy": {
            "current_params": strategy.get("current_params", {}),
            "current_regime": strategy.get("current_regime", "unknown"),
            "regime_confidence": strategy.get("regime_confidence", 0),
            "last_tuned_at": strategy.get("last_tuned_at", ""),
        },
        "signal_weights": weights,
        "tuning_history": tuning_history[-20:],
        "backtest": {
            "summary": backtest.get("summary", {}),
            "signal_performance": backtest.get("signal_performance", []),
            "monthly_returns": backtest.get("monthly_returns", []),
            "score_buckets": backtest.get("score_buckets", []),
        },
        "universe": universe_cfg,
    }


def generate_html(data: dict) -> str:
    """대시보드 HTML 생성."""
    data_json = json.dumps(data, ensure_ascii=False, default=str)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Bot Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Noto+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg: #0a0e17;
  --surface: #111827;
  --surface2: #1a2235;
  --border: #2a3448;
  --text: #e2e8f0;
  --text2: #94a3b8;
  --accent: #38bdf8;
  --green: #34d399;
  --red: #f87171;
  --yellow: #fbbf24;
  --purple: #a78bfa;
  --orange: #fb923c;
  --font-mono: 'JetBrains Mono', monospace;
  --font-body: 'Noto Sans KR', sans-serif;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
  line-height: 1.6;
  min-height: 100vh;
}}
.topbar {{
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
  border-bottom: 1px solid var(--border);
  padding: 12px 24px;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(12px);
}}
.topbar h1 {{
  font-family: var(--font-mono);
  font-size: 20px;
  font-weight: 700;
  background: linear-gradient(90deg, var(--accent), var(--purple));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
.topbar .meta {{
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text2);
}}
.regime-badge {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
}}
.regime-bullish {{ background: rgba(52,211,153,0.15); color: var(--green); border: 1px solid rgba(52,211,153,0.3); }}
.regime-bearish {{ background: rgba(248,113,113,0.15); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }}
.regime-sideways {{ background: rgba(251,191,36,0.15); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }}
.regime-unknown,.regime-conservative {{ background: rgba(148,163,184,0.15); color: var(--text2); border: 1px solid rgba(148,163,184,0.3); }}

.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}

.tabs {{
  display: flex;
  gap: 4px;
  margin: 0;
  background: rgba(15,23,42,0.6);
  border-radius: 8px;
  padding: 3px;
  border: 1px solid var(--border);
  overflow-x: auto;
}}
.tab {{
  padding: 7px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text2);
  transition: all 0.2s;
  white-space: nowrap;
  border: none;
  background: none;
}}
.tab:hover {{ color: var(--text); background: var(--surface2); }}
.tab.active {{ background: var(--accent); color: #0a0e17; font-weight: 600; }}

.news-src-btn {{
  padding: 5px 12px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--surface); color: var(--text2); cursor: pointer;
  font-size: 12px; font-family: var(--font-mono); transition: all 0.2s;
}}
.news-src-btn:hover {{ color: var(--text1); border-color: var(--accent); }}
.news-src-btn.active {{ background: var(--accent); color: #0a0e17; border-color: var(--accent); }}
.news-item {{
  padding: 12px 16px; border-bottom: 1px solid var(--border);
  transition: background 0.15s;
}}
.news-item:hover {{ background: var(--surface2); }}
.news-item:last-child {{ border-bottom: none; }}

.tab-content {{ display: none; }}
.tab-desc {{
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text2);
  margin: 0 0 16px 0;
  padding: 10px 14px;
  background: var(--surface);
  border-left: 3px solid var(--accent);
  border-radius: 0 8px 8px 0;
}}

.period-btns {{ display: flex; gap: 4px; }}
.period-btn {{
  padding: 5px 14px; border-radius: 6px; cursor: pointer;
  font-size: 12px; color: var(--text2); border: 1px solid var(--surface2);
  background: var(--surface); transition: all 0.2s; font-family: var(--font-mono);
}}
.period-btn:hover {{ color: var(--text); border-color: var(--accent); }}
.period-btn.active {{ background: var(--accent); color: #0a0e17; border-color: var(--accent); font-weight: 600; }}
.tab-content.active {{ display: block; }}

/* ── 카드 ── */
.grid {{ display: grid; gap: 16px; }}
.grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
.grid-3 {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
.grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); }}

.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}}
.card-header {{
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: var(--text2);
  margin-bottom: 8px;
}}
.card-value {{
  font-family: var(--font-mono);
  font-size: 28px;
  font-weight: 700;
}}
.card-sub {{
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text2);
  margin-top: 4px;
}}
.positive {{ color: var(--green); }}
.negative {{ color: var(--red); }}
.neutral {{ color: var(--yellow); }}

/* ── 테이블 ── */
.table-wrap {{
  overflow-x: auto;
  border-radius: 8px;
  border: 1px solid var(--border);
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-mono);
  font-size: 13px;
}}
th {{
  background: var(--surface2);
  padding: 10px 14px;
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text2);
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
}}
td {{
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}}
tr:hover td {{ background: rgba(56,189,248,0.04); }}
.status-open {{ color: var(--accent); }}
.status-take_profit {{ color: var(--green); }}
.status-stop_loss {{ color: var(--red); }}
.status-expired {{ color: var(--yellow); }}
.status-sell_signal {{ color: var(--accent); }}
.status-strategy_rebalance {{ color: #a78bfa; }}
.status-trailing_stop {{ color: #22d3ee; }}

.chart-box {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 16px;
}}
.chart-box h3 {{
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text2);
  margin-bottom: 16px;
  text-transform: uppercase;
  letter-spacing: 1px;
}}
canvas {{ max-height: 320px; }}

.weight-bar {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-family: var(--font-mono);
  font-size: 12px;
}}
.weight-bar .label {{ width: 160px; color: var(--text2); text-align: right; }}
.weight-bar .bar {{
  flex: 1;
  height: 22px;
  background: var(--surface2);
  border-radius: 4px;
  position: relative;
  overflow: hidden;
}}
.weight-bar .fill {{
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
}}
.weight-bar .val {{
  width: 50px;
  text-align: right;
  font-weight: 600;
}}

.param-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}}
.param-item {{
  background: var(--surface2);
  border-radius: 8px;
  padding: 14px;
  text-align: center;
}}
.param-item .label {{
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 1px;
}}
.param-item .value {{
  font-family: var(--font-mono);
  font-size: 24px;
  font-weight: 700;
  color: var(--accent);
  margin-top: 4px;
}}

.strat-row {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 13px;
}}
.strat-row:last-child {{ border-bottom: none; }}
.strat-row .label {{ color: var(--text2); }}
.strat-row .val {{ font-weight: 600; color: var(--text); }}
.strat-row .val.accent {{ color: var(--accent); }}
.strat-row .val.green {{ color: var(--green); }}
.strat-row .val.yellow {{ color: var(--yellow); }}

.empty-state {{
  text-align: center;
  padding: 60px 20px;
  color: var(--text2);
  font-family: var(--font-mono);
}}
.empty-state .icon {{ font-size: 48px; margin-bottom: 16px; }}

.section-title {{
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  margin: 24px 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}}

/* ── 스크롤바 ── */
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: var(--bg); }}
::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--text2); }}

@media (max-width: 768px) {{
  .grid-4 {{ grid-template-columns: repeat(2, 1fr); }}
  .grid-2 {{ grid-template-columns: 1fr; }}
  .topbar {{ padding: 8px 12px; }}
  .topbar h1 {{ font-size: 16px; }}
  .tabs {{ flex-wrap: nowrap; }}
  .tab {{ padding: 6px 10px; font-size: 11px; }}
}}
</style>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-3935883771879302"
     crossorigin="anonymous"></script>
</head>
<body>

<div class="topbar">
  <div style="display:flex;align-items:center;justify-content:space-between;width:100%;margin-bottom:8px;">
    <div>
      <h1>📈 Stock Bot Dashboard</h1>
      <div class="meta" id="lastUpdate"></div>
    </div>
    <div style="display:flex;align-items:center;gap:12px;">
      <span class="regime-badge" id="regimeBadge"></span>
    </div>
  </div>
  <div class="tabs">
    <button class="tab active" onclick="showTab('guide')">📖 가이드</button>
    <button class="tab" onclick="showTab('market')">🌍 시장 현황</button>
    <button class="tab" onclick="showTab('positions')">💼 포지션</button>
    <button class="tab" onclick="showTab('performance')">📊 성과</button>
    <button class="tab" onclick="showTab('backtest')">🔬 백테스트</button>
    <button class="tab" onclick="showTab('tuning')">🧠 자기학습</button>
    <button class="tab" onclick="showTab('strategy')">⚙️ 전략 설정</button>
    <button class="tab" onclick="showTab('earnings')">📅 실적 캘린더</button>
    <button class="tab" onclick="showTab('reports')">📋 주간 리포트</button>
    <button class="tab" onclick="showTab('news')">📰 경제 뉴스</button>
  </div>
</div>

<div class="container">

  <!-- ════ TAB 0: 시장 현황 ════ -->
  <!-- ════ TAB 0: 프로젝트 가이드 ════ -->
  <div id="tab-guide" class="tab-content active">
    <div class="card" style="max-width:900px;margin:0 auto;">

      <div style="text-align:center;margin-bottom:24px;">
        <div style="font-size:42px;margin-bottom:8px;">📈🤖</div>
        <h2 style="font-size:24px;color:var(--text1);margin:0 0 6px;">AI Stock Trading Bot</h2>
        <p style="color:var(--text2);font-size:14px;margin:0;">기술적 분석 + 멀티 타임프레임 + 재무 필터 + 자기학습 기반 미국 주식 자동 추천 시스템</p>
      </div>

      <div style="background:var(--bg);border-radius:8px;padding:16px;margin-bottom:20px;">
        <div style="font-size:14px;font-weight:600;color:var(--accent);margin-bottom:8px;">⚠️ 면책 조항</div>
        <div style="font-size:12px;color:var(--text2);line-height:1.6;">
          이 시스템은 교육 및 정보 제공 목적으로만 사용됩니다. 투자 자문이 아니며, 모든 투자 결정은 본인의 책임입니다.
          과거 성과가 미래 수익을 보장하지 않습니다.
        </div>
      </div>

      <!-- 시스템 개요 -->
      <div style="margin-bottom:24px;">
        <h3 style="font-size:16px;color:var(--text1);margin:0 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px;">🔍 시스템 개요</h3>
        <div style="font-size:13px;color:var(--text2);line-height:1.8;">
          S&P 500 종목을 대상으로 매일 장 전에 자동으로 종목을 분석하고 추천합니다.
          <strong style="color:var(--text1);">5단계 필터 체인</strong>으로 종목을 선별합니다:
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:12px;">
          <div style="background:var(--bg);border-radius:6px;padding:10px;text-align:center;">
            <div style="font-size:20px;">💰</div>
            <div style="font-size:11px;font-weight:600;color:var(--text1);">재무 필터</div>
            <div style="font-size:10px;color:var(--text2);">PER · ROE · 영업이익률<br>매출 성장률 기준 검증</div>
          </div>
          <div style="background:var(--bg);border-radius:6px;padding:10px;text-align:center;">
            <div style="font-size:20px;">📊</div>
            <div style="font-size:11px;font-weight:600;color:var(--text1);">기술적 분석</div>
            <div style="font-size:10px;color:var(--text2);">RSI · MACD · 볼린저<br>스토캐스틱 · 눌림목</div>
          </div>
          <div style="background:var(--bg);border-radius:6px;padding:10px;text-align:center;">
            <div style="font-size:20px;">🔭</div>
            <div style="font-size:11px;font-weight:600;color:var(--text1);">멀티 타임프레임</div>
            <div style="font-size:10px;color:var(--text2);">월봉 방향 · 주봉 추세<br>일봉 진입 타이밍</div>
          </div>
          <div style="background:var(--bg);border-radius:6px;padding:10px;text-align:center;">
            <div style="font-size:20px;">⏰</div>
            <div style="font-size:11px;font-weight:600;color:var(--text1);">진입 타이밍</div>
            <div style="font-size:10px;color:var(--text2);">BB 스퀴즈 확장<br>거래량 폭발 · 캔들 패턴</div>
          </div>
          <div style="background:var(--bg);border-radius:6px;padding:10px;text-align:center;">
            <div style="font-size:20px;">📰</div>
            <div style="font-size:11px;font-weight:600;color:var(--text1);">뉴스 분석</div>
            <div style="font-size:10px;color:var(--text2);">실시간 뉴스 감성 분석<br>뉴스 보너스 점수</div>
          </div>
        </div>
      </div>

      <!-- 리스크 관리 -->
      <div style="margin-bottom:24px;">
        <h3 style="font-size:16px;color:var(--text1);margin:0 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px;">🛡️ 리스크 관리</h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
          <div style="background:var(--bg);border-radius:6px;padding:12px;">
            <div style="font-size:12px;font-weight:600;color:var(--red);margin-bottom:4px;">🔴 손절 (Stop Loss)</div>
            <div style="font-size:11px;color:var(--text2);line-height:1.6;">ATR 기반 동적 손절가 설정. 진입 시 자동 계산되며 절대 무시하지 않습니다.</div>
          </div>
          <div style="background:var(--bg);border-radius:6px;padding:12px;">
            <div style="font-size:12px;font-weight:600;color:var(--green);margin-bottom:4px;">🟢 익절 (Take Profit)</div>
            <div style="font-size:11px;color:var(--text2);line-height:1.6;">TP 50%에서 트레일링 스탑 활성화. TP 100%에서 부분 청산(50%).</div>
          </div>
          <div style="background:var(--bg);border-radius:6px;padding:12px;">
            <div style="font-size:12px;font-weight:600;color:var(--accent);margin-bottom:4px;">🔄 트레일링 스탑</div>
            <div style="font-size:11px;color:var(--text2);line-height:1.6;">수익이 나기 시작하면 손절가를 자동으로 올려 수익을 보호합니다.</div>
          </div>
          <div style="background:var(--bg);border-radius:6px;padding:12px;">
            <div style="font-size:12px;font-weight:600;color:var(--yellow);margin-bottom:4px;">⏱️ 보유 기간 만료</div>
            <div style="font-size:11px;color:var(--text2);line-height:1.6;">최대 보유일 초과 시 자동 청산. 자금이 묶이는 것을 방지합니다.</div>
          </div>
        </div>
      </div>

      <!-- 자기학습 -->
      <div style="margin-bottom:24px;">
        <h3 style="font-size:16px;color:var(--text1);margin:0 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px;">🧠 자기학습 시스템</h3>
        <div style="font-size:13px;color:var(--text2);line-height:1.8;">
          매월 자동으로 전략 파라미터를 최적화합니다:
        </div>
        <div style="background:var(--bg);border-radius:6px;padding:14px;margin-top:10px;font-family:var(--font-mono);font-size:11px;color:var(--text2);line-height:1.8;">
          1️⃣ 현재 파라미터로 기준 백테스트 → 기준 점수<br>
          2️⃣ 시장 레짐 감지 (강세/약세/횡보)<br>
          3️⃣ 20회 반복 탐색: 후보 파라미터 생성 → 백테스트 → 비교<br>
          4️⃣ 복합 점수 평가: 승률 30% + PF 25% + 샤프 20% + 기대값 15% - MDD 10%<br>
          5️⃣ 5% 이상 개선 시에만 채택 (과적합 방지)
        </div>
      </div>

      <!-- 대시보드 탭 설명 -->
      <div style="margin-bottom:24px;">
        <h3 style="font-size:16px;color:var(--text1);margin:0 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px;">📑 대시보드 탭 안내</h3>
        <div style="display:grid;gap:6px;">
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">🌍</span>
            <div><strong style="font-size:12px;color:var(--text1);">시장 현황</strong> <span style="font-size:11px;color:var(--text2);">— 주요 지수, 포트폴리오 현금/투자 비율, 시장 레짐</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">💼</span>
            <div><strong style="font-size:12px;color:var(--text1);">포지션</strong> <span style="font-size:11px;color:var(--text2);">— 보유 종목 상세 (P&L, MTF, 재무 지표, 트레일링 상태)</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">📊</span>
            <div><strong style="font-size:12px;color:var(--text1);">성과</strong> <span style="font-size:11px;color:var(--text2);">— 누적 P&L 차트, 월별 수익률, 청산 유형 분포</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">🔬</span>
            <div><strong style="font-size:12px;color:var(--text1);">백테스트</strong> <span style="font-size:11px;color:var(--text2);">— 과거 데이터 기반 전략 성과 검증 결과</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">🧠</span>
            <div><strong style="font-size:12px;color:var(--text1);">자기학습</strong> <span style="font-size:11px;color:var(--text2);">— 파라미터 튜닝 이력, 신호 가중치 변화</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">⚙️</span>
            <div><strong style="font-size:12px;color:var(--text1);">전략 설정</strong> <span style="font-size:11px;color:var(--text2);">— 현재 적용 중인 전략 파라미터, 시장 레짐</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">📅</span>
            <div><strong style="font-size:12px;color:var(--text1);">실적 캘린더</strong> <span style="font-size:11px;color:var(--text2);">— 보유 종목 어닝 일정, 월간 실적 캘린더</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">📋</span>
            <div><strong style="font-size:12px;color:var(--text1);">주간 리포트</strong> <span style="font-size:11px;color:var(--text2);">— 매주 일요일 자동 생성, 주간 거래/성과 요약</span></div>
          </div>
          <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--bg);border-radius:6px;">
            <span style="font-size:16px;width:24px;text-align:center;">📰</span>
            <div><strong style="font-size:12px;color:var(--text1);">경제 뉴스</strong> <span style="font-size:11px;color:var(--text2);">— 실시간 RSS 뉴스 (Yahoo/CNBC/MarketWatch, 한글 번역)</span></div>
          </div>
        </div>
      </div>

      <!-- 자동화 일정 -->
      <div style="margin-bottom:24px;">
        <h3 style="font-size:16px;color:var(--text1);margin:0 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px;">⏰ 자동화 스케줄</h3>
        <div style="display:grid;gap:6px;">
          <div style="display:flex;justify-content:space-between;padding:8px 12px;background:var(--bg);border-radius:6px;font-size:12px;">
            <span style="color:var(--text1);font-weight:600;">🔍 종목 추천 + 포지션 업데이트</span>
            <span style="color:var(--text2);">평일 매일 (UTC 21:30 / KST 06:30)</span>
          </div>
          <div style="display:flex;justify-content:space-between;padding:8px 12px;background:var(--bg);border-radius:6px;font-size:12px;">
            <span style="color:var(--text1);font-weight:600;">📋 주간 리포트</span>
            <span style="color:var(--text2);">매주 일요일 (UTC 09:00 / KST 18:00)</span>
          </div>
          <div style="display:flex;justify-content:space-between;padding:8px 12px;background:var(--bg);border-radius:6px;font-size:12px;">
            <span style="color:var(--text1);font-weight:600;">🧠 자기학습</span>
            <span style="color:var(--text2);">매월 첫 일요일 (UTC 10:00 / KST 19:00)</span>
          </div>
          <div style="display:flex;justify-content:space-between;padding:8px 12px;background:var(--bg);border-radius:6px;font-size:12px;">
            <span style="color:var(--text1);font-weight:600;">📅 실적 캘린더</span>
            <span style="color:var(--text2);">어닝 시즌 (1·4·7·10월) 1일, 15일</span>
          </div>
        </div>
      </div>

      <!-- 기술 스택 -->
      <div style="margin-bottom:16px;">
        <h3 style="font-size:16px;color:var(--text1);margin:0 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px;">🛠️ 기술 스택</h3>
        <div style="display:flex;flex-wrap:wrap;gap:6px;">
          <span style="font-size:10px;padding:3px 8px;border-radius:4px;background:#3b82f620;color:#60a5fa;">Python</span>
          <span style="font-size:10px;padding:3px 8px;border-radius:4px;background:#f59e0b20;color:#fbbf24;">yfinance</span>
          <span style="font-size:10px;padding:3px 8px;border-radius:4px;background:#10b98120;color:#34d399;">pandas</span>
          <span style="font-size:10px;padding:3px 8px;border-radius:4px;background:#8b5cf620;color:#a78bfa;">GitHub Actions</span>
          <span style="font-size:10px;padding:3px 8px;border-radius:4px;background:#ef444420;color:#f87171;">Discord Webhook</span>
          <span style="font-size:10px;padding:3px 8px;border-radius:4px;background:#06b6d420;color:#22d3ee;">GitHub Pages</span>
          <span style="font-size:10px;padding:3px 8px;border-radius:4px;background:#ec489920;color:#f472b6;">Chart.js</span>
        </div>
      </div>

      <div style="text-align:center;padding-top:12px;border-top:1px solid var(--border);">
        <a href="https://github.com/blackrabbitDeveloper/stock-notify" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:13px;">
          ⭐ GitHub Repository →
        </a>
      </div>

    </div>
  </div>

  <div id="tab-market" class="tab-content">
    <p class="tab-desc">주요 시장 지수와 환율, 원자재 시세를 실시간으로 확인합니다.</p>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
      <span style="color:var(--text2);font-size:14px;">📅 기간:</span>
      <div class="period-btns" id="periodBtns">
        <button class="period-btn" data-range="1mo" onclick="switchPeriod('1mo')">1개월</button>
        <button class="period-btn" data-range="3mo" onclick="switchPeriod('3mo')">3개월</button>
        <button class="period-btn active" data-range="6mo" onclick="switchPeriod('6mo')">6개월</button>
        <button class="period-btn" data-range="1y" onclick="switchPeriod('1y')">1년</button>
      </div>
      <span id="marketStatus" style="color:var(--text2);font-size:12px;margin-left:auto;"></span>
    </div>
    <div class="grid grid-4" id="marketCards">
      <div class="empty-state" style="grid-column:1/-1"><div class="icon">⏳</div>시장 데이터 로딩 중...</div>
    </div>
    <div class="grid grid-2" style="margin-top:16px;">
      <div class="chart-box"><h3 id="sp500Title">📈 S&P 500</h3><canvas id="sp500Chart"></canvas></div>
      <div class="chart-box"><h3 id="nasdaqTitle">📈 NASDAQ 100</h3><canvas id="nasdaqChart"></canvas></div>
    </div>
    <div class="grid grid-2" style="margin-top:16px;">
      <div class="chart-box"><h3 id="usdkrwTitle">💱 USD/KRW 환율</h3><canvas id="usdkrwChart"></canvas></div>
      <div class="chart-box"><h3 id="goldTitle">🥇 Gold 시세</h3><canvas id="goldChart"></canvas></div>
    </div>
  </div>

  <!-- ════ TAB 1: 포지션 ════ -->
  <div id="tab-positions" class="tab-content">
    <p class="tab-desc">현재 보유 중인 포지션과 최근 청산 이력을 관리합니다.</p>
    <div class="grid grid-4" id="statCards"></div>
    <div class="grid grid-2" style="margin-top:16px;">
      <div class="chart-box"><h3>💰 포지션 / 현금 비율</h3><canvas id="cashRatioChart"></canvas></div>
      <div class="chart-box"><h3>📊 포지션 사용률</h3><div id="portfolioInfo"></div></div>
    </div>
    <div class="section-title">📌 오픈 포지션</div>
    <div class="table-wrap" id="openPositionsTable"></div>
    <div class="section-title">📜 최근 청산 이력</div>
    <div class="table-wrap" id="historyTable"></div>
  </div>

  <!-- ════ TAB 2: 성과 ════ -->
  <div id="tab-performance" class="tab-content">
    <p class="tab-desc">누적 수익률, 월별 성과, 청산 유형 등 전체 트레이딩 성과를 분석합니다.</p>
    <div class="grid grid-2">
      <div class="chart-box"><h3>📈 누적 수익률</h3><canvas id="cumulativeChart"></canvas></div>
      <div class="chart-box"><h3>📊 월별 성과</h3><canvas id="monthlyChart"></canvas></div>
    </div>
    <div class="grid grid-2" style="margin-top:16px;">
      <div class="chart-box"><h3>🎯 청산 유형 비율</h3><canvas id="exitTypeChart"></canvas></div>
      <div class="chart-box"><h3>📋 월별 상세</h3><div id="monthlyDetailTable"></div></div>
    </div>
  </div>

  <!-- ════ TAB 3: 백테스트 ════ -->
  <div id="tab-backtest" class="tab-content">
    <p class="tab-desc">과거 데이터 기반으로 현재 전략의 성과를 검증한 결과입니다.</p>
    <div class="grid grid-4" id="btStatCards"></div>
    <div class="grid grid-2" style="margin-top:16px;">
      <div class="chart-box"><h3>📡 신호별 성과</h3><canvas id="signalChart"></canvas></div>
      <div class="chart-box"><h3>🎯 점수 구간별 성과</h3><canvas id="scoreBucketChart"></canvas></div>
    </div>
    <div class="chart-box" style="margin-top:16px;"><h3>📅 백테스트 월별 수익</h3><canvas id="btMonthlyChart"></canvas></div>
  </div>

  <!-- ════ TAB 4: 자기학습 ════ -->
  <div id="tab-tuning" class="tab-content">
    <p class="tab-desc">시장 레짐에 따라 자동으로 최적화되는 전략 파라미터와 신호 가중치입니다.</p>
    <div class="section-title">⚙️ 현재 전략 파라미터</div>
    <div class="param-grid" id="paramGrid"></div>
    <div class="section-title" style="margin-top:24px;">📡 신호 가중치</div>
    <div id="weightBars"></div>
    <div class="section-title" style="margin-top:24px;">📜 튜닝 이력</div>
    <div class="table-wrap" id="tuningHistoryTable"></div>
  </div>

  <!-- ════ TAB 5: 전략 설정 ════ -->
  <div id="tab-strategy" class="tab-content">
    <p class="tab-desc">현재 적용 중인 전략의 전체 설정값을 한눈에 확인합니다.</p>

    <div class="grid grid-2">
      <!-- 유니버스 설정 -->
      <div class="card">
        <div class="card-header">🌐 종목 유니버스</div>
        <div id="stratUniverse" style="margin-top:12px;"></div>
      </div>
      <!-- 진입/청산 조건 -->
      <div class="card">
        <div class="card-header">🎯 진입 & 청산 조건</div>
        <div id="stratEntry" style="margin-top:12px;"></div>
      </div>
    </div>

    <div class="grid grid-2" style="margin-top:16px;">
      <!-- 시장 레짐 -->
      <div class="card">
        <div class="card-header">🌡️ 시장 레짐</div>
        <div id="stratRegime" style="margin-top:12px;"></div>
      </div>
      <!-- AI 설정 -->
      <div class="card">
        <div class="card-header">🤖 AI & 분석 설정</div>
        <div id="stratAI" style="margin-top:12px;"></div>
      </div>
    </div>

    <div style="margin-top:16px;">
      <!-- 신호 가중치 전체 -->
      <div class="card">
        <div class="card-header">📡 신호별 가중치 현황</div>
        <div id="stratWeightsChart" style="margin-top:12px;"><canvas id="stratWeightCanvas"></canvas></div>
      </div>
    </div>
  </div>

  <!-- ════ TAB 6: 실적 캘린더 ════ -->
  <div id="tab-earnings" class="tab-content">
    <p class="tab-desc">유니버스 종목의 실적 발표 일정입니다. 보유 종목은 빨간색으로 표시됩니다.</p>
    <div class="grid grid-2">
      <div class="card">
        <div class="card-header">⚠️ 이번 주 & 다음 주 실적 발표</div>
        <div id="earningsUpcoming" style="margin-top:12px;"></div>
      </div>
      <div class="card">
        <div class="card-header">📊 실적 발표 통계</div>
        <div id="earningsStats" style="margin-top:12px;"></div>
      </div>
    </div>
    <div style="margin-top:16px;">
      <div class="card">
        <div class="card-header">📅 월간 실적 캘린더</div>
        <div id="earningsCalendar" style="margin-top:12px;overflow-x:auto;"></div>
      </div>
    </div>
  </div>

  <!-- ════ TAB 7: 주간 리포트 ════ -->
  <div id="tab-reports" class="tab-content">
    <p class="tab-desc">매주 일요일 자동 생성되는 주간 리포트 목록입니다.</p>
    <div id="weeklyReportsList"></div>
  </div>

  <!-- ════ TAB 8: 경제 뉴스 ════ -->
  <div id="tab-news" class="tab-content">
    <p class="tab-desc">미국 주식시장 주요 뉴스를 실시간으로 가져옵니다.</p>
    <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;">
      <button class="news-src-btn active" onclick="filterNews('all')">📰 전체</button>
      <button class="news-src-btn" onclick="filterNews('yahoo')">Yahoo Finance</button>
      <button class="news-src-btn" onclick="filterNews('cnbc')">CNBC</button>
      <button class="news-src-btn" onclick="filterNews('marketwatch')">MarketWatch</button>
      <button id="newsRefreshBtn" onclick="loadAllNews()" style="margin-left:auto;padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--surface);color:var(--accent);cursor:pointer;font-size:12px;">🔄 새로고침</button>
    </div>
    <div id="newsLastUpdated" style="font-size:11px;color:var(--text2);margin-bottom:12px;"></div>
    <div id="newsList"></div>
  </div>
</div>

<footer style="max-width:1400px;margin:40px auto 0;padding:20px 20px 32px;border-top:1px solid var(--border);text-align:center;font-family:var(--font-mono);font-size:11px;color:var(--text2);line-height:1.8;">
  <div style="margin-bottom:6px;">⚠️ <strong style="color:var(--yellow);">면책 조항</strong></div>
  이 봇은 교육 및 정보 제공 목적으로만 사용됩니다.<br>
  투자 자문이 아닙니다 · 모든 투자 결정은 본인의 책임입니다 · 과거 성과가 미래 수익을 보장하지 않습니다 · 반드시 손절매를 설정하세요
</footer>

<script>
let D = {data_json};

// ── GitHub raw 에서 최신 데이터 fetch (실시간 갱신) ──
const REPO_RAW = 'https://raw.githubusercontent.com/blackrabbitDeveloper/stock-notify/main';

async function fetchLiveData() {{
  const files = {{
    positions:    REPO_RAW + '/data/positions.json',
    history:      REPO_RAW + '/data/history.json',
    strategy:     REPO_RAW + '/config/strategy_state.json',
    weights:      REPO_RAW + '/config/signal_weights.json',
    tuning:       REPO_RAW + '/data/tuning_history.json',
    universe:     REPO_RAW + '/config/universe.yaml',
    weeklyIndex:  REPO_RAW + '/data/weekly_reports/index.json',
  }};

  async function grab(url) {{
    try {{
      const r = await fetch(url + '?t=' + Date.now());
      if (!r.ok) return null;
      return await r.json();
    }} catch(e) {{ return null; }}
  }}

  // 백테스트: 최신 JSON 파일 찾기
  async function grabLatestBacktest() {{
    try {{
      // GitHub API로 파일 목록 조회
      const r = await fetch('https://api.github.com/repos/blackrabbitDeveloper/stock-notify/contents/data/backtest?t=' + Date.now());
      if (!r.ok) return null;
      const items = await r.json();
      const jsons = items.filter(f => f.name.endsWith('.json')).sort((a,b) => b.name.localeCompare(a.name));
      if (!jsons.length) return null;
      const r2 = await fetch(jsons[0].download_url);
      return r2.ok ? await r2.json() : null;
    }} catch(e) {{ return null; }}
  }}

  const [pos, hist, strat, wt, tune, bt] = await Promise.all([
    grab(files.positions),
    grab(files.history),
    grab(files.strategy),
    grab(files.weights),
    grab(files.tuning),
    grabLatestBacktest(),
  ]);

  let updated = false;

  if (pos) {{
    D.positions = pos.positions || [];
    D.stats = pos.stats || {{}};
    updated = true;
  }}
  if (hist) {{
    D.history = Array.isArray(hist) ? hist.slice(-100) : [];
    // 일별 누적 PnL 재계산
    let cum = 0;
    const dailyPnl = {{}};
    const sorted = D.history.slice().sort((a,b) => (a.exit_date||'').localeCompare(b.exit_date||''));
    for (const h of sorted) {{
      cum += (h.pnl_pct || 0);
      dailyPnl[h.exit_date] = Math.round(cum * 100) / 100;
    }}
    D.daily_cumulative_pnl = dailyPnl;
    // 월별 성과 재계산
    const mp = {{}};
    for (const h of sorted) {{
      const m = (h.exit_date || '').slice(0,7);
      if (!m) continue;
      if (!mp[m]) mp[m] = {{trades:0, wins:0, total_pnl:0}};
      mp[m].trades++;
      mp[m].total_pnl += (h.pnl_pct || 0);
      if ((h.pnl_pct || 0) > 0) mp[m].wins++;
    }}
    for (const m of Object.keys(mp)) {{
      mp[m].win_rate = mp[m].trades ? Math.round(mp[m].wins / mp[m].trades * 1000) / 10 : 0;
      mp[m].total_pnl = Math.round(mp[m].total_pnl * 100) / 100;
    }}
    D.monthly_performance = mp;
    // 청산 유형
    const et = {{take_profit:0, stop_loss:0, expired:0, sell_signal:0, strategy_rebalance:0, trailing_stop:0}};
    for (const h of D.history) {{ if (et[h.close_reason] !== undefined) et[h.close_reason]++; }}
    D.exit_types = et;
    updated = true;
  }}
  if (strat) {{
    D.strategy = {{
      current_params: strat.current_params || {{}},
      current_regime: strat.current_regime || 'unknown',
      regime_confidence: strat.regime_confidence || 0,
      last_tuned_at: strat.last_tuned_at || '',
    }};
    updated = true;
  }}
  if (wt) {{ D.signal_weights = wt; updated = true; }}
  if (tune) {{ D.tuning_history = Array.isArray(tune) ? tune.slice(-20) : []; updated = true; }}
  if (bt) {{
    D.backtest = {{
      summary: bt.summary || {{}},
      signal_performance: bt.signal_performance || [],
      monthly_returns: bt.monthly_returns || [],
      score_buckets: bt.score_buckets || [],
    }};
    updated = true;
  }}

  if (updated) {{
    D.generated_at = new Date().toISOString();
    console.log('✅ GitHub 에서 최신 데이터 로드 완료');
  }} else {{
    console.log('⚠️ 실시간 데이터 로드 실패, 정적 데이터 사용');
  }}
}}

// ── 유틸 ──
const fmt = (v, d=2) => v != null ? Number(v).toFixed(d) : '—';
const pnlClass = v => v > 0 ? 'positive' : v < 0 ? 'negative' : 'neutral';
const pnlSign = v => v > 0 ? '+' + fmt(v) : fmt(v);
const regimeIcon = r => ({{bullish:'🐂',bearish:'🐻',sideways:'📊',conservative:'🛡️'}})[r] || '❓';
const regimeClass = r => 'regime-' + (r || 'unknown');

// ── 신호 가중치 한국어 라벨 ──
const weightLabels = {{
  pullback_score: '눈림목', breakout_score: '돌파', divergence_score: '다이버전스',
  stoch_cross_up: '스토캐스틱', golden_cross: '골든크로스', ma_alignment: '정배열',
  macd_cross_up: 'MACD상향', bullish_volume: '거래량', obv_rising: 'OBV상승',
  strong_trend: '추세강도', bb_squeeze_breakout: 'BB스퀀즈', rr_bonus: 'R:R보너스',
  rsi_oversold_bounce: 'RSI반등',
  sell_dead_cross: '매도:데드크로스', sell_macd_down: '매도:MACD하향',
  sell_bearish_div: '매도:약세다이버', sell_rsi_overbought: '매도:RSI과매수',
  sell_stoch_overbought: '매도:스토캐스틱', sell_bb_upper_reject: '매도:BB상단',
}};

// ── 탭 전환 ──
function showTab(id) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  event.target.classList.add('active');
  // 뉴스 탭 진입 시 자동 로드
  if (id === 'news' && !_newsLoaded) loadAllNews();
}}

// ── 초기화 ──
function init() {{
  // 상단바
  const gen = D.generated_at ? new Date(D.generated_at) : new Date();
  document.getElementById('lastUpdate').textContent = '갱신: ' + gen.toLocaleString('ko-KR');
  const regime = D.strategy?.current_regime || 'unknown';
  const conf = D.strategy?.regime_confidence || 0;
  const badge = document.getElementById('regimeBadge');
  badge.className = 'regime-badge ' + regimeClass(regime);
  badge.textContent = regimeIcon(regime) + ' ' + regime.toUpperCase() + ' (' + Math.round(conf * 100) + '%)';

  renderMarket();
  renderStatCards();
  renderOpenPositions();
  renderHistory();
  renderPerformance();
  renderBacktest();
  renderTuning();
  renderStrategy();
  renderEarnings();
  renderReports();
}}

// ════ TAB 0: 시장 현황 (실시간 API) ════
const MARKET_CFG = {{
  sp500:   {{ ticker: '%5EGSPC', name: 'S&P 500',    icon: '🇺🇸', unit: '',  color: '#38bdf8', bg: 'rgba(56,189,248,0.08)',  canvas: 'sp500Chart',  title: 'sp500Title' }},
  nasdaq:  {{ ticker: '%5ENDX',  name: 'NASDAQ 100', icon: '💻', unit: '',  color: '#a78bfa', bg: 'rgba(167,139,250,0.08)', canvas: 'nasdaqChart', title: 'nasdaqTitle' }},
  usd_krw: {{ ticker: 'KRW%3DX', name: 'USD/KRW',   icon: '💱', unit: '₩', color: '#fbbf24', bg: 'rgba(251,191,36,0.08)',  canvas: 'usdkrwChart', title: 'usdkrwTitle' }},
  gold:    {{ ticker: 'GC%3DF',  name: 'Gold',       icon: '🥇', unit: '$', color: '#fb923c', bg: 'rgba(251,146,60,0.08)',  canvas: 'goldChart',   title: 'goldTitle' }},
}};
let marketCharts = {{}};
let currentPeriod = '6mo';
const periodLabels = {{ '1mo': '1개월', '3mo': '3개월', '6mo': '6개월', '1y': '1년' }};

function switchPeriod(range) {{
  currentPeriod = range;
  document.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', b.dataset.range === range));
  fetchMarketData(range);
}}

async function fetchYahoo(ticker, range) {{
  const intervals = {{ '1mo': '1d', '3mo': '1d', '6mo': '1d', '1y': '1wk' }};
  const interval = intervals[range] || '1d';
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${{ticker}}?range=${{range}}&interval=${{interval}}`;
  // 직접 호출 시도 → CORS 실패시 프록시 폴백
  try {{
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(resp.status);
    return await resp.json();
  }} catch(e) {{
    const proxy = `https://corsproxy.io/?${{encodeURIComponent(url)}}`;
    const resp2 = await fetch(proxy);
    if (!resp2.ok) throw new Error(`proxy ${{resp2.status}}`);
    return await resp2.json();
  }}
}}

function parseYahoo(json) {{
  const r = json?.chart?.result?.[0];
  if (!r) return null;
  const ts = r.timestamp || [];
  const closes = r.indicators?.quote?.[0]?.close || [];
  const dates = [], values = [];
  for (let i = 0; i < ts.length; i++) {{
    if (closes[i] == null) continue;
    const d = new Date(ts[i] * 1000);
    dates.push(`${{String(d.getMonth()+1).padStart(2,'0')}}-${{String(d.getDate()).padStart(2,'0')}}`);
    values.push(Math.round(closes[i] * 100) / 100);
  }}
  if (!values.length) return null;
  const current = values[values.length - 1];
  const prev = values.length >= 2 ? values[values.length - 2] : current;
  const first = values[0];
  const dayChg = prev ? Math.round((current - prev) / prev * 10000) / 100 : 0;
  const perChg = first ? Math.round((current - first) / first * 10000) / 100 : 0;
  return {{ current, dayChg, perChg, dates, values }};
}}

async function fetchMarketData(range) {{
  const statusEl = document.getElementById('marketStatus');
  statusEl.textContent = '⏳ 데이터 가져오는 중...';

  const results = {{}};
  const promises = Object.entries(MARKET_CFG).map(async ([key, cfg]) => {{
    try {{
      const json = await fetchYahoo(cfg.ticker, range);
      const parsed = parseYahoo(json);
      if (parsed) results[key] = parsed;
    }} catch(e) {{
      console.warn(`${{cfg.name}} 로드 실패:`, e);
    }}
  }});
  await Promise.all(promises);

  const now = new Date();
  statusEl.textContent = `✅ ${{now.toLocaleTimeString('ko-KR')}} 기준 · ${{Object.keys(results).length}}/4 지표`;

  // 카드 렌더링
  const fmtPrice = (k, v) => k === 'usd_krw' ? v.toLocaleString('ko-KR', {{maximumFractionDigits:2}}) : v.toLocaleString('en-US', {{maximumFractionDigits:2}});

  if (!Object.keys(results).length) {{
    document.getElementById('marketCards').innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">🌍</div>시장 데이터를 가져올 수 없습니다<br><small style="color:var(--text2)">네트워크 확인 후 새로고침해 주세요</small></div>';
    return;
  }}

  let cards = '';
  for (const [key, cfg] of Object.entries(MARKET_CFG)) {{
    const d = results[key];
    if (!d) continue;
    const dc = d.dayChg, pc = d.perChg;
    cards += `<div class="card">
      <div class="card-header">${{cfg.icon}} ${{cfg.name}}</div>
      <div class="card-value" style="font-size:22px;">${{cfg.unit}}${{fmtPrice(key, d.current)}}</div>
      <div class="card-sub">
        <span class="${{pnlClass(dc)}}">일간 ${{dc > 0 ? '+' : ''}}${{dc.toFixed(2)}}%</span>
        &nbsp;·&nbsp;
        <span class="${{pnlClass(pc)}}"> ${{periodLabels[range]}} ${{pc > 0 ? '+' : ''}}${{pc.toFixed(2)}}%</span>
      </div>
    </div>`;
  }}
  document.getElementById('marketCards').innerHTML = cards;

  // 차트 렌더링
  for (const [key, cfg] of Object.entries(MARKET_CFG)) {{
    const d = results[key];
    const el = document.getElementById(cfg.canvas);
    const titleEl = document.getElementById(cfg.title);
    if (!el) continue;

    const titleIcons = {{ sp500: '📈', nasdaq: '📈', usd_krw: '💱', gold: '🥇' }};
    if (titleEl) titleEl.textContent = `${{titleIcons[key] || '📈'}} ${{cfg.name}} (${{periodLabels[range]}})`;

    // 기존 차트 파괴
    if (marketCharts[key]) {{ marketCharts[key].destroy(); marketCharts[key] = null; }}

    if (!d || !d.dates.length) continue;

    marketCharts[key] = new Chart(el, {{
      type: 'line',
      data: {{
        labels: d.dates,
        datasets: [{{
          label: cfg.name,
          data: d.values,
          borderColor: cfg.color,
          backgroundColor: cfg.bg,
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHitRadius: 8,
          borderWidth: 2,
        }}]
      }},
      options: {{
        ...chartOpts(''),
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            mode: 'index', intersect: false,
            callbacks: {{
              label: (ctx) => `${{cfg.name}}: ${{cfg.unit || '$'}}${{ctx.parsed.y.toLocaleString()}}`
            }}
          }},
        }},
        interaction: {{ mode: 'nearest', axis: 'x', intersect: false }},
      }},
    }});
  }}
}}

function renderMarket() {{
  fetchMarketData(currentPeriod);
}}

// ════ TAB 1: 포지션 ════
function renderStatCards() {{
  const s = D.stats || {{}};
  const openCount = (D.positions || []).filter(p => p.status === 'open').length;
  const html = [
    statCard('오픈 포지션', openCount, '', 'accent'),
    statCard('총 거래', s.total_trades || 0, `승 ${{s.wins||0}} / 패 ${{s.losses||0}}`, ''),
    statCard('승률', fmt(s.win_rate||0,1)+'%', `만료 ${{s.expired||0}} / 매도 ${{s.sell_signal||0}}건`, pnlClass(s.win_rate-50)),
    statCard('누적 수익', pnlSign(s.total_pnl_pct||0)+'%', `평균 ${{pnlSign(s.avg_pnl_pct||0)}}%`, pnlClass(s.total_pnl_pct)),
  ].join('');
  document.getElementById('statCards').innerHTML = html;

  // ── 포지션/현금 비율 차트 ──
  const pf = D.portfolio || {{}};
  const investPct = pf.current_invest_pct || 0;
  const cashPct = pf.current_cash_pct || 100;
  const targetCash = pf.target_cash_pct || 30;

  new Chart(document.getElementById('cashRatioChart'), {{
    type: 'doughnut',
    data: {{
      labels: ['투자 비중', '현금 비중'],
      datasets: [{{
        data: [investPct, cashPct],
        backgroundColor: ['#38bdf8', '#1e293b'],
        borderColor: ['#38bdf8', '#334155'],
        borderWidth: 2,
      }}]
    }},
    options: {{
      responsive: true,
      cutout: '65%',
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', font: {{ family: "'JetBrains Mono'" }} }} }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{ return ctx.label + ': ' + ctx.parsed.toFixed(1) + '%'; }}
          }}
        }}
      }},
    }},
  }});

  // ── 포지션 정보 패널 ──
  const regimeEmoji = {{'bullish':'🐂','bearish':'🐻','sideways':'📊','conservative':'🛡️','volatile':'⚡'}}[pf.regime] || '❓';
  document.getElementById('portfolioInfo').innerHTML = `
    <div style="padding:12px;">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
        <div style="text-align:center;padding:16px;background:var(--bg);border-radius:8px;">
          <div style="font-size:28px;font-weight:bold;color:var(--accent)">${{investPct.toFixed(1)}}%</div>
          <div style="color:var(--text2);font-size:12px;margin-top:4px;">투자 비중</div>
        </div>
        <div style="text-align:center;padding:16px;background:var(--bg);border-radius:8px;">
          <div style="font-size:28px;font-weight:bold;color:#64748b">${{cashPct.toFixed(1)}}%</div>
          <div style="color:var(--text2);font-size:12px;margin-top:4px;">현금 비중</div>
        </div>
      </div>
      <div style="font-size:13px;color:var(--text2);line-height:1.8;">
        ${{regimeEmoji}} 레짐: <strong style="color:var(--text1)">${{pf.regime || 'unknown'}}</strong><br>
        📦 포지션: <strong style="color:var(--text1)">${{pf.open_count || 0}} / ${{pf.max_positions || 10}}</strong>
        <span style="color:var(--accent)">(${{pf.usage_pct || 0}}%)</span><br>
        🎯 목표 현금: <strong style="color:var(--text1)">${{targetCash}}%</strong>
        (${{pf.regime === 'bearish' ? '하락장 방어' : pf.regime === 'bullish' ? '공격적 투자' : '균형 유지'}})<br>
        🔓 남은 슬롯: <strong style="color:var(--green)">${{pf.available_slots || 0}}개</strong>
      </div>
    </div>
  `;
}}

function statCard(title, value, sub, cls) {{
  return `<div class="card"><div class="card-header">${{title}}</div><div class="card-value ${{cls}}">${{value}}</div><div class="card-sub">${{sub}}</div></div>`;
}}

function renderOpenPositions() {{
  const open = (D.positions || []).filter(p => p.status === 'open');
  if (!open.length) {{
    document.getElementById('openPositionsTable').innerHTML = '<div class="empty-state"><div class="icon">📭</div>오픈 포지션이 없습니다</div>';
    return;
  }}

  // MTF 정렬 아이콘
  const mtfIcon = {{'strong_bull':'🟢🟢','bull':'🟢','neutral':'🟡','bear':'🔴','strong_bear':'🔴🔴','':'⚪'}};

  let html = '';
  for (const p of open) {{
    const last = p.price_history?.length ? p.price_history[p.price_history.length-1].close : p.entry_price;
    const pnl = ((last - p.entry_price) / p.entry_price * 100);
    const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text2)';

    // 상태 태그
    const tags = [];
    if (p.trailing_active) tags.push('<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:#7c3aed;color:white;">트레일링</span>');
    if (p.partial_closed) tags.push('<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:#f59e0b;color:white;">부분청산</span>');

    // 재무 데이터
    const fund = p.fundamentals || {{}};
    const per = fund.per != null ? fund.per.toFixed(1) : '—';
    const roe = fund.roe != null ? fund.roe.toFixed(1) + '%' : '—';
    const opMargin = fund.operating_margin != null ? fund.operating_margin.toFixed(1) + '%' : '—';
    const revGrowth = fund.revenue_growth != null ? fund.revenue_growth.toFixed(1) + '%' : '—';

    // MTF
    const mtf = mtfIcon[p.mtf_alignment || ''] || '⚪';
    const mtfLabel = p.mtf_alignment || '—';

    html += `<div class="card" style="margin-bottom:10px;padding:14px;">
      <!-- 1행: 종목 + P&L -->
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="font-size:17px;font-weight:700;color:var(--text1);">${{p.ticker}}</span>
          ${{p.sector ? `<span style="font-size:10px;padding:1px 6px;border-radius:3px;background:var(--surface2);color:var(--text2);">${{p.sector}}</span>` : ''}}
          ${{tags.join(' ')}}
        </div>
        <div style="text-align:right;">
          <div style="font-size:20px;font-weight:700;color:${{pnlColor}};">${{pnl >= 0 ? '+' : ''}}${{pnl.toFixed(2)}}%</div>
          <div style="font-size:11px;color:var(--text2);">${{fmt(last)}}</div>
        </div>
      </div>

      <!-- 2행: 가격 정보 -->
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px;">
        <div style="text-align:center;padding:5px;background:var(--bg);border-radius:5px;">
          <div style="font-size:12px;font-weight:600;">${{fmt(p.entry_price)}}</div>
          <div style="font-size:9px;color:var(--text2);">진입가</div>
        </div>
        <div style="text-align:center;padding:5px;background:var(--bg);border-radius:5px;">
          <div style="font-size:12px;font-weight:600;color:var(--red);">${{fmt(p.stop_loss)}}</div>
          <div style="font-size:9px;color:var(--text2);">손절</div>
        </div>
        <div style="text-align:center;padding:5px;background:var(--bg);border-radius:5px;">
          <div style="font-size:12px;font-weight:600;color:var(--green);">${{fmt(p.take_profit)}}</div>
          <div style="font-size:9px;color:var(--text2);">익절</div>
        </div>
        <div style="text-align:center;padding:5px;background:var(--bg);border-radius:5px;">
          <div style="font-size:12px;font-weight:600;">${{fmt(p.tech_score,1)}}</div>
          <div style="font-size:9px;color:var(--text2);">점수</div>
        </div>
      </div>

      <!-- 3행: MTF + 타이밍 -->
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;">
        <span style="font-size:10px;padding:2px 7px;border-radius:4px;background:var(--surface2);color:var(--text1);">
          ${{mtf}} MTF: ${{mtfLabel}}${{p.mtf_score ? ' (' + (p.mtf_score > 0 ? '+' : '') + p.mtf_score.toFixed(1) + ')' : ''}}
        </span>
        ${{p.timing_details && p.timing_details !== '패턴 없음' ? `<span style="font-size:10px;padding:2px 7px;border-radius:4px;background:#7c3aed22;color:#a78bfa;">⏰ ${{p.timing_details}}</span>` : ''}}
      </div>

      <!-- 4행: 재무 지표 -->
      <div style="display:flex;gap:8px;flex-wrap:wrap;font-size:10px;color:var(--text2);">
        <span>PER <strong style="color:var(--text1);">${{per}}</strong></span>
        <span>ROE <strong style="color:var(--text1);">${{roe}}</strong></span>
        <span>영업이익률 <strong style="color:var(--text1);">${{opMargin}}</strong></span>
        <span>매출성장 <strong style="color:var(--text1);">${{revGrowth}}</strong></span>
        <span style="margin-left:auto;">진입 ${{p.entry_date}}</span>
      </div>
    </div>`;
  }}

  document.getElementById('openPositionsTable').innerHTML = html;
}}

function renderHistory() {{
  const hist = (D.history || []).slice().reverse().slice(0, 30);
  if (!hist.length) {{
    document.getElementById('historyTable').innerHTML = '<div class="empty-state"><div class="icon">📜</div>청산 이력이 없습니다</div>';
    return;
  }}
  let html = '<table><thead><tr><th>종목</th><th>진입</th><th>청산</th><th>P&L</th><th>유형</th><th>보유일</th><th>진입일</th></tr></thead><tbody>';
  for (const h of hist) {{
    const reason = h.close_reason || '';
    html += `<tr>
      <td><strong>${{h.ticker}}</strong></td>
      <td>${{fmt(h.entry_price)}}</td>
      <td>${{fmt(h.exit_price)}}</td>
      <td class="${{pnlClass(h.pnl_pct)}}"><strong>${{pnlSign(h.pnl_pct)}}%</strong></td>
      <td class="status-${{reason}}">${{{{take_profit:'✅ 익절',stop_loss:'🛑 손절',expired:'⏰ 만료',sell_signal:'📉 매도',strategy_rebalance:'🔄 재검증',trailing_stop:'📈 트레일링'}}[reason]||reason}}</td>
      <td>${{h.hold_days||'—'}}</td>
      <td>${{h.entry_date}}</td>
    </tr>`;
  }}
  html += '</tbody></table>';
  document.getElementById('historyTable').innerHTML = html;
}}

// ════ TAB 2: 성과 ════
function renderPerformance() {{
  // 누적 수익 차트
  const cumData = D.daily_cumulative_pnl || {{}};
  const dates = Object.keys(cumData).sort();
  if (dates.length > 0) {{
    new Chart(document.getElementById('cumulativeChart'), {{
      type: 'line',
      data: {{
        labels: dates,
        datasets: [{{
          label: '누적 수익률 (%)',
          data: dates.map(d => cumData[d]),
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56,189,248,0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: dates.length > 30 ? 0 : 3,
        }}]
      }},
      options: chartOpts(''),
    }});
  }}

  // 월별 성과 차트
  const mp = D.monthly_performance || {{}};
  const months = Object.keys(mp).sort();
  if (months.length > 0) {{
    new Chart(document.getElementById('monthlyChart'), {{
      type: 'bar',
      data: {{
        labels: months,
        datasets: [{{
          label: '월 수익률 (%)',
          data: months.map(m => mp[m].total_pnl),
          backgroundColor: months.map(m => mp[m].total_pnl >= 0 ? 'rgba(52,211,153,0.7)' : 'rgba(248,113,113,0.7)'),
          borderRadius: 4,
        }}]
      }},
      options: chartOpts(''),
    }});

    // 월별 상세 테이블
    let mhtml = '<table><thead><tr><th>월</th><th>거래</th><th>승률</th><th>수익</th></tr></thead><tbody>';
    for (const m of months) {{
      const d = mp[m];
      mhtml += `<tr><td>${{m}}</td><td>${{d.trades}}</td><td>${{d.win_rate}}%</td><td class="${{pnlClass(d.total_pnl)}}">${{pnlSign(d.total_pnl)}}%</td></tr>`;
    }}
    mhtml += '</tbody></table>';
    document.getElementById('monthlyDetailTable').innerHTML = mhtml;
  }}

  // 청산 유형 도넛
  const et = D.exit_types || {{}};
  const total = (et.take_profit||0) + (et.stop_loss||0) + (et.expired||0) + (et.sell_signal||0) + (et.strategy_rebalance||0) + (et.trailing_stop||0);
  if (total > 0) {{
    new Chart(document.getElementById('exitTypeChart'), {{
      type: 'doughnut',
      data: {{
        labels: ['익절', '손절', '만료', '매도', '재검증', '트레일링'],
        datasets: [{{
          data: [et.take_profit||0, et.stop_loss||0, et.expired||0, et.sell_signal||0, et.strategy_rebalance||0, et.trailing_stop||0],
          backgroundColor: ['#34d399', '#f87171', '#fbbf24', '#60a5fa', '#a78bfa', '#22d3ee'],
          borderWidth: 0,
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', font: {{ family: "'JetBrains Mono'" }} }} }},
        }},
      }},
    }});
  }}
}}

// ════ TAB 3: 백테스트 ════
function renderBacktest() {{
  const bt = D.backtest?.summary || {{}};
  if (!bt.total_trades) {{
    document.getElementById('btStatCards').innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">🔬</div>백테스트 데이터가 없습니다</div>';
    return;
  }}

  document.getElementById('btStatCards').innerHTML = [
    statCard('총 거래', bt.total_trades, `승률 ${{fmt(bt.win_rate,1)}}%`, ''),
    statCard('Profit Factor', fmt(bt.profit_factor), `기대값 ${{pnlSign(bt.expected_value_pct)}}%`, pnlClass(bt.profit_factor-1)),
    statCard('샤프 비율', fmt(bt.sharpe_ratio), `MDD ${{fmt(bt.portfolio_max_drawdown_pct,1)}}%`, pnlClass(bt.sharpe_ratio)),
    statCard('누적 수익', pnlSign(bt.total_pnl_pct)+'%', `평균 ${{pnlSign(bt.avg_pnl_pct)}}%`, pnlClass(bt.total_pnl_pct)),
  ].join('');

  // 신호별 성과
  const sp = D.backtest?.signal_performance || [];
  if (sp.length) {{
    const sorted = sp.slice().sort((a,b) => (b.avg_pnl||0) - (a.avg_pnl||0));
    new Chart(document.getElementById('signalChart'), {{
      type: 'bar',
      data: {{
        labels: sorted.map(s => s.signal),
        datasets: [
          {{
            label: '평균 수익 (%)',
            data: sorted.map(s => s.avg_pnl),
            backgroundColor: sorted.map(s => s.avg_pnl >= 0 ? 'rgba(52,211,153,0.7)' : 'rgba(248,113,113,0.7)'),
            borderRadius: 4,
          }},
        ]
      }},
      options: {{ ...chartOpts(''), indexAxis: 'y' }},
    }});
  }}

  // 점수 구간별
  const sb = D.backtest?.score_buckets || [];
  if (sb.length) {{
    new Chart(document.getElementById('scoreBucketChart'), {{
      type: 'bar',
      data: {{
        labels: sb.map(s => s.range),
        datasets: [
          {{ label: '승률 (%)', data: sb.map(s => s.win_rate), backgroundColor: 'rgba(56,189,248,0.6)', borderRadius: 4 }},
          {{ label: '평균수익 (%)', data: sb.map(s => s.avg_pnl), backgroundColor: 'rgba(167,139,250,0.6)', borderRadius: 4 }},
        ]
      }},
      options: chartOpts(''),
    }});
  }}

  // 백테스트 월별
  const bm = D.backtest?.monthly_returns || [];
  if (bm.length) {{
    new Chart(document.getElementById('btMonthlyChart'), {{
      type: 'bar',
      data: {{
        labels: bm.map(m => m.month),
        datasets: [{{
          label: '월 수익 (%)',
          data: bm.map(m => m.total_pnl_pct),
          backgroundColor: bm.map(m => m.total_pnl_pct >= 0 ? 'rgba(52,211,153,0.7)' : 'rgba(248,113,113,0.7)'),
          borderRadius: 4,
        }}]
      }},
      options: chartOpts(''),
    }});
  }}
}}

// ════ TAB 4: 자기학습 ════
function renderTuning() {{
  const params = D.strategy?.current_params || {{}};
  const paramLabels = {{
    top_n: '일별 선택 종목',
    min_tech_score: '최소 기술 점수',
    atr_stop_mult: '손절 ATR 배수',
    atr_tp_mult: '익절 ATR 배수',
    max_hold_days: '최대 보유일',
    sell_threshold: '매도 임계값',
    max_positions: '최대 포지션',
    max_daily_entries: '일별 진입 제한',
    trailing_atr_mult: '트레일링 ATR 배수',
    trailing_min_pct: '트레일링 최소 %',
  }};

  let phtml = '';
  for (const [k, v] of Object.entries(params)) {{
    phtml += `<div class="param-item"><div class="label">${{paramLabels[k]||k}}</div><div class="value">${{v}}</div></div>`;
  }}
  document.getElementById('paramGrid').innerHTML = phtml || '<div class="empty-state">파라미터 없음</div>';

  // 신호 가중치 바
  const w = D.signal_weights || {{}};
  const wKeys = Object.keys(w).sort((a,b) => w[b] - w[a]);
  let whtml = '';
  for (const k of wKeys) {{
    const v = w[k];
    const pct = Math.min(100, (v / 2.5) * 100);
    const color = v > 1.2 ? 'var(--green)' : v < 0.8 ? 'var(--red)' : 'var(--accent)';
    whtml += `<div class="weight-bar">
      <span class="label">${{weightLabels[k]||k}}</span>
      <div class="bar"><div class="fill" style="width:${{pct}}%;background:${{color}}"></div></div>
      <span class="val" style="color:${{color}}">${{v.toFixed(2)}}</span>
    </div>`;
  }}
  document.getElementById('weightBars').innerHTML = whtml || '<div class="empty-state">가중치 데이터 없음</div>';

  // 튜닝 이력 테이블
  const th = (D.tuning_history || []).slice().reverse();
  if (th.length) {{
    let thtml = '<table><thead><tr><th>날짜</th><th>레짐</th><th>거래</th><th>승률</th><th>PF</th><th>변경</th></tr></thead><tbody>';
    for (const t of th) {{
      const s = t.summary || {{}};
      const pc = Object.keys(t.param_changes||{{}}).length;
      const wc = Object.keys(t.weight_changes||{{}}).length;
      thtml += `<tr>
        <td>${{(t.timestamp||'').slice(0,10)}}</td>
        <td><span class="regime-badge ${{regimeClass(t.regime)}}" style="font-size:11px;padding:2px 8px;">${{regimeIcon(t.regime)}} ${{t.regime}}</span></td>
        <td>${{s.total_trades||'—'}}</td>
        <td>${{fmt(s.win_rate,1)}}%</td>
        <td>${{fmt(s.profit_factor)}}</td>
        <td>파라미터 ${{pc}}건, 가중치 ${{wc}}건</td>
      </tr>`;
    }}
    thtml += '</tbody></table>';
    document.getElementById('tuningHistoryTable').innerHTML = thtml;
  }} else {{
    document.getElementById('tuningHistoryTable').innerHTML = '<div class="empty-state"><div class="icon">🧠</div>튜닝 이력이 없습니다</div>';
  }}
}}

// ════ TAB 5: 전략 설정 ════
function renderStrategy() {{
  const uni = D.universe || {{}};
  const auto = uni.auto || {{}};
  const ai = uni.ai_explainer || {{}};
  const params = D.strategy?.current_params || {{}};
  const regime = D.strategy?.current_regime || 'unknown';
  const conf = D.strategy?.regime_confidence || 0;
  const lastTuned = D.strategy?.last_tuned_at || '';
  const w = D.signal_weights || {{}};

  const row = (label, val, cls) => '<div class="strat-row"><span class="label">' + label + '</span><span class="val ' + (cls||'') + '">' + val + '</span></div>';

  // 1. 유니버스
  let uhtml = '';
  uhtml += row('종목 풀', (auto.pool || 'sp500').toUpperCase(), 'accent');
  uhtml += row('최소 가격', '$' + (auto.min_price || 5));
  uhtml += row('최대 가격', '$' + (auto.max_price || 500));
  uhtml += row('최종 유니버스', (auto.max_final_universe || 150) + '종목');
  uhtml += row('기술적 필터', '상위 ' + (auto.tech_filter_count || 30) + '종목');
  uhtml += row('데이터 기간', (auto.data_days || 90) + '일');
  uhtml += row('뉴스 보너스', auto.use_news_bonus !== false ? '✅ 사용' : '❌ 미사용', auto.use_news_bonus !== false ? 'green' : '');
  document.getElementById('stratUniverse').innerHTML = uhtml;

  // 2. 진입/청산 조건
  let ehtml = '';
  ehtml += row('최소 기술 점수', params.min_tech_score || auto.min_tech_score || 4.0, 'accent');
  ehtml += row('일별 선택 종목', (params.top_n || 5) + '개');
  ehtml += row('손절 ATR 배수', (params.atr_stop_mult || 2.0) + 'x');
  ehtml += row('익절 ATR 배수', (params.atr_tp_mult || 4.0) + 'x');
  ehtml += row('최대 보유일', (params.max_hold_days || 7) + '일');
  ehtml += row('매도 임계값', (params.sell_threshold || 4.0), 'yellow');
  ehtml += row('최대 포지션', (params.max_positions || 10) + '개');
  ehtml += row('일별 진입 제한', (params.max_daily_entries || 3) + '개');
  ehtml += row('스코어링', '기술 70% + 뉴스 30%');
  document.getElementById('stratEntry').innerHTML = ehtml;

  // 3. 시장 레짐
  let rhtml = '';
  const rIcon = regimeIcon(regime);
  rhtml += row('현재 레짐', rIcon + ' ' + regime.toUpperCase(), regime === 'bullish' ? 'green' : regime === 'bearish' ? '' : 'yellow');
  rhtml += row('신뢰도', Math.round(conf * 100) + '%', 'accent');
  rhtml += row('마지막 튜닝', lastTuned ? lastTuned.slice(0, 10) : '미실행');
  rhtml += row('튜닝 이력', (D.tuning_history || []).length + '회');
  const bt = D.backtest?.summary || {{}};
  if (bt.total_trades) {{
    rhtml += row('백테스트 승률', fmt(bt.win_rate, 1) + '%', bt.win_rate >= 50 ? 'green' : '');
    rhtml += row('Profit Factor', fmt(bt.profit_factor), bt.profit_factor >= 1 ? 'green' : '');
  }}
  document.getElementById('stratRegime').innerHTML = rhtml;

  // 4. AI 설정
  let ahtml = '';
  ahtml += row('AI 분석', ai.enabled !== false ? '✅ 활성화' : '❌ 비활성', ai.enabled !== false ? 'green' : '');
  ahtml += row('모델', ai.model_name || 'gemini-2.5-flash', 'accent');
  ahtml += row('감성 분석', 'VADER (NLTK)', 'accent');
  ahtml += row('뉴스 소스', 'Finnhub API');
  ahtml += row('가격 데이터', 'Yahoo Finance');
  document.getElementById('stratAI').innerHTML = ahtml;

  // 5. 신호 가중치 수평 바 차트
  const wKeys = Object.keys(w).sort((a, b) => w[b] - w[a]);
  if (wKeys.length) {{
    new Chart(document.getElementById('stratWeightCanvas'), {{
      type: 'bar',
      data: {{
        labels: wKeys.map(k => weightLabels[k]||k),
        datasets: [{{
          label: '가중치',
          data: wKeys.map(k => w[k]),
          backgroundColor: wKeys.map(k => w[k] > 1.2 ? 'rgba(52,211,153,0.7)' : w[k] < 0.8 ? 'rgba(248,113,113,0.7)' : 'rgba(56,189,248,0.5)'),
          borderRadius: 4,
        }}]
      }},
      options: {{
        ...chartOpts(''),
        indexAxis: 'y',
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: function(ctx) {{ return '가중치: ' + ctx.parsed.x.toFixed(2); }}
            }}
          }}
        }},
        scales: {{
          x: {{ min: 0, max: 2.5, ticks: {{ color: '#64748b' }}, grid: {{ color: 'rgba(42,52,72,0.5)' }} }},
          y: {{ ticks: {{ color: '#94a3b8', font: {{ family: "'JetBrains Mono'", size: 11 }} }}, grid: {{ display: false }} }},
        }},
      }},
    }});
  }} else {{
    document.getElementById('stratWeightsChart').innerHTML = '<div class="empty-state" style="padding:30px;">아직 신호 가중치 데이터가 없습니다<br><small style="color:var(--text2)">자기학습 실행 후 표시됩니다</small></div>';
  }}
}}

// ════ TAB 6: 실적 캘린더 ════
function renderEarnings() {{
  const earnings = D.earnings_calendar || [];
  const today = new Date();
  today.setHours(0,0,0,0);

  const toDate = (s) => {{ const d = new Date(s + 'T00:00:00'); d.setHours(0,0,0,0); return d; }};
  const fmt = (d) => `${{d.getMonth()+1}}/${{d.getDate()}}`;
  const dayNames = ['일', '월', '화', '수', '목', '금', '토'];

  // ── 이번 주 & 다음 주 ──
  const weekEnd = new Date(today); weekEnd.setDate(today.getDate() + 14);
  const upcoming = earnings.filter(e => {{
    const d = toDate(e.date);
    return d >= today && d <= weekEnd;
  }});

  let upHtml = '';
  if (upcoming.length === 0) {{
    upHtml = '<div class="empty-state" style="padding:20px;">다가오는 2주 내 실적 발표가 없습니다</div>';
  }} else {{
    upHtml = '<table class="table"><thead><tr><th>날짜</th><th>요일</th><th>종목</th><th>상태</th></tr></thead><tbody>';
    for (const e of upcoming) {{
      const d = toDate(e.date);
      const isHold = e.is_holding;
      const rowClass = isHold ? 'style="background:rgba(248,113,113,0.1);"' : '';
      const badge = isHold ? '<span style="color:#f87171;font-weight:bold;">⚠️ 보유중</span>' : '<span style="color:var(--text2);">—</span>';
      upHtml += `<tr ${{rowClass}}><td>${{fmt(d)}}</td><td>${{dayNames[d.getDay()]}}</td><td><strong>${{e.ticker}}</strong></td><td>${{badge}}</td></tr>`;
    }}
    upHtml += '</tbody></table>';
  }}
  document.getElementById('earningsUpcoming').innerHTML = upHtml;

  // ── 통계 ──
  const holdEarnings = earnings.filter(e => e.is_holding);
  const thisMonth = earnings.filter(e => {{
    const d = toDate(e.date);
    return d.getMonth() === today.getMonth() && d.getFullYear() === today.getFullYear();
  }});
  const nextMonth = earnings.filter(e => {{
    const d = toDate(e.date);
    const nm = new Date(today.getFullYear(), today.getMonth() + 1, 1);
    return d.getMonth() === nm.getMonth() && d.getFullYear() === nm.getFullYear();
  }});

  let stHtml = '<div style="padding:12px;font-size:14px;line-height:2;">';
  stHtml += `📋 전체 수집: <strong>${{earnings.length}}</strong>건<br>`;
  stHtml += `📅 이번 달: <strong>${{thisMonth.length}}</strong>건<br>`;
  stHtml += `📅 다음 달: <strong>${{nextMonth.length}}</strong>건<br>`;
  stHtml += `⚠️ 보유종목 실적: <strong style="color:#f87171;">${{holdEarnings.length}}</strong>건`;
  if (holdEarnings.length > 0) {{
    stHtml += ' (' + holdEarnings.map(e => e.ticker).join(', ') + ')';
  }}
  stHtml += '</div>';
  document.getElementById('earningsStats').innerHTML = stHtml;

  // ── 월간 캘린더 렌더링 ──
  const year = today.getFullYear();
  const month = today.getMonth();

  // 이번 달 + 다음 달 렌더링
  let calHtml = '';
  for (let m = 0; m < 2; m++) {{
    const cm = new Date(year, month + m, 1);
    const monthName = `${{cm.getFullYear()}}년 ${{cm.getMonth()+1}}월`;
    const firstDay = cm.getDay();
    const daysInMonth = new Date(cm.getFullYear(), cm.getMonth() + 1, 0).getDate();

    // 이 달의 어닝 데이터 맵
    const monthEarnings = {{}};
    earnings.forEach(e => {{
      const d = toDate(e.date);
      if (d.getMonth() === cm.getMonth() && d.getFullYear() === cm.getFullYear()) {{
        const day = d.getDate();
        if (!monthEarnings[day]) monthEarnings[day] = [];
        monthEarnings[day].push(e);
      }}
    }});

    calHtml += `<h3 style="margin:20px 0 10px;color:var(--text1);">${{monthName}}</h3>`;
    calHtml += '<table style="width:100%;border-collapse:collapse;table-layout:fixed;">';
    calHtml += '<thead><tr>';
    for (const dn of dayNames) calHtml += `<th style="padding:8px;text-align:center;color:var(--text2);border-bottom:1px solid var(--border);font-size:12px;">${{dn}}</th>`;
    calHtml += '</tr></thead><tbody><tr>';

    // 첫 주 빈칸
    for (let i = 0; i < firstDay; i++) calHtml += '<td style="padding:4px;border:1px solid var(--border);vertical-align:top;height:80px;"></td>';

    for (let day = 1; day <= daysInMonth; day++) {{
      const dow = (firstDay + day - 1) % 7;
      const isToday = (cm.getMonth() === today.getMonth() && cm.getFullYear() === today.getFullYear() && day === today.getDate());
      const todayBorder = isToday ? 'border:2px solid var(--accent);' : 'border:1px solid var(--border);';
      const todayBg = isToday ? 'background:rgba(56,189,248,0.05);' : '';

      calHtml += `<td style="padding:4px;vertical-align:top;height:80px;${{todayBorder}}${{todayBg}}">`;
      calHtml += `<div style="font-size:11px;color:${{isToday ? 'var(--accent)' : 'var(--text2)'}};margin-bottom:2px;">${{day}}</div>`;

      if (monthEarnings[day]) {{
        for (const e of monthEarnings[day]) {{
          const bg = e.is_holding ? 'rgba(248,113,113,0.2)' : 'rgba(56,189,248,0.1)';
          const color = e.is_holding ? '#f87171' : 'var(--text1)';
          const icon = e.is_holding ? '⚠️' : '📊';
          calHtml += `<div style="font-size:10px;padding:1px 3px;margin:1px 0;border-radius:3px;background:${{bg}};color:${{color}};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${{e.ticker}}">${{icon}} ${{e.ticker}}</div>`;
        }}
      }}

      calHtml += '</td>';
      if (dow === 6 && day < daysInMonth) calHtml += '</tr><tr>';
    }}

    // 마지막 주 빈칸
    const lastDow = (firstDay + daysInMonth - 1) % 7;
    for (let i = lastDow + 1; i < 7; i++) calHtml += '<td style="padding:4px;border:1px solid var(--border);vertical-align:top;height:80px;"></td>';
    calHtml += '</tr></tbody></table>';
  }}

  document.getElementById('earningsCalendar').innerHTML = calHtml;
}}

// ════ TAB 7: 주간 리포트 (GitHub raw fetch) ════
async function renderReports() {{
  const container = document.getElementById('weeklyReportsList');
  container.innerHTML = '<div class="empty-state" style="padding:40px;">📡 주간 리포트 로딩 중...</div>';

  // 1) index.json fetch
  let index = [];
  try {{
    const res = await fetch(DATA_URLS.weeklyIndex + '?t=' + Date.now());
    if (res.ok) index = await res.json();
  }} catch (e) {{
    container.innerHTML = '<div class="empty-state" style="padding:40px;">아직 주간 리포트가 없습니다.<br><small style="color:var(--text2);">매주 일요일 자동 생성됩니다.</small></div>';
    return;
  }}

  if (!index.length) {{
    container.innerHTML = '<div class="empty-state" style="padding:40px;">아직 주간 리포트가 없습니다.<br><small style="color:var(--text2);">매주 일요일 자동 생성됩니다.</small></div>';
    return;
  }}

  // 2) 각 리포트 fetch (병렬)
  const baseUrl = REPO_RAW + '/data/weekly_reports/';
  const fetches = index.map(async (entry) => {{
    try {{
      const res = await fetch(baseUrl + entry.file + '?t=' + Date.now());
      if (res.ok) return await res.json();
    }} catch (e) {{}}
    return null;
  }});
  const reports = (await Promise.all(fetches)).filter(Boolean);

  if (!reports.length) {{
    container.innerHTML = '<div class="empty-state" style="padding:40px;">리포트 파일을 불러올 수 없습니다.</div>';
    return;
  }}

  // 3) 렌더링
  let html = '';
  for (const r of reports) {{
    const ts = r.trade_summary || {{}};
    const regime = r.regime || {{}};
    const pnl = ts.total_pnl_pct || 0;
    const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text2)';
    const regimeEmoji = {{'bullish':'🐂','bearish':'🐻','sideways':'📊','conservative':'🛡️','volatile':'⚡'}}[regime.regime] || '❓';

    html += `<div class="card" style="margin-bottom:12px;">`;
    html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">`;
    html += `<div style="font-size:15px;font-weight:600;color:var(--text1);">📋 ${{ts.period || r.week || ''}}</div>`;
    html += `<div style="font-size:20px;font-weight:700;color:${{pnlColor}};">${{pnl >= 0 ? '+' : ''}}${{pnl.toFixed(2)}}%</div>`;
    html += `</div>`;

    html += `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px;">`;
    html += `<div style="text-align:center;padding:8px;background:var(--bg);border-radius:6px;">
      <div style="font-size:18px;font-weight:bold;color:var(--accent);">${{ts.new_entries || 0}}</div>
      <div style="font-size:10px;color:var(--text2);">신규 진입</div></div>`;
    html += `<div style="text-align:center;padding:8px;background:var(--bg);border-radius:6px;">
      <div style="font-size:18px;font-weight:bold;">${{ts.closed || 0}}</div>
      <div style="font-size:10px;color:var(--text2);">청산</div></div>`;
    html += `<div style="text-align:center;padding:8px;background:var(--bg);border-radius:6px;">
      <div style="font-size:18px;font-weight:bold;color:var(--green);">${{ts.win_rate || 0}}%</div>
      <div style="font-size:10px;color:var(--text2);">승률</div></div>`;
    html += `<div style="text-align:center;padding:8px;background:var(--bg);border-radius:6px;">
      <div style="font-size:18px;font-weight:bold;">${{regimeEmoji}} ${{regime.regime || '?'}}</div>
      <div style="font-size:10px;color:var(--text2);">레짐</div></div>`;
    html += `</div>`;

    const details = ts.closed_details || [];
    if (details.length > 0) {{
      html += `<div style="font-size:12px;color:var(--text2);margin-bottom:4px;">청산 내역:</div>`;
      html += `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;">`;
      for (const d of details.slice(0, 10)) {{
        const dc = (d.pnl_pct||0) > 0 ? 'var(--green)' : 'var(--red)';
        html += `<span style="font-size:11px;padding:2px 6px;border-radius:4px;background:var(--bg);color:${{dc}};">
          ${{d.reason}} ${{d.ticker}} ${{(d.pnl_pct||0) >= 0 ? '+' : ''}}${{(d.pnl_pct||0).toFixed(1)}}%</span>`;
      }}
      html += `</div>`;
    }}

    const holdings = r.holdings || [];
    if (holdings.length > 0) {{
      html += `<div style="font-size:12px;color:var(--text2);margin-bottom:4px;">보유 포지션 (${{holdings.length}}개):</div>`;
      html += `<div style="display:flex;flex-wrap:wrap;gap:4px;">`;
      for (const h of holdings) {{
        const hp = h.unrealized_pnl;
        const hc = hp != null ? (hp > 0 ? 'var(--green)' : 'var(--red)') : 'var(--text2)';
        const hv = hp != null ? `${{hp >= 0 ? '+' : ''}}${{hp.toFixed(1)}}%` : 'N/A';
        html += `<span style="font-size:11px;padding:2px 6px;border-radius:4px;background:var(--bg);color:${{hc}};">${{h.ticker}} ${{hv}}</span>`;
      }}
      html += `</div>`;
    }}

    html += `</div>`;
  }}

  container.innerHTML = html;
}}

// ════ TAB 8: 경제 뉴스 (RSS fetch) ════
const NEWS_FEEDS = {{
  yahoo: {{
    name: 'Yahoo Finance',
    url: 'https://finance.yahoo.com/news/rssindex',
    color: '#7c3aed',
  }},
  cnbc: {{
    name: 'CNBC',
    url: 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147',
    color: '#0ea5e9',
  }},
  marketwatch: {{
    name: 'MarketWatch',
    url: 'https://feeds.content.dowjones.io/public/rss/mw_topstories',
    color: '#f59e0b',
  }},
}};

let _allNewsItems = [];
let _newsFilter = 'all';
let _newsLoaded = false;

async function fetchRSS(key, feed) {{
  const proxy = 'https://api.allorigins.win/raw?url=' + encodeURIComponent(feed.url);
  try {{
    const res = await fetch(proxy);
    if (!res.ok) return [];
    const text = await res.text();
    const parser = new DOMParser();
    const xml = parser.parseFromString(text, 'text/xml');
    const items = xml.querySelectorAll('item');
    const results = [];
    items.forEach((item, i) => {{
      if (i >= 15) return;
      const title = item.querySelector('title')?.textContent || '';
      const link = item.querySelector('link')?.textContent || '';
      const pubDate = item.querySelector('pubDate')?.textContent || '';
      const desc = item.querySelector('description')?.textContent || '';
      // 태그 제거
      const cleanDesc = desc.replace(/<[^>]*>/g, '').substring(0, 200);
      results.push({{
        source: key,
        sourceName: feed.name,
        sourceColor: feed.color,
        title,
        link,
        pubDate,
        date: pubDate ? new Date(pubDate) : new Date(0),
        desc: cleanDesc,
      }});
    }});
    return results;
  }} catch (e) {{
    console.warn(`RSS fetch failed (${{key}}):`, e);
    return [];
  }}
}}

async function translateText(text) {{
  if (!text || text.length < 3) return text;
  try {{
    const url = 'https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ko&dt=t&q=' + encodeURIComponent(text);
    const res = await fetch(url);
    if (!res.ok) return text;
    const data = await res.json();
    return data[0].map(s => s[0]).join('') || text;
  }} catch (e) {{
    return text;
  }}
}}

async function translateBatch(items) {{
  // 제목 + 설명을 병렬 번역 (최대 30건, 너무 많으면 rate limit)
  const tasks = [];
  for (const n of items.slice(0, 30)) {{
    tasks.push(
      translateText(n.title).then(t => {{ n.title_ko = t; }}),
      n.desc ? translateText(n.desc).then(d => {{ n.desc_ko = d; }}) : Promise.resolve()
    );
  }}
  // 5개씩 배치 실행 (rate limit 방지)
  for (let i = 0; i < tasks.length; i += 10) {{
    await Promise.all(tasks.slice(i, i + 10));
    if (i + 10 < tasks.length) await new Promise(r => setTimeout(r, 300));
  }}
}}

async function loadAllNews() {{
  const container = document.getElementById('newsList');
  const refreshBtn = document.getElementById('newsRefreshBtn');
  refreshBtn.disabled = true;
  refreshBtn.textContent = '⏳ 뉴스 수집 중...';
  container.innerHTML = '<div class="empty-state" style="padding:40px;">📡 뉴스 로딩 중...</div>';

  const fetches = Object.entries(NEWS_FEEDS).map(([k, f]) => fetchRSS(k, f));
  const results = await Promise.all(fetches);
  _allNewsItems = results.flat().sort((a, b) => b.date - a.date);

  // 1차: 영어로 먼저 표시
  _newsLoaded = true;
  renderNews();

  const now = new Date();
  document.getElementById('newsLastUpdated').textContent =
    `마지막 업데이트: ${{now.toLocaleString('ko-KR')}} · ${{_allNewsItems.length}}건 · 🔄 번역 중...`;
  refreshBtn.textContent = '🔄 번역 중...';

  // 2차: 한글 번역 후 갱신
  try {{
    await translateBatch(_allNewsItems);
    renderNews();
    document.getElementById('newsLastUpdated').textContent =
      `마지막 업데이트: ${{now.toLocaleString('ko-KR')}} · ${{_allNewsItems.length}}건 · ✅ 번역 완료`;
  }} catch (e) {{
    document.getElementById('newsLastUpdated').textContent =
      `마지막 업데이트: ${{now.toLocaleString('ko-KR')}} · ${{_allNewsItems.length}}건 · ⚠️ 일부 번역 실패`;
  }}

  refreshBtn.disabled = false;
  refreshBtn.textContent = '🔄 새로고침';
}}

function filterNews(src) {{
  _newsFilter = src;
  document.querySelectorAll('.news-src-btn').forEach(btn => {{
    btn.classList.toggle('active',
      (src === 'all' && btn.textContent.includes('전체')) ||
      btn.textContent.toLowerCase().includes(src));
  }});
  renderNews();
}}

function renderNews() {{
  const container = document.getElementById('newsList');
  let items = _allNewsItems;
  if (_newsFilter !== 'all') {{
    items = items.filter(n => n.source === _newsFilter);
  }}

  if (!items.length) {{
    container.innerHTML = '<div class="empty-state" style="padding:40px;">뉴스를 불러올 수 없습니다.</div>';
    return;
  }}

  let html = '<div class="card" style="padding:0;overflow:hidden;">';
  for (const n of items.slice(0, 30)) {{
    const ago = _timeAgo(n.date);
    const title = n.title_ko || n.title;
    const desc = n.desc_ko || n.desc;
    const hasKo = n.title_ko ? '' : ' style="opacity:0.7;"';
    html += `<div class="news-item">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
        <span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${{n.sourceColor}};color:white;font-weight:600;">${{n.sourceName}}</span>
        <span style="font-size:11px;color:var(--text2);">${{ago}}</span>
      </div>
      <a href="${{n.link}}" target="_blank" rel="noopener"${{hasKo}} style="color:var(--text1);text-decoration:none;font-size:14px;font-weight:500;line-height:1.4;display:block;">
        ${{title}}
      </a>
      ${{desc ? `<div style="font-size:12px;color:var(--text2);margin-top:4px;line-height:1.4;">${{desc}}</div>` : ''}}
      ${{n.title_ko ? `<div style="font-size:11px;color:var(--text2);margin-top:2px;opacity:0.5;">${{n.title}}</div>` : ''}}
    </div>`;
  }}
  html += '</div>';
  container.innerHTML = html;
}}

function _timeAgo(date) {{
  if (!date || date.getTime() === 0) return '';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return '방금 전';
  if (diff < 3600) return Math.floor(diff / 60) + '분 전';
  if (diff < 86400) return Math.floor(diff / 3600) + '시간 전';
  return Math.floor(diff / 86400) + '일 전';
}}

// ── Chart.js 공통 옵션 ──
function chartOpts(yLabel) {{
  return {{
    responsive: true,
    maintainAspectRatio: true,
    scales: {{
      x: {{ ticks: {{ color: '#64748b', font: {{ family: "'JetBrains Mono'", size: 10 }} }}, grid: {{ color: 'rgba(42,52,72,0.5)' }} }},
      y: {{ ticks: {{ color: '#64748b', font: {{ family: "'JetBrains Mono'", size: 10 }} }}, grid: {{ color: 'rgba(42,52,72,0.5)' }},
           title: yLabel ? {{ display:true, text:yLabel, color:'#94a3b8' }} : {{}} }},
    }},
    plugins: {{
      legend: {{ labels: {{ color: '#94a3b8', font: {{ family: "'JetBrains Mono'" }} }} }},
    }},
  }};
}}

// 실시간 데이터 fetch 후 초기화
fetchLiveData().then(() => init()).catch(() => init());
</script>
</body>
</html>"""


def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    print("📊 대시보드 데이터 수집...")
    data = collect_dashboard_data()

    print("🎨 HTML 생성...")
    html = generate_html(data)

    output = DOCS_DIR / "index.html"
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = output.stat().st_size / 1024
    print(f"✅ 대시보드 생성 완료: {output} ({size_kb:.1f} KB)")
    print(f"   포지션: {len(data['positions'])}개")
    print(f"   이력: {len(data['history'])}건")
    print(f"   시장지표: {len(data.get('market_indices', {}))}개")
    print(f"   백테스트: {'있음' if data['backtest']['summary'] else '없음'}")
    print(f"   자기학습: {'있음' if data['strategy']['current_params'] else '없음'}")


if __name__ == "__main__":
    main()