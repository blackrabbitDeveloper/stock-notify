"""
재무 지표(Fundamental) 분석기.

yfinance에서 PER, ROE, 영업이익률, 매출 성장률을 수집하고
하드 필터 / 소프트 점수 / 별도 표시 3가지 모드로 활용.

모드:
  hard_filter  — 기준 미달 시 종목 제외
  soft_score   — 기술 점수에 가감점 (±1.0)
  display_only — 필터 없이 대시보드에 정보만 표시
"""
import logging
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  기본 설정
# ══════════════════════════════════════════════════════

# 하드 필터 기준 (이 범위 밖이면 제외)
HARD_FILTER_DEFAULTS = {
    "per_max": 80,          # PER > 80 → 과대평가 제외
    "per_min": 0,           # PER < 0 → 적자 기업 제외
    "roe_min": 5,           # ROE < 5% → 수익성 부족 제외
    "operating_margin_min": 0,   # 영업이익률 < 0% → 적자 제외
    "revenue_growth_min": -20,   # 매출 성장률 < -20% → 급감 제외
}

# 소프트 점수 기준 (가감점 범위: -1.0 ~ +1.0)
SOFT_SCORE_WEIGHTS = {
    "per": 0.25,               # PER 점수 가중
    "roe": 0.30,               # ROE 점수 가중
    "operating_margin": 0.25,  # 영업이익률 점수 가중
    "revenue_growth": 0.20,    # 매출 성장률 점수 가중
}


# ══════════════════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════════════════

def fetch_fundamentals(ticker: str) -> Optional[Dict]:
    """yfinance에서 재무 지표 수집."""
    try:
        info = yf.Ticker(ticker).info
        if not info:
            return None

        per = info.get("trailingPE") or info.get("forwardPE")
        roe = info.get("returnOnEquity")
        op_margin = info.get("operatingMargins")
        rev_growth = info.get("revenueGrowth")

        # yfinance는 비율을 소수(0.15 = 15%)로 반환
        data = {
            "ticker": ticker,
            "per": round(per, 2) if per is not None else None,
            "roe": round(roe * 100, 2) if roe is not None else None,
            "operating_margin": round(op_margin * 100, 2) if op_margin is not None else None,
            "revenue_growth": round(rev_growth * 100, 2) if rev_growth is not None else None,
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
        }
        return data

    except Exception as e:
        logger.debug(f"{ticker} 재무 데이터 실패: {e}")
        return None


def fetch_fundamentals_batch(tickers: List[str],
                             max_workers: int = 10) -> Dict[str, Dict]:
    """
    여러 종목의 재무 지표를 병렬 수집.
    Returns: {ticker: fundamental_data}
    """
    results = {}
    total = len(tickers)
    logger.info(f"[재무] {total}개 종목 재무 데이터 수집...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_fundamentals, t): t for t in tickers}
        done = 0
        for future in as_completed(futures):
            done += 1
            ticker = futures[future]
            try:
                data = future.result()
                if data:
                    results[ticker] = data
            except Exception:
                pass
            if done % 20 == 0 or done == total:
                logger.info(f"  [{done}/{total}] 수집 완료 ({len(results)}건)")

    logger.info(f"[재무] 수집 완료: {len(results)}/{total}건")
    return results


# ══════════════════════════════════════════════════════
#  하드 필터
# ══════════════════════════════════════════════════════

def hard_filter(fundamentals: Dict, thresholds: Optional[Dict] = None) -> Tuple[bool, str]:
    """
    재무 기준 미달 시 False 반환.
    Returns: (통과 여부, 실패 사유)
    """
    th = {**HARD_FILTER_DEFAULTS, **(thresholds or {})}

    per = fundamentals.get("per")
    roe = fundamentals.get("roe")
    op_margin = fundamentals.get("operating_margin")
    rev_growth = fundamentals.get("revenue_growth")

    # PER 체크
    if per is not None:
        if per < th["per_min"]:
            return False, f"PER 적자({per:.1f})"
        if per > th["per_max"]:
            return False, f"PER 과대({per:.1f}>{th['per_max']})"

    # ROE 체크
    if roe is not None:
        if roe < th["roe_min"]:
            return False, f"ROE 부족({roe:.1f}%<{th['roe_min']}%)"

    # 영업이익률 체크
    if op_margin is not None:
        if op_margin < th["operating_margin_min"]:
            return False, f"영업적자({op_margin:.1f}%)"

    # 매출 성장률 체크
    if rev_growth is not None:
        if rev_growth < th["revenue_growth_min"]:
            return False, f"매출급감({rev_growth:.1f}%)"

    return True, "통과"


# ══════════════════════════════════════════════════════
#  소프트 점수
# ══════════════════════════════════════════════════════

def _score_per(per: Optional[float]) -> float:
    """PER 점수: 10~25 최고, 너무 낮거나 높으면 감점."""
    if per is None:
        return 0.0
    if per < 0:
        return -1.0       # 적자
    if 10 <= per <= 25:
        return 1.0         # 적정 범위
    if 5 <= per < 10 or 25 < per <= 40:
        return 0.5         # 약간 벗어남
    if per > 60:
        return -0.5        # 고평가
    return 0.0


def _score_roe(roe: Optional[float]) -> float:
    """ROE 점수: 15% 이상 우수, 5% 미만 부족."""
    if roe is None:
        return 0.0
    if roe >= 20:
        return 1.0
    if roe >= 15:
        return 0.7
    if roe >= 10:
        return 0.3
    if roe >= 5:
        return 0.0
    return -0.5  # ROE 5% 미만


def _score_operating_margin(margin: Optional[float]) -> float:
    """영업이익률 점수: 20% 이상 우수, 0% 미만 적자."""
    if margin is None:
        return 0.0
    if margin >= 25:
        return 1.0
    if margin >= 15:
        return 0.7
    if margin >= 8:
        return 0.3
    if margin >= 0:
        return 0.0
    return -0.7  # 영업적자


def _score_revenue_growth(growth: Optional[float]) -> float:
    """매출 성장률 점수: 20% 이상 고성장, 마이너스면 감점."""
    if growth is None:
        return 0.0
    if growth >= 30:
        return 1.0
    if growth >= 15:
        return 0.7
    if growth >= 5:
        return 0.3
    if growth >= 0:
        return 0.0
    if growth >= -10:
        return -0.3
    return -0.7  # 매출 급감


def calculate_fundamental_score(fundamentals: Dict) -> float:
    """
    재무 지표 기반 종합 점수.
    범위: -1.0 ~ +1.0 (기술 점수에 가감)
    """
    scores = {
        "per": _score_per(fundamentals.get("per")),
        "roe": _score_roe(fundamentals.get("roe")),
        "operating_margin": _score_operating_margin(fundamentals.get("operating_margin")),
        "revenue_growth": _score_revenue_growth(fundamentals.get("revenue_growth")),
    }

    weighted_sum = sum(
        scores[k] * SOFT_SCORE_WEIGHTS.get(k, 0.25)
        for k in scores
    )

    return round(max(-1.0, min(1.0, weighted_sum)), 3)


# ══════════════════════════════════════════════════════
#  통합 필터 (3가지 모드 통합)
# ══════════════════════════════════════════════════════

def apply_fundamental_filter(
    tickers: List[str],
    mode: str = "soft_score",
    hard_thresholds: Optional[Dict] = None,
) -> Dict[str, Dict]:
    """
    종목 목록에 재무 필터 적용.

    Args:
        tickers: 종목 목록
        mode: "hard_filter" | "soft_score" | "display_only"
        hard_thresholds: 하드 필터 기준 (mode=hard_filter일 때)

    Returns:
        {ticker: {
            "fundamentals": {...},       # 원본 재무 데이터
            "fundamental_score": float,  # 소프트 점수 (-1 ~ +1)
            "passed_hard_filter": bool,  # 하드 필터 통과 여부
            "filter_reason": str,        # 필터 실패 사유
        }}
    """
    # 일괄 수집
    raw_data = fetch_fundamentals_batch(tickers)

    result = {}
    passed = 0
    filtered = 0

    for t in tickers:
        fund = raw_data.get(t)
        entry = {
            "fundamentals": fund,
            "fundamental_score": 0.0,
            "passed_hard_filter": True,
            "filter_reason": "데이터 없음" if fund is None else "통과",
        }

        if fund:
            # 소프트 점수는 항상 계산
            entry["fundamental_score"] = calculate_fundamental_score(fund)

            # 하드 필터
            ok, reason = hard_filter(fund, hard_thresholds)
            entry["passed_hard_filter"] = ok
            entry["filter_reason"] = reason

            if mode == "hard_filter" and not ok:
                filtered += 1
            else:
                passed += 1
        else:
            # 데이터 없는 종목: 하드 필터에서도 통과 (데이터 부족은 제외 사유 아님)
            passed += 1

        result[t] = entry

    logger.info(f"[재무] 모드={mode} | 통과={passed} | 필터링={filtered}")
    return result
