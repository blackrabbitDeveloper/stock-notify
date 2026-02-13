#!/usr/bin/env python3
"""
백테스트 실행 스크립트

사용법:
  # 기본 백테스트 (90일, 상위 5종목)
  python run_backtest.py

  # 180일 백테스트, 상위 3종목
  python run_backtest.py --days 180 --top 3

  # S&P 500 풀로 365일 백테스트 + 결과 내보내기
  python run_backtest.py --pool sp500 --days 365 --export

  # 파라미터 최적화 (시간 오래 걸림!)
  python run_backtest.py --optimize

  # 빠른 최적화 (축소된 그리드)
  python run_backtest.py --optimize --quick

  # Discord로 결과 전송
  python run_backtest.py --discord
"""

import argparse
import sys
import os

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.backtester import BacktestEngine, print_report, export_results
from src.backtest_utils import send_backtest_to_discord, ParameterOptimizer


def main():
    parser = argparse.ArgumentParser(
        description="Stock Notify Bot — 백테스트 & 파라미터 최적화",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python run_backtest.py                          # 기본 90일
  python run_backtest.py --days 180 --top 3       # 180일, 상위 3종목
  python run_backtest.py --optimize               # 파라미터 최적화
  python run_backtest.py --optimize --quick        # 빠른 최적화
  python run_backtest.py --export --discord        # 내보내기 + Discord
        """
    )

    # 백테스트 기본 옵션
    parser.add_argument("--days", type=int, default=90,
                        help="백테스트 기간 (거래일, 기본 90)")
    parser.add_argument("--top", type=int, default=5,
                        help="일별 선택 종목 수 (기본 5)")
    parser.add_argument("--pool", type=str, default="nasdaq100",
                        choices=["nasdaq100", "sp500"],
                        help="종목 풀 (기본 nasdaq100)")
    parser.add_argument("--min-score", type=float, default=4.0,
                        help="최소 기술 점수 (기본 4.0)")
    parser.add_argument("--hold", type=int, default=7,
                        help="최대 보유일 (기본 7)")
    parser.add_argument("--sl-mult", type=float, default=2.0,
                        help="손절 ATR 배수 (기본 2.0)")
    parser.add_argument("--tp-mult", type=float, default=4.0,
                        help="익절 ATR 배수 (기본 4.0)")

    # 출력 옵션
    parser.add_argument("--export", action="store_true",
                        help="결과를 data/backtest/에 JSON/CSV로 저장")
    parser.add_argument("--discord", action="store_true",
                        help="결과를 Discord로 전송")

    # 최적화 옵션
    parser.add_argument("--optimize", action="store_true",
                        help="파라미터 그리드 서치 실행")
    parser.add_argument("--quick", action="store_true",
                        help="축소된 그리드로 빠른 최적화")

    args = parser.parse_args()

    if args.optimize:
        # ── 파라미터 최적화 모드 ──
        if args.quick:
            grid = {
                "top_n": [3, 5],
                "min_tech_score": [4.0, 5.0],
                "atr_stop_mult": [1.5, 2.0],
                "atr_tp_mult": [3.0, 4.0],
                "max_hold_days": [5, 7],
            }
            print("⚡ 빠른 최적화 모드 (32개 조합)")
        else:
            grid = None  # 기본 그리드 사용
            print("🔍 전체 최적화 모드 (243개 조합 — 시간 소요)")

        optimizer = ParameterOptimizer(
            pool=args.pool,
            backtest_days=args.days,
            param_grid=grid,
        )

        results = optimizer.run()
        optimizer.print_top(10)

        # 최적 파라미터로 상세 백테스트
        if results:
            best = results[0]["params"]
            print(f"\n{'=' * 70}")
            print(f"🏆 최적 파라미터로 상세 백테스트 실행")
            print(f"{'=' * 70}")

            engine = BacktestEngine(
                pool=args.pool,
                backtest_days=args.days,
                **best,
            )
            detail = engine.run()
            print_report(detail)

            if args.export:
                export_results(detail)
            if args.discord:
                send_backtest_to_discord(detail)

    else:
        # ── 단일 백테스트 모드 ──
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
            print(f"\n📁 결과 저장 완료: {path}")

        if args.discord:
            send_backtest_to_discord(result)
            print("📨 Discord 전송 완료")


if __name__ == "__main__":
    main()
