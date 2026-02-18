#!/usr/bin/env python3
"""
포지션 리밸런싱 실행 스크립트

열린 포지션을 재평가하고, max_positions 초과 시 하위 종목을 청산합니다.
자기학습(run_self_tuning.py) 실행 시에도 자동으로 호출됩니다.

사용법:
  python run_rebalance.py                  # 기본 실행 (실시간 가격 + 자동 청산)
  python run_rebalance.py --dry-run        # 미리보기 (실제 청산 안함)
  python run_rebalance.py --max 5          # 최대 5개만 유지
  python run_rebalance.py --no-fetch       # 실시간 가격 안 가져옴 (기존 종가 사용)
  python run_rebalance.py --force          # max_positions 이하여도 강제 재평가 출력
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.position_tracker import rebalance_positions, load_positions


def main():
    parser = argparse.ArgumentParser(description="포지션 리밸런싱")
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 저장하지 않고 결과만 표시")
    parser.add_argument("--max", type=int, default=None,
                        help="유지할 최대 포지션 수 (기본: strategy_state에서 로드)")
    parser.add_argument("--no-fetch", action="store_true",
                        help="실시간 가격 조회 안 함 (기존 price_history 사용)")
    parser.add_argument("--force", action="store_true",
                        help="포지션 수 정상이어도 강제 재평가 출력")
    args = parser.parse_args()

    # 현재 상태 표시
    data = load_positions()
    open_count = len([p for p in data["positions"] if p["status"] == "open"])
    print(f"\n📊 현재 오픈 포지션: {open_count}개")

    if args.force and args.max is None:
        # --force일 때 max를 현재보다 작게 설정해서 강제 실행
        args.max = max(1, open_count - 1) if open_count > 1 else 1
        print(f"  ⚡ 강제 모드: max_positions={args.max}로 설정")

    result = rebalance_positions(
        max_positions=args.max,
        fetch_live=not args.no_fetch,
        dry_run=args.dry_run,
    )

    summary = result.get("summary", {})
    if summary.get("action") == "none":
        print("\n✅ 리밸런싱 불필요 — 포지션 수가 정상 범위입니다.")
        if not args.force:
            print("   💡 강제 재평가: python run_rebalance.py --force")
    elif args.dry_run:
        print("\n⚠️ DRY RUN 모드 — 위 결과는 적용되지 않았습니다.")
        print("   실제 적용: python run_rebalance.py")

    return result


if __name__ == "__main__":
    main()
