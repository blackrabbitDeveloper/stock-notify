#!/usr/bin/env python3
"""
자기 학습(Self-Tuning) 실행 스크립트

사용법:
  python run_self_tuning.py              # 기본 실행 (sp500, 90일, 20회, hard_filter)
  python run_self_tuning.py --days 90    # 90일 백테스트 기반
  python run_self_tuning.py --discord    # Discord 알림 포함
  python run_self_tuning.py --dry-run    # 변경사항 미적용 (확인만)
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.self_tuning import SelfTuningEngine, send_tuning_report_to_discord
from src.logger import logger


def main():
    parser = argparse.ArgumentParser(description="자기 학습 전략 엔진")
    parser.add_argument("--days", type=int, default=90, help="백테스트 기간 거래일 (기본 90)")
    parser.add_argument("--pool", type=str, default="sp500", help="종목 풀")
    parser.add_argument("--iterations", type=int, default=20, help="탐색 반복 횟수 (기본 20)")
    parser.add_argument("--min-improvement", type=float, default=5.0, help="채택 최소 개선률 %% (기본 5.0)")
    parser.add_argument("--fundamental-mode", type=str, default="hard_filter",
                        choices=["hard_filter", "soft_score", "display_only", "off"],
                        help="재무 필터 모드 (기본 hard_filter)")
    parser.add_argument("--discord", action="store_true", help="Discord 알림 전송")
    parser.add_argument("--dry-run", action="store_true", help="변경사항 미적용 (확인만)")
    args = parser.parse_args()

    engine = SelfTuningEngine(
        pool=args.pool,
        backtest_days=args.days,
        max_iterations=args.iterations,
        min_improvement=args.min_improvement,
        fundamental_mode=args.fundamental_mode,
    )

    if args.dry_run:
        logger.info("🔍 DRY RUN 모드 — 변경사항을 적용하지 않습니다")
        # 백업
        import copy
        orig_save = engine._save_state
        engine._save_state = lambda *a, **kw: logger.info("  [DRY RUN] 저장 스킵")

    report = engine.run()

    if args.dry_run:
        print("\n⚠️ DRY RUN — 위 변경사항은 적용되지 않았습니다.")
        print("실제 적용하려면 --dry-run 없이 실행하세요.\n")

    if args.discord and report.get("status") == "completed":
        send_tuning_report_to_discord(report)
        print("📨 Discord 전송 완료")


if __name__ == "__main__":
    main()
