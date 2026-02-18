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
DEFAULT_ATR_STOP_MULT  = 2.0
DEFAULT_ATR_TP_MULT    = 4.0
DEFAULT_MAX_HOLD_DAYS  = 7
DEFAULT_SELL_THRESHOLD = 4.0   # 매도 점수 임계값 (이상이면 기술적 청산)
DEFAULT_MAX_POSITIONS  = 10    # 최대 동시 보유 포지션 수
DEFAULT_MAX_DAILY_ENTRIES = 3  # 하루 최대 신규 진입 수

STRATEGY_STATE_FILE = Path("config/strategy_state.json")

def _load_tuned_params():
    """strategy_state.json에서 자기학습된 파라미터 로드."""
    if STRATEGY_STATE_FILE.exists():
        try:
            with open(STRATEGY_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            p = state.get("current_params", {})
            return {
                "atr_stop_mult":      float(p.get("atr_stop_mult",      DEFAULT_ATR_STOP_MULT)),
                "atr_tp_mult":        float(p.get("atr_tp_mult",        DEFAULT_ATR_TP_MULT)),
                "max_hold_days":      int(p.get("max_hold_days",        DEFAULT_MAX_HOLD_DAYS)),
                "sell_threshold":     float(p.get("sell_threshold",      DEFAULT_SELL_THRESHOLD)),
                "max_positions":      int(p.get("max_positions",         DEFAULT_MAX_POSITIONS)),
                "max_daily_entries":  int(p.get("max_daily_entries",     DEFAULT_MAX_DAILY_ENTRIES)),
            }
        except Exception:
            pass
    return {
        "atr_stop_mult":     DEFAULT_ATR_STOP_MULT,
        "atr_tp_mult":       DEFAULT_ATR_TP_MULT,
        "max_hold_days":     DEFAULT_MAX_HOLD_DAYS,
        "sell_threshold":    DEFAULT_SELL_THRESHOLD,
        "max_positions":     DEFAULT_MAX_POSITIONS,
        "max_daily_entries": DEFAULT_MAX_DAILY_ENTRIES,
    }

# 포지션 상태
STATUS_OPEN        = "open"
STATUS_TP          = "take_profit"
STATUS_SL          = "stop_loss"
STATUS_EXPIRED     = "expired"
STATUS_SELL_SIGNAL = "sell_signal"


# ══════════════════════════════════════════════════════
#  positions.json  (열린 포지션 + 누적 통계)
# ══════════════════════════════════════════════════════

def _empty_stats() -> Dict:
    return {
        "total_trades": 0,
        "wins":         0,
        "losses":       0,
        "expired":      0,
        "sell_signal":  0,
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
    tuned = _load_tuned_params()
    if atr and atr > 0:
        sl = round(entry_price - tuned["atr_stop_mult"] * atr, 4)
        tp = round(entry_price + tuned["atr_tp_mult"]   * atr, 4)
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
    최대 포지션 수/일별 진입 수 제한 적용.
    """
    data = load_positions()
    open_tickers = {p["ticker"] for p in data["positions"] if p["status"] == STATUS_OPEN}
    open_count = len(open_tickers)
    added = []

    tuned = _load_tuned_params()
    max_positions = tuned["max_positions"]
    max_daily = tuned["max_daily_entries"]

    if open_count >= max_positions:
        print(f"[INFO] 포지션 가득 참 ({open_count}/{max_positions}) → 신규 진입 차단")
        return

    available_slots = min(max_daily, max_positions - open_count)
    print(f"[INFO] 포지션 현황: {open_count}/{max_positions} | 오늘 진입 가능: {available_slots}개")

    for r in rows:
        if len(added) >= available_slots:
            print(f"[INFO] 일별 진입 한도 도달 ({len(added)}/{available_slots}) → 중단")
            break

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

def _fetch_history_for_analysis(tickers: List[str], days: int = 60) -> Dict[str, pd.DataFrame]:
    """기술적 매도 신호 분석을 위한 종목별 OHLCV 히스토리 수집."""
    if not tickers:
        return {}
    try:
        df = yf.download(tickers, period=f"{days + 10}d", interval="1d",
                         progress=False, auto_adjust=False, group_by="ticker")
        if df is None or df.empty:
            return {}

        result = {}
        if len(tickers) == 1:
            t = tickers[0]
            sub = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(sub) >= 20:
                sub = sub.reset_index()
                sub.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
                result[t] = sub
        else:
            for t in tickers:
                try:
                    if t not in df.columns.get_level_values(0):
                        continue
                    sub = df[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
                    if len(sub) >= 20:
                        sub = sub.reset_index()
                        sub.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
                        result[t] = sub
                except Exception:
                    continue
        return result
    except Exception as e:
        print(f"[ERROR] _fetch_history_for_analysis: {e}")
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
    tuned   = _load_tuned_params()

    # 기술적 매도 신호 분석을 위한 히스토리 데이터 수집
    from .technical_analyzer import analyze_stock_technical, calculate_sell_score
    history_data = _fetch_history_for_analysis(tickers, days=60)
    sell_threshold = tuned["sell_threshold"]
    print(f"[INFO] 매도 신호 분석: {len(history_data)}/{len(tickers)}종목 히스토리 확보 | 임계값={sell_threshold}")

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

        # ── 청산 판단 (1: 손절/익절/만료) ────────
        reason = None
        if price <= sl:
            reason = STATUS_SL
        elif price >= tp:
            reason = STATUS_TP
        elif days >= tuned["max_hold_days"]:
            reason = STATUS_EXPIRED

        # ── 청산 판단 (2: 기술적 매도 신호) ──────
        sell_info = None
        if reason is None and t in history_data:
            try:
                analysis = analyze_stock_technical(history_data[t])
                if analysis:
                    sell_result = calculate_sell_score(analysis)
                    sell_score = sell_result["sell_score"]
                    sell_signals = sell_result["sell_signals"]

                    if sell_score >= sell_threshold:
                        reason = STATUS_SELL_SIGNAL
                        sell_info = sell_result
                        print(f"[INFO] 📉 {t}: 매도 신호 감지! "
                              f"score={sell_score:.1f} >= {sell_threshold} "
                              f"signals={sell_signals}")
                    else:
                        print(f"[INFO] {t}: 매도 점수={sell_score:.1f} < {sell_threshold} (유지)")
            except Exception as e:
                print(f"[WARN] {t} 매도 분석 실패: {e}")

        if reason:
            pos["status"]       = reason
            pos["exit_price"]   = round(price, 4)
            pos["exit_date"]    = today
            pos["pnl_pct"]      = round(pnl, 2)
            pos["close_reason"] = reason
            if sell_info:
                pos["sell_signals"] = sell_info.get("sell_signals", [])
                pos["sell_score"]   = sell_info.get("sell_score", 0)
            newly_closed.append(pos)
            emoji = {"take_profit": "✅", "stop_loss": "🛑",
                     "expired": "⏰", "sell_signal": "📉"}.get(reason, "?")
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
    sells  = [p for p in closed if p.get("status") == STATUS_SELL_SIGNAL]

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
        "sell_signal":   len(sells),
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


# ══════════════════════════════════════════════════════
#  리밸런싱: 포지션 재검증 + 초과분 청산
# ══════════════════════════════════════════════════════

def rebalance_positions(
    max_positions: int = None,
    fetch_live: bool = True,
    dry_run: bool = False,
) -> Dict:
    """
    열린 포지션을 재평가하고, max_positions 초과 시 하위 종목을 청산한다.

    Args:
        max_positions: 유지할 최대 포지션 수 (None이면 strategy_state에서 로드)
        fetch_live: True면 실시간 가격 fetch, False면 price_history 마지막 종가 사용
        dry_run: True면 실제 저장하지 않고 결과만 반환

    Returns:
        {"kept": [...], "closed": [...], "summary": {...}}
    """
    data = load_positions()
    tuned = _load_tuned_params()

    if max_positions is None:
        max_positions = tuned.get("max_positions", DEFAULT_MAX_POSITIONS)

    open_pos = [p for p in data["positions"] if p["status"] == STATUS_OPEN]
    print(f"\n{'='*60}")
    print(f"🔄 포지션 리밸런싱 (현재 {len(open_pos)}개 → 최대 {max_positions}개)")
    print(f"{'='*60}")

    if len(open_pos) <= max_positions:
        print(f"  ✅ 포지션 수 정상 ({len(open_pos)} ≤ {max_positions}) → 리밸런싱 불필요")
        return {"kept": open_pos, "closed": [], "summary": {"action": "none"}}

    # ── 실시간 가격 가져오기 ──
    tickers = [p["ticker"] for p in open_pos]
    live_prices = {}
    if fetch_live:
        print(f"  📡 {len(tickers)}개 종목 실시간 가격 조회...")
        live_prices = _fetch_close_prices(tickers)
        fetched = len([t for t in tickers if t in live_prices])
        print(f"  📡 {fetched}/{len(tickers)}개 가격 수신")

    # ── 각 포지션 재평가 ──
    scored = []
    for p in open_pos:
        entry = p["entry_price"]

        # 현재가 결정: 실시간 > price_history 마지막 > entry_price
        if p["ticker"] in live_prices:
            current = live_prices[p["ticker"]]
        elif p.get("price_history"):
            current = p["price_history"][-1]["close"]
        else:
            current = entry

        pnl_pct = (current - entry) / entry * 100.0
        tech = p.get("tech_score", 0)
        combined = p.get("combined_score", 0)

        # 재평가 점수: combined(50%) + 수익률 보정(30%) + 기술점수(20%)
        pnl_bonus = min(3.0, max(-3.0, pnl_pct * 0.5))
        reeval = combined * 0.5 + pnl_bonus * 0.3 + tech * 0.2

        scored.append({
            "position": p,
            "current_price": round(current, 4),
            "pnl_pct": round(pnl_pct, 2),
            "reeval_score": round(reeval, 3),
        })

    # 점수순 정렬
    scored.sort(key=lambda x: x["reeval_score"], reverse=True)

    keep = scored[:max_positions]
    to_close = scored[max_positions:]

    # ── 결과 출력 ──
    today = datetime.now(timezone.utc).date().isoformat()

    print(f"\n  ✅ 유지 ({len(keep)}개):")
    for s in keep:
        p = s["position"]
        emoji = "🟢" if s["pnl_pct"] >= 0 else "🔴"
        print(f"    {emoji} {p['ticker']:<6} P&L: {s['pnl_pct']:+6.1f}%  점수: {s['reeval_score']:.2f}")

    print(f"\n  ❌ 청산 ({len(to_close)}개):")
    newly_closed = []
    for s in to_close:
        p = s["position"]
        emoji = "🟢" if s["pnl_pct"] >= 0 else "🔴"
        print(f"    {emoji} {p['ticker']:<6} P&L: {s['pnl_pct']:+6.1f}%  점수: {s['reeval_score']:.2f}")

        if not dry_run:
            p["status"] = "strategy_rebalance"
            p["exit_price"] = s["current_price"]
            p["exit_date"] = today
            p["pnl_pct"] = s["pnl_pct"]
            p["close_reason"] = "strategy_rebalance"

        newly_closed.append({
            "ticker": p["ticker"],
            "entry_price": p["entry_price"],
            "entry_date": p["entry_date"],
            "exit_price": s["current_price"],
            "exit_date": today,
            "pnl_pct": s["pnl_pct"],
            "close_reason": "strategy_rebalance",
            "tech_score": p.get("tech_score", 0),
            "combined_score": p.get("combined_score", 0),
            "hold_days": _calendar_days_since(p["entry_date"]),
        })

    # ── 저장 ──
    if not dry_run and to_close:
        # stats 재계산
        all_closed = [p for p in data["positions"] if p["status"] != STATUS_OPEN]
        data["stats"] = _recalc_stats(all_closed)
        save_positions(data)
        _append_history(newly_closed)
        print(f"\n  💾 저장 완료 (positions + history)")
    elif dry_run and to_close:
        print(f"\n  ⚠️ DRY RUN — 실제 저장하지 않음")

    # 요약
    total_pnl = sum(s["pnl_pct"] for s in to_close)
    wins = len([s for s in to_close if s["pnl_pct"] > 0])
    losses = len(to_close) - wins

    summary = {
        "action": "rebalanced" if to_close else "none",
        "kept": len(keep),
        "closed": len(to_close),
        "closed_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": losses,
    }

    print(f"\n{'─'*60}")
    print(f"  📊 리밸런싱 결과: 유지 {len(keep)} / 청산 {len(to_close)} "
          f"(승{wins}/패{losses}, P&L: {total_pnl:+.1f}%)")

    return {"kept": [s["position"] for s in keep], "closed": newly_closed, "summary": summary}
