#!/usr/bin/env python3
"""
백테스트 엔진 검증 테스트

단계별로 핵심 로직을 검증합니다:
  1. 모듈 임포트 검증
  2. 모의 데이터 생성 → 기술적 분석 검증
  3. 단일 트레이드 시뮬레이션 검증
  4. 과열 필터 검증
  5. 소규모 백테스트 실행 (실제 데이터, 짧은 기간)
  6. 리포트 출력 검증
  7. 내보내기 검증

사용법:
  python test_backtest.py           # 전체 테스트
  python test_backtest.py --quick   # 빠른 테스트 (모의 데이터만)
  python test_backtest.py --live    # 실제 데이터 백테스트 (20일)
"""

import argparse
import sys
import os
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

passed = 0
failed = 0
errors = []


def test(name):
    """테스트 데코레이터."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            global passed, failed
            print(f"\n{'─' * 60}")
            print(f"🧪 테스트: {name}")
            print(f"{'─' * 60}")
            try:
                func(*args, **kwargs)
                passed += 1
                print(f"  ✅ PASS")
            except Exception as e:
                failed += 1
                errors.append((name, str(e)))
                print(f"  ❌ FAIL: {e}")
                traceback.print_exc()
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════
#  모의 데이터 생성
# ══════════════════════════════════════════════════════

def generate_mock_ohlcv(ticker: str, days: int = 120, start_price: float = 100.0,
                        trend: str = "up", volatility: float = 0.02) -> pd.DataFrame:
    """
    모의 OHLCV 데이터 생성.
    trend: "up" | "down" | "sideways" | "volatile"
    """
    np.random.seed(hash(ticker) % (2**31))
    dates = pd.bdate_range(end=datetime.now(), periods=days)
    
    prices = [start_price]
    for i in range(1, days):
        if trend == "up":
            drift = 0.001
        elif trend == "down":
            drift = -0.001
        elif trend == "volatile":
            drift = 0.0
            volatility = 0.04
        else:
            drift = 0.0
        
        change = drift + volatility * np.random.randn()
        prices.append(prices[-1] * (1 + change))
    
    prices = np.array(prices)
    highs = prices * (1 + np.abs(np.random.randn(days)) * 0.01)
    lows = prices * (1 - np.abs(np.random.randn(days)) * 0.01)
    volumes = np.random.randint(1_000_000, 50_000_000, days).astype(float)
    
    # 가끔 거래량 급증
    for i in np.random.choice(days, size=days // 10, replace=False):
        volumes[i] *= np.random.uniform(2, 5)
    
    return pd.DataFrame({
        "Date": dates,
        "Open": prices * (1 + np.random.randn(days) * 0.005),
        "High": highs,
        "Low": lows,
        "Close": prices,
        "Volume": volumes,
        "ticker": ticker,
    })


def generate_multi_ticker_data(n_tickers: int = 30, days: int = 120) -> pd.DataFrame:
    """여러 종목의 모의 데이터 생성."""
    tickers = [f"TEST{i:03d}" for i in range(n_tickers)]
    trends = ["up", "down", "sideways", "volatile"] * (n_tickers // 4 + 1)
    
    frames = []
    for i, t in enumerate(tickers):
        df = generate_mock_ohlcv(
            t, days=days,
            start_price=np.random.uniform(20, 300),
            trend=trends[i],
            volatility=np.random.uniform(0.01, 0.035),
        )
        frames.append(df)
    
    return pd.concat(frames, ignore_index=True)


# ══════════════════════════════════════════════════════
#  테스트 1: 모듈 임포트
# ══════════════════════════════════════════════════════

@test("1. 모듈 임포트")
def test_imports():
    from src.backtester import (
        BacktestEngine, Trade, _simulate_trade, _is_overheated,
        _extract_signals, _calc_atr_from_df, print_report, export_results
    )
    from src.backtest_utils import send_backtest_to_discord, ParameterOptimizer
    from src.technical_analyzer import analyze_stock_technical, calculate_technical_score
    print("  모든 모듈 임포트 성공")


# ══════════════════════════════════════════════════════
#  테스트 2: 기술적 분석 (모의 데이터)
# ══════════════════════════════════════════════════════

@test("2. 기술적 분석 (모의 데이터)")
def test_technical_analysis():
    from src.technical_analyzer import analyze_stock_technical, calculate_technical_score
    
    # 상승 추세 데이터
    df_up = generate_mock_ohlcv("UPTEST", days=60, trend="up")
    tech = analyze_stock_technical(df_up)
    
    assert tech is not None, "기술적 분석 결과가 None"
    assert "rsi" in tech, "RSI 없음"
    assert "macd" in tech, "MACD 없음"
    assert "bb_position" in tech, "볼린저밴드 없음"
    assert "pullback" in tech, "눌림목 분석 없음"
    assert "breakout" in tech, "돌파 분석 없음"
    assert "divergence" in tech, "다이버전스 분석 없음"
    assert "risk_reward" in tech, "리스크/리워드 없음"
    
    score = calculate_technical_score(tech)
    assert 0 <= score <= 10, f"점수 범위 이상: {score}"
    
    print(f"  상승 추세 분석:")
    print(f"    RSI: {tech['rsi']:.1f}")
    print(f"    MACD: {tech['macd']:.4f}")
    print(f"    BB Position: {tech['bb_position']:.2f}")
    print(f"    Golden Cross: {tech['golden_cross']}")
    print(f"    MA Alignment: {tech['ma_alignment']}")
    print(f"    Tech Score: {score:.2f}")
    
    # 하락 추세 데이터
    df_down = generate_mock_ohlcv("DNTEST", days=60, trend="down")
    tech_down = analyze_stock_technical(df_down)
    score_down = calculate_technical_score(tech_down)
    
    print(f"  하락 추세 분석:")
    print(f"    RSI: {tech_down['rsi']:.1f}")
    print(f"    Tech Score: {score_down:.2f}")
    
    # 상승 추세 점수가 하락보다 높아야 함 (일반적으로)
    print(f"  상승({score:.2f}) vs 하락({score_down:.2f})")


# ══════════════════════════════════════════════════════
#  테스트 3: Trade 시뮬레이션
# ══════════════════════════════════════════════════════

@test("3. Trade 시뮬레이션 — 익절")
def test_trade_take_profit():
    from src.backtester import Trade, _simulate_trade
    
    trade = Trade(
        ticker="TEST", entry_date="2025-01-01",
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        tech_score=7.0, signals=["골든크로스"]
    )
    
    # 이틀 후 익절 가격 도달
    future = pd.DataFrame({
        "Date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
        "Open": [101, 105, 108],
        "High": [103, 111, 112],  # 둘째날 고가 111 → TP 110 도달
        "Low":  [99, 104, 107],
        "Close": [102, 109, 111],
    })
    
    result = _simulate_trade(trade, future)
    
    assert result.status == "take_profit", f"익절 예상인데 {result.status}"
    assert result.exit_price == 110.0, f"TP 가격 불일치: {result.exit_price}"
    assert result.hold_days == 2, f"보유일 불일치: {result.hold_days}"
    assert result.pnl_pct is not None and result.pnl_pct > 0, f"손익 이상: {result.pnl_pct}"
    
    print(f"  진입: ${trade.entry_price} → 청산: ${result.exit_price}")
    print(f"  상태: {result.status}, 보유: {result.hold_days}일")
    print(f"  손익: {result.pnl_pct:+.2f}%")


@test("4. Trade 시뮬레이션 — 손절")
def test_trade_stop_loss():
    from src.backtester import Trade, _simulate_trade
    
    trade = Trade(
        ticker="TEST", entry_date="2025-01-01",
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        tech_score=5.0, signals=["MACD상향"]
    )
    
    # 첫날 손절 가격 도달
    future = pd.DataFrame({
        "Date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
        "Open": [99, 96],
        "High": [100, 97],
        "Low":  [94, 93],  # 첫날 저가 94 → SL 95 도달
        "Close": [96, 94],
    })
    
    result = _simulate_trade(trade, future)
    
    assert result.status == "stop_loss", f"손절 예상인데 {result.status}"
    assert result.exit_price == 95.0, f"SL 가격 불일치: {result.exit_price}"
    assert result.hold_days == 1, f"보유일 불일치: {result.hold_days}"
    assert result.pnl_pct is not None and result.pnl_pct < 0, f"손익 이상: {result.pnl_pct}"
    
    print(f"  진입: ${trade.entry_price} → 청산: ${result.exit_price}")
    print(f"  상태: {result.status}, 손익: {result.pnl_pct:+.2f}%")


@test("5. Trade 시뮬레이션 — 만료")
def test_trade_expired():
    from src.backtester import Trade, _simulate_trade
    
    trade = Trade(
        ticker="TEST", entry_date="2025-01-01",
        entry_price=100.0, stop_loss=90.0, take_profit=120.0,
        tech_score=6.0, signals=["이평정배열"]
    )
    
    # SL/TP 모두 도달하지 않고 7일 경과
    future = pd.DataFrame({
        "Date": pd.to_datetime([f"2025-01-{d:02d}" for d in range(2, 12)]),
        "Open":  [100.5, 101, 100, 99.5, 100.2, 101, 100.8, 99.8, 100.3, 100.1],
        "High":  [102, 102, 101.5, 101, 102, 102.5, 102, 101, 101.5, 101],
        "Low":   [99, 99.5, 98.5, 98, 99, 99.5, 99, 98.5, 99, 98.8],
        "Close": [101, 100.5, 99.5, 100, 101, 100.8, 100, 99.5, 100.2, 100],
    })
    
    result = _simulate_trade(trade, future)
    
    assert result.status == "expired", f"만료 예상인데 {result.status}"
    assert result.hold_days == 7, f"보유일 불일치: {result.hold_days} (예상 7)"
    
    print(f"  진입: ${trade.entry_price} → 청산: ${result.exit_price}")
    print(f"  상태: {result.status}, 보유: {result.hold_days}일")
    print(f"  손익: {result.pnl_pct:+.2f}%")


# ══════════════════════════════════════════════════════
#  테스트 4: 과열 필터
# ══════════════════════════════════════════════════════

@test("6. 과열 필터")
def test_overheated():
    from src.backtester import _is_overheated
    
    # 과열 종목 (RSI 높고 + 연속 상승)
    overheated = {
        "rsi": 80,
        "consecutive_up": 6,
        "bb_position": 0.97,
        "ma5_deviation": 15,
        "volume_ratio": 4,
        "divergence": {"bearish_divergence": True},
    }
    assert _is_overheated(overheated, 6.0) == True, "과열 종목이 통과함"
    print("  과열 종목 → 필터링 ✅")
    
    # 정상 종목
    normal = {
        "rsi": 50,
        "consecutive_up": 2,
        "bb_position": 0.5,
        "ma5_deviation": 3,
        "volume_ratio": 1.5,
        "divergence": {"bearish_divergence": False},
    }
    assert _is_overheated(normal, 1.0) == False, "정상 종목이 필터링됨"
    print("  정상 종목 → 통과 ✅")


# ══════════════════════════════════════════════════════
#  테스트 5: 신호 추출
# ══════════════════════════════════════════════════════

@test("7. 신호 추출")
def test_extract_signals():
    from src.backtester import _extract_signals
    
    tech = {
        "golden_cross": True,
        "macd_cross_up": True,
        "ma_alignment": True,
        "bullish_volume": True,
        "volume_ratio": 2.5,
        "stoch_cross_up": False,
        "bb_squeeze": False,
        "pullback": {"pullback_to_ma20": True, "pullback_to_ma50": False, "pullback_to_bb_lower": False},
        "breakout": {"breakout_detected": False},
        "divergence": {"bullish_divergence": False},
    }
    
    signals = _extract_signals(tech)
    assert len(signals) > 0, "신호가 없음"
    assert "골든크로스" in signals, "골든크로스 누락"
    assert "20MA눌림목" in signals, "눌림목 누락"
    
    print(f"  추출된 신호: {signals}")


# ══════════════════════════════════════════════════════
#  테스트 6: ATR 계산
# ══════════════════════════════════════════════════════

@test("8. ATR 계산")
def test_atr():
    from src.backtester import _calc_atr_from_df
    
    df = generate_mock_ohlcv("ATRTEST", days=30, volatility=0.02)
    atr = _calc_atr_from_df(df)
    
    assert atr is not None, "ATR이 None"
    assert atr > 0, f"ATR이 0 이하: {atr}"
    
    # ATR은 대략 가격의 1~5% 범위여야 함
    avg_price = df["Close"].mean()
    atr_pct = atr / avg_price * 100
    assert 0.1 < atr_pct < 20, f"ATR 비율 이상: {atr_pct:.2f}%"
    
    print(f"  ATR: {atr:.4f} (가격 대비 {atr_pct:.2f}%)")


# ══════════════════════════════════════════════════════
#  테스트 7: 리포트 출력
# ══════════════════════════════════════════════════════

@test("9. 리포트 출력 (모의 결과)")
def test_report():
    from src.backtester import print_report
    
    # 모의 결과
    mock_result = {
        "config": {
            "pool": "test", "backtest_days": 30, "top_n": 3,
            "min_tech_score": 4.0, "max_hold_days": 7,
            "atr_stop_mult": 2.0, "atr_tp_mult": 4.0,
            "commission_pct": 0, "slippage_pct": 0.05,
        },
        "summary": {
            "total_trades": 50,
            "win_rate": 56.0,
            "avg_pnl_pct": 0.85,
            "median_pnl_pct": 0.42,
            "total_pnl_pct": 42.5,
            "std_pnl_pct": 3.2,
            "avg_win_pct": 4.1,
            "avg_loss_pct": -3.3,
            "profit_factor": 1.55,
            "expected_value_pct": 0.85,
            "sharpe_ratio": 1.12,
            "max_consecutive_wins": 5,
            "max_consecutive_losses": 3,
            "avg_hold_days": 4.2,
            "portfolio_max_drawdown_pct": 8.5,
        },
        "exit_breakdown": {
            "take_profit": 20, "stop_loss": 15, "expired": 15,
            "tp_rate": 40.0, "sl_rate": 30.0, "exp_rate": 30.0,
        },
        "monthly_returns": [
            {"month": "2025-01", "trades": 25, "total_pnl_pct": 18.5, "win_rate": 60.0},
            {"month": "2025-02", "trades": 25, "total_pnl_pct": 24.0, "win_rate": 52.0},
        ],
        "signal_performance": [
            {"signal": "20MA눌림목", "count": 15, "avg_pnl": 1.2, "win_rate": 62.0},
            {"signal": "돌파", "count": 10, "avg_pnl": 0.9, "win_rate": 58.0},
            {"signal": "골든크로스", "count": 8, "avg_pnl": 0.5, "win_rate": 50.0},
        ],
        "score_bracket_performance": [
            {"bracket": "4.0~5.0", "trades": 10, "avg_pnl": 0.3, "win_rate": 50.0},
            {"bracket": "5.0~6.0", "trades": 15, "avg_pnl": 0.7, "win_rate": 53.0},
            {"bracket": "6.0~7.0", "trades": 15, "avg_pnl": 1.1, "win_rate": 60.0},
            {"bracket": "7.0~8.0", "trades": 8, "avg_pnl": 1.5, "win_rate": 67.0},
            {"bracket": "8.0+", "trades": 2, "avg_pnl": 2.1, "win_rate": 100.0},
        ],
        "top_traded_tickers": [{"ticker": "NVDA", "trades": 5}],
        "best_tickers": [{"ticker": "NVDA", "avg_pnl": 2.3, "trades": 5}],
        "worst_tickers": [{"ticker": "INTC", "avg_pnl": -1.8, "trades": 3}],
        "trades": [],
    }
    
    print_report(mock_result)
    print("  리포트 출력 완료 ✅")


# ══════════════════════════════════════════════════════
#  테스트 8: 내보내기
# ══════════════════════════════════════════════════════

@test("10. 내보내기 (JSON/CSV)")
def test_export():
    from src.backtester import export_results
    
    mock_result = {
        "config": {"pool": "test"},
        "summary": {"total_trades": 2},
        "trades": [
            {
                "ticker": "AAPL", "entry_date": "2025-01-01",
                "entry_price": 150.0, "stop_loss": 145.0, "take_profit": 160.0,
                "tech_score": 7.5, "signals": ["골든크로스", "MACD상향"],
                "exit_date": "2025-01-03", "exit_price": 160.0,
                "pnl_pct": 6.62, "status": "take_profit",
                "hold_days": 2, "max_drawdown_pct": -0.5, "max_favorable_pct": 6.8,
            },
            {
                "ticker": "TSLA", "entry_date": "2025-01-01",
                "entry_price": 250.0, "stop_loss": 240.0, "take_profit": 270.0,
                "tech_score": 6.2, "signals": ["20MA눌림목"],
                "exit_date": "2025-01-02", "exit_price": 240.0,
                "pnl_pct": -4.05, "status": "stop_loss",
                "hold_days": 1, "max_drawdown_pct": -4.2, "max_favorable_pct": 0.8,
            },
        ],
    }
    
    output_dir = "data/backtest_test"
    path = export_results(mock_result, output_dir=output_dir)
    
    assert Path(path).exists(), f"JSON 파일 생성 안됨: {path}"
    
    # CSV 확인
    csv_files = list(Path(output_dir).glob("trades_*.csv"))
    assert len(csv_files) > 0, "CSV 파일 생성 안됨"
    
    df = pd.read_csv(csv_files[0])
    assert len(df) == 2, f"CSV 행 수 불일치: {len(df)}"
    assert "ticker" in df.columns, "ticker 컬럼 없음"
    
    print(f"  JSON: {path}")
    print(f"  CSV: {csv_files[0]}")
    print(f"  CSV 행: {len(df)}, 컬럼: {list(df.columns)}")
    
    # 정리
    import shutil
    shutil.rmtree(output_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════
#  테스트 9: 실제 데이터 미니 백테스트
# ══════════════════════════════════════════════════════

@test("11. 실제 데이터 미니 백테스트 (20일)")
def test_live_mini():
    from src.backtester import BacktestEngine, print_report
    
    engine = BacktestEngine(
        pool="nasdaq100",
        backtest_days=20,
        top_n=3,
        min_tech_score=4.0,
    )
    
    result = engine.run()
    s = result.get("summary", {})
    
    total = s.get("total_trades", 0)
    print(f"  총 거래: {total}")
    
    if total == 0:
        print("  ⚠️ 거래가 0건 — 데이터 부족 또는 조건 미충족 (기대 가능)")
        return
    
    assert s.get("win_rate") is not None, "승률 없음"
    assert s.get("avg_pnl_pct") is not None, "평균 손익 없음"
    assert s.get("profit_factor") is not None, "PF 없음"
    
    print(f"  승률: {s['win_rate']:.1f}%")
    print(f"  평균 수익: {s['avg_pnl_pct']:+.2f}%")
    print(f"  PF: {s['profit_factor']:.2f}")
    print(f"  샤프: {s['sharpe_ratio']:.2f}")
    
    # 상세 리포트
    print_report(result)
    
    # 트레이드 검증
    trades = result.get("trades", [])
    for t in trades[:3]:
        assert t["entry_price"] > 0, f"진입가 이상: {t}"
        assert t["exit_price"] is not None, f"청산가 없음: {t}"
        assert t["status"] in ("take_profit", "stop_loss", "expired", "no_data"), f"상태 이상: {t}"
        print(f"    {t['ticker']}: {t['entry_price']:.2f} → {t['exit_price']:.2f} "
              f"({t['pnl_pct']:+.2f}%, {t['status']})")


# ══════════════════════════════════════════════════════
#  테스트 10: 파라미터 최적화 (미니)
# ══════════════════════════════════════════════════════

@test("12. 파라미터 최적화 (미니, 4조합)")
def test_optimizer_mini():
    from src.backtest_utils import ParameterOptimizer
    
    mini_grid = {
        "top_n": [3, 5],
        "atr_stop_mult": [1.5, 2.0],
    }
    
    optimizer = ParameterOptimizer(
        pool="nasdaq100",
        backtest_days=15,
        param_grid=mini_grid,
    )
    
    results = optimizer.run()
    assert len(results) > 0, "최적화 결과 없음"
    
    optimizer.print_top(5)
    
    best = results[0]
    print(f"  최적 파라미터: {best['params']}")
    print(f"  점수: {best['score']:.2f}")


# ══════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════

def main():
    global passed, failed
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="빠른 테스트 (모의 데이터만)")
    parser.add_argument("--live", action="store_true", help="실제 데이터 테스트 포함")
    args = parser.parse_args()
    
    print("=" * 60)
    print("🧪 백테스트 엔진 검증 테스트")
    print("=" * 60)
    
    # 항상 실행하는 테스트 (모의 데이터, 네트워크 불필요)
    test_imports()
    test_technical_analysis()
    test_trade_take_profit()
    test_trade_stop_loss()
    test_trade_expired()
    test_overheated()
    test_extract_signals()
    test_atr()
    test_report()
    test_export()
    
    # 실제 데이터 테스트 (네트워크 필요)
    if not args.quick:
        print(f"\n{'=' * 60}")
        print("🌐 실제 데이터 테스트 (yfinance 다운로드)")
        print(f"{'=' * 60}")
        test_live_mini()
        
        if args.live:
            test_optimizer_mini()
    
    # 결과 요약
    print(f"\n{'═' * 60}")
    print(f"📊 테스트 결과: ✅ {passed} PASS / ❌ {failed} FAIL")
    print(f"{'═' * 60}")
    
    if errors:
        print("\n실패 목록:")
        for name, err in errors:
            print(f"  ❌ {name}: {err}")
    
    if failed == 0:
        print("\n🎉 모든 테스트 통과!")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
