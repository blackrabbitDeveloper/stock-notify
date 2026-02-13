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


def load_json(path, default=None):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


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

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "positions": positions,
        "stats": stats,
        "history": history[-100:],  # 최근 100건
        "daily_cumulative_pnl": daily_pnl,
        "monthly_performance": monthly_perf,
        "exit_types": exit_types,
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
    <button class="tab active" onclick="showTab('positions')">💼 포지션</button>
    <button class="tab" onclick="showTab('performance')">📊 성과</button>
    <button class="tab" onclick="showTab('backtest')">🔬 백테스트</button>
    <button class="tab" onclick="showTab('tuning')">🧠 자기학습</button>
  </div>

  <!-- ════ TAB 1: 포지션 ════ -->
  <div id="tab-positions" class="tab-content active">
    <div class="grid grid-4" id="statCards"></div>
    <div class="section-title">📌 오픈 포지션</div>
    <div class="table-wrap" id="openPositionsTable"></div>
    <div class="section-title">📜 최근 청산 이력</div>
    <div class="table-wrap" id="historyTable"></div>
  </div>

  <!-- ════ TAB 2: 성과 ════ -->
  <div id="tab-performance" class="tab-content">
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
    <div class="grid grid-4" id="btStatCards"></div>
    <div class="grid grid-2" style="margin-top:16px;">
      <div class="chart-box"><h3>📡 신호별 성과</h3><canvas id="signalChart"></canvas></div>
      <div class="chart-box"><h3>🎯 점수 구간별 성과</h3><canvas id="scoreBucketChart"></canvas></div>
    </div>
    <div class="chart-box" style="margin-top:16px;"><h3>📅 백테스트 월별 수익</h3><canvas id="btMonthlyChart"></canvas></div>
  </div>

  <!-- ════ TAB 4: 자기학습 ════ -->
  <div id="tab-tuning" class="tab-content">
    <div class="section-title">⚙️ 현재 전략 파라미터</div>
    <div class="param-grid" id="paramGrid"></div>
    <div class="section-title" style="margin-top:24px;">📡 신호 가중치</div>
    <div id="weightBars"></div>
    <div class="section-title" style="margin-top:24px;">📜 튜닝 이력</div>
    <div class="table-wrap" id="tuningHistoryTable"></div>
  </div>
</div>

<script>
const D = {data_json};

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

  renderStatCards();
  renderOpenPositions();
  renderHistory();
  renderPerformance();
  renderBacktest();
  renderTuning();
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

init();
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
    print(f"   백테스트: {'있음' if data['backtest']['summary'] else '없음'}")
    print(f"   자기학습: {'있음' if data['strategy']['current_params'] else '없음'}")


if __name__ == "__main__":
    main()
