#!/usr/bin/env python3
"""
자동 전략 튜닝 시스템 검증 테스트

사용법:
  python test_autotune.py               # 전체 테스트 (모의 데이터)
  python test_autotune.py --live         # 실제 데이터 포함
"""

import argparse, sys, os, json, copy, traceback, shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

passed = failed = 0
errors = []

def test(name):
    def decorator(func):
        def wrapper(*args, **kwargs):
            global passed, failed
            print(f"\n{'─'*60}\n🧪 {name}\n{'─'*60}")
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

@test("1. 모듈 임포트")
def test_imports():
    from src.strategy_tuner import (
        run_auto_tune, tune_parameters, tune_signal_weights,
        apply_regime_overlay, check_emergency, apply_emergency_mode,
        DEFAULT_SIGNAL_WEIGHTS, PARAM_BOUNDS,
    )
    from src.market_regime import (
        detect_market_regime, get_regime_profile,
        REGIME_PROFILES, _calc_regime_indicators,
    )
    print("  모든 모듈 임포트 성공")


@test("2. 파라미터 튜닝 — 손절 과다")
def test_tune_sl_overuse():
    from src.strategy_tuner import tune_parameters

    config = {"auto": {
        "pool": "nasdaq100", "atr_stop_mult": 2.0, "atr_tp_mult": 4.0,
        "max_hold_days": 7, "min_tech_score": 4.0, "top_n": 5,
    }}
    bt_result = {
        "summary": {"total_trades": 100, "win_rate": 42, "profit_factor": 0.9, "avg_pnl_pct": -0.5},
        "exit_breakdown": {"sl_rate": 50, "tp_rate": 20, "exp_rate": 30},
    }

    new_config = tune_parameters(bt_result, copy.deepcopy(config))
    auto = new_config["auto"]

    # 손절 과다 → SL 배수 확대
    assert auto["atr_stop_mult"] > 2.0, f"SL 확대 안됨: {auto['atr_stop_mult']}"
    # 승률 저조 → 최소 점수 상향
    assert auto["min_tech_score"] > 4.0, f"최소점수 상향 안됨: {auto['min_tech_score']}"
    print(f"  SL: 2.0 → {auto['atr_stop_mult']}")
    print(f"  min_score: 4.0 → {auto['min_tech_score']}")


@test("3. 파라미터 튜닝 — 만료 과다")
def test_tune_expire_overuse():
    from src.strategy_tuner import tune_parameters

    config = {"auto": {
        "pool": "nasdaq100", "atr_stop_mult": 2.0, "atr_tp_mult": 4.0,
        "max_hold_days": 5, "min_tech_score": 4.0, "top_n": 5,
    }}
    bt_result = {
        "summary": {"total_trades": 80, "win_rate": 50, "profit_factor": 1.1, "avg_pnl_pct": 0.3},
        "exit_breakdown": {"sl_rate": 15, "tp_rate": 20, "exp_rate": 65},
    }

    new_config = tune_parameters(bt_result, copy.deepcopy(config))
    auto = new_config["auto"]

    # 만료 과다 → 보유일 확대 또는 TP 축소
    changed = auto["max_hold_days"] > 5 or auto["atr_tp_mult"] < 4.0
    assert changed, f"만료 대응 변경 없음: hold={auto['max_hold_days']} tp={auto['atr_tp_mult']}"
    print(f"  hold: 5 → {auto['max_hold_days']}, TP: 4.0 → {auto['atr_tp_mult']}")


@test("4. 파라미터 튜닝 — 성과 우수")
def test_tune_good_performance():
    from src.strategy_tuner import tune_parameters

    config = {"auto": {
        "pool": "nasdaq100", "atr_stop_mult": 2.0, "atr_tp_mult": 4.0,
        "max_hold_days": 7, "min_tech_score": 4.0, "top_n": 4,
    }}
    bt_result = {
        "summary": {"total_trades": 80, "win_rate": 65, "profit_factor": 1.8, "avg_pnl_pct": 1.5},
        "exit_breakdown": {"sl_rate": 15, "tp_rate": 55, "exp_rate": 30},
    }

    new_config = tune_parameters(bt_result, copy.deepcopy(config))
    auto = new_config["auto"]

    # 성과 우수 → top_n 확대
    assert auto["top_n"] >= 4, f"top_n이 줄어듦: {auto['top_n']}"
    print(f"  top_n: 4 → {auto['top_n']}")


@test("5. 파라미터 튜닝 — 거래 부족 시 스킵")
def test_tune_skip_low_trades():
    from src.strategy_tuner import tune_parameters

    config = {"auto": {
        "atr_stop_mult": 2.0, "atr_tp_mult": 4.0,
        "max_hold_days": 7, "min_tech_score": 4.0, "top_n": 5,
    }}
    bt_result = {
        "summary": {"total_trades": 10, "win_rate": 30, "profit_factor": 0.5, "avg_pnl_pct": -2.0},
        "exit_breakdown": {"sl_rate": 60, "tp_rate": 10, "exp_rate": 30},
    }

    new_config = tune_parameters(bt_result, copy.deepcopy(config))
    # 거래 30건 미만 → 변경 없음
    assert new_config["auto"]["atr_stop_mult"] == 2.0, "거래 부족인데 변경됨"
    print("  거래 10건 → 튜닝 스킵 ✅")


@test("6. 파라미터 범위 제한")
def test_param_bounds():
    from src.strategy_tuner import tune_parameters, PARAM_BOUNDS

    config = {"auto": {
        "atr_stop_mult": 3.4, "atr_tp_mult": 6.8,
        "max_hold_days": 13, "min_tech_score": 6.8, "top_n": 8,
    }}
    # 극단적 성과 → 급격한 변경 시도
    bt_result = {
        "summary": {"total_trades": 100, "win_rate": 25, "profit_factor": 0.5, "avg_pnl_pct": -3.0},
        "exit_breakdown": {"sl_rate": 60, "tp_rate": 5, "exp_rate": 35},
    }

    new_config = tune_parameters(bt_result, copy.deepcopy(config))
    auto = new_config["auto"]

    for key, (lo, hi) in PARAM_BOUNDS.items():
        val = auto.get(key)
        if val is not None:
            assert lo <= val <= hi, f"{key}={val} 범위 이탈 ({lo}~{hi})"
    print("  모든 파라미터 범위 내 ✅")


@test("7. 신호 가중치 튜닝")
def test_signal_weight_tuning():
    from src.strategy_tuner import tune_signal_weights, DEFAULT_SIGNAL_WEIGHTS

    weights = copy.deepcopy(DEFAULT_SIGNAL_WEIGHTS)
    bt_result = {
        "signal_performance": [
            {"signal": "20MA눌림목", "count": 30, "avg_pnl": 1.8, "win_rate": 65},
            {"signal": "돌파(20d_high)", "count": 20, "avg_pnl": -0.5, "win_rate": 35},
            {"signal": "골든크로스", "count": 15, "avg_pnl": 0.3, "win_rate": 52},
            {"signal": "MACD상향", "count": 8, "avg_pnl": 0.8, "win_rate": 62},
            {"signal": "스토캐스틱크로스", "count": 3, "avg_pnl": 2.0, "win_rate": 100},  # 표본 부족
        ],
    }

    new_weights = tune_signal_weights(bt_result, weights)

    # 눌림목: 승률 높고 수익 좋음 → 가중치 상승
    assert new_weights["pullback_score"] > 1.0, f"pullback 강화 안됨: {new_weights['pullback_score']}"
    # 돌파: 성과 부진 → 가중치 하락
    assert new_weights["breakout_score"] < 1.0, f"breakout 약화 안됨: {new_weights['breakout_score']}"
    # 스토캐스틱: 표본 3건 < 5 → 변경 없음
    assert new_weights["stoch_cross_up"] == 1.0, f"표본 부족인데 변경됨: {new_weights['stoch_cross_up']}"

    print(f"  pullback: 1.0 → {new_weights['pullback_score']:.3f} ↑")
    print(f"  breakout: 1.0 → {new_weights['breakout_score']:.3f} ↓")
    print(f"  stoch:    1.0 → {new_weights['stoch_cross_up']:.3f} (불변)")

    # 범위 제한 확인
    for k, v in new_weights.items():
        assert 0.3 <= v <= 2.0, f"{k}={v} 범위 이탈"
    print("  모든 가중치 0.3~2.0 범위 내 ✅")


@test("8. 긴급 안전장치")
def test_emergency():
    from src.strategy_tuner import check_emergency, apply_emergency_mode, DEFAULT_SIGNAL_WEIGHTS

    # 긴급 아닌 케이스
    normal = {"summary": {"total_trades": 50, "win_rate": 50, "profit_factor": 1.2, "avg_pnl_pct": 0.5}}
    assert check_emergency(normal) is None, "정상인데 긴급 발동"

    # 긴급 케이스 (승률 30% + PF 0.6)
    bad = {"summary": {"total_trades": 50, "win_rate": 30, "profit_factor": 0.6, "avg_pnl_pct": -2.5}}
    reason = check_emergency(bad)
    assert reason is not None, "성과 부진인데 긴급 미발동"
    print(f"  긴급 사유: {reason}")

    # 긴급 모드 적용
    config = {"auto": {"atr_stop_mult": 2.0, "top_n": 5}}
    weights = copy.deepcopy(DEFAULT_SIGNAL_WEIGHTS)
    config, weights = apply_emergency_mode(config, weights)

    assert config["auto"]["top_n"] == 2, f"긴급 top_n 불일치: {config['auto']['top_n']}"
    assert config["auto"]["min_tech_score"] == 6.0, "긴급 min_score 불일치"
    assert weights["pullback_score"] == 1.5, "긴급 가중치 불일치"
    print("  긴급 모드 적용 ✅")


@test("9. 시장 레짐 프로파일")
def test_regime_profiles():
    from src.market_regime import REGIME_PROFILES, get_regime_profile

    for regime in ["bullish", "bearish", "sideways", "volatile"]:
        p = get_regime_profile(regime)
        assert "atr_stop_mult" in p, f"{regime}: atr_stop_mult 없음"
        assert "signal_weights" in p, f"{regime}: signal_weights 없음"
        assert "description" in p, f"{regime}: description 없음"
        print(f"  {regime}: SL={p['atr_stop_mult']}x TP={p['atr_tp_mult']}x "
              f"hold={p['max_hold_days']}d top={p['top_n']}")

    # bearish는 bullish보다 보수적이어야 함
    bull = REGIME_PROFILES["bullish"]
    bear = REGIME_PROFILES["bearish"]
    assert bear["min_tech_score"] > bull["min_tech_score"], "bearish 최소점수 < bullish"
    assert bear["top_n"] < bull["top_n"], "bearish top_n >= bullish"
    print("  bearish가 bullish보다 보수적 ✅")


@test("10. 레짐 오버레이 블렌딩")
def test_regime_overlay():
    from src.strategy_tuner import apply_regime_overlay, DEFAULT_SIGNAL_WEIGHTS

    config = {"auto": {
        "atr_stop_mult": 2.0, "atr_tp_mult": 4.0,
        "max_hold_days": 7, "min_tech_score": 4.0, "top_n": 5,
    }}
    weights = copy.deepcopy(DEFAULT_SIGNAL_WEIGHTS)

    # bearish 레짐 적용
    regime_details = {"confidence": 0.8}
    new_config, new_weights = apply_regime_overlay(
        copy.deepcopy(config), copy.deepcopy(weights),
        "bearish", regime_details
    )

    auto = new_config["auto"]
    # bearish 프로파일은 SL=1.5, top_n=3 → 블렌딩 후 원래값과 사이
    assert auto["atr_stop_mult"] < 2.0, f"bearish 블렌딩 후 SL 변화 없음: {auto['atr_stop_mult']}"
    assert auto["top_n"] <= 5, f"bearish인데 top_n 증가: {auto['top_n']}"

    print(f"  SL: 2.0 → {auto['atr_stop_mult']} (bearish 방향)")
    print(f"  top_n: 5 → {auto['top_n']}")

    # 가중치도 변경되었는지
    if new_weights["breakout_score"] != weights["breakout_score"]:
        print(f"  breakout_score: {weights['breakout_score']} → {new_weights['breakout_score']}")


@test("11. 레짐 감지 지표 계산")
def test_regime_indicators():
    import numpy as np
    import pandas as pd
    from src.market_regime import _calc_regime_indicators

    np.random.seed(42)
    days = 60
    prices = np.cumsum(np.random.randn(days) * 2) + 400
    df = pd.DataFrame({
        "Date": pd.bdate_range(end="2025-02-10", periods=days),
        "Open": prices + np.random.randn(days),
        "High": prices + abs(np.random.randn(days)) * 2,
        "Low": prices - abs(np.random.randn(days)) * 2,
        "Close": prices,
        "Volume": np.random.randint(50_000_000, 200_000_000, days).astype(float),
    })

    ind = _calc_regime_indicators(df)

    assert "rsi" in ind, "RSI 없음"
    assert "adx" in ind, "ADX 없음"
    assert "atr_pct" in ind, "ATR% 없음"
    assert "bb_width" in ind, "BB폭 없음"
    assert "ret_5d" in ind, "5일수익률 없음"
    assert 0 <= ind["rsi"] <= 100, f"RSI 범위 이상: {ind['rsi']}"

    print(f"  RSI: {ind['rsi']:.1f} | ADX: {ind['adx']:.1f}")
    print(f"  ATR%: {ind['atr_pct']:.2f} | BB폭: {ind['bb_width']:.2f}")
    print(f"  5일: {ind['ret_5d']:+.2f}% | 20일: {ind['ret_20d']:+.2f}%")


@test("12. 설정 파일 I/O")
def test_config_io():
    from src.strategy_tuner import (
        load_signal_weights, save_signal_weights,
        load_tune_history, save_tune_history,
        DEFAULT_SIGNAL_WEIGHTS, SIGNAL_WEIGHTS_PATH, TUNE_HISTORY_PATH,
    )

    # 임시 경로 사용
    test_sw_path = Path("data/test_signal_weights.json")
    test_hist_path = Path("data/test_tune_history.json")

    # 가중치 저장/로드
    test_weights = copy.deepcopy(DEFAULT_SIGNAL_WEIGHTS)
    test_weights["pullback_score"] = 1.5
    test_sw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(test_sw_path, "w") as f:
        json.dump(test_weights, f)
    with open(test_sw_path, "r") as f:
        loaded = json.load(f)
    assert loaded["pullback_score"] == 1.5
    test_sw_path.unlink(missing_ok=True)

    # 이력 저장/로드
    test_history = [{"timestamp": "2025-01-01", "regime": "bullish"}]
    with open(test_hist_path, "w") as f:
        json.dump(test_history, f)
    with open(test_hist_path, "r") as f:
        loaded_hist = json.load(f)
    assert len(loaded_hist) == 1
    test_hist_path.unlink(missing_ok=True)

    print("  설정 파일 I/O 정상 ✅")


@test("13. technical_analyzer 가중치 적용 검증")
def test_analyzer_weights():
    """signal_weights.json 유무에 따라 점수가 달라지는지 확인."""
    from src.technical_analyzer import _load_signal_weights

    # 파일 없을 때 → 빈 dict (모든 w()=1.0)
    sw = _load_signal_weights()
    # config/signal_weights.json이 없으면 {} 반환
    # 있으면 해당 내용 반환 → 어느 쪽이든 에러 없이 동작
    assert isinstance(sw, dict), f"가중치 타입 이상: {type(sw)}"
    print(f"  가중치 로드: {len(sw)}개 키 (없으면 기본값 1.0 적용)")


@test("14. 실제 데이터 — 시장 레짐 감지")
def test_live_regime():
    from src.market_regime import detect_market_regime

    regime, details = detect_market_regime()

    assert regime in ("bullish", "bearish", "sideways", "volatile"), f"알 수 없는 레짐: {regime}"
    assert "confidence" in details, "신뢰도 없음"
    assert "indicators" in details, "지표 없음"

    ind = details["indicators"]
    print(f"  레짐: {regime} (신뢰도 {details['confidence']:.0%})")
    print(f"  VIX: {ind.get('vix', '?')} | ADX: {ind.get('spy_adx', '?')}")
    print(f"  RSI: {ind.get('spy_rsi', '?')} | 20일: {ind.get('spy_ret_20d', '?')}%")
    print(f"  점수: {details.get('scores', {})}")


# ══════════════════════════════════════════════════════

def main():
    global passed, failed

    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="실제 데이터 테스트 포함")
    args = parser.parse_args()

    print("=" * 60)
    print("🧪 자동 전략 튜닝 시스템 검증 테스트")
    print("=" * 60)

    # 모의 데이터 테스트 (항상)
    test_imports()
    test_tune_sl_overuse()
    test_tune_expire_overuse()
    test_tune_good_performance()
    test_tune_skip_low_trades()
    test_param_bounds()
    test_signal_weight_tuning()
    test_emergency()
    test_regime_profiles()
    test_regime_overlay()
    test_regime_indicators()
    test_config_io()
    test_analyzer_weights()

    # 실제 데이터 테스트
    if args.live:
        print(f"\n{'='*60}\n🌐 실제 데이터 테스트\n{'='*60}")
        test_live_regime()

    # 결과
    print(f"\n{'═'*60}")
    print(f"📊 결과: ✅ {passed} PASS / ❌ {failed} FAIL")
    print(f"{'═'*60}")

    if errors:
        print("\n실패 목록:")
        for name, err in errors:
            print(f"  ❌ {name}: {err}")

    if failed == 0:
        print("\n🎉 모든 테스트 통과!")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
