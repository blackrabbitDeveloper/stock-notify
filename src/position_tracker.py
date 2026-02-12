"""
포지션 트래커
- 추천 종목을 JSON으로 저장/불러오기
- ATR 기반 손절/익절 가격 계산
- 매일 장 마감 후 포지션 상태 업데이트
- 최종 수익률 집계

파일 구조:
  data/positions.json  → 열린 포지션 + 누적 통계 (가볍게 유지)
  data/history.json    → 청산된 모든 이력 (영구 보관, append 방식)
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── 파일 경로 ──────────────────────────────────────────
POSITIONS_FILE = Path("data/positions.json")
HISTORY_FILE   = Path("data/history.json")

# ── 상수 ──────────────────────────────────────────────
ATR_STOP_MULT = 2.0   # 손절: 진입가 - 2x ATR
ATR_TP_MULT   = 4.0   # 익절: 진입가 + 4x ATR
MAX_HOLD_DAYS = 7     # 최대 보유 기간 (캘린더 기준)

# 포지션 상태
STATUS_OPEN    = "open"
STATUS_TP      = "take_profit"
STATUS_SL      = "stop_loss"
STATUS_EXPIRED = "expired"


# ══════════════════════════════════════════════════════
#  positions.json  (열린 포지션 + 누적 통계)
# ══════════════════════════════════════════════════════

def _empty_stats() -> Dict:
    return {
        "total_trades": 0,
        "wins":         0,
        "losses":       0,
        "expired":      0,
        "total_pnl_pct": 0.0,
        "win_rate":      0.0,
        "avg_pnl_pct":   0.0,
        "best_trade":    None,
        "worst_trade":   None,
        "last_updated":  None,
    }

def load_positions() -> Dict:
    """positions.json 불러오기."""
    if not POSITIONS_FILE.exists():
        return {"positions": [], "stats": _empty_stats()}
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("positions", [])
        data.setdefault("stats", _empty_stats())
        # 구버전 호환: closed 키가 있으면 history로 이전 후 제거
        if "closed" in data and data["closed"]:
            print(f"[INFO] migrating {len(data['closed'])} closed records → history.json")
            _append_history(data.pop("closed"))
        else:
            data.pop("closed", None)
        return data
    except Exception as e:
        print(f"[ERROR] load_positions: {e}")
        return {"positions": [], "stats": _empty_stats()}

def save_positions(data: Dict) -> None:
    """positions.json 저장 (closed 키 없이)."""
    data.pop("closed", None)   # 혹시 남아 있으면 제거
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"[INFO] positions saved → {POSITIONS_FILE}")


# ══════════════════════════════════════════════════════
#  history.json  (청산 이력 영구 보관)
# ══════════════════════════════════════════════════════

def load_history() -> List[Dict]:
    """history.json 전체 불러오기."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] load_history: {e}")
        return []

def _save_history(records: List[Dict]) -> None:
    """history.json 전체 저장."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    print(f"[INFO] history saved → {HISTORY_FILE}  ({len(records)} records)")

def _append_history(newly_closed: List[Dict]) -> None:
    """청산된 포지션을 history.json에 추가."""
    if not newly_closed:
        return
    history = load_history()
    history.extend(newly_closed)
    _save_history(history)


# ══════════════════════════════════════════════════════
#  ATR 기반 손절/익절 계산
# ══════════════════════════════════════════════════════

def _get_atr(ticker: str, entry_date: str) -> Optional[float]:
    """진입일 기준 직전 14일 ATR 계산."""
    try:
        end   = datetime.fromisoformat(entry_date).date()
        start = end - timedelta(days=30)
        df = yf.download(ticker, start=start, end=end,
                         interval="1d", progress=False, auto_adjust=False)
        if df is None or len(df) < 10:
            return None
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
        close = df["Close"].squeeze()
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(14).mean().iloc[-1])
    except Exception as e:
        print(f"[WARN] _get_atr({ticker}): {e}")
        return None

def calc_sl_tp(entry_price: float, atr: Optional[float]) -> Tuple[float, float]:
    """손절/익절 가격 반환. ATR 없으면 -5% / +10% 폴백."""
    if atr and atr > 0:
        sl = round(entry_price - ATR_STOP_MULT * atr, 4)
        tp = round(entry_price + ATR_TP_MULT   * atr, 4)
    else:
        sl = round(entry_price * 0.95, 4)
        tp = round(entry_price * 1.10, 4)
    return sl, tp


# ══════════════════════════════════════════════════════
#  포지션 등록
# ══════════════════════════════════════════════════════

def register_positions(rows: List[Dict], recommend_date: str) -> None:
    """
    신규 추천 종목을 포지션으로 등록.
    이미 열려있는 종목은 중복 등록하지 않음.
    """
    data = load_positions()
    open_tickers = {p["ticker"] for p in data["positions"] if p["status"] == STATUS_OPEN}
    added = []

    for r in rows:
        ticker = r.get("ticker")
        if not ticker or ticker in open_tickers:
            continue

        entry_price = r.get("last_price") or r.get("prev_close")
        if not entry_price or entry_price <= 0:
            print(f"[WARN] register_positions: no valid price for {ticker}, skip")
            continue

        atr    = _get_atr(ticker, recommend_date)
        sl, tp = calc_sl_tp(entry_price, atr)

        position = {
            "ticker":         ticker,
            "status":         STATUS_OPEN,
            "entry_price":    round(float(entry_price), 4),
            "entry_date":     recommend_date,
            "atr":            round(float(atr), 4) if atr else None,
            "stop_loss":      sl,
            "take_profit":    tp,
            "tech_score":     round(float(r.get("tech_score", 0)), 2),
            "combined_score": round(float(r.get("score", 0)), 2),
            "exit_price":     None,
            "exit_date":      None,
            "pnl_pct":        None,
            "close_reason":   None,
            "price_history":  [],
        }
        data["positions"].append(position)
        open_tickers.add(ticker)
        added.append(ticker)
        print(f"[INFO] registered: {ticker}  entry={entry_price:.2f}  "
              f"SL={sl:.2f}  TP={tp:.2f}  ATR={atr}")

    if added:
        save_positions(data)
        print(f"[INFO] {len(added)} new positions registered: {added}")
    else:
        print("[INFO] no new positions to register")


# ══════════════════════════════════════════════════════
#  포지션 업데이트 (매일 장 마감 후)
# ══════════════════════════════════════════════════════

def _fetch_close_prices(tickers: List[str]) -> Dict[str, float]:
    """당일 종가 일괄 조회."""
    if not tickers:
        return {}
    try:
        df = yf.download(tickers, period="5d", interval="1d",
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return {}
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            return {
                t: float(close[t].dropna().iloc[-1])
                for t in tickers
                if t in close.columns and not close[t].dropna().empty
            }
        else:
            t = tickers[0]
            s = close.dropna()
            return {t: float(s.iloc[-1])} if not s.empty else {}
    except Exception as e:
        print(f"[ERROR] _fetch_close_prices: {e}")
        return {}

def _calendar_days_since(entry_date: str) -> int:
    try:
        entry = datetime.fromisoformat(entry_date).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - entry).days
    except Exception:
        return 0

def update_positions() -> Tuple[List[Dict], List[Dict]]:
    """
    열린 포지션을 당일 종가 기준으로 업데이트.

    - 손절/익절/만료 → positions.json에서 제거 + history.json에 추가
    - 계속 보유 → price_history에 오늘 종가 append

    Returns:
        (still_open, newly_closed)
    """
    data     = load_positions()
    open_pos = [p for p in data["positions"] if p["status"] == STATUS_OPEN]

    if not open_pos:
        print("[INFO] no open positions to update")
        return [], []

    tickers = [p["ticker"] for p in open_pos]
    prices  = _fetch_close_prices(tickers)
    today   = datetime.now(timezone.utc).date().isoformat()

    newly_closed: List[Dict] = []
    still_open:   List[Dict] = []

    for pos in open_pos:
        t     = pos["ticker"]
        price = prices.get(t)

        if price is None:
            print(f"[WARN] no price for {t}, keeping open")
            still_open.append(pos)
            continue

        # 보유 중 가격 이력 기록
        pos.setdefault("price_history", [])
        pos["price_history"].append({"date": today, "close": round(price, 4)})

        entry = pos["entry_price"]
        sl    = pos["stop_loss"]
        tp    = pos["take_profit"]
        days  = _calendar_days_since(pos["entry_date"])
        pnl   = (price - entry) / entry * 100.0

        # ── 청산 판단 ──────────────────────────
        reason = None
        if price <= sl:
            reason = STATUS_SL
        elif price >= tp:
            reason = STATUS_TP
        elif days >= MAX_HOLD_DAYS:
            reason = STATUS_EXPIRED

        if reason:
            pos["status"]       = reason
            pos["exit_price"]   = round(price, 4)
            pos["exit_date"]    = today
            pos["pnl_pct"]      = round(pnl, 2)
            pos["close_reason"] = reason
            newly_closed.append(pos)
            emoji = {"take_profit": "✅", "stop_loss": "🛑", "expired": "⏰"}.get(reason, "?")
            print(f"[INFO] closed {emoji} {t}: {reason}  pnl={pnl:+.2f}%  days={days}")
        else:
            still_open.append(pos)

    # positions.json: 청산 종목 제거, 보유 종목만 유지
    data["positions"] = still_open

    # 누적 통계: history 전체 + 오늘 청산분 합산
    all_closed = load_history() + newly_closed
    data["stats"] = _recalc_stats(all_closed)

    save_positions(data)

    # history.json: 오늘 청산분 append
    _append_history(newly_closed)

    return still_open, newly_closed


# ══════════════════════════════════════════════════════
#  통계 계산
# ══════════════════════════════════════════════════════

def _recalc_stats(closed: List[Dict]) -> Dict:
    if not closed:
        return _empty_stats()

    pnls   = [p["pnl_pct"] for p in closed if p.get("pnl_pct") is not None]
    wins   = [p for p in closed if (p.get("pnl_pct") or 0) > 0]
    losses = [p for p in closed if (p.get("pnl_pct") or 0) <= 0]
    exps   = [p for p in closed if p.get("status") == STATUS_EXPIRED]

    total_pnl = sum(pnls) if pnls else 0.0
    avg_pnl   = total_pnl / len(pnls) if pnls else 0.0
    win_rate  = len(wins) / len(closed) * 100 if closed else 0.0

    best  = max(closed, key=lambda p: p.get("pnl_pct") or -999)
    worst = min(closed, key=lambda p: p.get("pnl_pct") or  999)

    return {
        "total_trades":  len(closed),
        "wins":          len(wins),
        "losses":        len(losses),
        "expired":       len(exps),
        "total_pnl_pct": round(total_pnl, 2),
        "win_rate":      round(win_rate, 1),
        "avg_pnl_pct":   round(avg_pnl, 2),
        "best_trade":    {"ticker": best.get("ticker"),  "pnl_pct": best.get("pnl_pct")},
        "worst_trade":   {"ticker": worst.get("ticker"), "pnl_pct": worst.get("pnl_pct")},
        "last_updated":  datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════
#  현황 요약
# ══════════════════════════════════════════════════════

def get_summary() -> Dict:
    """현재 포지션 현황 + 통계 요약 반환."""
    data     = load_positions()
    open_pos = [p for p in data["positions"] if p["status"] == STATUS_OPEN]

    # 열린 포지션 미실현 손익 계산
    tickers = [p["ticker"] for p in open_pos]
    prices  = _fetch_close_prices(tickers) if tickers else {}
    for pos in open_pos:
        p = prices.get(pos["ticker"])
        if p:
            pos["current_price"]  = round(p, 4)
            pos["unrealized_pnl"] = round((p - pos["entry_price"]) / pos["entry_price"] * 100, 2)
        else:
            pos["current_price"]  = None
            pos["unrealized_pnl"] = None

    # 당일 청산 제외한 최근 이력 (당일분은 Discord에서 별도 임베드로 표시)
    today = datetime.now(timezone.utc).date().isoformat()
    recent_closed = sorted(
        [p for p in load_history() if p.get("exit_date") != today],
        key=lambda p: p.get("exit_date") or "",
        reverse=True,
    )[:5]

    return {
        "open":          open_pos,
        "stats":         data["stats"],
        "recent_closed": recent_closed,
    }
