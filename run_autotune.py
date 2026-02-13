#!/usr/bin/env python3
"""
자동 전략 튜닝 실행 스크립트

사용법:
  python run_autotune.py                  # 기본 실행 (60일 백테스트 → 자동 조정)
  python run_autotune.py --days 90        # 90일 백테스트
  python run_autotune.py --dry-run        # 저장하지 않고 미리보기만
  python run_autotune.py --discord        # Discord로 결과 전송
  python run_autotune.py --rollback       # 직전 설정으로 롤백
"""

import argparse
import copy
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.strategy_tuner import (
    run_auto_tune, load_config, save_config,
    load_signal_weights, save_signal_weights,
    load_tune_history, TUNE_HISTORY_PATH,
)
from src.logger import logger


def send_tune_discord(result: dict) -> None:
    """튜닝 결과를 Discord로 전송."""
    import requests

    url = (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip().strip('"').strip("'")
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL 없음 — Discord 전송 스킵")
        return

    bt = result.get("backtest_summary", {})
    regime = result.get("regime", "?")
    conf = result.get("regime_details", {}).get("confidence", 0)
    emergency = result.get("emergency")
    param_changes = result.get("param_changes", [])
    weight_changes = result.get("weight_changes", [])
    nc = result.get("new_config", {})
    indicators = result.get("regime_details", {}).get("indicators", {})

    # 색상
    if emergency:
        color = 0xff0000
        title = "🚨 긴급 보수적 모드 전환"
    elif len(param_changes) + len(weight_changes) > 0:
        color = 0x00aaff
        title = "🔧 자동 전략 튜닝 완료"
    else:
        color = 0x888888
        title = "🔧 자동 전략 튜닝 — 변경 없음"

    # 변경 사항 텍스트
    changes_text = ""
    if emergency:
        changes_text = f"**🚨 긴급 사유:** {emergency}\n"
    if param_changes:
        changes_text += "**파라미터:**\n" + "\n".join(f"→ {c}" for c in param_changes) + "\n"
    if weight_changes:
        changes_text += "**가중치:**\n" + "\n".join(f"→ {c}" for c in weight_changes[:8])
        if len(weight_changes) > 8:
            changes_text += f"\n... +{len(weight_changes)-8}건"
    if not changes_text:
        changes_text = "변경 사항 없음"

    # 시장 레짐 텍스트
    regime_map = {"bullish": "🟢 상승", "bearish": "🔴 하락", "sideways": "🟡 횡보", "volatile": "🟠 고변동"}
    regime_text = regime_map.get(regime, regime)

    embed = {
        "title": title,
        "color": color,
        "fields": [
            {
                "name": "📊 백테스트 성과",
                "value": (
                    f"거래: **{bt.get('total_trades', 0)}회** | "
                    f"승률: **{bt.get('win_rate', 0):.1f}%**\n"
                    f"PF: **{bt.get('profit_factor', 0):.2f}** | "
                    f"샤프: **{bt.get('sharpe_ratio', 0):.2f}**\n"
                    f"평균: **{bt.get('avg_pnl_pct', 0):+.2f}%** | "
                    f"누적: **{bt.get('total_pnl_pct', 0):+.2f}%**"
                ),
                "inline": True,
            },
            {
                "name": "🌍 시장 레짐",
                "value": (
                    f"{regime_text} (신뢰도 {conf:.0%})\n"
                    f"VIX: {indicators.get('vix', '?')} | "
                    f"ADX: {indicators.get('spy_adx', '?')}\n"
                    f"RSI: {indicators.get('spy_rsi', '?')} | "
                    f"20일: {indicators.get('spy_ret_20d', '?')}%"
                ),
                "inline": True,
            },
            {
                "name": "📝 변경 사항",
                "value": changes_text[:1024],
            },
            {
                "name": "⚙️ 현재 설정",
                "value": (
                    f"SL: ATR×{nc.get('atr_stop_mult', '?')} | "
                    f"TP: ATR×{nc.get('atr_tp_mult', '?')}\n"
                    f"보유: {nc.get('max_hold_days', '?')}일 | "
                    f"최소점수: {nc.get('min_tech_score', '?')} | "
                    f"top_n: {nc.get('top_n', '?')}"
                ),
            },
        ],
    }

    payload = {"content": "**🔧 주간 자동 전략 튜닝**", "embeds": [embed]}

    try:
        resp = requests.post(url, json=payload, timeout=20)
        logger.info(f"Discord 전송: {resp.status_code}")
    except Exception as e:
        logger.error(f"Discord 전송 실패: {e}")


def rollback():
    """직전 튜닝 이전 상태로 롤백."""
    history = load_tune_history()
    if len(history) < 2:
        print("❌ 롤백할 이력이 없습니다 (최소 2회 이상 튜닝 필요)")
        return

    prev = history[-2]
    prev_config = load_config()
    prev_config["auto"] = prev.get("new_config", prev_config.get("auto", {}))
    save_config(prev_config)

    prev_weights = prev.get("new_weights")
    if prev_weights:
        save_signal_weights(prev_weights)

    # 이력에 롤백 기록
    history.append({
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "action": "rollback",
        "rolled_back_to": prev.get("timestamp", "?"),
    })
    from src.strategy_tuner import save_tune_history
    save_tune_history(history)

    print(f"✅ 롤백 완료 → {prev.get('timestamp', '?')} 시점으로 복원")
    print(f"   설정: {prev.get('new_config', {})}")


def main():
    parser = argparse.ArgumentParser(
        description="Stock Notify Bot — 자동 전략 튜닝",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=60,
                        help="백테스트 기간 (거래일, 기본 60)")
    parser.add_argument("--dry-run", action="store_true",
                        help="저장하지 않고 미리보기만")
    parser.add_argument("--discord", action="store_true",
                        help="결과를 Discord로 전송")
    parser.add_argument("--rollback", action="store_true",
                        help="직전 설정으로 롤백")

    args = parser.parse_args()

    if args.rollback:
        rollback()
        return

    result = run_auto_tune(
        backtest_days=args.days,
        dry_run=args.dry_run,
    )

    if args.discord:
        send_tune_discord(result)
        print("📨 Discord 전송 완료")


if __name__ == "__main__":
    main()
