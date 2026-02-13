#!/usr/bin/env python3
"""
자기 학습(Self-Tuning) 시스템 통합 검증 테스트

단계별 검증:
  1. 모듈 임포트 검증
  2. 시장 레짐 감지 (엣지 케이스 포함)
  3. 신호 가중치 최적화 (조정 방향, 범위 제한, 샘플 보호)
  4. 파라미터 자동 조정 (블렌딩, 미세조정, 범위 클램핑)
  5. 안전 장치 (성과 열화 → 보수적 전환)
  6. signal_weights.json ↔ technical_analyzer 연동
  7. strategy_state.json ↔ main.py 연동
  8. 설정 파일 저장/로드 사이클
  9. Discord 알림 포맷 검증
  10. 실제 데이터 통합 테스트 (백테스트 → 자기 학습 → 파라미터 적용)

사용법:
  python test_self_tuning.py             # 전체 테스트
  python test_self_tuning.py --quick     # 모의 데이터만 (네트워크 불필요)
  python test_self_tuning.py --live      # 실제 데이터 통합 테스트
"""

import argparse
import json
import sys
import os
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

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
#  테스트 1: 모듈 임포트
# ══════════════════════════════════════════════════════

@test("1. 모듈 임포트")
def test_imports():
    from src.self_tuning import (
        MarketRegimeDetector,
        SignalWeightOptimizer,
        ParameterTuner,
        SafetyGuard,
        SelfTuningEngine,
        send_tuning_report_to_discord,
        PARAM_BOUNDS, REGIME_PRESETS, SAFETY_THRESHOLDS,
        DEFAULT_SIGNAL_KEYS, WEIGHT_BOUNDS,
        _load_json, _save_json, _clamp,
    )
    from src.backtester import BacktestEngine, print_report
    from src.technical_analyzer import calculate_technical_score, _load_signal_weights
    print("  모든 모듈 임포트 성공")
    print(f"  PARAM_BOUNDS 키: {list(PARAM_BOUNDS.keys())}")
    print(f"  레짐 프리셋: {list(REGIME_PRESETS.keys())}")
    print(f"  기본 신호 키: {len(DEFAULT_SIGNAL_KEYS)}개")


# ══════════════════════════════════════════════════════
#  테스트 2: 시장 레짐 감지
# ══════════════════════════════════════════════════════

@test("2. 시장 레짐 감지 — 기본 케이스")
def test_regime_basic():
    from src.self_tuning import MarketRegimeDetector

    detector = MarketRegimeDetector()

    # 강세장
    bt_bullish = {
        "monthly_returns": [
            {"month": "2025-01", "total_pnl_pct": 8, "win_rate": 60, "trades": 30},
            {"month": "2025-02", "total_pnl_pct": 10, "win_rate": 62, "trades": 25},
            {"month": "2025-03", "total_pnl_pct": 12, "win_rate": 58, "trades": 28},
        ],
        "summary": {"portfolio_max_drawdown_pct": 5},
    }
    regime, conf = detector.detect(bt_bullish)
    assert regime == "bullish", f"강세장 판정 실패: {regime}"
    assert conf > 0.5, f"강세장 신뢰도 낮음: {conf}"
    print(f"  강세장: {regime} (신뢰도 {conf:.0%})")

    # 약세장
    bt_bearish = {
        "monthly_returns": [
            {"month": "2025-01", "total_pnl_pct": -8, "win_rate": 38, "trades": 30},
            {"month": "2025-02", "total_pnl_pct": -10, "win_rate": 35, "trades": 25},
            {"month": "2025-03", "total_pnl_pct": -12, "win_rate": 32, "trades": 28},
        ],
        "summary": {"portfolio_max_drawdown_pct": 20},
    }
    regime, conf = detector.detect(bt_bearish)
    assert regime == "bearish", f"약세장 판정 실패: {regime}"
    print(f"  약세장: {regime} (신뢰도 {conf:.0%})")

    # 횡보장
    bt_sideways = {
        "monthly_returns": [
            {"month": "2025-01", "total_pnl_pct": 1, "win_rate": 51, "trades": 30},
            {"month": "2025-02", "total_pnl_pct": -1, "win_rate": 49, "trades": 25},
            {"month": "2025-03", "total_pnl_pct": 0.5, "win_rate": 50, "trades": 28},
        ],
        "summary": {"portfolio_max_drawdown_pct": 8},
    }
    regime, conf = detector.detect(bt_sideways)
    assert regime == "sideways", f"횡보장 판정 실패: {regime}"
    print(f"  횡보장: {regime} (신뢰도 {conf:.0%})")


@test("3. 시장 레짐 감지 — 엣지 케이스")
def test_regime_edge():
    from src.self_tuning import MarketRegimeDetector

    detector = MarketRegimeDetector()

    # 데이터 부족 (월 1개)
    regime, conf = detector.detect({
        "monthly_returns": [{"total_pnl_pct": 10, "win_rate": 70}],
        "summary": {},
    })
    assert regime == "sideways" and conf == 0.3, f"데이터부족 실패: {regime}, {conf}"
    print(f"  데이터 부족 → {regime} ({conf:.0%})")

    # 빈 데이터
    regime, conf = detector.detect({"monthly_returns": [], "summary": {}})
    assert regime == "sideways", f"빈 데이터 실패: {regime}"
    print(f"  빈 데이터 → {regime} ({conf:.0%})")

    # MDD 매우 높음 (하락장 강화)
    regime, conf = detector.detect({
        "monthly_returns": [
            {"total_pnl_pct": -3, "win_rate": 44, "trades": 30},
            {"total_pnl_pct": -2, "win_rate": 46, "trades": 25},
            {"month": "2025-03", "total_pnl_pct": -4, "win_rate": 42, "trades": 28},
        ],
        "summary": {"portfolio_max_drawdown_pct": 25},
    })
    assert regime == "bearish", f"고MDD 판정 실패: {regime}"
    print(f"  고MDD → {regime} ({conf:.0%})")


# ══════════════════════════════════════════════════════
#  테스트 3: 신호 가중치 최적화
# ══════════════════════════════════════════════════════

@test("4. 신호 가중치 — 성과 기반 조정")
def test_signal_weights_basic():
    from src.self_tuning import SignalWeightOptimizer, SIGNAL_WEIGHTS_PATH

    # 임시 가중치 파일 생성
    test_weights = {"pullback_score": 1.0, "macd_cross_up": 1.0, "bullish_volume": 1.0}
    SIGNAL_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNAL_WEIGHTS_PATH, "w") as f:
        json.dump(test_weights, f)

    optimizer = SignalWeightOptimizer()

    # 좋은 신호(눌림목) + 나쁜 신호(MACD)
    bt_result = {
        "signal_performance": [
            {"signal": "20MA눌림목", "count": 30, "avg_pnl": 2.5, "win_rate": 65},
            {"signal": "MACD상향", "count": 20, "avg_pnl": -1.5, "win_rate": 38},
            {"signal": "거래량2.3x", "count": 15, "avg_pnl": 1.0, "win_rate": 55},
        ],
    }

    new_weights, changes = optimizer.optimize(bt_result)

    # 눌림목 가중치 올라야 함
    assert new_weights.get("pullback_score", 1.0) > 1.0, \
        f"눌림목 가중치 안 올라감: {new_weights.get('pullback_score')}"
    print(f"  눌림목: 1.0 → {new_weights.get('pullback_score', '?')} ↑")

    # MACD 가중치 내려야 함
    assert new_weights.get("macd_cross_up", 1.0) < 1.0, \
        f"MACD 가중치 안 내려감: {new_weights.get('macd_cross_up')}"
    print(f"  MACD:   1.0 → {new_weights.get('macd_cross_up', '?')} ↓")

    # 거래량 가중치 약간 올라야 함
    vol_w = new_weights.get("bullish_volume", 1.0)
    assert vol_w >= 1.0, f"거래량 가중치 내려감: {vol_w}"
    print(f"  거래량: 1.0 → {vol_w} ↑")

    # 변경 기록 있어야 함
    assert len(changes) >= 2, f"변경 기록 부족: {len(changes)}"
    print(f"  변경 기록: {len(changes)}개")


@test("5. 신호 가중치 — 범위 제한 & 샘플 보호")
def test_signal_weights_safety():
    from src.self_tuning import SignalWeightOptimizer, SIGNAL_WEIGHTS_PATH, WEIGHT_BOUNDS

    # 이미 높은 가중치
    high_weights = {"pullback_score": 2.4, "macd_cross_up": 0.35}
    with open(SIGNAL_WEIGHTS_PATH, "w") as f:
        json.dump(high_weights, f)

    optimizer = SignalWeightOptimizer()

    # 매우 좋은 성과 → 가중치가 상한(2.5)를 넘으면 안 됨
    bt = {"signal_performance": [
        {"signal": "20MA눌림목", "count": 50, "avg_pnl": 5.0, "win_rate": 90},
    ]}
    new_w, _ = optimizer.optimize(bt)
    assert new_w.get("pullback_score", 999) <= WEIGHT_BOUNDS["max"], \
        f"가중치 상한 초과: {new_w.get('pullback_score')}"
    print(f"  상한 제한: {new_w.get('pullback_score', '?')} ≤ {WEIGHT_BOUNDS['max']}")

    # 샘플 부족 (3회) → 변경 없어야 함
    bt_small = {"signal_performance": [
        {"signal": "골든크로스", "count": 3, "avg_pnl": 10.0, "win_rate": 100},
    ]}
    optimizer2 = SignalWeightOptimizer()
    optimizer2.current_weights = {"golden_cross": 1.0}
    new_w2, ch2 = optimizer2.optimize(bt_small)
    assert "golden_cross" not in ch2, f"샘플부족인데 변경됨: {ch2}"
    print(f"  샘플 부족(3회) → 변경 없음 ✅")


@test("6. 신호 가중치 — 빈 데이터 처리")
def test_signal_weights_empty():
    from src.self_tuning import SignalWeightOptimizer

    optimizer = SignalWeightOptimizer()
    optimizer.current_weights = {"pullback_score": 1.2}

    # 빈 신호 데이터
    new_w, ch = optimizer.optimize({"signal_performance": []})
    assert new_w == optimizer.current_weights, "빈 데이터에서 가중치 변경됨"
    assert len(ch) == 0, f"빈 데이터에서 변경 기록 있음: {ch}"
    print(f"  빈 데이터 → 가중치 유지 ✅")

    # 키 자체가 없는 경우
    new_w2, ch2 = optimizer.optimize({})
    assert len(ch2) == 0
    print(f"  키 없음 → 가중치 유지 ✅")


# ══════════════════════════════════════════════════════
#  테스트 4: 파라미터 자동 조정
# ══════════════════════════════════════════════════════

@test("7. 파라미터 — 레짐 블렌딩")
def test_param_blending():
    from src.self_tuning import ParameterTuner, REGIME_PRESETS, STRATEGY_STATE_PATH

    # 초기 상태 파일 생성
    STRATEGY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_params = {"top_n": 5, "min_tech_score": 4.0, "atr_stop_mult": 2.0,
                   "atr_tp_mult": 4.0, "max_hold_days": 7}
    with open(STRATEGY_STATE_PATH, "w") as f:
        json.dump({"current_params": init_params}, f)

    tuner = ParameterTuner()

    # 강세장 → min_tech_score 낮아져야 함
    bt = {
        "summary": {"total_trades": 50, "win_rate": 55, "profit_factor": 1.3,
                     "avg_pnl_pct": 0.8, "avg_win_pct": 3.5, "avg_loss_pct": -2.8,
                     "avg_hold_days": 4.5, "portfolio_max_drawdown_pct": 8,
                     "sharpe_ratio": 1.0, "expected_value_pct": 0.5,
                     "max_consecutive_losses": 3},
        "exit_breakdown": {"tp_rate": 35, "sl_rate": 30, "exp_rate": 35},
    }

    new_params, changes = tuner.tune(bt, regime="bullish", regime_confidence=0.8)

    bullish_preset = REGIME_PRESETS["bullish"]["min_tech_score"]
    assert new_params["min_tech_score"] <= init_params["min_tech_score"], \
        f"강세장인데 기준 안 낮아짐: {new_params['min_tech_score']}"
    print(f"  강세장: min_score {init_params['min_tech_score']} → {new_params['min_tech_score']}")

    # 약세장 → min_tech_score 높아져야 함
    tuner2 = ParameterTuner()
    tuner2.current_params = dict(init_params)
    new_p2, ch2 = tuner2.tune(bt, regime="bearish", regime_confidence=0.8)
    assert new_p2["min_tech_score"] >= init_params["min_tech_score"], \
        f"약세장인데 기준 안 높아짐: {new_p2['min_tech_score']}"
    print(f"  약세장: min_score {init_params['min_tech_score']} → {new_p2['min_tech_score']}")


@test("8. 파라미터 — 성과 기반 미세조정")
def test_param_performance_adjust():
    from src.self_tuning import ParameterTuner, STRATEGY_STATE_PATH

    init = {"top_n": 5, "min_tech_score": 4.0, "atr_stop_mult": 2.0,
            "atr_tp_mult": 4.0, "max_hold_days": 7}
    with open(STRATEGY_STATE_PATH, "w") as f:
        json.dump({"current_params": init}, f)

    # 손절 과다 (sl_rate 50%)
    tuner = ParameterTuner()
    bt_sl = {
        "summary": {"total_trades": 50, "win_rate": 45, "profit_factor": 1.0,
                     "avg_pnl_pct": 0.1, "avg_win_pct": 3.0, "avg_loss_pct": -3.2,
                     "avg_hold_days": 3, "portfolio_max_drawdown_pct": 12,
                     "sharpe_ratio": 0.5, "expected_value_pct": 0.1,
                     "max_consecutive_losses": 4},
        "exit_breakdown": {"tp_rate": 25, "sl_rate": 50, "exp_rate": 25},
    }
    new_p, ch = tuner.tune(bt_sl, "sideways", 0.5)
    assert new_p["atr_stop_mult"] > init["atr_stop_mult"], \
        f"손절과다인데 SL 안 늘어남: {new_p['atr_stop_mult']}"
    print(f"  손절과다: SL {init['atr_stop_mult']} → {new_p['atr_stop_mult']}")

    # 만료 과다 (exp_rate 55%)
    tuner2 = ParameterTuner()
    tuner2.current_params = dict(init)
    bt_exp = {
        "summary": {"total_trades": 50, "win_rate": 48, "profit_factor": 1.0,
                     "avg_pnl_pct": 0.05, "avg_win_pct": 2.5, "avg_loss_pct": -2.6,
                     "avg_hold_days": 6.5, "portfolio_max_drawdown_pct": 10,
                     "sharpe_ratio": 0.3, "expected_value_pct": 0.05,
                     "max_consecutive_losses": 5},
        "exit_breakdown": {"tp_rate": 20, "sl_rate": 25, "exp_rate": 55},
    }
    new_p2, ch2 = tuner2.tune(bt_exp, "sideways", 0.5)
    assert new_p2["atr_tp_mult"] < init["atr_tp_mult"], \
        f"만료과다인데 TP 안 줄어듦: {new_p2['atr_tp_mult']}"
    print(f"  만료과다: TP {init['atr_tp_mult']} → {new_p2['atr_tp_mult']}")

    # 높은 MDD → 보수적
    tuner3 = ParameterTuner()
    tuner3.current_params = dict(init)
    bt_dd = {
        "summary": {"total_trades": 50, "win_rate": 50, "profit_factor": 1.1,
                     "avg_pnl_pct": 0.3, "avg_win_pct": 3.0, "avg_loss_pct": -2.8,
                     "avg_hold_days": 5, "portfolio_max_drawdown_pct": 25,
                     "sharpe_ratio": 0.5, "expected_value_pct": 0.3,
                     "max_consecutive_losses": 5},
        "exit_breakdown": {"tp_rate": 30, "sl_rate": 35, "exp_rate": 35},
    }
    new_p3, ch3 = tuner3.tune(bt_dd, "sideways", 0.5)
    assert new_p3["top_n"] < init["top_n"], \
        f"고MDD인데 종목수 안 줄어듦: {new_p3['top_n']}"
    print(f"  고MDD: top_n {init['top_n']} → {new_p3['top_n']}")


@test("9. 파라미터 — 범위 클램핑")
def test_param_clamping():
    from src.self_tuning import PARAM_BOUNDS, _clamp

    # 극단값이 범위 내로 클램핑되는지 확인
    for key, bounds in PARAM_BOUNDS.items():
        lo, hi = bounds["min"], bounds["max"]
        assert _clamp(-999, lo, hi) == lo, f"{key}: 하한 클램핑 실패"
        assert _clamp(999, lo, hi) == hi, f"{key}: 상한 클램핑 실패"
        mid = (lo + hi) / 2
        assert _clamp(mid, lo, hi) == mid, f"{key}: 중간값 변경됨"

    print(f"  모든 파라미터 범위 클램핑 정상 ({len(PARAM_BOUNDS)}개)")


@test("10. 파라미터 — 거래수 부족 시 스킵")
def test_param_insufficient_trades():
    from src.self_tuning import ParameterTuner, STRATEGY_STATE_PATH

    init = {"top_n": 5, "min_tech_score": 4.0, "atr_stop_mult": 2.0,
            "atr_tp_mult": 4.0, "max_hold_days": 7}
    with open(STRATEGY_STATE_PATH, "w") as f:
        json.dump({"current_params": init}, f)

    tuner = ParameterTuner()
    bt = {
        "summary": {"total_trades": 5, "win_rate": 80},  # 5거래 → 부족
        "exit_breakdown": {},
    }
    new_p, ch = tuner.tune(bt, "bullish", 0.9)
    assert ch.get("skipped") == True, f"거래부족인데 조정 실행됨: {ch}"
    assert new_p == init, "거래부족인데 파라미터 변경됨"
    print(f"  거래 5회 → 스킵 ✅")


# ══════════════════════════════════════════════════════
#  테스트 5: 안전 장치
# ══════════════════════════════════════════════════════

@test("11. 안전 장치 — 정상/위험/부족")
def test_safety_guard():
    from src.self_tuning import SafetyGuard

    guard = SafetyGuard()

    # 정상
    ok, msg = guard.check({"win_rate": 55, "profit_factor": 1.5,
                           "max_consecutive_losses": 3, "total_trades": 50})
    assert ok == True, f"정상인데 위험 판정: {msg}"
    print(f"  정상 → 안전: {msg}")

    # 승률 열화
    ok, msg = guard.check({"win_rate": 35, "profit_factor": 1.0,
                           "max_consecutive_losses": 3, "total_trades": 50})
    assert ok == False, f"승률35%인데 안전 판정"
    print(f"  승률 열화 → 위험: {msg}")

    # PF 열화
    ok, msg = guard.check({"win_rate": 50, "profit_factor": 0.6,
                           "max_consecutive_losses": 3, "total_trades": 50})
    assert ok == False, f"PF 0.6인데 안전 판정"
    print(f"  PF 열화 → 위험: {msg}")

    # 연속 패배
    ok, msg = guard.check({"win_rate": 50, "profit_factor": 1.0,
                           "max_consecutive_losses": 10, "total_trades": 50})
    assert ok == False, f"연속패배10인데 안전 판정"
    print(f"  연속 패배 → 위험: {msg}")

    # 복합 열화
    ok, msg = guard.check({"win_rate": 30, "profit_factor": 0.5,
                           "max_consecutive_losses": 12, "total_trades": 100})
    assert ok == False
    print(f"  복합 열화 → 위험: {msg}")

    # 거래 부족
    ok, msg = guard.check({"win_rate": 10, "profit_factor": 0.1, "total_trades": 5})
    assert ok == True, f"거래부족인데 위험 판정"
    print(f"  거래 부족 → 보류: {msg}")


@test("12. 안전 장치 — 보수적 파라미터")
def test_safety_conservative():
    from src.self_tuning import SafetyGuard, PARAM_BOUNDS

    guard = SafetyGuard()
    conservative = guard.get_conservative_params()

    # 보수적 파라미터가 범위 내인지 확인
    for key, val in conservative.items():
        bounds = PARAM_BOUNDS.get(key, {})
        lo = bounds.get("min", val)
        hi = bounds.get("max", val)
        assert lo <= val <= hi, f"{key}: {val} 범위 초과 ({lo}~{hi})"

    # 보수적이면 min_tech_score가 높아야 함
    assert conservative["min_tech_score"] >= 5.0
    assert conservative["top_n"] <= 4
    assert conservative["atr_stop_mult"] <= 2.0
    print(f"  보수적 파라미터: {conservative}")


# ══════════════════════════════════════════════════════
#  테스트 6: technical_analyzer 연동
# ══════════════════════════════════════════════════════

@test("13. signal_weights.json ↔ technical_analyzer 연동")
def test_weights_integration():
    from src.self_tuning import SIGNAL_WEIGHTS_PATH

    # 가중치 파일 생성 (눌림목 2배, MACD 0.5배)
    custom_weights = {
        "pullback_score": 2.0,
        "macd_cross_up": 0.5,
        "golden_cross": 1.5,
    }
    SIGNAL_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNAL_WEIGHTS_PATH, "w") as f:
        json.dump(custom_weights, f)

    # technical_analyzer가 가중치를 로드하는지 확인
    from src.technical_analyzer import _load_signal_weights
    loaded = _load_signal_weights()

    assert loaded.get("pullback_score") == 2.0, f"눌림목 가중치 불일치: {loaded}"
    assert loaded.get("macd_cross_up") == 0.5, f"MACD 가중치 불일치: {loaded}"
    assert loaded.get("golden_cross") == 1.5, f"골든크로스 가중치 불일치: {loaded}"
    print(f"  로드된 가중치: {loaded}")

    # 가중치가 점수에 실제로 반영되는지 확인 (모의 분석 데이터)
    from src.technical_analyzer import calculate_technical_score

    # 눌림목 신호만 있는 분석 결과
    mock_analysis_pullback = {
        "pullback": {"pullback_to_ma20": True, "pullback_score": 2.0},
        "breakout": {}, "divergence": {},
        "rsi": 50, "macd_histogram": 0, "bb_position": 0.5,
        "golden_cross": False, "dead_cross": False, "ma_alignment": False,
        "macd_cross_up": False, "macd_cross_down": False,
        "bullish_volume": False, "volume_ratio": 1.0,
        "stoch_oversold": False, "stoch_cross_up": False,
        "consecutive_up": 0, "ma5_deviation": 0,
        "vwap_ratio": 1.0, "strong_trend": False,
        "bb_squeeze": False, "obv_rising": False,
        "risk_reward": {"risk_reward_ratio": 2.0},
        "price_change_pct": 0,
    }

    score_with_high_weight = calculate_technical_score(mock_analysis_pullback)

    # 가중치를 낮추고 다시 계산
    with open(SIGNAL_WEIGHTS_PATH, "w") as f:
        json.dump({"pullback_score": 0.5}, f)

    # 모듈 캐시 때문에 다시 로드
    score_with_low_weight = calculate_technical_score(mock_analysis_pullback)

    assert score_with_high_weight > score_with_low_weight, \
        f"가중치 높을 때({score_with_high_weight}) ≤ 낮을 때({score_with_low_weight})"
    print(f"  높은 가중치(2.0): 점수 {score_with_high_weight:.2f}")
    print(f"  낮은 가중치(0.5): 점수 {score_with_low_weight:.2f}")
    print(f"  차이: {score_with_high_weight - score_with_low_weight:.2f} → 반영 확인 ✅")


# ══════════════════════════════════════════════════════
#  테스트 7: 설정 파일 저장/로드 사이클
# ══════════════════════════════════════════════════════

@test("14. 설정 파일 저장/로드 사이클")
def test_config_save_load():
    from src.self_tuning import (
        _save_json, _load_json,
        STRATEGY_STATE_PATH, SIGNAL_WEIGHTS_PATH, TUNING_HISTORY_PATH,
    )

    # strategy_state.json
    test_state = {
        "version": 1,
        "current_params": {"top_n": 4, "min_tech_score": 4.5},
        "current_regime": "bearish",
        "regime_confidence": 0.75,
        "tuning_history": [{"timestamp": "2025-01-01T00:00:00"}],
    }
    _save_json(STRATEGY_STATE_PATH, test_state)
    loaded = _load_json(STRATEGY_STATE_PATH)
    assert loaded["current_regime"] == "bearish"
    assert loaded["current_params"]["top_n"] == 4
    print(f"  strategy_state.json 저장/로드 ✅")

    # signal_weights.json
    test_weights = {"pullback_score": 1.5, "macd_cross_up": 0.8}
    _save_json(SIGNAL_WEIGHTS_PATH, test_weights)
    loaded_w = _load_json(SIGNAL_WEIGHTS_PATH)
    assert loaded_w["pullback_score"] == 1.5
    print(f"  signal_weights.json 저장/로드 ✅")

    # tuning_history.json
    TUNING_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    test_hist = [{"timestamp": "2025-01-01", "regime": "bullish"}]
    _save_json(TUNING_HISTORY_PATH, test_hist)
    loaded_h = _load_json(TUNING_HISTORY_PATH)
    assert len(loaded_h) == 1
    print(f"  tuning_history.json 저장/로드 ✅")

    # 존재하지 않는 파일
    loaded_none = _load_json(Path("config/nonexistent.json"), {"default": True})
    assert loaded_none == {"default": True}
    print(f"  없는 파일 → 기본값 반환 ✅")


# ══════════════════════════════════════════════════════
#  테스트 8: Discord 알림 포맷
# ══════════════════════════════════════════════════════

@test("15. Discord 알림 포맷 검증")
def test_discord_format():
    """Discord 전송 함수가 에러 없이 실행되는지 확인 (실제 전송 안 함)."""
    from src.self_tuning import send_tuning_report_to_discord

    mock_report = {
        "timestamp": "2025-02-13T00:00:00+00:00",
        "status": "completed",
        "backtest_summary": {
            "total_trades": 50, "win_rate": 56.0,
            "profit_factor": 1.5, "total_pnl_pct": 42.0,
        },
        "regime": {"type": "bullish", "confidence": 0.8},
        "safety": {"is_safe": True, "message": "✅ 정상"},
        "param_changes": {
            "min_tech_score": {"old": 4.0, "new": 3.5},
        },
        "weight_changes": {
            "pullback_score": {"old": 1.0, "new": 1.15},
        },
    }

    # DISCORD_WEBHOOK_URL이 없으면 전송 스킵 (에러 없이)
    os.environ.pop("DISCORD_WEBHOOK_URL", None)
    send_tuning_report_to_discord(mock_report)
    print(f"  URL 없음 → 스킵 (에러 없음) ✅")

    # status가 skipped면 전송 안 함
    mock_report["status"] = "skipped"
    send_tuning_report_to_discord(mock_report)
    print(f"  status=skipped → 스킵 ✅")


# ══════════════════════════════════════════════════════
#  테스트 9: main.py 연동
# ══════════════════════════════════════════════════════

@test("16. main.py — strategy_state.json 로드 검증")
def test_main_integration():
    from src.self_tuning import STRATEGY_STATE_PATH, _save_json

    # 자기 학습 결과 저장
    tuned_state = {
        "current_params": {
            "top_n": 3,
            "min_tech_score": 5.0,
            "atr_stop_mult": 1.5,
            "atr_tp_mult": 3.5,
            "max_hold_days": 5,
        },
        "current_regime": "bearish",
    }
    _save_json(STRATEGY_STATE_PATH, tuned_state)

    # main.py의 load_cfg 함수 테스트
    from src.main import load_cfg
    cfg = load_cfg()
    auto = cfg.get("auto", {})

    assert auto.get("min_tech_score") == 5.0, \
        f"min_tech_score 불일치: {auto.get('min_tech_score')}"
    assert auto.get("top_n") == 3, \
        f"top_n 불일치: {auto.get('top_n')}"
    assert auto.get("atr_stop_mult") == 1.5, \
        f"atr_stop_mult 불일치: {auto.get('atr_stop_mult')}"
    print(f"  min_tech_score: {auto.get('min_tech_score')} ✅")
    print(f"  top_n: {auto.get('top_n')} ✅")
    print(f"  atr_stop_mult: {auto.get('atr_stop_mult')} ✅")
    print(f"  atr_tp_mult: {auto.get('atr_tp_mult')} ✅")
    print(f"  max_hold_days: {auto.get('max_hold_days')} ✅")


# ══════════════════════════════════════════════════════
#  테스트 10: 실제 데이터 통합 테스트
# ══════════════════════════════════════════════════════

@test("17. 실제 데이터 통합 — 백테스트 → 자기 학습")
def test_live_integration():
    from src.self_tuning import SelfTuningEngine, STRATEGY_STATE_PATH, SIGNAL_WEIGHTS_PATH

    # 초기 상태 클린업
    for p in [STRATEGY_STATE_PATH, SIGNAL_WEIGHTS_PATH]:
        if p.exists():
            p.unlink()

    engine = SelfTuningEngine(pool="nasdaq100", backtest_days=20)
    report = engine.run()

    status = report.get("status", "unknown")
    print(f"  상태: {status}")

    if status == "skipped":
        print(f"  ⚠️ 거래 수 부족으로 스킵 (데이터 상태에 따라 정상)")
        return

    assert status == "completed", f"실행 실패: {status}"

    # 결과 구조 검증
    assert "backtest_summary" in report
    assert "regime" in report
    assert "safety" in report
    assert "param_changes" in report
    assert "weight_changes" in report

    summary = report["backtest_summary"]
    regime = report["regime"]
    print(f"  거래: {summary.get('total_trades', 0)}회")
    print(f"  승률: {summary.get('win_rate', 0):.1f}%")
    print(f"  레짐: {regime.get('type', '?')} ({regime.get('confidence', 0):.0%})")
    print(f"  안전: {report['safety'].get('message', '?')}")

    # 설정 파일 생성 확인
    assert STRATEGY_STATE_PATH.exists(), "strategy_state.json 미생성"
    assert SIGNAL_WEIGHTS_PATH.exists(), "signal_weights.json 미생성"

    # 저장된 상태 검증
    with open(STRATEGY_STATE_PATH) as f:
        state = json.load(f)
    assert "current_params" in state
    assert "current_regime" in state
    assert "tuning_history" in state
    print(f"  저장된 레짐: {state['current_regime']}")
    print(f"  저장된 파라미터: {state['current_params']}")

    # 가중치 파일 검증
    with open(SIGNAL_WEIGHTS_PATH) as f:
        weights = json.load(f)
    print(f"  저장된 가중치: {len(weights)}개 키")

    param_changes = report.get("param_changes", {})
    weight_changes = report.get("weight_changes", {})
    print(f"  파라미터 변경: {len(param_changes)}개")
    print(f"  가중치 변경: {len(weight_changes)}개")


# ══════════════════════════════════════════════════════
#  정리 & 메인
# ══════════════════════════════════════════════════════

def cleanup():
    """테스트용 설정 파일 정리 (원본 복구)."""
    from src.self_tuning import STRATEGY_STATE_PATH, SIGNAL_WEIGHTS_PATH, TUNING_HISTORY_PATH

    # 테스트가 만든 파일은 그대로 두되, 백업이 있으면 복구
    for p in [STRATEGY_STATE_PATH, SIGNAL_WEIGHTS_PATH]:
        backup = p.with_suffix(".json.bak")
        if backup.exists():
            shutil.copy2(backup, p)
            backup.unlink()


def backup_configs():
    """기존 설정 파일 백업."""
    from src.self_tuning import STRATEGY_STATE_PATH, SIGNAL_WEIGHTS_PATH

    for p in [STRATEGY_STATE_PATH, SIGNAL_WEIGHTS_PATH]:
        if p.exists():
            shutil.copy2(p, p.with_suffix(".json.bak"))


def main():
    global passed, failed

    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="빠른 테스트 (모의 데이터만)")
    parser.add_argument("--live", action="store_true", help="실제 데이터 통합 테스트")
    args = parser.parse_args()

    print("=" * 60)
    print("🧪 자기 학습 시스템 통합 검증 테스트")
    print("=" * 60)

    # 기존 설정 백업
    backup_configs()

    try:
        # 모의 데이터 테스트 (항상 실행)
        test_imports()
        test_regime_basic()
        test_regime_edge()
        test_signal_weights_basic()
        test_signal_weights_safety()
        test_signal_weights_empty()
        test_param_blending()
        test_param_performance_adjust()
        test_param_clamping()
        test_param_insufficient_trades()
        test_safety_guard()
        test_safety_conservative()
        test_weights_integration()
        test_config_save_load()
        test_discord_format()
        test_main_integration()

        # 실제 데이터 테스트 (선택적)
        if not args.quick:
            print(f"\n{'=' * 60}")
            print("🌐 실제 데이터 통합 테스트")
            print(f"{'=' * 60}")
            test_live_integration()

    finally:
        cleanup()

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
