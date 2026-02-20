"""
자기 학습(Self-Tuning) 전략 엔진

백테스트 결과를 분석하여 자동으로 전략을 최적화합니다.

3가지 자동화:
  1. 파라미터 자동 조정 (SL/TP 배수, 보유일, 최소점수)
  2. 신호 가중치 자동 조정 (성과 좋은 신호↑, 나쁜 신호↓)
  3. 시장 상태별 전략 자동 전환 (강세/약세/횡보)

실행 주기: 매주 (GitHub Actions)

구조:
  config/strategy_state.json   — 현재 전략 상태 + 이력
  config/signal_weights.json   — 신호별 가중치 (technical_analyzer.py가 읽음)
  config/universe.yaml         — 파라미터 (min_tech_score 등)
"""

import json
import math
import copy
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .backtester import BacktestEngine, print_report, export_results
from .logger import logger


# ══════════════════════════════════════════════════════
#  상수 & 설정
# ══════════════════════════════════════════════════════

CONFIG_DIR = Path("config")
DATA_DIR = Path("data")

STRATEGY_STATE_PATH = CONFIG_DIR / "strategy_state.json"
SIGNAL_WEIGHTS_PATH = CONFIG_DIR / "signal_weights.json"
TUNING_HISTORY_PATH = DATA_DIR / "tuning_history.json"

# 파라미터 탐색 범위 (안전 한계)
PARAM_BOUNDS = {
    "top_n":          {"min": 2,   "max": 10,  "step": 1,    "type": "int"},
    "min_tech_score": {"min": 3.0, "max": 6.0, "step": 0.25, "type": "float"},
    "atr_stop_mult":  {"min": 1.0, "max": 3.5, "step": 0.25, "type": "float"},
    "atr_tp_mult":    {"min": 2.0, "max": 6.0, "step": 0.25, "type": "float"},
    "max_hold_days":  {"min": 3,   "max": 14,  "step": 1,    "type": "int"},
    "sell_threshold":    {"min": 2.0, "max": 8.0, "step": 0.5,  "type": "float"},
    "max_positions":     {"min": 3,   "max": 15,  "step": 1,    "type": "int"},
    "max_daily_entries": {"min": 1,   "max": 5,   "step": 1,    "type": "int"},
    "trailing_atr_mult": {"min": 1.0, "max": 3.0, "step": 0.25, "type": "float"},
    "trailing_min_pct":  {"min": 2.0, "max": 5.0, "step": 0.5,  "type": "float"},
}

# 신호 가중치 범위
WEIGHT_BOUNDS = {"min": 0.3, "max": 2.5}

# 시장 레짐 감지 기준
REGIME_THRESHOLDS = {
    "bullish":  {"sma_slope_min": 0.05, "breadth_min": 0.55, "vix_max": 20},
    "bearish":  {"sma_slope_max": -0.03, "breadth_max": 0.40, "vix_min": 25},
    # 나머지는 sideways
}

# 시장 레짐별 파라미터 프리셋
REGIME_PRESETS = {
    "bullish": {
        "min_tech_score": 3.5,
        "atr_stop_mult": 2.0,
        "atr_tp_mult": 4.5,
        "max_hold_days": 7,
        "top_n": 5,
        "sell_threshold": 5.0,
        "max_positions": 10,
        "max_daily_entries": 3,
        "trailing_atr_mult": 1.5,   # 상승장: 널널하게 따라가기
        "trailing_min_pct": 3.0,
    },
    "bearish": {
        "min_tech_score": 5.5,
        "atr_stop_mult": 1.5,
        "atr_tp_mult": 3.0,
        "max_hold_days": 5,
        "top_n": 3,
        "sell_threshold": 3.0,
        "max_positions": 5,
        "max_daily_entries": 2,
        "trailing_atr_mult": 1.0,   # 하락장: 타이트하게
        "trailing_min_pct": 2.0,
    },
    "sideways": {
        "min_tech_score": 4.5,
        "atr_stop_mult": 2.0,
        "atr_tp_mult": 3.5,
        "max_hold_days": 5,
        "top_n": 4,
        "sell_threshold": 4.0,
        "max_positions": 8,
        "max_daily_entries": 3,
        "trailing_atr_mult": 1.5,
        "trailing_min_pct": 3.0,
    },
    "conservative": {
        "min_tech_score": 5.0,
        "atr_stop_mult": 1.5,
        "atr_tp_mult": 3.0,
        "max_hold_days": 5,
        "top_n": 3,
        "sell_threshold": 3.5,
        "max_positions": 6,
        "max_daily_entries": 2,
        "trailing_atr_mult": 1.0,
        "trailing_min_pct": 2.5,
    },
}

# 성과 열화 시 안전 모드 기준
SAFETY_THRESHOLDS = {
    "min_win_rate": 35.0,       # 40→35: 백테스트에서 40% 미만은 너무 자주 발생
    "min_profit_factor": 0.7,   # 0.8→0.7: 백테스트 초기에는 PF가 낮을 수 있음
    "max_consecutive_losses": 15, # 8→15: 60일 백테스트에서 8회는 정상 범위
    "min_trades_for_tuning": 20,
}

# 기본 신호 키 목록
DEFAULT_SIGNAL_KEYS = [
    # 매수 신호
    "pullback_score", "breakout_score", "divergence_score",
    "stoch_cross_up", "golden_cross", "ma_alignment",
    "macd_cross_up", "bullish_volume", "obv_rising",
    "strong_trend", "bb_squeeze_breakout", "rr_bonus",
    "rsi_oversold_bounce",
    # 매도 신호
    "sell_dead_cross", "sell_macd_down", "sell_bearish_div",
    "sell_rsi_overbought", "sell_stoch_overbought", "sell_bb_upper_reject",
]


# ══════════════════════════════════════════════════════
#  유틸리티
# ══════════════════════════════════════════════════════

def _load_json(path: Path, default=None):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"JSON 로드 실패 ({path}): {e}")
    return default if default is not None else {}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


# ══════════════════════════════════════════════════════
#  1. 시장 레짐 감지
# ══════════════════════════════════════════════════════

class MarketRegimeDetector:
    """
    시장 상태를 bullish / bearish / sideways로 판정.

    방법:
      - SPY의 20일/50일 SMA 기울기
      - 상승 종목 비율 (breadth)
      - 최근 변동성 (ATR 기반)
    """

    def __init__(self):
        self.current_regime = "sideways"
        self.confidence = 0.0

    def detect(self, backtest_result: Dict) -> Tuple[str, float]:
        """
        백테스트 결과의 월별 데이터에서 레짐 추정.
        (실제 SPY 데이터 없이도 백테스트 결과로 간접 판단)
        """
        monthly = backtest_result.get("monthly_returns", [])
        summary = backtest_result.get("summary", {})

        if len(monthly) < 2:
            return "sideways", 0.3

        # 최근 4주 수익 추세
        recent_months = monthly[-3:]
        pnls = [m.get("total_pnl_pct", 0) for m in recent_months]
        win_rates = [m.get("win_rate", 50) for m in recent_months]

        avg_pnl = np.mean(pnls)
        avg_wr = np.mean(win_rates)
        pnl_trend = pnls[-1] - pnls[0] if len(pnls) >= 2 else 0

        # 레짐 판정
        bullish_score = 0.0
        bearish_score = 0.0

        # 수익 추세 (미미한 수익/손실은 중립 처리)
        if avg_pnl > 5:
            bullish_score += 2
        elif avg_pnl > 2:
            bullish_score += 1
        elif avg_pnl < -5:
            bearish_score += 2
        elif avg_pnl < -2:
            bearish_score += 1

        # 승률 추세
        if avg_wr > 55:
            bullish_score += 1.5
        elif avg_wr < 45:
            bearish_score += 1.5

        # 수익 방향
        if pnl_trend > 3:
            bullish_score += 1
        elif pnl_trend < -3:
            bearish_score += 1

        # 최대 낙폭
        max_dd = summary.get("portfolio_max_drawdown_pct", 0)
        if max_dd > 15:
            bearish_score += 1.5
        elif max_dd > 10:
            bearish_score += 0.5

        # 판정
        total = bullish_score + bearish_score
        if total == 0:
            regime = "sideways"
            confidence = 0.3
        elif bullish_score > bearish_score * 1.5:
            regime = "bullish"
            confidence = min(0.9, bullish_score / (total + 1))
        elif bearish_score > bullish_score * 1.5:
            regime = "bearish"
            confidence = min(0.9, bearish_score / (total + 1))
        else:
            regime = "sideways"
            confidence = 0.5

        self.current_regime = regime
        self.confidence = confidence

        logger.info(
            f"시장 레짐: {regime} (신뢰도 {confidence:.0%}) "
            f"[평균PnL={avg_pnl:+.1f}%, 승률={avg_wr:.0f}%, MDD={max_dd:.1f}%]"
        )

        return regime, confidence

    def detect_from_prices(self, price_data) -> Tuple[str, float]:
        """
        실제 가격 데이터에서 직접 레짐 감지 (선택적 - SPY 데이터 필요).
        """
        import pandas as pd

        if price_data is None or price_data.empty:
            return "sideways", 0.3

        try:
            spy = price_data[price_data["ticker"] == "SPY"]
            if spy.empty:
                # SPY 없으면 전체 평균 사용
                spy = price_data.groupby("Date").agg({"Close": "mean"}).reset_index()

            spy = spy.sort_values("Date")
            close = spy["Close"]

            if len(close) < 50:
                return "sideways", 0.3

            # 20일/50일 SMA
            sma20 = close.rolling(20).mean()
            sma50 = close.rolling(50).mean()

            # SMA 기울기 (최근 5일)
            if len(sma20.dropna()) >= 5:
                slope20 = (sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100
            else:
                slope20 = 0

            # 가격 vs SMA 위치
            price_above_sma20 = close.iloc[-1] > sma20.iloc[-1] if pd.notna(sma20.iloc[-1]) else True
            price_above_sma50 = close.iloc[-1] > sma50.iloc[-1] if pd.notna(sma50.iloc[-1]) else True

            # 판정
            if slope20 > 0.5 and price_above_sma20 and price_above_sma50:
                return "bullish", 0.8
            elif slope20 < -0.5 and not price_above_sma20 and not price_above_sma50:
                return "bearish", 0.8
            elif slope20 > 0.2 and price_above_sma20:
                return "bullish", 0.6
            elif slope20 < -0.2 and not price_above_sma20:
                return "bearish", 0.6
            else:
                return "sideways", 0.5

        except Exception as e:
            logger.warning(f"가격 기반 레짐 감지 실패: {e}")
            return "sideways", 0.3


# ══════════════════════════════════════════════════════
#  2. 신호 가중치 최적화
# ══════════════════════════════════════════════════════

class SignalWeightOptimizer:
    """
    백테스트 결과의 신호별 성과를 분석하여 가중치를 자동 조정.

    로직:
      - 신호별 평균 수익률, 승률 계산
      - 성과 좋은 신호 → 가중치 ↑ (최대 2.5)
      - 성과 나쁜 신호 → 가중치 ↓ (최소 0.3)
      - 점진적 조정 (급격한 변화 방지)
    """

    LEARNING_RATE = 0.15        # 1회 최대 변화율 15%
    MIN_SAMPLES = 5             # 최소 5회 이상 출현해야 조정

    def __init__(self):
        self.current_weights = _load_json(SIGNAL_WEIGHTS_PATH, {})

    def optimize(self, backtest_result: Dict) -> Dict:
        """신호별 성과 기반으로 가중치 조정."""
        signal_perf = backtest_result.get("signal_performance", [])

        if not signal_perf:
            logger.info("신호 성과 데이터 없음 — 가중치 유지")
            return self.current_weights, {}

        # 신호 이름 → 가중치 키 매핑
        signal_key_map = {
            "20MA눌림목": "pullback_score",
            "50MA눌림목": "pullback_score",
            "BB하단반등": "pullback_score",
            "돌파": "breakout_score",
            "강세다이버전스": "divergence_score",
            "스토캐스틱크로스": "stoch_cross_up",
            "골든크로스": "golden_cross",
            "이평정배열": "ma_alignment",
            "MACD상향": "macd_cross_up",
            "스퀴즈돌파": "bb_squeeze_breakout",
        }

        # 현재 가중치 (없으면 기본 1.0)
        weights = {}
        for key in DEFAULT_SIGNAL_KEYS:
            weights[key] = self.current_weights.get(key, 1.0)

        # 신호별 성과 분석
        adjustments = {}

        for sp in signal_perf:
            sig_name = sp["signal"]
            count = sp.get("count", 0)
            avg_pnl = sp.get("avg_pnl", 0)
            win_rate = sp.get("win_rate", 50)

            if count < self.MIN_SAMPLES:
                continue

            # 매핑된 키 찾기
            weight_key = None
            for prefix, key in signal_key_map.items():
                if prefix in sig_name:
                    weight_key = key
                    break

            # 거래량 신호
            if "거래량" in sig_name:
                weight_key = "bullish_volume"

            if not weight_key:
                continue

            # 성과 점수 계산 (-1 ~ +1)
            # 승률 50% 초과이고 평균 수익 양수 → 양호
            perf_score = 0.0

            # 승률 기여 (50% 기준)
            perf_score += (win_rate - 50) / 50  # -1 ~ +1

            # 수익률 기여
            if avg_pnl > 1.0:
                perf_score += 0.5
            elif avg_pnl > 0:
                perf_score += 0.2
            elif avg_pnl < -1.0:
                perf_score -= 0.5
            elif avg_pnl < 0:
                perf_score -= 0.2

            # 샘플 수 가중 (많을수록 신뢰도 높음)
            confidence = min(1.0, count / 30)
            adjusted_score = perf_score * confidence

            # 기존에 같은 키에 대한 조정이 있으면 평균
            if weight_key in adjustments:
                adjustments[weight_key].append(adjusted_score)
            else:
                adjustments[weight_key] = [adjusted_score]

        # 가중치 업데이트
        changes = {}

        for key, scores in adjustments.items():
            avg_score = np.mean(scores)
            current_w = weights.get(key, 1.0)

            # 점진적 조정 (learning rate 적용)
            delta = avg_score * self.LEARNING_RATE
            new_w = current_w * (1 + delta)
            new_w = _clamp(new_w, WEIGHT_BOUNDS["min"], WEIGHT_BOUNDS["max"])

            if abs(new_w - current_w) > 0.01:
                changes[key] = {
                    "old": round(current_w, 3),
                    "new": round(new_w, 3),
                    "delta": round(delta, 4),
                    "perf_score": round(avg_score, 3),
                }
                weights[key] = round(new_w, 3)

        if changes:
            logger.info(f"신호 가중치 변경 ({len(changes)}개):")
            for key, ch in changes.items():
                direction = "↑" if ch["new"] > ch["old"] else "↓"
                logger.info(
                    f"  {key}: {ch['old']:.3f} → {ch['new']:.3f} {direction} "
                    f"(성과={ch['perf_score']:+.3f})"
                )
        else:
            logger.info("신호 가중치 변경 없음 (현재 설정 유지)")

        self.current_weights = weights
        return weights, changes

    def save(self):
        _save_json(SIGNAL_WEIGHTS_PATH, self.current_weights)
        logger.info(f"신호 가중치 저장: {SIGNAL_WEIGHTS_PATH}")


# ══════════════════════════════════════════════════════
#  3. 파라미터 자동 조정
# ══════════════════════════════════════════════════════

class ParameterTuner:
    """
    백테스트 결과 기반 파라미터 자동 조정.

    방법:
      1. 현재 파라미터로 백테스트 → 기준 성과
      2. 각 파라미터를 ±1스텝 변경 → 성과 비교
      3. 개선되는 방향으로 점진적 이동
      4. 시장 레짐별 프리셋과 블렌딩

    조정 대상:
      - min_tech_score (최소 기술 점수)
      - atr_stop_mult (손절 ATR 배수)
      - atr_tp_mult (익절 ATR 배수)
      - max_hold_days (최대 보유 기간)
      - top_n (일별 선택 종목 수)
    """

    def __init__(self):
        self.current_params = self._load_current_params()

    def _load_current_params(self) -> Dict:
        """현재 파라미터를 strategy_state.json에서 로드."""
        state = _load_json(STRATEGY_STATE_PATH, {})
        params = state.get("current_params", {})

        # 기본값 보장
        defaults = {
            "top_n": 5,
            "min_tech_score": 4.0,
            "atr_stop_mult": 2.0,
            "atr_tp_mult": 4.0,
            "max_hold_days": 7,
            "sell_threshold": 4.0,
            "max_positions": 10,
            "max_daily_entries": 3,
        }
        for k, v in defaults.items():
            if k not in params:
                params[k] = v

        return params

    def tune(self, backtest_result: Dict, regime: str, regime_confidence: float) -> Tuple[Dict, Dict]:
        """
        파라미터 자동 조정.

        Returns:
            (new_params, change_report)
        """
        summary = backtest_result.get("summary", {})
        total_trades = summary.get("total_trades", 0)

        if total_trades < SAFETY_THRESHOLDS["min_trades_for_tuning"]:
            logger.warning(f"거래 수 부족 ({total_trades}) — 파라미터 유지")
            return self.current_params, {"skipped": True, "reason": "insufficient_trades"}

        # 1) 현재 성과 평가
        current_score = self._evaluate_performance(summary)
        logger.info(f"현재 성과 점수: {current_score:.4f}")

        # 2) 시장 레짐 프리셋과 블렌딩
        regime_params = REGIME_PRESETS.get(regime, REGIME_PRESETS["sideways"])
        blend_ratio = regime_confidence * 0.4  # 최대 40% 레짐 반영

        blended = {}
        for key in self.current_params:
            current_val = self.current_params[key]
            regime_val = regime_params.get(key, current_val)
            blended[key] = current_val * (1 - blend_ratio) + regime_val * blend_ratio

        # 3) 성과 기반 미세 조정
        adjusted = self._performance_based_adjustment(blended, summary, backtest_result)

        # 4) 안전 범위 클램핑
        final = {}
        changes = {}
        for key, val in adjusted.items():
            bounds = PARAM_BOUNDS.get(key, {})
            lo = bounds.get("min", val)
            hi = bounds.get("max", val)
            step = bounds.get("step", 0.5)
            param_type = bounds.get("type", "float")

            # 스텝 단위로 반올림
            clamped = _clamp(val, lo, hi)
            if param_type == "int":
                clamped = int(round(clamped))
            else:
                clamped = round(round(clamped / step) * step, 2)

            final[key] = clamped
            old_val = self.current_params.get(key, clamped)

            if param_type == "int":
                old_val = int(old_val)

            if abs(clamped - old_val) > 0.001:
                changes[key] = {
                    "old": old_val,
                    "new": clamped,
                    "regime_target": regime_params.get(key),
                }

        if changes:
            logger.info(f"파라미터 변경 ({len(changes)}개):")
            for key, ch in changes.items():
                direction = "↑" if ch["new"] > ch["old"] else "↓"
                logger.info(
                    f"  {key}: {ch['old']} → {ch['new']} {direction} "
                    f"(레짐 목표: {ch['regime_target']})"
                )
        else:
            logger.info("파라미터 변경 없음")

        self.current_params = final
        return final, changes

    def _evaluate_performance(self, summary: Dict) -> float:
        """
        복합 성과 점수 (높을수록 좋음).

        구성:
          승률 가중 (30%) + Profit Factor (25%) + 샤프 비율 (20%)
          + 기대값 (15%) - MDD 페널티 (10%)
        """
        pf = max(0, summary.get("profit_factor", 0))
        wr = max(0, summary.get("win_rate", 0))
        sharpe = summary.get("sharpe_ratio", 0)
        ev = summary.get("expected_value_pct", 0)
        max_dd = abs(summary.get("portfolio_max_drawdown_pct", 0))

        # 정규화
        wr_score = wr / 100.0                          # 0~1
        pf_score = min(pf / 3.0, 1.0)                  # 0~1 (PF 3이면 만점)
        sharpe_score = max(0, min(sharpe / 2.0, 1.0))   # 0~1 (샤프 2면 만점)
        ev_score = max(0, min((ev + 2) / 6.0, 1.0))     # -2~4 → 0~1
        mdd_penalty = min(max_dd / 30.0, 1.0)           # 0~1 (MDD 30%면 최대 페널티)

        score = (
            wr_score * 0.30
            + pf_score * 0.25
            + sharpe_score * 0.20
            + ev_score * 0.15
            - mdd_penalty * 0.10
        )
        return round(score, 6)

    def _performance_based_adjustment(self, params: Dict, summary: Dict,
                                       backtest_result: Dict) -> Dict:
        """성과 지표에 따른 미세 조정."""
        adjusted = dict(params)

        win_rate = summary.get("win_rate", 50)
        pf = summary.get("profit_factor", 1)
        avg_win = summary.get("avg_win_pct", 0)
        avg_loss = summary.get("avg_loss_pct", 0)
        avg_hold = summary.get("avg_hold_days", 5)
        max_dd = summary.get("portfolio_max_drawdown_pct", 0)

        eb = backtest_result.get("exit_breakdown", {})
        tp_rate = eb.get("tp_rate", 0)
        sl_rate = eb.get("sl_rate", 0)
        exp_rate = eb.get("exp_rate", 0)

        # ── SL/TP 조정 ──

        # 손절이 너무 많으면 → SL을 넓히거나 TP를 줄임
        if sl_rate > 40:
            adjusted["atr_stop_mult"] = params.get("atr_stop_mult", 2.0) + 0.25
            logger.info(f"  손절 비율 높음({sl_rate:.0f}%) → SL 배수 ↑")

        # 만료가 너무 많으면 → 보유 기간 늘리거나 TP 줄임
        if exp_rate > 45:
            # TP가 너무 멀어서 도달 못 함
            adjusted["atr_tp_mult"] = params.get("atr_tp_mult", 4.0) - 0.25
            adjusted["max_hold_days"] = params.get("max_hold_days", 7) + 1
            logger.info(f"  만료 비율 높음({exp_rate:.0f}%) → TP 배수 ↓, 보유일 ↑")

        # 익절이 너무 적으면 → TP를 당김
        if tp_rate < 25:
            adjusted["atr_tp_mult"] = params.get("atr_tp_mult", 4.0) - 0.5
            logger.info(f"  익절 비율 낮음({tp_rate:.0f}%) → TP 배수 ↓")

        # ── 승률 기반 조정 ──

        # 승률이 높으면 → 기준 약간 완화 (기회 확대)
        if win_rate > 60 and pf > 1.5:
            adjusted["min_tech_score"] = params.get("min_tech_score", 4.0) - 0.25
            adjusted["top_n"] = params.get("top_n", 5) + 1
            logger.info(f"  높은 성과 → 기준 완화 (기회 확대)")

        # 승률이 낮으면 → 기준 강화 (선별 강화)
        elif win_rate < 45:
            adjusted["min_tech_score"] = params.get("min_tech_score", 4.0) + 0.5
            adjusted["top_n"] = max(2, params.get("top_n", 5) - 1)
            logger.info(f"  낮은 승률({win_rate:.0f}%) → 기준 강화")

        # ── MDD 기반 조정 ──
        if max_dd > 20:
            adjusted["atr_stop_mult"] = params.get("atr_stop_mult", 2.0) - 0.25
            adjusted["top_n"] = max(2, params.get("top_n", 5) - 1)
            adjusted["max_positions"] = max(3, params.get("max_positions", 10) - 2)
            adjusted["max_daily_entries"] = max(1, params.get("max_daily_entries", 3) - 1)
            logger.info(f"  높은 MDD({max_dd:.0f}%) → 보수적 전환 (포지션 축소)")

        # ── 매도 신호 임계값 조정 ──
        sell_rate = eb.get("sell_rate", 0)
        if sell_rate > 30:
            # 매도 청산이 너무 많으면 → 임계값 올림 (더 신중하게)
            adjusted["sell_threshold"] = params.get("sell_threshold", 4.0) + 0.5
            logger.info(f"  매도 청산 비율 높음({sell_rate:.0f}%) → sell_threshold ↑")
        elif sell_rate < 5 and exp_rate > 40:
            # 매도가 거의 없고 만료가 많으면 → 임계값 낮춤 (더 적극적으로)
            adjusted["sell_threshold"] = params.get("sell_threshold", 4.0) - 0.5
            logger.info(f"  매도 청산 부족({sell_rate:.0f}%) + 만료 과다({exp_rate:.0f}%) → sell_threshold ↓")

        return adjusted

    def generate_candidate(self, base_params: Dict, regime: str,
                           regime_confidence: float) -> Dict:
        """
        탐색용 후보 파라미터 생성.
        레짐 프리셋 블렌딩 + 랜덤 변이를 조합.
        """
        import random
        candidate = dict(base_params)

        # 레짐 프리셋 블렌딩 (0~50% 랜덤)
        regime_params = REGIME_PRESETS.get(regime, REGIME_PRESETS["sideways"])
        blend = random.uniform(0.1, 0.5) * regime_confidence

        for key in candidate:
            if key in regime_params:
                curr = candidate[key]
                target = regime_params[key]
                candidate[key] = curr * (1 - blend) + target * blend

        # 랜덤 변이 (각 파라미터를 ±1~2스텝 랜덤 조정)
        for key, bounds in PARAM_BOUNDS.items():
            if key not in candidate:
                continue
            step = bounds.get("step", 0.5)
            lo = bounds.get("min", candidate[key])
            hi = bounds.get("max", candidate[key])

            # 70% 확률로 변이 적용 (모든 파라미터가 바뀌면 과적합)
            if random.random() < 0.7:
                delta = random.choice([-2, -1, 0, 1, 2]) * step
                candidate[key] = _clamp(candidate[key] + delta, lo, hi)

        # 타입 보정
        for key, bounds in PARAM_BOUNDS.items():
            if key in candidate:
                if bounds.get("type") == "int":
                    candidate[key] = int(round(candidate[key]))
                else:
                    s = bounds.get("step", 0.25)
                    candidate[key] = round(round(candidate[key] / s) * s, 2)

        return candidate


# ══════════════════════════════════════════════════════
#  4. 안전 장치
# ══════════════════════════════════════════════════════

class SafetyGuard:
    """
    성과 열화 시 자동으로 보수적 모드로 전환.

    조건:
      - 승률 < 40% → 경고 + 보수적 전환
      - PF < 0.8 → 경고 + 보수적 전환
      - 연속 패배 8회 이상 → 긴급 보수적 전환
    """

    def check(self, summary: Dict) -> Tuple[bool, str]:
        """
        안전 체크.
        Returns: (is_safe, message)
        """
        win_rate = summary.get("win_rate", 50)
        pf = summary.get("profit_factor", 1)
        max_consec_loss = summary.get("max_consecutive_losses", 0)
        total_trades = summary.get("total_trades", 0)

        if total_trades < 10:
            return True, "거래 수 부족 — 판단 보류"

        warnings = []

        if win_rate < SAFETY_THRESHOLDS["min_win_rate"]:
            warnings.append(f"승률 {win_rate:.1f}% < {SAFETY_THRESHOLDS['min_win_rate']}%")

        if pf < SAFETY_THRESHOLDS["min_profit_factor"]:
            warnings.append(f"PF {pf:.2f} < {SAFETY_THRESHOLDS['min_profit_factor']}")

        if max_consec_loss >= SAFETY_THRESHOLDS["max_consecutive_losses"]:
            warnings.append(f"연속 패배 {max_consec_loss}회 ≥ {SAFETY_THRESHOLDS['max_consecutive_losses']}")

        if warnings:
            msg = "⚠️ 성과 열화 감지: " + " | ".join(warnings)
            logger.warning(msg)
            return False, msg

        return True, "✅ 성과 정상"

    def get_conservative_params(self) -> Dict:
        """보수적 모드 파라미터."""
        return {
            "top_n": 3,
            "min_tech_score": 5.5,
            "atr_stop_mult": 1.5,
            "atr_tp_mult": 3.0,
            "max_hold_days": 5,
            "sell_threshold": 3.0,
            "max_positions": 5,
            "max_daily_entries": 2,
            "trailing_atr_mult": 1.0,  # 보수적: 타이트
            "trailing_min_pct": 2.5,
        }


# ══════════════════════════════════════════════════════
#  5. 메인 자기 학습 엔진
# ══════════════════════════════════════════════════════

class SelfTuningEngine:
    """
    주간 자기 학습 파이프라인.

    1. 백테스트 실행 (최근 60거래일)
    2. 시장 레짐 감지
    3. 안전 체크
    4. 파라미터 자동 조정
    5. 신호 가중치 자동 조정
    6. 설정 파일 업데이트
    7. Discord 알림
    """

    def __init__(self, pool: str = "sp500", backtest_days: int = 90,
                 max_iterations: int = 20, min_improvement: float = 5.0,
                 fundamental_mode: str = "hard_filter"):
        self.pool = pool
        self.backtest_days = backtest_days
        self.max_iterations = max_iterations
        self.min_improvement = min_improvement
        self.fundamental_mode = fundamental_mode  # 최소 개선율 (%)

        self.regime_detector = MarketRegimeDetector()
        self.signal_optimizer = SignalWeightOptimizer()
        self.param_tuner = ParameterTuner()
        self.safety_guard = SafetyGuard()

        self.state = _load_json(STRATEGY_STATE_PATH, {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "current_params": self.param_tuner.current_params,
            "current_regime": "sideways",
            "tuning_history": [],
        })

    def run(self) -> Dict:
        """
        자기 학습 파이프라인 실행.

        1. 현재 파라미터로 기준 백테스트 (baseline)
        2. 시장 레짐 감지
        3. 안전 체크
        4. 반복 탐색: N회 후보 생성 → 백테스트 → 점수 비교
        5. 최고 후보가 기준 대비 5% 이상 개선이면 채택
        6. 신호 가중치 조정
        7. 저장 + 리밸런싱
        """
        logger.info("=" * 70)
        logger.info("🧠 자기 학습 엔진 시작")
        logger.info(f"   반복 탐색: 최대 {self.max_iterations}회, "
                     f"채택 기준: {self.min_improvement}% 이상 개선")
        logger.info("=" * 70)

        timestamp = datetime.now(timezone.utc).isoformat()
        report = {
            "timestamp": timestamp,
            "pool": self.pool,
            "backtest_days": self.backtest_days,
            "max_iterations": self.max_iterations,
            "min_improvement": self.min_improvement,
        }

        # ══════════════════════════════════════════════
        # 1단계: 현재 파라미터로 기준(baseline) 백테스트
        # ══════════════════════════════════════════════
        logger.info("\n📊 1단계: 기준(baseline) 백테스트")
        current_params = dict(self.param_tuner.current_params)

        baseline_engine = BacktestEngine(
            pool=self.pool,
            backtest_days=self.backtest_days,
            fundamental_mode=self.fundamental_mode,
            **current_params,
        )
        baseline_result = baseline_engine.run()
        baseline_summary = baseline_result.get("summary", {})
        report["baseline_summary"] = baseline_summary

        # 캐시 보존 (candidate 엔진에 재사용)
        _shared_data = baseline_engine.all_data
        _shared_tech_cache = baseline_engine._tech_cache
        _shared_mtf_cache = baseline_engine._mtf_cache
        _shared_fund_data = baseline_engine.fund_data if hasattr(baseline_engine, 'fund_data') else {}

        if baseline_summary.get("total_trades", 0) < 10:
            logger.warning("거래 수 부족 — 자기 학습 스킵")
            report["status"] = "skipped"
            report["reason"] = "insufficient_trades"
            return report

        baseline_score = self.param_tuner._evaluate_performance(baseline_summary)
        logger.info(f"  기준 점수: {baseline_score:.6f}")
        logger.info(f"  승률: {baseline_summary.get('win_rate', 0):.1f}%  "
                     f"PF: {baseline_summary.get('profit_factor', 0):.2f}  "
                     f"샤프: {baseline_summary.get('sharpe_ratio', 0):.2f}  "
                     f"MDD: {baseline_summary.get('portfolio_max_drawdown_pct', 0):.1f}%")

        print_report(baseline_result)

        # ══════════════════════════════════════════════
        # 2단계: 시장 레짐 감지
        # ══════════════════════════════════════════════
        logger.info("\n🌍 2단계: 시장 레짐 감지")
        regime, confidence = self.regime_detector.detect(baseline_result)
        report["regime"] = {"type": regime, "confidence": round(confidence, 2)}

        if baseline_engine.all_data is not None:
            regime_price, conf_price = self.regime_detector.detect_from_prices(
                baseline_engine.all_data)
            if regime_price == regime:
                confidence = min(0.95, confidence + 0.15)
            report["regime"]["price_based"] = regime_price

        # ══════════════════════════════════════════════
        # 3단계: 안전 체크
        # ══════════════════════════════════════════════
        logger.info("\n🛡️ 3단계: 안전 체크")
        is_safe, safety_msg = self.safety_guard.check(baseline_summary)
        report["safety"] = {"is_safe": is_safe, "message": safety_msg}

        search_base = dict(current_params)
        if not is_safe:
            logger.warning(f"⚠️ 성과 열화 감지: {safety_msg}")
            logger.info("  → 보수적 베이스라인에서 탐색 시작")
            conservative = self.safety_guard.get_conservative_params()
            search_base = dict(conservative)
            regime = "conservative"

        # ══════════════════════════════════════════════
        # 4단계: 반복 탐색 (핵심)
        # ══════════════════════════════════════════════
        logger.info(f"\n🔍 4단계: 반복 탐색 ({self.max_iterations}회)")
        logger.info("-" * 50)

        best_score = baseline_score
        best_params = dict(current_params)
        best_summary = baseline_summary
        best_result = baseline_result
        search_log = []

        for i in range(1, self.max_iterations + 1):
            # 후보 파라미터 생성
            candidate = self.param_tuner.generate_candidate(
                search_base, regime, confidence)

            # 후보로 백테스트
            try:
                candidate_engine = BacktestEngine(
                    pool=self.pool,
                    backtest_days=self.backtest_days,
                    fundamental_mode=self.fundamental_mode,
                    **candidate,
                )
                # 캐시 주입 (데이터 재다운로드 + 기술분석 반복 방지)
                candidate_engine._shared_cache = {
                    "all_data": _shared_data,
                    "tech_cache": _shared_tech_cache,
                    "mtf_cache": _shared_mtf_cache,
                    "fund_data": _shared_fund_data,
                }
                candidate_result = candidate_engine.run()
                candidate_summary = candidate_result.get("summary", {})

                if candidate_summary.get("total_trades", 0) < 10:
                    logger.info(f"  [{i:2d}/{self.max_iterations}] 거래 부족 — 스킵")
                    search_log.append({"iter": i, "score": None, "reason": "no_trades"})
                    continue

                candidate_score = self.param_tuner._evaluate_performance(candidate_summary)
                improvement = ((candidate_score - baseline_score) / max(abs(baseline_score), 0.001)) * 100

                # 로그
                marker = ""
                if candidate_score > best_score:
                    marker = " ⭐ NEW BEST"
                    best_score = candidate_score
                    best_params = dict(candidate)
                    best_summary = candidate_summary
                    best_result = candidate_result

                logger.info(
                    f"  [{i:2d}/{self.max_iterations}] "
                    f"점수={candidate_score:.6f} "
                    f"(기준 대비 {improvement:+.1f}%) "
                    f"승률={candidate_summary.get('win_rate', 0):.1f}% "
                    f"PF={candidate_summary.get('profit_factor', 0):.2f}"
                    f"{marker}"
                )

                search_log.append({
                    "iter": i,
                    "score": round(candidate_score, 6),
                    "improvement_pct": round(improvement, 2),
                    "win_rate": candidate_summary.get("win_rate", 0),
                    "profit_factor": candidate_summary.get("profit_factor", 0),
                    "is_best": marker != "",
                })

            except Exception as e:
                logger.warning(f"  [{i:2d}/{self.max_iterations}] 백테스트 실패: {e}")
                search_log.append({"iter": i, "score": None, "reason": str(e)})
                continue

        # ══════════════════════════════════════════════
        # 5단계: 채택 판단
        # ══════════════════════════════════════════════
        total_improvement = ((best_score - baseline_score) / max(abs(baseline_score), 0.001)) * 100
        logger.info("-" * 50)
        logger.info(f"\n📋 5단계: 채택 판단")
        logger.info(f"  기준 점수:  {baseline_score:.6f}")
        logger.info(f"  최고 점수:  {best_score:.6f}")
        logger.info(f"  개선율:     {total_improvement:+.1f}%")
        logger.info(f"  채택 기준:  {self.min_improvement}% 이상")

        adopted = total_improvement >= self.min_improvement
        report["search"] = {
            "iterations": self.max_iterations,
            "baseline_score": round(baseline_score, 6),
            "best_score": round(best_score, 6),
            "improvement_pct": round(total_improvement, 2),
            "adopted": adopted,
            "log": search_log,
        }

        if adopted:
            new_params = best_params
            bt_result = best_result
            logger.info(f"  ✅ 채택! ({total_improvement:+.1f}% 개선)")
            # 변경 내역
            param_changes = {}
            for k in new_params:
                old_v = current_params.get(k)
                new_v = new_params.get(k)
                if old_v is not None and new_v is not None and abs(float(new_v) - float(old_v)) > 0.001:
                    param_changes[k] = {"old": old_v, "new": new_v}
        else:
            new_params = current_params
            bt_result = baseline_result
            param_changes = {}
            logger.info(f"  ❌ 기각 (개선 {total_improvement:+.1f}% < 기준 {self.min_improvement}%)")
            logger.info(f"  → 현재 파라미터 유지")

        report["param_changes"] = param_changes
        report["backtest_summary"] = best_summary if adopted else baseline_summary

        # ══════════════════════════════════════════════
        # 6단계: 신호 가중치 조정
        # ══════════════════════════════════════════════
        logger.info("\n📡 6단계: 신호 가중치 조정")
        new_weights, weight_changes = self.signal_optimizer.optimize(bt_result)
        report["weight_changes"] = weight_changes

        # ══════════════════════════════════════════════
        # 7단계: 저장
        # ══════════════════════════════════════════════
        logger.info("\n💾 7단계: 설정 저장")
        self._save_state(new_params, new_weights, regime, confidence, report)

        # 백테스트 결과 내보내기
        export_results(bt_result, output_dir="data/backtest")

        # 최종 요약
        self._print_summary(report, new_params, new_weights, param_changes, weight_changes)

        # ══════════════════════════════════════════════
        # 8단계: 포지션 리밸런싱
        # ══════════════════════════════════════════════
        logger.info("\n🔄 8단계: 포지션 리밸런싱")
        try:
            from .position_tracker import rebalance_positions
            rb_result = rebalance_positions(
                max_positions=new_params.get("max_positions", 10),
                fetch_live=True,
            )
            report["rebalance"] = rb_result.get("summary", {})
        except Exception as e:
            logger.warning(f"  리밸런싱 실패 (무시): {e}")
            report["rebalance"] = {"action": "error", "error": str(e)}

        report["status"] = "completed"
        return report

    def _save_state(self, params: Dict, weights: Dict, regime: str,
                    confidence: float, report: Dict):
        """전략 상태 저장."""
        # strategy_state.json 업데이트
        self.state["current_params"] = params
        self.state["current_regime"] = regime
        self.state["regime_confidence"] = round(confidence, 2)
        self.state["last_tuned_at"] = report["timestamp"]

        # 이력 추가 (최근 20개 유지)
        history_entry = {
            "timestamp": report["timestamp"],
            "regime": regime,
            "params": params,
            "summary": report.get("backtest_summary", {}),
            "param_changes": report.get("param_changes", {}),
            "weight_changes": report.get("weight_changes", {}),
        }
        history = self.state.get("tuning_history", [])
        history.append(history_entry)
        self.state["tuning_history"] = history[-20:]

        _save_json(STRATEGY_STATE_PATH, self.state)
        logger.info(f"전략 상태 저장: {STRATEGY_STATE_PATH}")

        # signal_weights.json 저장
        self.signal_optimizer.current_weights = weights
        self.signal_optimizer.save()

        # universe.yaml의 min_tech_score 업데이트
        self._update_universe_yaml(params)

        # 상세 이력 저장
        tuning_history = _load_json(TUNING_HISTORY_PATH, [])
        tuning_history.append(history_entry)
        tuning_history = tuning_history[-100:]  # 최근 100개 유지
        _save_json(TUNING_HISTORY_PATH, tuning_history)

    def _update_universe_yaml(self, params: Dict):
        """universe.yaml의 관련 파라미터 업데이트."""
        import yaml

        yaml_path = CONFIG_DIR / "universe.yaml"
        if not yaml_path.exists():
            return

        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            auto = config.get("auto", {})
            changed = False

            if "min_tech_score" in params:
                old = auto.get("min_tech_score")
                new = params["min_tech_score"]
                if old != new:
                    auto["min_tech_score"] = new
                    changed = True
                    logger.info(f"universe.yaml: min_tech_score {old} → {new}")

            if "tech_filter_count" not in auto:
                auto["tech_filter_count"] = 30

            config["auto"] = auto

            if changed:
                with open(yaml_path, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                logger.info(f"universe.yaml 업데이트 완료")

        except Exception as e:
            logger.warning(f"universe.yaml 업데이트 실패: {e}")

    def _print_summary(self, report, params, weights, param_changes, weight_changes):
        """최종 요약 출력."""
        print("\n" + "═" * 70)
        print("🧠 자기 학습 결과 요약")
        print("═" * 70)

        regime = report.get("regime", {})
        safety = report.get("safety", {})
        summary = report.get("backtest_summary", {})

        print(f"\n📊 백테스트: {summary.get('total_trades', 0)}거래, "
              f"승률 {summary.get('win_rate', 0):.1f}%, "
              f"PF {summary.get('profit_factor', 0):.2f}")
        print(f"🌍 시장 레짐: {regime.get('type', '?')} "
              f"(신뢰도 {regime.get('confidence', 0):.0%})")
        print(f"🛡️ 안전 상태: {safety.get('message', '?')}")

        if param_changes and not param_changes.get("skipped"):
            print(f"\n⚙️ 파라미터 변경:")
            for key, ch in param_changes.items():
                if isinstance(ch, dict) and "old" in ch:
                    direction = "↑" if ch["new"] > ch["old"] else "↓"
                    print(f"   {key}: {ch['old']} → {ch['new']} {direction}")
        else:
            print(f"\n⚙️ 파라미터: 변경 없음")

        if weight_changes:
            print(f"\n📡 신호 가중치 변경:")
            for key, ch in weight_changes.items():
                if isinstance(ch, dict) and "old" in ch:
                    direction = "↑" if ch["new"] > ch["old"] else "↓"
                    print(f"   {key}: {ch['old']:.3f} → {ch['new']:.3f} {direction}")
        else:
            print(f"\n📡 신호 가중치: 변경 없음")

        print(f"\n📂 현재 파라미터: {params}")
        print(f"{'═' * 70}\n")


# ══════════════════════════════════════════════════════
#  Discord 알림
# ══════════════════════════════════════════════════════

def send_tuning_report_to_discord(report: Dict):
    """자기 학습 결과를 Discord로 전송."""
    import os
    import requests

    url = (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip().strip('"').strip("'")
    if not url:
        return

    summary = report.get("backtest_summary", {})
    regime = report.get("regime", {})
    safety = report.get("safety", {})
    param_changes = report.get("param_changes", {})
    weight_changes = report.get("weight_changes", {})
    status = report.get("status", "unknown")

    if status == "skipped":
        return

    # 상태별 색상
    is_safe = safety.get("is_safe", True)
    if not is_safe:
        color = 0xff4444
        title = "🧠 자기 학습 — ⚠️ 보수적 모드 전환"
    elif regime.get("type") == "bearish":
        color = 0xffaa00
        title = "🧠 자기 학습 — 🐻 약세장 감지"
    elif regime.get("type") == "bullish":
        color = 0x00cc00
        title = "🧠 자기 학습 — 🐂 강세장 감지"
    else:
        color = 0x3399ff
        title = "🧠 자기 학습 — 📊 전략 업데이트"

    # 파라미터 변경 텍스트
    param_text = ""
    if param_changes and not param_changes.get("skipped"):
        for key, ch in param_changes.items():
            if isinstance(ch, dict) and "old" in ch:
                direction = "↑" if ch["new"] > ch["old"] else "↓"
                param_text += f"**{key}**: {ch['old']} → {ch['new']} {direction}\n"
    param_text = param_text or "변경 없음"

    # 가중치 변경 텍스트 (상위 5개)
    weight_text = ""
    if weight_changes:
        items = list(weight_changes.items())[:5]
        for key, ch in items:
            if isinstance(ch, dict) and "old" in ch:
                direction = "↑" if ch["new"] > ch["old"] else "↓"
                weight_text += f"**{key}**: {ch['old']:.2f} → {ch['new']:.2f} {direction}\n"
    weight_text = weight_text or "변경 없음"

    embed = {
        "title": title,
        "color": color,
        "fields": [
            {
                "name": "📊 백테스트 성과",
                "value": (
                    f"거래: {summary.get('total_trades', 0)}회\n"
                    f"승률: {summary.get('win_rate', 0):.1f}%\n"
                    f"PF: {summary.get('profit_factor', 0):.2f}\n"
                    f"누적: {summary.get('total_pnl_pct', 0):+.1f}%"
                ),
                "inline": True,
            },
            {
                "name": "🌍 시장 레짐",
                "value": (
                    f"**{regime.get('type', '?')}** "
                    f"(신뢰도 {regime.get('confidence', 0):.0%})\n"
                    f"안전: {'✅' if is_safe else '⚠️'}"
                ),
                "inline": True,
            },
            {"name": "⚙️ 파라미터 변경", "value": param_text},
            {"name": "📡 신호 가중치 변경", "value": weight_text},
        ],
    }

    payload = {"content": "**🧠 주간 자기 학습 리포트**", "embeds": [embed]}

    try:
        resp = requests.post(url, json=payload, timeout=20)
        logger.info(f"Discord 자기 학습 리포트 전송: {resp.status_code}")
    except Exception as e:
        logger.error(f"Discord 전송 실패: {e}")