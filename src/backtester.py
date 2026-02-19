"""
백테스팅 엔진 v1

현재 봇의 기술적 분석 전략을 과거 데이터에 적용하여 성과를 검증합니다.

핵심 로직:
  1. 과거 N일 동안 매일 "그 날 봇이 돌았더라면" 시뮬레이션
  2. 각 날짜별로 기술적 분석 → 상위 종목 선별
  3. 진입가(당일 종가) / 손절(ATR 기반) / 익절(ATR 기반) / 만료(7일)
  4. 이후 실제 가격 데이터로 청산 여부 판정
  5. 종합 통계: 승률, 평균수익, 최대낙폭, 샤프비율 등

사용법:
  python -m src.backtester                    # 기본 90일 백테스트
  python -m src.backtester --days 180         # 180일
  python -m src.backtester --days 365 --top 5 # 365일, 상위 5종목
  python -m src.backtester --export           # 결과를 JSON/CSV로 내보내기
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from .technical_analyzer import analyze_stock_technical, calculate_technical_score
from .logger import logger

# ── 상수 ──────────────────────────────────────────
ATR_STOP_MULT = 2.0
ATR_TP_MULT = 4.0
MAX_HOLD_DAYS = 7           # 캘린더일 기준 최대 보유
MIN_TECH_SCORE = 4.0        # 최소 기술 점수
LOOKBACK_BARS = 60          # 기술적 분석에 필요한 과거 봉 수
COMMISSION_PCT = 0.0        # 수수료 (기본 0%, 필요시 조정)
SLIPPAGE_PCT = 0.05         # 슬리피지 0.05%


# ══════════════════════════════════════════════════════
#  데이터 로더
# ══════════════════════════════════════════════════════

def _download_data(tickers: List[str], start: str, end: str) -> pd.DataFrame:
    """
    yfinance로 일봉 데이터 일괄 다운로드.
    반환: [Date, Open, High, Low, Close, Volume, ticker] long 형식.
    """
    logger.info(f"다운로드: {len(tickers)}개 종목 ({start} ~ {end})")

    df = yf.download(
        tickers=tickers,
        start=start,
        end=end,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    frames = []

    if isinstance(df.columns, pd.MultiIndex):
        lv0 = [str(c) for c in df.columns.get_level_values(0)]
        field_on_0 = "Close" in lv0

        df_reset = df.reset_index()

        for t in tickers:
            try:
                if field_on_0:
                    sub = pd.DataFrame({
                        "Date": df_reset["Date"],
                        "Open": df_reset[("Open", t)],
                        "High": df_reset[("High", t)],
                        "Low": df_reset[("Low", t)],
                        "Close": df_reset[("Close", t)],
                        "Volume": df_reset[("Volume", t)],
                        "ticker": t,
                    })
                else:
                    sub = pd.DataFrame({
                        "Date": df_reset["Date"],
                        "Open": df_reset[(t, "Open")],
                        "High": df_reset[(t, "High")],
                        "Low": df_reset[(t, "Low")],
                        "Close": df_reset[(t, "Close")],
                        "Volume": df_reset[(t, "Volume")],
                        "ticker": t,
                    })
                sub = sub.dropna(subset=["Close", "Volume"])
                sub = sub[sub["Close"] > 0]
                if not sub.empty:
                    frames.append(sub)
            except KeyError:
                continue
    else:
        t = tickers[0] if isinstance(tickers, list) else tickers
        sub = df.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        sub["ticker"] = t
        sub = sub.dropna(subset=["Close", "Volume"])
        sub = sub[sub["Close"] > 0]
        if not sub.empty:
            frames.append(sub)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["Date"] = pd.to_datetime(result["Date"])
    logger.info(f"다운로드 완료: {len(result)}행, {result['ticker'].nunique()}종목")
    return result


# ══════════════════════════════════════════════════════
#  단일 트레이드 시뮬레이션
# ══════════════════════════════════════════════════════

class Trade:
    """개별 트레이드 기록."""

    def __init__(self, ticker: str, entry_date: str, entry_price: float,
                 stop_loss: float, take_profit: float, tech_score: float,
                 signals: List[str]):
        self.ticker = ticker
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.tech_score = tech_score
        self.signals = signals

        # 청산 정보 (나중에 채움)
        self.exit_date: Optional[str] = None
        self.exit_price: Optional[float] = None
        self.pnl_pct: Optional[float] = None
        self.status: Optional[str] = None  # take_profit / stop_loss / expired / sell_signal
        self.hold_days: int = 0
        self.max_drawdown_pct: float = 0.0     # 보유 중 최대 낙폭
        self.max_favorable_pct: float = 0.0    # 보유 중 최대 이익
        self.sell_signals: List[str] = []      # 매도 신호 목록
        self.sell_score: float = 0.0           # 매도 점수
        self.partial_closed: bool = False      # 부분 청산 여부

    def to_dict(self) -> Dict:
        return {
            "ticker": self.ticker,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 4),
            "stop_loss": round(self.stop_loss, 4),
            "take_profit": round(self.take_profit, 4),
            "tech_score": round(self.tech_score, 2),
            "signals": self.signals,
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 4) if self.exit_price else None,
            "pnl_pct": round(self.pnl_pct, 4) if self.pnl_pct is not None else None,
            "status": self.status,
            "hold_days": self.hold_days,
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_favorable_pct": round(self.max_favorable_pct, 4),
            "sell_signals": self.sell_signals,
            "sell_score": round(self.sell_score, 2),
        }


def _simulate_trade(trade: Trade, future_data: pd.DataFrame,
                    max_hold_days: int = MAX_HOLD_DAYS,
                    sell_threshold: float = 4.0,
                    hist_data: pd.DataFrame = None,
                    trailing_atr_mult: float = 1.5,
                    trailing_min_pct: float = 3.0) -> Trade:
    """
    진입 이후 실제 가격으로 트레이드 청산 시뮬레이션.
    트레일링 스탑 + 부분 청산 지원.

    future_data: 진입일 다음날부터의 OHLCV (해당 종목)
    hist_data: 진입일까지의 OHLCV (매도 신호 분석용, optional)
    """
    if future_data.empty:
        trade.status = "no_data"
        trade.pnl_pct = 0.0
        return trade

    entry = trade.entry_price
    sl = trade.stop_loss
    tp = trade.take_profit
    max_dd = 0.0
    max_fav = 0.0

    # ATR 역산 (sl에서 atr_stop_mult 기반)
    atr = (entry - sl) / ATR_STOP_MULT if entry > sl else entry * 0.02

    # 트레일링 상태
    tp_half = entry + (tp - entry) * 0.5   # TP의 50% 지점
    highest_price = entry
    trailing_active = False
    trailing_sl = sl
    partial_closed = False
    partial_pnl = 0.0   # 부분 청산 수익

    # 매도 신호 분석용 히스토리 구축
    use_sell_signal = (hist_data is not None and len(hist_data) >= 30
                       and sell_threshold < 99)

    for i, (_, row) in enumerate(future_data.iterrows()):
        day_num = i + 1
        low = row["Low"]
        high = row["High"]
        close = row["Close"]

        dd_pct = (low - entry) / entry * 100
        fav_pct = (high - entry) / entry * 100
        max_dd = min(max_dd, dd_pct)
        max_fav = max(max_fav, fav_pct)

        # 최고가 갱신
        if high > highest_price:
            highest_price = high

        # 트레일링 활성화 체크
        if not trailing_active and highest_price >= tp_half:
            trailing_active = True

        # 트레일링 SL 갱신
        if trailing_active and atr > 0:
            trail_dist = max(atr * trailing_atr_mult, highest_price * trailing_min_pct / 100)
            new_trail_sl = highest_price - trail_dist
            if new_trail_sl > trailing_sl:
                trailing_sl = new_trail_sl

        effective_sl = trailing_sl if trailing_active else sl

        # 1순위: 손절 / 트레일링 스탑
        if low <= effective_sl:
            exit_px = effective_sl
            trade.exit_price = exit_px
            trade.exit_date = str(row["Date"].date()) if hasattr(row["Date"], "date") else str(row["Date"])
            trade.status = "trailing_stop" if trailing_active else "stop_loss"
            trade.hold_days = day_num
            break

        # 2순위: TP 도달 → 부분 청산
        if high >= tp and not partial_closed:
            partial_closed = True
            partial_pnl = (tp - entry) / entry * 100  # 50% 물량의 수익률
            # 나머지 50%는 트레일링 계속, 만료 면제
            if not trailing_active:
                trailing_active = True
            continue  # 전량 청산하지 않고 계속

        # 3순위: 매도 신호 (2일차부터)
        if use_sell_signal and day_num >= 2:
            try:
                from .technical_analyzer import analyze_stock_technical, calculate_sell_score
                combined = pd.concat([hist_data, future_data.iloc[:i+1]], ignore_index=True)
                if len(combined) >= 30:
                    analysis = analyze_stock_technical(combined)
                    if analysis:
                        sell_result = calculate_sell_score(analysis)
                        if sell_result["sell_score"] >= sell_threshold:
                            trade.exit_price = close
                            trade.exit_date = str(row["Date"].date()) if hasattr(row["Date"], "date") else str(row["Date"])
                            trade.status = "sell_signal"
                            trade.hold_days = day_num
                            trade.sell_signals = sell_result["sell_signals"]
                            trade.sell_score = sell_result["sell_score"]
                            break
            except Exception:
                pass

        # 4순위: 만료 (부분 청산 안 된 포지션만)
        if day_num >= max_hold_days and not partial_closed:
            trade.exit_price = close
            trade.exit_date = str(row["Date"].date()) if hasattr(row["Date"], "date") else str(row["Date"])
            trade.status = "expired"
            trade.hold_days = day_num
            break
    else:
        last = future_data.iloc[-1]
        trade.exit_price = last["Close"]
        trade.exit_date = str(last["Date"].date()) if hasattr(last["Date"], "date") else str(last["Date"])
        trade.status = "trailing_stop" if trailing_active else "expired"
        trade.hold_days = len(future_data)

    # 손익 계산 (수수료 + 슬리피지 포함)
    if trade.exit_price and trade.entry_price > 0:
        remaining_pnl = (trade.exit_price - trade.entry_price) / trade.entry_price * 100

        if partial_closed:
            # 가중 평균: 50% 부분청산(TP) + 50% 트레일링
            trade.pnl_pct = (partial_pnl * 0.5 + remaining_pnl * 0.5) - COMMISSION_PCT - SLIPPAGE_PCT
            trade.partial_closed = True
        else:
            trade.pnl_pct = remaining_pnl - COMMISSION_PCT - SLIPPAGE_PCT
    else:
        trade.pnl_pct = 0.0

    trade.max_drawdown_pct = max_dd
    trade.max_favorable_pct = max_fav

    return trade


# ══════════════════════════════════════════════════════
#  과열 필터 (ranker.py에서 가져옴)
# ══════════════════════════════════════════════════════

def _is_overheated(tech: Dict, day_ret: float) -> bool:
    reasons = []
    if tech.get('rsi', 50) > 75:
        reasons.append('rsi')
    if tech.get('consecutive_up', 0) >= 5:
        reasons.append('consecutive')
    if tech.get('bb_position', 0.5) > 0.95:
        reasons.append('bb')
    if tech.get('ma5_deviation', 0) > 12:
        reasons.append('ma_dev')
    if day_ret > 5 and tech.get('volume_ratio', 1) > 3:
        reasons.append('spike')
    if tech.get('divergence', {}).get('bearish_divergence', False):
        reasons.append('divergence')
    return len(reasons) >= 2


def _extract_signals(tech: Dict) -> List[str]:
    """기술적 분석 결과에서 주요 신호 문자열 추출."""
    signals = []
    pb = tech.get('pullback', {})
    if pb.get('pullback_to_ma20'):
        signals.append("20MA눌림목")
    if pb.get('pullback_to_ma50'):
        signals.append("50MA눌림목")
    if pb.get('pullback_to_bb_lower'):
        signals.append("BB하단반등")

    bo = tech.get('breakout', {})
    if bo.get('breakout_detected'):
        signals.append(f"돌파({bo.get('breakout_type', '')})")

    div = tech.get('divergence', {})
    if div.get('bullish_divergence'):
        signals.append("강세다이버전스")

    if tech.get('golden_cross'):
        signals.append("골든크로스")
    if tech.get('macd_cross_up'):
        signals.append("MACD상향")
    if tech.get('ma_alignment'):
        signals.append("이평정배열")
    if tech.get('bullish_volume'):
        signals.append(f"거래량{tech.get('volume_ratio', 1):.1f}x")
    if tech.get('stoch_cross_up'):
        signals.append("스토캐스틱크로스")
    if tech.get('bb_squeeze') and bo.get('breakout_detected'):
        signals.append("스퀴즈돌파")

    return signals


# ══════════════════════════════════════════════════════
#  ATR 계산 (독립적)
# ══════════════════════════════════════════════════════

def _calc_atr_from_df(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(df) < period + 1:
        return None
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else None


# ══════════════════════════════════════════════════════
#  메인 백테스터
# ══════════════════════════════════════════════════════

class BacktestEngine:
    """
    백테스팅 엔진.

    과거 N일 동안 매일 봇이 실행되었다고 가정하고,
    기술적 분석 → 상위 종목 선별 → 실제 가격으로 청산 시뮬레이션.
    """

    def __init__(
        self,
        pool: str = "nasdaq100",
        backtest_days: int = 90,
        top_n: int = 5,
        min_tech_score: float = MIN_TECH_SCORE,
        max_hold_days: int = MAX_HOLD_DAYS,
        atr_stop_mult: float = ATR_STOP_MULT,
        atr_tp_mult: float = ATR_TP_MULT,
        sell_threshold: float = 4.0,
        max_positions: int = 10,
        max_daily_entries: int = 3,
        trailing_atr_mult: float = 1.5,
        trailing_min_pct: float = 3.0,
    ):
        self.pool = pool
        self.backtest_days = backtest_days
        self.top_n = top_n
        self.min_tech_score = min_tech_score
        self.max_hold_days = max_hold_days
        self.atr_stop_mult = atr_stop_mult
        self.atr_tp_mult = atr_tp_mult
        self.sell_threshold = sell_threshold
        self.max_positions = max_positions
        self.max_daily_entries = max_daily_entries
        self.trailing_atr_mult = trailing_atr_mult
        self.trailing_min_pct = trailing_min_pct

        self.trades: List[Trade] = []
        self.daily_log: List[Dict] = []
        self.all_data: Optional[pd.DataFrame] = None

    def _get_pool_tickers(self) -> List[str]:
        """종목 풀 가져오기 (universe_builder 재사용)."""
        try:
            from .universe_builder import get_pool
            tickers = get_pool(self.pool)
        except Exception:
            tickers = [
                "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
                "BRK-B", "AVGO", "JPM", "UNH", "V", "MA", "HD", "PG",
                "COST", "JNJ", "ABBV", "CRM", "AMD", "NFLX", "LIN",
                "MRK", "ADBE", "TXN", "QCOM", "ISRG", "INTU", "AMAT",
            ]
        return tickers

    def run(self) -> Dict:
        """
        백테스트 실행.

        Returns:
            결과 딕셔너리 (통계 + 트레이드 내역)
        """
        tickers = self._get_pool_tickers()
        logger.info(f"백테스트 시작: {self.pool} ({len(tickers)}종목), {self.backtest_days}일")

        # 데이터 다운로드 (lookback + backtest + hold 기간 포함)
        total_days = LOOKBACK_BARS + self.backtest_days + self.max_hold_days + 30
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=total_days)

        self.all_data = _download_data(
            tickers,
            start_date.isoformat(),
            end_date.isoformat(),
        )

        if self.all_data.empty:
            logger.error("데이터 다운로드 실패")
            return self._empty_result()

        # 거래일 목록 (모든 종목에서 공통으로 존재하는 날짜)
        date_counts = self.all_data.groupby("Date")["ticker"].nunique()
        # 충분한 종목이 있는 거래일만 사용 (최소 20종목)
        valid_dates = date_counts[date_counts >= 20].index.sort_values()

        if len(valid_dates) < LOOKBACK_BARS + 10:
            logger.error(f"유효한 거래일 부족: {len(valid_dates)}")
            return self._empty_result()

        # 백테스트 시작 날짜 (lookback 이후부터)
        bt_start_idx = LOOKBACK_BARS
        bt_dates = valid_dates[bt_start_idx:]

        # 최근 backtest_days 거래일만 사용
        if len(bt_dates) > self.backtest_days:
            bt_dates = bt_dates[-self.backtest_days:]

        logger.info(f"백테스트 기간: {bt_dates[0].date()} ~ {bt_dates[-1].date()} ({len(bt_dates)}거래일)")

        # 진행중인 포지션 추적 (동일 종목 중복 진입 방지)
        active_tickers = set()

        for sim_idx, sim_date in enumerate(bt_dates):
            if sim_idx % 10 == 0:
                logger.info(f"  시뮬레이션 {sim_idx+1}/{len(bt_dates)} ({sim_date.date()})")

            # 이 날짜 기준으로 분석에 사용할 과거 데이터 (lookback)
            hist_mask = self.all_data["Date"] <= sim_date
            hist_data = self.all_data[hist_mask]

            # 만료/청산된 포지션 제거
            self._check_expired_positions(active_tickers, sim_date, valid_dates)

            # 기술적 분석 실행
            candidates = self._analyze_day(hist_data, sim_date, active_tickers, tickers)

            if not candidates:
                continue

            # 상위 N개 선별 (포지션 제한 적용)
            candidates.sort(key=lambda x: x["tech_score"], reverse=True)
            available_slots = min(
                self.top_n,
                self.max_daily_entries,
                max(0, self.max_positions - len(active_tickers))
            )
            selected = candidates[:available_slots]

            if not selected:
                continue

            # 트레이드 생성
            for c in selected:
                ticker = c["ticker"]
                entry_price = c["close"]
                atr = c["atr"]

                if atr and atr > 0:
                    sl = entry_price - self.atr_stop_mult * atr
                    tp = entry_price + self.atr_tp_mult * atr
                else:
                    sl = entry_price * 0.95
                    tp = entry_price * 1.10

                trade = Trade(
                    ticker=ticker,
                    entry_date=str(sim_date.date()),
                    entry_price=entry_price,
                    stop_loss=sl,
                    take_profit=tp,
                    tech_score=c["tech_score"],
                    signals=c["signals"],
                )

                # 진입 이후 데이터로 시뮬레이션
                future = self.all_data[
                    (self.all_data["ticker"] == ticker) &
                    (self.all_data["Date"] > sim_date)
                ].sort_values("Date").head(self.max_hold_days + 2)

                # 매도 신호 분석용 히스토리 (진입일까지)
                hist_for_sell = self.all_data[
                    (self.all_data["ticker"] == ticker) &
                    (self.all_data["Date"] <= sim_date)
                ].sort_values("Date").tail(LOOKBACK_BARS)

                trade = _simulate_trade(
                    trade, future,
                    max_hold_days=self.max_hold_days,
                    sell_threshold=self.sell_threshold,
                    hist_data=hist_for_sell,
                    trailing_atr_mult=getattr(self, 'trailing_atr_mult', 1.5),
                    trailing_min_pct=getattr(self, 'trailing_min_pct', 3.0),
                )
                self.trades.append(trade)
                active_tickers.add(ticker)

            # 일별 로그
            self.daily_log.append({
                "date": str(sim_date.date()),
                "candidates": len(candidates),
                "selected": len(selected),
                "active_positions": len(active_tickers),
            })

        # 결과 계산
        return self._calculate_results()

    def _analyze_day(
        self,
        hist_data: pd.DataFrame,
        sim_date: pd.Timestamp,
        active_tickers: set,
        all_tickers: List[str],
    ) -> List[Dict]:
        """특정 날짜 기준 기술적 분석 실행."""
        candidates = []

        for ticker in all_tickers:
            if ticker in active_tickers:
                continue

            g = hist_data[hist_data["ticker"] == ticker].sort_values("Date")

            if len(g) < 30:
                continue

            # 최근 LOOKBACK_BARS개만 사용
            g = g.tail(LOOKBACK_BARS)

            last = g.iloc[-1]
            prev = g.iloc[-2] if len(g) >= 2 else last

            if pd.isna(last["Close"]) or last["Close"] <= 0:
                continue

            day_ret = (last["Close"] / prev["Close"] - 1) * 100 if prev["Close"] > 0 else 0

            # 기술적 분석
            tech = analyze_stock_technical(g)
            if not tech:
                continue

            score = calculate_technical_score(tech)

            # 과열 필터
            if _is_overheated(tech, day_ret):
                continue

            # 최소 점수 필터
            if score < self.min_tech_score:
                continue

            # ATR 계산
            atr = _calc_atr_from_df(g)

            signals = _extract_signals(tech)

            candidates.append({
                "ticker": ticker,
                "close": float(last["Close"]),
                "day_ret": day_ret,
                "tech_score": score,
                "atr": atr,
                "signals": signals,
            })

        return candidates

    def _check_expired_positions(self, active_tickers: set, current_date, valid_dates):
        """만료/청산된 트레이드의 종목을 active에서 제거."""
        to_remove = set()
        for trade in self.trades:
            if trade.ticker in active_tickers and trade.exit_date:
                try:
                    exit_d = pd.Timestamp(trade.exit_date)
                    if exit_d <= current_date:
                        to_remove.add(trade.ticker)
                except Exception:
                    pass
        active_tickers -= to_remove

    def _calculate_results(self) -> Dict:
        """종합 통계 계산."""
        if not self.trades:
            return self._empty_result()

        completed = [t for t in self.trades if t.status and t.pnl_pct is not None]

        if not completed:
            return self._empty_result()

        pnls = [t.pnl_pct for t in completed]
        wins = [t for t in completed if t.pnl_pct > 0]
        losses = [t for t in completed if t.pnl_pct <= 0]

        tp_trades = [t for t in completed if t.status == "take_profit"]
        sl_trades = [t for t in completed if t.status == "stop_loss"]
        exp_trades = [t for t in completed if t.status == "expired"]
        sell_trades = [t for t in completed if t.status == "sell_signal"]
        trail_trades = [t for t in completed if t.status == "trailing_stop"]
        partial_trades = [t for t in completed if getattr(t, 'partial_closed', False)]

        # 기본 통계
        total = len(completed)
        win_rate = len(wins) / total * 100 if total > 0 else 0
        avg_pnl = np.mean(pnls)
        median_pnl = np.median(pnls)
        total_pnl = sum(pnls)
        std_pnl = np.std(pnls) if len(pnls) > 1 else 0

        # 승리/패배 평균
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0

        # 손익비 (Profit Factor)
        gross_profit = sum(t.pnl_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # 기대값 (Expected Value per Trade)
        ev = (win_rate / 100 * avg_win) + ((100 - win_rate) / 100 * avg_loss)

        # 샤프 비율 (일간 기준 → 연환산)
        sharpe = (avg_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0

        # 최대 연속 승/패
        max_consec_wins, max_consec_losses = self._max_consecutive(completed)

        # 보유 기간 통계
        hold_days = [t.hold_days for t in completed]
        avg_hold = np.mean(hold_days)

        # 최대 낙폭 (포트폴리오 레벨)
        portfolio_dd = self._calc_portfolio_drawdown(completed)

        # 월별 수익
        monthly = self._calc_monthly_returns(completed)

        # 종목별 빈도
        ticker_freq = defaultdict(int)
        ticker_pnl = defaultdict(list)
        for t in completed:
            ticker_freq[t.ticker] += 1
            ticker_pnl[t.ticker].append(t.pnl_pct)

        top_tickers = sorted(ticker_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        best_tickers = sorted(
            [(k, np.mean(v), len(v)) for k, v in ticker_pnl.items() if len(v) >= 2],
            key=lambda x: x[1], reverse=True
        )[:5]
        worst_tickers = sorted(
            [(k, np.mean(v), len(v)) for k, v in ticker_pnl.items() if len(v) >= 2],
            key=lambda x: x[1]
        )[:5]

        # 신호별 성과
        signal_stats = self._calc_signal_performance(completed)

        # 점수 구간별 성과
        score_brackets = self._calc_score_bracket_performance(completed)

        result = {
            "config": {
                "pool": self.pool,
                "backtest_days": self.backtest_days,
                "top_n": self.top_n,
                "min_tech_score": self.min_tech_score,
                "max_hold_days": self.max_hold_days,
                "atr_stop_mult": self.atr_stop_mult,
                "atr_tp_mult": self.atr_tp_mult,
                "sell_threshold": self.sell_threshold,
                "max_positions": self.max_positions,
                "max_daily_entries": self.max_daily_entries,
                "commission_pct": COMMISSION_PCT,
                "slippage_pct": SLIPPAGE_PCT,
            },
            "summary": {
                "total_trades": total,
                "win_rate": round(win_rate, 2),
                "avg_pnl_pct": round(avg_pnl, 4),
                "median_pnl_pct": round(median_pnl, 4),
                "total_pnl_pct": round(total_pnl, 4),
                "std_pnl_pct": round(std_pnl, 4),
                "avg_win_pct": round(avg_win, 4),
                "avg_loss_pct": round(avg_loss, 4),
                "profit_factor": round(profit_factor, 4),
                "expected_value_pct": round(ev, 4),
                "sharpe_ratio": round(sharpe, 4),
                "max_consecutive_wins": max_consec_wins,
                "max_consecutive_losses": max_consec_losses,
                "avg_hold_days": round(avg_hold, 2),
                "portfolio_max_drawdown_pct": round(portfolio_dd, 4),
            },
            "exit_breakdown": {
                "take_profit": len(tp_trades),
                "stop_loss": len(sl_trades),
                "expired": len(exp_trades),
                "sell_signal": len(sell_trades),
                "trailing_stop": len(trail_trades),
                "partial_closed": len(partial_trades),
                "tp_rate": round(len(tp_trades) / total * 100, 2) if total > 0 else 0,
                "sl_rate": round(len(sl_trades) / total * 100, 2) if total > 0 else 0,
                "exp_rate": round(len(exp_trades) / total * 100, 2) if total > 0 else 0,
                "sell_rate": round(len(sell_trades) / total * 100, 2) if total > 0 else 0,
                "trail_rate": round(len(trail_trades) / total * 100, 2) if total > 0 else 0,
            },
            "monthly_returns": monthly,
            "top_traded_tickers": [
                {"ticker": t, "trades": n} for t, n in top_tickers
            ],
            "best_tickers": [
                {"ticker": t, "avg_pnl": round(p, 2), "trades": n}
                for t, p, n in best_tickers
            ],
            "worst_tickers": [
                {"ticker": t, "avg_pnl": round(p, 2), "trades": n}
                for t, p, n in worst_tickers
            ],
            "signal_performance": signal_stats,
            "score_bracket_performance": score_brackets,
            "trades": [t.to_dict() for t in completed],
        }

        return result

    def _max_consecutive(self, trades: List[Trade]) -> Tuple[int, int]:
        """최대 연속 승/패."""
        max_w = max_l = cur_w = cur_l = 0
        for t in trades:
            if t.pnl_pct > 0:
                cur_w += 1
                cur_l = 0
            else:
                cur_l += 1
                cur_w = 0
            max_w = max(max_w, cur_w)
            max_l = max(max_l, cur_l)
        return max_w, max_l

    def _calc_portfolio_drawdown(self, trades: List[Trade]) -> float:
        """포트폴리오 레벨 최대 낙폭 (누적 수익 기준)."""
        sorted_trades = sorted(trades, key=lambda t: t.exit_date or t.entry_date)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for t in sorted_trades:
            cumulative += (t.pnl_pct or 0)
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        return max_dd

    def _calc_monthly_returns(self, trades: List[Trade]) -> List[Dict]:
        """월별 수익 집계."""
        monthly = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})

        for t in trades:
            if t.exit_date:
                month = t.exit_date[:7]  # "YYYY-MM"
                monthly[month]["trades"] += 1
                monthly[month]["pnl"] += (t.pnl_pct or 0)
                if (t.pnl_pct or 0) > 0:
                    monthly[month]["wins"] += 1

        result = []
        for month in sorted(monthly.keys()):
            m = monthly[month]
            wr = m["wins"] / m["trades"] * 100 if m["trades"] > 0 else 0
            result.append({
                "month": month,
                "trades": m["trades"],
                "total_pnl_pct": round(m["pnl"], 2),
                "win_rate": round(wr, 1),
            })

        return result

    def _calc_signal_performance(self, trades: List[Trade]) -> List[Dict]:
        """진입 신호별 성과 분석."""
        signal_data = defaultdict(lambda: {"count": 0, "pnls": []})

        for t in trades:
            for sig in t.signals:
                signal_data[sig]["count"] += 1
                signal_data[sig]["pnls"].append(t.pnl_pct or 0)

        result = []
        for sig, data in sorted(signal_data.items(), key=lambda x: x[1]["count"], reverse=True):
            pnls = data["pnls"]
            result.append({
                "signal": sig,
                "count": data["count"],
                "avg_pnl": round(np.mean(pnls), 2),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
            })

        return result

    def _calc_score_bracket_performance(self, trades: List[Trade]) -> List[Dict]:
        """기술 점수 구간별 성과."""
        brackets = [
            (4.0, 5.0, "4.0~5.0"),
            (5.0, 6.0, "5.0~6.0"),
            (6.0, 7.0, "6.0~7.0"),
            (7.0, 8.0, "7.0~8.0"),
            (8.0, 10.1, "8.0+"),
        ]
        result = []
        for lo, hi, label in brackets:
            group = [t for t in trades if lo <= t.tech_score < hi]
            if not group:
                continue
            pnls = [t.pnl_pct or 0 for t in group]
            result.append({
                "bracket": label,
                "trades": len(group),
                "avg_pnl": round(np.mean(pnls), 2),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
            })
        return result

    def _empty_result(self) -> Dict:
        return {
            "config": {},
            "summary": {"total_trades": 0, "error": "데이터 부족"},
            "trades": [],
        }


# ══════════════════════════════════════════════════════
#  콘솔 리포트 출력
# ══════════════════════════════════════════════════════

def print_report(result: Dict):
    """백테스트 결과를 콘솔에 예쁘게 출력."""
    cfg = result.get("config", {})
    s = result.get("summary", {})

    if s.get("total_trades", 0) == 0:
        print("\n❌ 백테스트 결과가 없습니다.")
        return

    print("\n" + "=" * 70)
    print("📊 백테스트 결과 리포트")
    print("=" * 70)

    # 설정
    print(f"\n⚙️  설정")
    print(f"   풀: {cfg.get('pool', '?')} | 기간: {cfg.get('backtest_days', '?')}거래일")
    print(f"   상위 {cfg.get('top_n', '?')}종목/일 | 최소점수: {cfg.get('min_tech_score', '?')}")
    print(f"   손절: ATR×{cfg.get('atr_stop_mult', '?')} | 익절: ATR×{cfg.get('atr_tp_mult', '?')}")
    print(f"   최대보유: {cfg.get('max_hold_days', '?')}일 | 매도임계: {cfg.get('sell_threshold', '?')} | 수수료: {cfg.get('commission_pct', 0)}%")

    # 핵심 지표
    print(f"\n{'─' * 70}")
    print(f"📈 핵심 성과")
    print(f"{'─' * 70}")
    print(f"   총 거래수:     {s['total_trades']}")
    print(f"   승률:          {s['win_rate']:.1f}%")
    print(f"   평균 수익:     {s['avg_pnl_pct']:+.2f}%")
    print(f"   중앙값 수익:   {s['median_pnl_pct']:+.2f}%")
    print(f"   누적 수익:     {s['total_pnl_pct']:+.2f}%")
    print(f"   표준편차:      {s['std_pnl_pct']:.2f}%")

    print(f"\n   평균 수익(승): {s['avg_win_pct']:+.2f}%")
    print(f"   평균 손실(패): {s['avg_loss_pct']:+.2f}%")
    print(f"   Profit Factor: {s['profit_factor']:.2f}")
    print(f"   기대값/거래:   {s['expected_value_pct']:+.2f}%")
    print(f"   샤프 비율:     {s['sharpe_ratio']:.2f}")

    print(f"\n   최대 연속 승:  {s['max_consecutive_wins']}회")
    print(f"   최대 연속 패:  {s['max_consecutive_losses']}회")
    print(f"   평균 보유기간: {s['avg_hold_days']:.1f}일")
    print(f"   최대 낙폭:     {s['portfolio_max_drawdown_pct']:.2f}%")

    # 청산 유형
    eb = result.get("exit_breakdown", {})
    print(f"\n{'─' * 70}")
    print(f"🎯 청산 유형")
    print(f"{'─' * 70}")
    print(f"   ✅ 익절: {eb.get('take_profit', 0)}회 ({eb.get('tp_rate', 0):.1f}%)")
    print(f"   🛑 손절: {eb.get('stop_loss', 0)}회 ({eb.get('sl_rate', 0):.1f}%)")
    print(f"   ⏰ 만료: {eb.get('expired', 0)}회 ({eb.get('exp_rate', 0):.1f}%)")
    print(f"   📉 매도: {eb.get('sell_signal', 0)}회 ({eb.get('sell_rate', 0):.1f}%)")

    # 월별 수익
    monthly = result.get("monthly_returns", [])
    if monthly:
        print(f"\n{'─' * 70}")
        print(f"📅 월별 수익")
        print(f"{'─' * 70}")
        for m in monthly:
            bar_len = max(0, int(abs(m['total_pnl_pct']) / 2))
            bar = "█" * min(bar_len, 30)
            emoji = "🟢" if m['total_pnl_pct'] >= 0 else "🔴"
            print(f"   {m['month']}  {emoji} {m['total_pnl_pct']:+6.2f}%  "
                  f"({m['trades']}거래, 승률 {m['win_rate']:.0f}%)  {bar}")

    # 신호별 성과
    sig_perf = result.get("signal_performance", [])
    if sig_perf:
        print(f"\n{'─' * 70}")
        print(f"📡 진입 신호별 성과 (상위 10)")
        print(f"{'─' * 70}")
        print(f"   {'신호':<20} {'횟수':>5} {'평균수익':>8} {'승률':>7}")
        for sp in sig_perf[:10]:
            emoji = "✅" if sp['avg_pnl'] > 0 else "❌"
            print(f"   {emoji} {sp['signal']:<18} {sp['count']:>5} "
                  f"{sp['avg_pnl']:+7.2f}% {sp['win_rate']:>6.1f}%")

    # 점수 구간별 성과
    score_b = result.get("score_bracket_performance", [])
    if score_b:
        print(f"\n{'─' * 70}")
        print(f"⭐ 기술 점수 구간별 성과")
        print(f"{'─' * 70}")
        print(f"   {'구간':<12} {'거래수':>6} {'평균수익':>8} {'승률':>7}")
        for sb in score_b:
            emoji = "✅" if sb['avg_pnl'] > 0 else "❌"
            print(f"   {emoji} {sb['bracket']:<10} {sb['trades']:>6} "
                  f"{sb['avg_pnl']:+7.2f}% {sb['win_rate']:>6.1f}%")

    # 최고/최악 종목
    best = result.get("best_tickers", [])
    worst = result.get("worst_tickers", [])
    if best or worst:
        print(f"\n{'─' * 70}")
        print(f"🏆 종목별 성과 (2회 이상 거래)")
        print(f"{'─' * 70}")
        if best:
            print(f"   최고:")
            for b in best:
                print(f"     🥇 {b['ticker']}: 평균 {b['avg_pnl']:+.2f}% ({b['trades']}회)")
        if worst:
            print(f"   최악:")
            for w in worst:
                print(f"     🥴 {w['ticker']}: 평균 {w['avg_pnl']:+.2f}% ({w['trades']}회)")

    # 전략 평가
    print(f"\n{'═' * 70}")
    print(f"💡 전략 평가")
    print(f"{'═' * 70}")

    # 자동 평가
    evaluations = []
    if s['win_rate'] >= 55:
        evaluations.append(f"✅ 승률 {s['win_rate']:.1f}% — 양호")
    elif s['win_rate'] >= 45:
        evaluations.append(f"⚠️ 승률 {s['win_rate']:.1f}% — 보통")
    else:
        evaluations.append(f"❌ 승률 {s['win_rate']:.1f}% — 개선 필요")

    if s['profit_factor'] >= 1.5:
        evaluations.append(f"✅ Profit Factor {s['profit_factor']:.2f} — 우수")
    elif s['profit_factor'] >= 1.0:
        evaluations.append(f"⚠️ Profit Factor {s['profit_factor']:.2f} — 수익 가능")
    else:
        evaluations.append(f"❌ Profit Factor {s['profit_factor']:.2f} — 손실 구간")

    if s['sharpe_ratio'] >= 1.0:
        evaluations.append(f"✅ 샤프 비율 {s['sharpe_ratio']:.2f} — 양호한 위험대비수익")
    elif s['sharpe_ratio'] >= 0.5:
        evaluations.append(f"⚠️ 샤프 비율 {s['sharpe_ratio']:.2f} — 보통")
    else:
        evaluations.append(f"❌ 샤프 비율 {s['sharpe_ratio']:.2f} — 위험 대비 수익 부족")

    if s['expected_value_pct'] > 0:
        evaluations.append(f"✅ 기대값 {s['expected_value_pct']:+.2f}%/거래 — 양의 기대값")
    else:
        evaluations.append(f"❌ 기대값 {s['expected_value_pct']:+.2f}%/거래 — 음의 기대값")

    for ev in evaluations:
        print(f"   {ev}")

    print(f"\n{'═' * 70}")
    print(f"⚠️  면책: 과거 성과가 미래 수익을 보장하지 않습니다.")
    print(f"{'═' * 70}\n")


# ══════════════════════════════════════════════════════
#  결과 내보내기
# ══════════════════════════════════════════════════════

def export_results(result: Dict, output_dir: str = "data/backtest"):
    """결과를 JSON + CSV로 저장."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 전체 결과 JSON
    json_path = out / f"backtest_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"결과 저장: {json_path}")

    # 트레이드 CSV
    trades = result.get("trades", [])
    if trades:
        csv_path = out / f"trades_{timestamp}.csv"
        df = pd.DataFrame(trades)
        # signals 리스트를 문자열로 변환
        if "signals" in df.columns:
            df["signals"] = df["signals"].apply(lambda x: ", ".join(x) if isinstance(x, list) else str(x))
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info(f"트레이드 저장: {csv_path}")

    # 요약 텍스트
    summary_path = out / f"summary_{timestamp}.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_report(result)
        f.write(buf.getvalue())
    logger.info(f"요약 저장: {summary_path}")

    return str(json_path)


# ══════════════════════════════════════════════════════
#  CLI 엔트리포인트
# ══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Stock Notify Bot 백테스터")
    parser.add_argument("--days", type=int, default=90, help="백테스트 기간 (거래일, 기본 90)")
    parser.add_argument("--top", type=int, default=5, help="일별 선택 종목 수 (기본 5)")
    parser.add_argument("--pool", type=str, default="nasdaq100", help="종목 풀 (nasdaq100 | sp500)")
    parser.add_argument("--min-score", type=float, default=4.0, help="최소 기술 점수 (기본 4.0)")
    parser.add_argument("--hold", type=int, default=7, help="최대 보유일 (기본 7)")
    parser.add_argument("--sl-mult", type=float, default=2.0, help="손절 ATR 배수 (기본 2.0)")
    parser.add_argument("--tp-mult", type=float, default=4.0, help="익절 ATR 배수 (기본 4.0)")
    parser.add_argument("--export", action="store_true", help="결과를 JSON/CSV로 내보내기")
    args = parser.parse_args()

    engine = BacktestEngine(
        pool=args.pool,
        backtest_days=args.days,
        top_n=args.top,
        min_tech_score=args.min_score,
        max_hold_days=args.hold,
        atr_stop_mult=args.sl_mult,
        atr_tp_mult=args.tp_mult,
    )

    result = engine.run()
    print_report(result)

    if args.export:
        path = export_results(result)
        print(f"\n📁 결과가 저장되었습니다: {path}")


if __name__ == "__main__":
    main()