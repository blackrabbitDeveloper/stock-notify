"""
자동 전략 튜닝 시스템

백테스트 결과 + 시장 레짐을 분석하여 전략을 자동으로 조정합니다.

조정 대상:
  1. 파라미터 (SL/TP 배수, 보유일, 최소점수, top_n)
  2. 신호 가중치 (성과 기반 강화/약화)
  3. 시장 레짐별 전략 프로파일 전환

안전장치:
  - 조정 범위 제한 (급격한 변경 방지)
  - 최소 거래 수 미충족 시 조정 스킵
  - 변경 이력 로깅
  - 롤백 기능
"""

import json
import copy
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .backtester import BacktestEngine, print_report
from .backtest_utils import ParameterOptimizer
from .market_regime import detect_market_regime, get_regime_profile, REGIME_PROFILES
from .logger import logger


# ── 경로 ──
CONFIG_PATH = Path("config/universe.yaml")
SIGNAL_WEIGHTS_PATH = Path("config/signal_weights.json")
TUNE_HISTORY_PATH = Path("data/tune_history.json")

# ── 안전장치 상수 ──
MIN_TRADES_FOR_TUNING = 30      # 최소 거래 수 (이하면 튜닝 스킵)
MAX_PARAM_CHANGE_PCT = 30       # 파라미터 최대 변경 비율 (%)
MIN_WIN_RATE_EMERGENCY = 35     # 긴급 보수적 전환 기준 승률
MIN_PF_EMERGENCY = 0.7          # 긴급 보수적 전환 기준 PF

# ── 파라미터 허용 범위 ──
PARAM_BOUNDS = {
    "atr_stop_mult": (1.0, 3.5),
    "atr_tp_mult":   (2.0, 7.0),
    "max_hold_days":  (3, 14),
    "min_tech_score": (3.0, 7.0),
    "top_n":          (2, 8),
}

# ── 기본 신호 가중치 (technical_analyzer.py의 현재 값 기준) ──
DEFAULT_SIGNAL_WEIGHTS = {
    # 진입 타이밍 (A 그룹)
    "pullback_score":       1.0,    # 눌림목 (최대 +2.5)
    "breakout_score":       1.0,    # 돌파 (최대 +3.0)
    "divergence_score":     1.0,    # 다이버전스 (+2.0 / -1.5)
    "stoch_cross_up":       1.0,    # 스토캐스틱 (+1.5 / +0.5)

    # 추세 확인 (B 그룹)
    "golden_cross":         1.0,    # 골든크로스 (+1.0)
    "ma_alignment":         1.0,    # 이평정배열 (+0.8)
    "macd_cross_up":        1.0,    # MACD (+1.0)

    # 거래량 (C 그룹)
    "bullish_volume":       1.0,    # 거래량 동반 (+1.5)
    "obv_rising":           1.0,    # OBV (+0.5)

    # 기타
    "rsi_oversold_bounce":  1.0,    # RSI 과매도 탈출 (+0.8)
    "bb_squeeze_breakout":  1.0,    # 볼린저 스퀴즈 돌파 (+1.5)
    "strong_trend":         1.0,    # ADX 강추세 (+0.5)
    "rr_bonus":             1.0,    # R:R 보너스 (+1.0 / +0.5)
}


# ══════════════════════════════════════════════════════
#  설정 파일 I/O
# ══════════════════════════════════════════════════════

def load_config() -> Dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(cfg: Dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    logger.info(f"설정 저장: {CONFIG_PATH}")


def load_signal_weights() -> Dict:
    if SIGNAL_WEIGHTS_PATH.exists():
        with open(SIGNAL_WEIGHTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return copy.deepcopy(DEFAULT_SIGNAL_WEIGHTS)


def save_signal_weights(weights: Dict) -> None:
    SIGNAL_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SIGNAL_WEIGHTS_PATH, "w", encoding="utf-8") as f:
        json.dump(weights, f, ensure_ascii=False, indent=2)
    logger.info(f"신호 가중치 저장: {SIGNAL_WEIGHTS_PATH}")


def load_tune_history() -> List[Dict]:
    if TUNE_HISTORY_PATH.exists():
        with open(TUNE_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_tune_history(history: List[Dict]) -> None:
    TUNE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TUNE_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, default=str)


# ══════════════════════════════════════════════════════
#  1. 파라미터 자동 조정
# ══════════════════════════════════════════════════════

def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _smooth_adjust(current: float, target: float, max_change_pct: float = MAX_PARAM_CHANGE_PCT) -> float:
    """급격한 변경 방지 — 현재값에서 target 방향으로 제한적으로 이동."""
    if current == 0:
        return target
    max_delta = abs(current) * (max_change_pct / 100)
    delta = target - current
    clamped_delta = max(-max_delta, min(max_delta, delta))
    return current + clamped_delta


def tune_parameters(backtest_result: Dict, current_config: Dict) -> Dict:
    """
    백테스트 결과를 바탕으로 파라미터 최적 조정.

    전략:
    - 손절이 너무 많으면 → SL 배수 확대 (여유)
    - 만료가 너무 많으면 → 보유일 확대 또는 TP 축소
    - 승률 낮으면 → 최소 점수 상향
    - 승률 높은데 수익 낮으면 → TP 확대
    """
    summary = backtest_result.get("summary", {})
    exit_breakdown = backtest_result.get("exit_breakdown", {})

    total = summary.get("total_trades", 0)
    if total < MIN_TRADES_FOR_TUNING:
        logger.info(f"거래 {total}건 < {MIN_TRADES_FOR_TUNING} → 파라미터 튜닝 스킵")
        return current_config

    auto = current_config.get("auto", {})
    win_rate = summary.get("win_rate", 50)
    pf = summary.get("profit_factor", 1.0)
    avg_pnl = summary.get("avg_pnl_pct", 0)
    sl_rate = exit_breakdown.get("sl_rate", 30)
    tp_rate = exit_breakdown.get("tp_rate", 30)
    exp_rate = exit_breakdown.get("exp_rate", 30)

    # 현재 값
    cur_sl = float(auto.get("atr_stop_mult", 2.0))
    cur_tp = float(auto.get("atr_tp_mult", 4.0))
    cur_hold = int(auto.get("max_hold_days", 7))
    cur_min_score = float(auto.get("min_tech_score", 4.0))
    cur_top_n = int(auto.get("top_n", 5))

    changes = []

    # ── 손절 비율 기반 SL 조정 ──
    if sl_rate > 40:
        # 손절이 너무 많음 → SL 확대 (여유)
        new_sl = _smooth_adjust(cur_sl, cur_sl * 1.15)
        changes.append(f"SL 확대 {cur_sl:.2f}→{new_sl:.2f} (손절률 {sl_rate:.0f}% 과다)")
    elif sl_rate < 15 and tp_rate < 30:
        # 손절이 너무 적음 → SL 축소 (타이트)
        new_sl = _smooth_adjust(cur_sl, cur_sl * 0.9)
        changes.append(f"SL 축소 {cur_sl:.2f}→{new_sl:.2f} (손절률 {sl_rate:.0f}% 과소)")
    else:
        new_sl = cur_sl

    # ── 만료 비율 기반 보유일/TP 조정 ──
    if exp_rate > 45:
        # 만료가 너무 많음 → TP 축소 또는 보유일 확대
        if cur_hold < 10:
            new_hold = _smooth_adjust(cur_hold, cur_hold + 2)
            changes.append(f"보유일 확대 {cur_hold}→{int(new_hold)} (만료률 {exp_rate:.0f}% 과다)")
        else:
            new_hold = cur_hold
        new_tp = _smooth_adjust(cur_tp, cur_tp * 0.85)
        changes.append(f"TP 축소 {cur_tp:.2f}→{new_tp:.2f}")
    elif exp_rate < 15 and tp_rate > 40:
        # 익절이 많고 만료 적음 → TP 확대 (이익 더 키움)
        new_tp = _smooth_adjust(cur_tp, cur_tp * 1.1)
        new_hold = cur_hold
        changes.append(f"TP 확대 {cur_tp:.2f}→{new_tp:.2f} (익절률 {tp_rate:.0f}% 양호)")
    else:
        new_tp = cur_tp
        new_hold = cur_hold

    # ── 승률 기반 최소 점수 조정 ──
    if win_rate < 40:
        new_min_score = _smooth_adjust(cur_min_score, cur_min_score + 0.5)
        changes.append(f"최소점수 상향 {cur_min_score:.1f}→{new_min_score:.1f} (승률 {win_rate:.0f}% 저조)")
    elif win_rate > 65 and total < 50:
        new_min_score = _smooth_adjust(cur_min_score, cur_min_score - 0.3)
        changes.append(f"최소점수 하향 {cur_min_score:.1f}→{new_min_score:.1f} (승률 높지만 거래 적음)")
    else:
        new_min_score = cur_min_score

    # ── top_n 조정 ──
    if win_rate > 60 and pf > 1.5:
        new_top_n = _smooth_adjust(cur_top_n, cur_top_n + 1)
        changes.append(f"top_n 확대 {cur_top_n}→{int(new_top_n)} (성과 우수)")
    elif win_rate < 40 or pf < 0.8:
        new_top_n = _smooth_adjust(cur_top_n, cur_top_n - 1)
        changes.append(f"top_n 축소 {cur_top_n}→{int(new_top_n)} (성과 부진)")
    else:
        new_top_n = cur_top_n

    # 범위 제한
    new_sl = round(_clamp(new_sl, *PARAM_BOUNDS["atr_stop_mult"]), 2)
    new_tp = round(_clamp(new_tp, *PARAM_BOUNDS["atr_tp_mult"]), 2)
    new_hold = int(_clamp(new_hold, *PARAM_BOUNDS["max_hold_days"]))
    new_min_score = round(_clamp(new_min_score, *PARAM_BOUNDS["min_tech_score"]), 1)
    new_top_n = int(_clamp(new_top_n, *PARAM_BOUNDS["top_n"]))

    # 설정 업데이트
    auto["atr_stop_mult"] = new_sl
    auto["atr_tp_mult"] = new_tp
    auto["max_hold_days"] = new_hold
    auto["min_tech_score"] = new_min_score
    auto["top_n"] = new_top_n
    current_config["auto"] = auto

    if changes:
        logger.info(f"파라미터 조정 ({len(changes)}건):")
        for c in changes:
            logger.info(f"  → {c}")
    else:
        logger.info("파라미터 변경 없음")

    return current_config


# ══════════════════════════════════════════════════════
#  2. 신호 가중치 자동 조정
# ══════════════════════════════════════════════════════

# 신호 이름 → signal_weights 키 매핑
SIGNAL_NAME_MAP = {
    "20MA눌림목":       "pullback_score",
    "50MA눌림목":       "pullback_score",
    "BB하단반등":        "pullback_score",
    "골든크로스":        "golden_cross",
    "MACD상향":         "macd_cross_up",
    "이평정배열":        "ma_alignment",
    "스토캐스틱크로스":   "stoch_cross_up",
    "강세다이버전스":     "divergence_score",
    "스퀴즈돌파":        "bb_squeeze_breakout",
}


def tune_signal_weights(backtest_result: Dict, current_weights: Dict) -> Dict:
    """
    신호별 성과를 바탕으로 가중치 조정.

    - 승률 60%+ & 양수 수익 → 가중치 ↑ (최대 1.5x)
    - 승률 40%- & 음수 수익 → 가중치 ↓ (최소 0.3x)
    - 표본 5건 미만 → 조정 안 함
    """
    signal_perf = backtest_result.get("signal_performance", [])

    if not signal_perf:
        logger.info("신호 성과 데이터 없음 → 가중치 유지")
        return current_weights

    new_weights = copy.deepcopy(current_weights)
    changes = []

    for sp in signal_perf:
        sig_name = sp["signal"]
        count = sp["count"]
        avg_pnl = sp["avg_pnl"]
        win_rate = sp["win_rate"]

        # 매핑된 키 찾기
        weight_key = None
        for name_part, key in SIGNAL_NAME_MAP.items():
            if name_part in sig_name:
                weight_key = key
                break

        # 돌파 종류 매핑
        if "돌파" in sig_name and "스퀴즈" not in sig_name:
            weight_key = "breakout_score"
        if "거래량" in sig_name:
            weight_key = "bullish_volume"

        if weight_key is None or weight_key not in new_weights:
            continue

        if count < 5:
            continue  # 표본 부족

        cur_w = new_weights[weight_key]

        # 조정 로직
        if win_rate >= 60 and avg_pnl > 0.5:
            # 성과 우수 → 강화
            factor = min(1.15, 1.0 + (win_rate - 60) / 100 + avg_pnl / 10)
            new_w = _smooth_adjust(cur_w, cur_w * factor, max_change_pct=20)
        elif win_rate <= 40 and avg_pnl < 0:
            # 성과 부진 → 약화
            factor = max(0.85, 1.0 - (40 - win_rate) / 100 + avg_pnl / 10)
            new_w = _smooth_adjust(cur_w, cur_w * factor, max_change_pct=20)
        else:
            # 보통 → 약간 1.0 방향으로 회귀
            new_w = _smooth_adjust(cur_w, cur_w * 0.95 + 1.0 * 0.05, max_change_pct=5)

        # 범위 제한
        new_w = round(_clamp(new_w, 0.3, 2.0), 3)

        if abs(new_w - cur_w) > 0.01:
            changes.append(f"{weight_key}: {cur_w:.3f}→{new_w:.3f} "
                           f"(승률 {win_rate:.0f}%, 수익 {avg_pnl:+.2f}%, {count}건)")
            new_weights[weight_key] = new_w

    if changes:
        logger.info(f"신호 가중치 조정 ({len(changes)}건):")
        for c in changes:
            logger.info(f"  → {c}")
    else:
        logger.info("신호 가중치 변경 없음")

    return new_weights


# ══════════════════════════════════════════════════════
#  3. 시장 레짐 기반 전략 전환
# ══════════════════════════════════════════════════════

def apply_regime_overlay(config: Dict, weights: Dict,
                         regime: str, regime_details: Dict) -> Tuple[Dict, Dict]:
    """
    시장 레짐에 따라 파라미터와 가중치를 오버레이.

    기존 튜닝 결과 위에 레짐 프로파일을 블렌딩합니다.
    (100% 교체가 아니라 가중 평균으로 부드럽게 전환)
    """
    profile = get_regime_profile(regime)
    confidence = regime_details.get("confidence", 0.5)

    # 블렌딩 비율: 레짐 신뢰도에 비례 (최대 60%)
    blend = min(0.6, confidence * 0.8)

    auto = config.get("auto", {})

    # 파라미터 블렌딩
    param_keys = ["atr_stop_mult", "atr_tp_mult", "max_hold_days", "min_tech_score", "top_n"]
    changes = []

    for key in param_keys:
        if key in profile and key in auto:
            cur = float(auto[key])
            regime_val = float(profile[key])
            blended = cur * (1 - blend) + regime_val * blend

            if key in ("max_hold_days", "top_n"):
                blended = int(round(blended))
            else:
                blended = round(blended, 2)

            # 범위 제한
            if key in PARAM_BOUNDS:
                blended = _clamp(blended, *PARAM_BOUNDS[key])

            if key in ("max_hold_days", "top_n"):
                blended = int(blended)

            if blended != auto[key]:
                changes.append(f"{key}: {auto[key]}→{blended} (레짐 {regime} blend {blend:.0%})")
                auto[key] = blended

    config["auto"] = auto

    # 신호 가중치 블렌딩
    regime_sw = profile.get("signal_weights", {})
    weight_changes = []

    for key, regime_w in regime_sw.items():
        if key in weights:
            cur_w = weights[key]
            blended_w = round(cur_w * (1 - blend) + regime_w * blend, 3)
            blended_w = _clamp(blended_w, 0.3, 2.0)

            if abs(blended_w - cur_w) > 0.01:
                weight_changes.append(f"{key}: {cur_w:.3f}→{blended_w:.3f}")
                weights[key] = blended_w

    if changes or weight_changes:
        logger.info(f"레짐 오버레이 적용 ({regime}, 신뢰도 {confidence:.0%}, blend {blend:.0%}):")
        for c in changes:
            logger.info(f"  📊 {c}")
        for c in weight_changes:
            logger.info(f"  📡 {c}")
    else:
        logger.info(f"레짐 오버레이: 변경 없음 ({regime})")

    return config, weights


# ══════════════════════════════════════════════════════
#  4. 긴급 안전장치
# ══════════════════════════════════════════════════════

def check_emergency(backtest_result: Dict) -> Optional[str]:
    """
    성과가 심각하게 부진하면 긴급 보수적 모드 전환.

    Returns:
        None (정상) 또는 긴급 사유 문자열
    """
    summary = backtest_result.get("summary", {})
    total = summary.get("total_trades", 0)

    if total < 20:
        return None

    win_rate = summary.get("win_rate", 50)
    pf = summary.get("profit_factor", 1.0)
    avg_pnl = summary.get("avg_pnl_pct", 0)

    reasons = []
    if win_rate < MIN_WIN_RATE_EMERGENCY:
        reasons.append(f"승률 {win_rate:.1f}% < {MIN_WIN_RATE_EMERGENCY}%")
    if pf < MIN_PF_EMERGENCY:
        reasons.append(f"PF {pf:.2f} < {MIN_PF_EMERGENCY}")
    if avg_pnl < -2.0:
        reasons.append(f"평균손익 {avg_pnl:+.2f}% 심각")

    if len(reasons) >= 2:
        return " + ".join(reasons)

    return None


def apply_emergency_mode(config: Dict, weights: Dict) -> Tuple[Dict, Dict]:
    """긴급 보수적 모드 적용."""
    logger.warning("🚨 긴급 보수적 모드 적용!")

    auto = config.get("auto", {})
    auto["atr_stop_mult"] = 1.5
    auto["atr_tp_mult"] = 3.0
    auto["max_hold_days"] = 3
    auto["min_tech_score"] = 6.0
    auto["top_n"] = 2
    config["auto"] = auto

    # 가중치: 보수적 (눌림목/다이버전스 위주)
    for key in weights:
        weights[key] = 0.7
    weights["pullback_score"] = 1.5
    weights["divergence_score"] = 1.3
    weights["rsi_oversold_bounce"] = 1.3
    weights["stoch_cross_up"] = 1.2

    return config, weights


# ══════════════════════════════════════════════════════
#  5. 메인 파이프라인
# ══════════════════════════════════════════════════════

def run_auto_tune(
    backtest_days: int = 60,
    dry_run: bool = False,
) -> Dict:
    """
    자동 전략 튜닝 전체 파이프라인.

    1. 백테스트 실행
    2. 시장 레짐 감지
    3. 파라미터 조정
    4. 신호 가중치 조정
    5. 레짐 오버레이
    6. 긴급 안전장치 확인
    7. 설정 저장 + 이력 기록

    Returns:
        튜닝 결과 요약 딕셔너리
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    logger.info("=" * 60)
    logger.info("🔧 자동 전략 튜닝 시작")
    logger.info("=" * 60)

    # ── 1. 백테스트 ──
    logger.info("\n[1/6] 백테스트 실행")
    config = load_config()
    auto = config.get("auto", {})

    engine = BacktestEngine(
        pool=auto.get("pool", "nasdaq100"),
        backtest_days=backtest_days,
        top_n=int(auto.get("top_n", 5)),
        min_tech_score=float(auto.get("min_tech_score", 4.0)),
        max_hold_days=int(auto.get("max_hold_days", 7)),
        atr_stop_mult=float(auto.get("atr_stop_mult", 2.0)),
        atr_tp_mult=float(auto.get("atr_tp_mult", 4.0)),
    )
    bt_result = engine.run()

    bt_summary = bt_result.get("summary", {})
    total_trades = bt_summary.get("total_trades", 0)
    logger.info(f"  백테스트 완료: {total_trades}거래, "
                f"승률 {bt_summary.get('win_rate', 0):.1f}%, "
                f"PF {bt_summary.get('profit_factor', 0):.2f}")

    # ── 2. 시장 레짐 감지 ──
    logger.info("\n[2/6] 시장 레짐 감지")
    regime, regime_details = detect_market_regime()
    logger.info(f"  레짐: {regime} (신뢰도 {regime_details.get('confidence', 0):.0%})")

    # ── 3. 긴급 안전장치 확인 ──
    logger.info("\n[3/6] 안전장치 확인")
    emergency = check_emergency(bt_result)
    is_emergency = emergency is not None

    if is_emergency:
        logger.warning(f"  🚨 긴급: {emergency}")

    # ── 4. 파라미터 조정 ──
    logger.info("\n[4/6] 파라미터 조정")
    old_config = copy.deepcopy(config)

    if is_emergency:
        config, weights = apply_emergency_mode(config, load_signal_weights())
    else:
        config = tune_parameters(bt_result, config)

        # ── 5. 신호 가중치 조정 ──
        logger.info("\n[5/6] 신호 가중치 조정")
        weights = load_signal_weights()
        weights = tune_signal_weights(bt_result, weights)

        # ── 6. 레짐 오버레이 ──
        logger.info("\n[6/6] 레짐 오버레이")
        config, weights = apply_regime_overlay(config, weights, regime, regime_details)

    # ── 변경 사항 요약 ──
    param_diff = _diff_configs(old_config, config)
    weight_diff = _diff_weights(load_signal_weights() if not is_emergency else DEFAULT_SIGNAL_WEIGHTS, weights)

    result = {
        "timestamp": timestamp,
        "backtest_summary": bt_summary,
        "regime": regime,
        "regime_details": regime_details,
        "emergency": emergency,
        "param_changes": param_diff,
        "weight_changes": weight_diff,
        "new_config": config.get("auto", {}),
        "new_weights": weights,
    }

    # ── 저장 ──
    if not dry_run:
        save_config(config)
        save_signal_weights(weights)

        # 이력 기록
        history = load_tune_history()
        history.append(result)
        # 최근 52주(1년) 이력만 보관
        if len(history) > 52:
            history = history[-52:]
        save_tune_history(history)

        logger.info("\n✅ 설정 저장 완료!")
    else:
        logger.info("\n⚠️ DRY_RUN: 저장 안 함")

    # 콘솔 리포트
    _print_tune_report(result)

    return result


def _diff_configs(old: Dict, new: Dict) -> List[str]:
    """설정 변경 사항 추출."""
    diffs = []
    old_auto = old.get("auto", {})
    new_auto = new.get("auto", {})

    for key in ["atr_stop_mult", "atr_tp_mult", "max_hold_days",
                "min_tech_score", "top_n"]:
        o = old_auto.get(key)
        n = new_auto.get(key)
        if o != n:
            diffs.append(f"{key}: {o} → {n}")

    return diffs


def _diff_weights(old: Dict, new: Dict) -> List[str]:
    """가중치 변경 사항 추출."""
    diffs = []
    for key in new:
        o = old.get(key, 1.0)
        n = new[key]
        if abs(o - n) > 0.01:
            diffs.append(f"{key}: {o:.3f} → {n:.3f}")
    return diffs


def _print_tune_report(result: Dict):
    """튜닝 결과 콘솔 출력."""
    print("\n" + "=" * 60)
    print("🔧 자동 전략 튜닝 결과")
    print("=" * 60)

    bt = result.get("backtest_summary", {})
    print(f"\n📊 백테스트: {bt.get('total_trades', 0)}거래 | "
          f"승률 {bt.get('win_rate', 0):.1f}% | "
          f"PF {bt.get('profit_factor', 0):.2f} | "
          f"샤프 {bt.get('sharpe_ratio', 0):.2f}")

    regime = result.get("regime", "?")
    conf = result.get("regime_details", {}).get("confidence", 0)
    print(f"\n🌍 시장 레짐: {regime} (신뢰도 {conf:.0%})")

    emergency = result.get("emergency")
    if emergency:
        print(f"\n🚨 긴급 모드: {emergency}")

    param_changes = result.get("param_changes", [])
    if param_changes:
        print(f"\n📊 파라미터 변경 ({len(param_changes)}건):")
        for c in param_changes:
            print(f"  → {c}")
    else:
        print("\n📊 파라미터 변경 없음")

    weight_changes = result.get("weight_changes", [])
    if weight_changes:
        print(f"\n📡 가중치 변경 ({len(weight_changes)}건):")
        for c in weight_changes:
            print(f"  → {c}")
    else:
        print("\n📡 가중치 변경 없음")

    # 새 설정 요약
    nc = result.get("new_config", {})
    print(f"\n⚙️ 현재 설정:")
    print(f"  SL: ATR×{nc.get('atr_stop_mult', '?')} | "
          f"TP: ATR×{nc.get('atr_tp_mult', '?')} | "
          f"보유: {nc.get('max_hold_days', '?')}일 | "
          f"최소점수: {nc.get('min_tech_score', '?')} | "
          f"top_n: {nc.get('top_n', '?')}")

    print(f"\n{'=' * 60}")
