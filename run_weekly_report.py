#!/usr/bin/env python3
"""
주간 리포트 생성기.

이번 주 거래 요약, 보유 포지션 현황, 시장 레짐 + 전략 파라미터를
Discord로 발송하고 data/weekly_reports/에 JSON 저장.

사용법:
  python run_weekly_report.py               # 기본 실행
  python run_weekly_report.py --discord     # Discord 발송 포함
  python run_weekly_report.py --weeks 2     # 최근 2주 범위
"""
import argparse
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

POSITIONS_FILE = Path("data/positions.json")
HISTORY_FILE = Path("data/history.json")
STRATEGY_FILE = Path("config/strategy_state.json")
REPORTS_DIR = Path("data/weekly_reports")


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def generate_report(weeks: int = 1) -> dict:
    """주간 리포트 데이터 생성."""
    now = datetime.now(timezone.utc)
    today = now.date()
    # 이번 주 월요일 ~ 일요일
    week_start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    week_end = today

    # ── 1. 이번 주 거래 요약 ──
    history = load_json(HISTORY_FILE, [])
    pos_data = load_json(POSITIONS_FILE, {"positions": [], "stats": {}})
    open_positions = [p for p in pos_data.get("positions", []) if p.get("status") == "open"]
    stats = pos_data.get("stats", {})

    # 이번 주 청산 건
    week_closed = []
    for h in history:
        exit_date = h.get("exit_date", "")
        if exit_date:
            try:
                d = datetime.fromisoformat(exit_date).date() if "T" in exit_date else datetime.strptime(exit_date, "%Y-%m-%d").date()
                if week_start <= d <= week_end:
                    week_closed.append(h)
            except Exception:
                continue

    # 이번 주 신규 진입
    week_entries = []
    for p in open_positions:
        entry_date = p.get("entry_date", "")
        if entry_date:
            try:
                d = datetime.fromisoformat(entry_date).date() if "T" in entry_date else datetime.strptime(entry_date, "%Y-%m-%d").date()
                if week_start <= d <= week_end:
                    week_entries.append(p)
            except Exception:
                continue

    # 주간 P&L
    week_pnls = [h.get("pnl_pct", 0) for h in week_closed if h.get("pnl_pct") is not None]
    week_wins = [p for p in week_pnls if p > 0]
    week_losses = [p for p in week_pnls if p <= 0]
    week_total_pnl = sum(week_pnls) if week_pnls else 0

    trade_summary = {
        "period": f"{week_start.isoformat()} ~ {week_end.isoformat()}",
        "new_entries": len(week_entries),
        "closed": len(week_closed),
        "wins": len(week_wins),
        "losses": len(week_losses),
        "win_rate": round(len(week_wins) / len(week_closed) * 100, 1) if week_closed else 0,
        "total_pnl_pct": round(week_total_pnl, 2),
        "avg_pnl_pct": round(week_total_pnl / len(week_closed), 2) if week_closed else 0,
        "best_trade": None,
        "worst_trade": None,
        "closed_details": [],
    }

    if week_closed:
        best = max(week_closed, key=lambda x: x.get("pnl_pct", -999))
        worst = min(week_closed, key=lambda x: x.get("pnl_pct", 999))
        trade_summary["best_trade"] = {"ticker": best.get("ticker"), "pnl_pct": best.get("pnl_pct")}
        trade_summary["worst_trade"] = {"ticker": worst.get("ticker"), "pnl_pct": worst.get("pnl_pct")}

        reason_labels = {
            "take_profit": "✅ 익절", "stop_loss": "🛑 손절", "expired": "⏰ 만료",
            "sell_signal": "📉 매도", "trailing_stop": "📈 트레일링",
            "strategy_rebalance": "🔄 재검증",
        }
        for h in sorted(week_closed, key=lambda x: x.get("exit_date", "")):
            trade_summary["closed_details"].append({
                "ticker": h.get("ticker"),
                "pnl_pct": h.get("pnl_pct"),
                "reason": reason_labels.get(h.get("close_reason"), h.get("close_reason", "?")),
                "hold_days": h.get("hold_days", 0),
                "exit_date": h.get("exit_date"),
            })

    # ── 2. 보유 포지션 현황 ──
    holdings = []
    for p in open_positions:
        unrealized = p.get("unrealized_pnl")
        if unrealized is None and p.get("current_price") and p.get("entry_price"):
            unrealized = round((p["current_price"] - p["entry_price"]) / p["entry_price"] * 100, 2)
        holdings.append({
            "ticker": p.get("ticker"),
            "entry_price": p.get("entry_price"),
            "current_price": p.get("current_price"),
            "unrealized_pnl": unrealized,
            "entry_date": p.get("entry_date"),
            "trailing_active": p.get("trailing_active", False),
            "partial_closed": p.get("partial_closed", False),
        })

    # ── 3. 시장 레짐 + 전략 파라미터 ──
    strategy = load_json(STRATEGY_FILE, {})
    current_params = strategy.get("current_params", {})
    regime_info = {
        "regime": strategy.get("current_regime", "unknown"),
        "confidence": strategy.get("regime_confidence", 0),
        "last_tuned": strategy.get("last_tuned_at", ""),
    }

    # 리포트 조립
    report = {
        "generated_at": now.isoformat(),
        "week": trade_summary["period"],
        "trade_summary": trade_summary,
        "holdings": holdings,
        "holdings_count": len(holdings),
        "cumulative_stats": stats,
        "regime": regime_info,
        "strategy_params": current_params,
    }

    return report


def save_report(report: dict) -> Path:
    """JSON으로 저장 + index.json 갱신."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filepath = REPORTS_DIR / f"weekly_{date_str}.json"

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # index.json 갱신 (최근 12건)
    index_path = REPORTS_DIR / "index.json"
    existing = []
    if index_path.exists():
        try:
            with open(index_path, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    # 중복 제거 후 추가
    filenames = {e["file"] for e in existing}
    if filepath.name not in filenames:
        existing.insert(0, {
            "file": filepath.name,
            "week": report.get("trade_summary", {}).get("period", ""),
            "pnl_pct": report.get("trade_summary", {}).get("total_pnl_pct", 0),
            "generated_at": report.get("generated_at", ""),
        })

    # 최근 12건만 유지
    existing = existing[:12]
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 리포트 저장: {filepath}")
    print(f"[INFO] 인덱스 갱신: {index_path} ({len(existing)}건)")
    return filepath


def send_to_discord(report: dict):
    """Discord 웹훅으로 발송."""
    import requests

    url = (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip().strip('"').strip("'")
    if not url:
        print("[WARN] DISCORD_WEBHOOK_URL 미설정 — Discord 발송 스킵")
        return

    ts = report["trade_summary"]
    regime = report["regime"]
    params = report["strategy_params"]

    # 색상 결정
    if ts["total_pnl_pct"] > 0:
        color = 0x34d399  # 녹색
    elif ts["total_pnl_pct"] < 0:
        color = 0xf87171  # 빨간
    else:
        color = 0x94a3b8  # 회색

    # 레짐 이모지
    regime_emoji = {
        "bullish": "🐂", "bearish": "🐻", "sideways": "📊",
        "conservative": "🛡️", "volatile": "⚡",
    }.get(regime["regime"], "❓")

    # ── 거래 요약 텍스트 ──
    trade_text = (
        f"신규 진입: **{ts['new_entries']}건**\n"
        f"청산: **{ts['closed']}건** (승 {ts['wins']} / 패 {ts['losses']})\n"
        f"승률: **{ts['win_rate']}%**\n"
        f"주간 P&L: **{ts['total_pnl_pct']:+.2f}%**"
    )
    if ts["best_trade"]:
        trade_text += f"\n🏆 최고: {ts['best_trade']['ticker']} ({ts['best_trade']['pnl_pct']:+.1f}%)"
    if ts["worst_trade"]:
        trade_text += f"\n💀 최저: {ts['worst_trade']['ticker']} ({ts['worst_trade']['pnl_pct']:+.1f}%)"

    # ── 청산 내역 텍스트 ──
    closed_text = ""
    for d in ts.get("closed_details", [])[:8]:
        closed_text += f"{d['reason']} **{d['ticker']}** {d['pnl_pct']:+.1f}% ({d['hold_days']}일)\n"
    closed_text = closed_text or "이번 주 청산 없음"

    # ── 보유 포지션 텍스트 ──
    holdings_text = ""
    for h in sorted(report["holdings"], key=lambda x: x.get("unrealized_pnl") or 0, reverse=True):
        pnl = h.get("unrealized_pnl")
        pnl_str = f"{pnl:+.1f}%" if pnl is not None else "N/A"
        trail = " 🔄" if h.get("trailing_active") else ""
        partial = " ½" if h.get("partial_closed") else ""
        holdings_text += f"**{h['ticker']}** {pnl_str}{trail}{partial}\n"
    holdings_text = holdings_text or "보유 포지션 없음"

    # ── 전략 텍스트 ──
    param_labels = {
        "top_n": "선택 종목", "min_tech_score": "최소 점수",
        "atr_stop_mult": "SL 배수", "atr_tp_mult": "TP 배수",
        "max_hold_days": "보유일", "sell_threshold": "매도 임계",
        "max_positions": "최대 포지션", "trailing_atr_mult": "트레일링 ATR",
    }
    strategy_text = (
        f"{regime_emoji} 레짐: **{regime['regime']}** "
        f"(신뢰도 {regime['confidence']:.0%})\n"
    )
    for k, label in param_labels.items():
        if k in params:
            strategy_text += f"{label}: **{params[k]}** · "
    strategy_text = strategy_text.rstrip(" · ")

    # ── Embed 조립 ──
    embed = {
        "title": f"📋 주간 리포트 — {ts['period']}",
        "color": color,
        "fields": [
            {"name": "📊 거래 요약", "value": trade_text, "inline": False},
            {"name": "📝 청산 내역", "value": closed_text[:1000], "inline": False},
            {"name": f"💼 보유 포지션 ({report['holdings_count']}개)", "value": holdings_text[:1000], "inline": False},
            {"name": "⚙️ 전략 상태", "value": strategy_text[:1000], "inline": False},
        ],
        "footer": {
            "text": f"누적 | 거래 {report['cumulative_stats'].get('total_trades', 0)}회 · "
                    f"승률 {report['cumulative_stats'].get('win_rate', 0)}% · "
                    f"P&L {report['cumulative_stats'].get('total_pnl_pct', 0):+.1f}%"
        },
        "timestamp": report["generated_at"],
    }

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            print("[INFO] Discord 발송 완료")
        else:
            print(f"[WARN] Discord 응답: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[ERROR] Discord 발송 실패: {e}")


def print_report(report: dict):
    """콘솔 출력."""
    ts = report["trade_summary"]
    regime = report["regime"]

    print("\n" + "=" * 60)
    print(f"📋 주간 리포트 — {ts['period']}")
    print("=" * 60)

    print(f"\n📊 거래 요약:")
    print(f"  신규 진입: {ts['new_entries']}건")
    print(f"  청산: {ts['closed']}건 (승 {ts['wins']} / 패 {ts['losses']})")
    print(f"  승률: {ts['win_rate']}%")
    print(f"  주간 P&L: {ts['total_pnl_pct']:+.2f}%")
    if ts["best_trade"]:
        print(f"  🏆 최고: {ts['best_trade']['ticker']} ({ts['best_trade']['pnl_pct']:+.1f}%)")
    if ts["worst_trade"]:
        print(f"  💀 최저: {ts['worst_trade']['ticker']} ({ts['worst_trade']['pnl_pct']:+.1f}%)")

    if ts["closed_details"]:
        print(f"\n📝 청산 내역:")
        for d in ts["closed_details"]:
            print(f"  {d['reason']} {d['ticker']} {d['pnl_pct']:+.1f}% ({d['hold_days']}일)")

    print(f"\n💼 보유 포지션: {report['holdings_count']}개")
    for h in report["holdings"]:
        pnl = h.get("unrealized_pnl")
        pnl_str = f"{pnl:+.1f}%" if pnl is not None else "N/A"
        print(f"  {h['ticker']:6s} {pnl_str}")

    print(f"\n⚙️ 시장 레짐: {regime['regime']} (신뢰도 {regime['confidence']:.0%})")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="주간 리포트 생성")
    parser.add_argument("--discord", action="store_true", help="Discord 발송")
    parser.add_argument("--weeks", type=int, default=1, help="범위 (주, 기본 1)")
    args = parser.parse_args()

    report = generate_report(weeks=args.weeks)
    print_report(report)
    save_report(report)

    if args.discord:
        send_to_discord(report)

    print("\n✅ 주간 리포트 완료")


if __name__ == "__main__":
    main()
