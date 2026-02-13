"""
백테스트 결과를 Discord Embed로 전송 + 파라미터 최적화

기능:
  1. 백테스트 결과를 Discord로 전송 (요약 임베드)
  2. 파라미터 그리드 서치로 최적 설정 탐색
"""

import os
import itertools
import requests
from typing import Dict, List, Optional
from .backtester import BacktestEngine, print_report
from .logger import logger


# ══════════════════════════════════════════════════════
#  Discord 전송
# ══════════════════════════════════════════════════════

def send_backtest_to_discord(result: Dict) -> None:
    """백테스트 결과를 Discord Embed로 전송."""
    url = (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip().strip('"').strip("'")
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL 없음 — Discord 전송 스킵")
        return

    s = result.get("summary", {})
    cfg = result.get("config", {})
    eb = result.get("exit_breakdown", {})

    if s.get("total_trades", 0) == 0:
        return

    # 전략 등급
    pf = s.get("profit_factor", 0)
    wr = s.get("win_rate", 0)
    if pf >= 1.5 and wr >= 55:
        grade = "🅰️ 우수"
        color = 0x00ff00
    elif pf >= 1.0 and wr >= 45:
        grade = "🅱️ 양호"
        color = 0xffff00
    else:
        grade = "🅲️ 개선필요"
        color = 0xff4444

    # 월별 수익 요약 (최근 3개월)
    monthly = result.get("monthly_returns", [])[-3:]
    monthly_str = "\n".join(
        f"{'🟢' if m['total_pnl_pct'] >= 0 else '🔴'} {m['month']}: "
        f"{m['total_pnl_pct']:+.2f}% ({m['trades']}거래)"
        for m in monthly
    ) if monthly else "데이터 없음"

    # 상위 신호
    signals = result.get("signal_performance", [])[:5]
    signal_str = "\n".join(
        f"{'✅' if sp['avg_pnl'] > 0 else '❌'} {sp['signal']}: "
        f"{sp['avg_pnl']:+.2f}% (승률 {sp['win_rate']:.0f}%, {sp['count']}회)"
        for sp in signals
    ) if signals else "데이터 없음"

    embed = {
        "title": f"📊 백테스트 결과  {grade}",
        "description": (
            f"**{cfg.get('pool', '?')}** | {cfg.get('backtest_days', '?')}거래일 | "
            f"상위 {cfg.get('top_n', '?')}종목/일\n"
            f"손절 ATR×{cfg.get('atr_stop_mult', '?')} | "
            f"익절 ATR×{cfg.get('atr_tp_mult', '?')} | "
            f"최대보유 {cfg.get('max_hold_days', '?')}일"
        ),
        "color": color,
        "fields": [
            {
                "name": "📈 핵심 지표",
                "value": (
                    f"총 거래: **{s['total_trades']}회**\n"
                    f"승률: **{s['win_rate']:.1f}%**\n"
                    f"평균 수익: **{s['avg_pnl_pct']:+.2f}%**\n"
                    f"누적 수익: **{s['total_pnl_pct']:+.2f}%**\n"
                    f"Profit Factor: **{s['profit_factor']:.2f}**\n"
                    f"기대값: **{s['expected_value_pct']:+.2f}%/거래**\n"
                    f"샤프 비율: **{s['sharpe_ratio']:.2f}**"
                ),
                "inline": True,
            },
            {
                "name": "🎯 청산 유형",
                "value": (
                    f"✅ 익절: {eb.get('take_profit', 0)}회 ({eb.get('tp_rate', 0):.1f}%)\n"
                    f"🛑 손절: {eb.get('stop_loss', 0)}회 ({eb.get('sl_rate', 0):.1f}%)\n"
                    f"⏰ 만료: {eb.get('expired', 0)}회 ({eb.get('exp_rate', 0):.1f}%)\n"
                    f"\n"
                    f"평균 승: {s.get('avg_win_pct', 0):+.2f}%\n"
                    f"평균 패: {s.get('avg_loss_pct', 0):+.2f}%\n"
                    f"보유기간: {s.get('avg_hold_days', 0):.1f}일\n"
                    f"최대낙폭: {s.get('portfolio_max_drawdown_pct', 0):.2f}%"
                ),
                "inline": True,
            },
            {
                "name": "📅 최근 월별 수익",
                "value": monthly_str,
            },
            {
                "name": "📡 진입 신호별 성과 (상위 5)",
                "value": signal_str,
            },
        ],
    }

    payload = {
        "content": "**📊 백테스트 리포트**",
        "embeds": [embed],
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        logger.info(f"Discord 백테스트 전송: {resp.status_code}")
    except Exception as e:
        logger.error(f"Discord 전송 실패: {e}")


# ══════════════════════════════════════════════════════
#  파라미터 최적화 (그리드 서치)
# ══════════════════════════════════════════════════════

class ParameterOptimizer:
    """
    그리드 서치로 최적 파라미터 조합 탐색.

    탐색 대상:
      - top_n: 일별 선택 종목 수
      - min_tech_score: 최소 기술 점수
      - atr_stop_mult: 손절 ATR 배수
      - atr_tp_mult: 익절 ATR 배수
      - max_hold_days: 최대 보유 기간

    최적화 기준:
      - profit_factor × win_rate (복합 지표)
    """

    DEFAULT_GRID = {
        "top_n": [3, 5, 7],
        "min_tech_score": [3.5, 4.0, 5.0],
        "atr_stop_mult": [1.5, 2.0, 2.5],
        "atr_tp_mult": [3.0, 4.0, 5.0],
        "max_hold_days": [5, 7, 10],
    }

    def __init__(
        self,
        pool: str = "nasdaq100",
        backtest_days: int = 90,
        param_grid: Optional[Dict] = None,
        metric: str = "composite",  # composite | profit_factor | sharpe | win_rate
    ):
        self.pool = pool
        self.backtest_days = backtest_days
        self.param_grid = param_grid or self.DEFAULT_GRID
        self.metric = metric
        self.results: List[Dict] = []

    def _score_result(self, summary: Dict) -> float:
        """결과에 점수를 매겨 비교."""
        total = summary.get("total_trades", 0)
        if total < 10:
            return -999  # 거래가 너무 적으면 신뢰 불가

        pf = summary.get("profit_factor", 0)
        wr = summary.get("win_rate", 0)
        sharpe = summary.get("sharpe_ratio", 0)
        ev = summary.get("expected_value_pct", 0)

        if self.metric == "profit_factor":
            return pf
        elif self.metric == "sharpe":
            return sharpe
        elif self.metric == "win_rate":
            return wr
        else:
            # 복합 지표: PF × (WR/100) + EV + Sharpe×0.5
            return pf * (wr / 100) + ev + sharpe * 0.5

    def run(self) -> List[Dict]:
        """그리드 서치 실행."""
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        combos = list(itertools.product(*values))

        logger.info(f"파라미터 최적화: {len(combos)}개 조합 탐색")

        for idx, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            logger.info(f"  [{idx+1}/{len(combos)}] {params}")

            try:
                engine = BacktestEngine(
                    pool=self.pool,
                    backtest_days=self.backtest_days,
                    top_n=params.get("top_n", 5),
                    min_tech_score=params.get("min_tech_score", 4.0),
                    max_hold_days=params.get("max_hold_days", 7),
                    atr_stop_mult=params.get("atr_stop_mult", 2.0),
                    atr_tp_mult=params.get("atr_tp_mult", 4.0),
                )

                result = engine.run()
                summary = result.get("summary", {})
                score = self._score_result(summary)

                self.results.append({
                    "params": params,
                    "score": round(score, 4),
                    "total_trades": summary.get("total_trades", 0),
                    "win_rate": summary.get("win_rate", 0),
                    "avg_pnl": summary.get("avg_pnl_pct", 0),
                    "profit_factor": summary.get("profit_factor", 0),
                    "sharpe": summary.get("sharpe_ratio", 0),
                    "ev": summary.get("expected_value_pct", 0),
                    "max_dd": summary.get("portfolio_max_drawdown_pct", 0),
                })

            except Exception as e:
                logger.warning(f"  조합 실패: {e}")
                continue

        # 점수 순 정렬
        self.results.sort(key=lambda x: x["score"], reverse=True)

        return self.results

    def print_top(self, n: int = 10):
        """상위 N개 파라미터 조합 출력."""
        print("\n" + "=" * 80)
        print("🏆 파라미터 최적화 결과 (상위 조합)")
        print("=" * 80)

        if not self.results:
            print("결과 없음")
            return

        print(f"\n{'순위':>4} {'점수':>7} {'승률':>6} {'평균':>7} {'PF':>6} "
              f"{'샤프':>6} {'거래수':>6} | 파라미터")
        print("-" * 80)

        for i, r in enumerate(self.results[:n], 1):
            p = r["params"]
            emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
            print(
                f"{emoji}{i:>2} {r['score']:>7.2f} {r['win_rate']:>5.1f}% "
                f"{r['avg_pnl']:>+6.2f}% {r['profit_factor']:>5.2f} "
                f"{r['sharpe']:>5.2f} {r['total_trades']:>6} | "
                f"top={p.get('top_n', '?')} min_s={p.get('min_tech_score', '?')} "
                f"SL={p.get('atr_stop_mult', '?')}x TP={p.get('atr_tp_mult', '?')}x "
                f"hold={p.get('max_hold_days', '?')}d"
            )

        best = self.results[0]
        print(f"\n✅ 최적 파라미터: {best['params']}")
        print(f"   점수: {best['score']:.2f} | 승률: {best['win_rate']:.1f}% | "
              f"PF: {best['profit_factor']:.2f} | EV: {best['ev']:+.2f}%")
