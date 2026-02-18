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
    exit_types = {"take_profit": 0, "stop_loss": 0, "expired": 0}
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

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "positions": positions,
        "stats": stats,
        "history": history[-100:],  # 최근 100건
        "daily_cumulative_pnl": daily_pnl,
        "monthly_performance": monthly_perf,
        "exit_types": exit_types,
        "market_indices": market_indices,
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
  padding: 16px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
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
  margin-bottom: 20px;
  background: var(--surface);
  border-radius: 12px;
  padding: 4px;
  border: 1px solid var(--border);
  overflow-x: auto;
}}
.tab {{
  padding: 10px 20px;
  border-radius: 8px;
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
  .topbar {{ flex-wrap: wrap; gap: 8px; }}
  .tabs {{ flex-wrap: nowrap; }}
}}
</style>
</head>
<body>

<div class="topbar">
  <div>
    <h1>📈 Stock Bot Dashboard</h1>
    <div class="meta" id="lastUpdate"></div>
  </div>
  <div style="display:flex;align-items:center;gap:12px;">
    <span class="regime-badge" id="regimeBadge"></span>
  </div>
</div>

<div class="container">
  <div class="tabs">
    <button class="tab active" onclick="showTab('market')">🌍 시장 현황</button>
    <button class="tab" onclick="showTab('positions')">💼 포지션</button>
    <button class="tab" onclick="showTab('performance')">📊 성과</button>
    <button class="tab" onclick="showTab('backtest')">🔬 백테스트</button>
    <button class="tab" onclick="showTab('tuning')">🧠 자기학습</button>
    <button class="tab" onclick="showTab('strategy')">⚙️ 전략 설정</button>
  </div>

  <!-- ════ TAB 0: 시장 현황 ════ -->
  <div id="tab-market" class="tab-content active">
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
    const et = {{take_profit:0, stop_loss:0, expired:0}};
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

// ── 탭 전환 ──
function showTab(id) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  event.target.classList.add('active');
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
    statCard('승률', fmt(s.win_rate||0,1)+'%', `만료 ${{s.expired||0}}건`, pnlClass(s.win_rate-50)),
    statCard('누적 수익', pnlSign(s.total_pnl_pct||0)+'%', `평균 ${{pnlSign(s.avg_pnl_pct||0)}}%`, pnlClass(s.total_pnl_pct)),
  ].join('');
  document.getElementById('statCards').innerHTML = html;
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
  let html = '<table><thead><tr><th>종목</th><th>진입가</th><th>현재가</th><th>P&L</th><th>손절</th><th>익절</th><th>점수</th><th>진입일</th></tr></thead><tbody>';
  for (const p of open) {{
    const last = p.price_history?.length ? p.price_history[p.price_history.length-1].close : p.entry_price;
    const pnl = ((last - p.entry_price) / p.entry_price * 100);
    html += `<tr>
      <td><strong>${{p.ticker}}</strong></td>
      <td>${{fmt(p.entry_price)}}</td>
      <td>${{fmt(last)}}</td>
      <td class="${{pnlClass(pnl)}}"><strong>${{pnlSign(pnl)}}%</strong></td>
      <td class="negative">${{fmt(p.stop_loss)}}</td>
      <td class="positive">${{fmt(p.take_profit)}}</td>
      <td>${{fmt(p.tech_score,1)}}</td>
      <td>${{p.entry_date}}</td>
    </tr>`;
  }}
  html += '</tbody></table>';
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
      <td class="status-${{reason}}">${{{{take_profit:'✅ 익절',stop_loss:'🛑 손절',expired:'⏰ 만료'}}[reason]||reason}}</td>
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
  const total = (et.take_profit||0) + (et.stop_loss||0) + (et.expired||0);
  if (total > 0) {{
    new Chart(document.getElementById('exitTypeChart'), {{
      type: 'doughnut',
      data: {{
        labels: ['익절', '손절', '만료'],
        datasets: [{{
          data: [et.take_profit||0, et.stop_loss||0, et.expired||0],
          backgroundColor: ['#34d399', '#f87171', '#fbbf24'],
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
      <span class="label">${{k}}</span>
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
  uhtml += row('종목 풀', (auto.pool || 'nasdaq100').toUpperCase(), 'accent');
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
        labels: wKeys,
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