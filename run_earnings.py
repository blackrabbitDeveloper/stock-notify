#!/usr/bin/env python3
"""
실적 발표 캘린더 수집기.

유니버스(sp500/nasdaq100) + 보유 종목의 어닝 날짜를 수집하여
data/earnings_calendar.json에 저장합니다.

사용법:
  python run_earnings.py                # 전략 설정의 pool 사용
  python run_earnings.py --pool sp500   # S&P 500
  python run_earnings.py --pool nasdaq100
  python run_earnings.py --days 90      # 90일 범위 수집

실행 주기: 월 1회 또는 어닝 시즌(1/4/7/10월) 시작 시
"""
import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

# ── 파일 경로 ──
EARNINGS_FILE = Path("data/earnings_calendar.json")
POSITIONS_FILE = Path("data/positions.json")
STRATEGY_FILE = Path("config/strategy_state.json")


def get_pool_tickers(pool: str) -> list:
    """유니버스 종목 목록 가져오기."""
    try:
        from src.universe_builder import get_pool
        return get_pool(pool)
    except Exception:
        pass

    # 폴백: 하드코딩된 주요 종목
    if pool == "nasdaq100":
        return [
            "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
            "AVGO", "COST", "AMD", "NFLX", "ADBE", "CRM", "QCOM",
            "ISRG", "INTU", "AMAT", "TXN", "MU", "LRCX", "PANW",
            "KLAC", "MRVL", "SNPS", "CDNS", "PYPL", "ABNB", "COIN",
        ]
    # sp500 폴백
    return [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
        "BRK-B", "AVGO", "JPM", "UNH", "V", "MA", "HD", "PG",
        "COST", "JNJ", "ABBV", "CRM", "AMD", "NFLX", "LIN",
        "MRK", "ADBE", "TXN", "QCOM", "ISRG", "INTU", "AMAT",
    ]


def get_open_tickers() -> set:
    """현재 보유 종목."""
    try:
        with open(POSITIONS_FILE, "r") as f:
            data = json.load(f)
        return set(
            p["ticker"] for p in data.get("positions", [])
            if p.get("status") == "open"
        )
    except Exception:
        return set()


def get_strategy_pool() -> str:
    """strategy_state.json에서 pool 설정 읽기."""
    try:
        with open(STRATEGY_FILE, "r") as f:
            state = json.load(f)
        # run_self_tuning.py에서 --pool로 전달된 값
        return state.get("pool", "sp500")
    except Exception:
        return "sp500"


def collect_earnings(tickers: list, open_tickers: set,
                     days_range: int = 60) -> list:
    """
    종목별 실적 발표일 수집.
    yfinance의 earnings_dates 사용, 실패 시 calendar 폴백.
    """
    earnings = []
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=7)
    window_end = today + timedelta(days=days_range)

    total = len(tickers)
    print(f"[INFO] 어닝 캘린더 수집: {total}개 종목 ({window_start} ~ {window_end})")

    for i, t in enumerate(tickers):
        try:
            info = yf.Ticker(t)

            # 1차: earnings_dates (가장 정확)
            try:
                dates = info.earnings_dates
                if dates is not None and not dates.empty:
                    found = False
                    for dt in dates.index:
                        d = dt.date() if hasattr(dt, "date") else dt
                        if window_start <= d <= window_end:
                            earnings.append({
                                "ticker": t,
                                "date": d.isoformat(),
                                "is_holding": t in open_tickers,
                                "source": "earnings_dates",
                            })
                            found = True
                    if found:
                        continue
            except Exception:
                pass

            # 2차: calendar 폴백
            try:
                cal = info.calendar
                if cal is not None:
                    earn_date = None
                    if isinstance(cal, dict):
                        earn_date = cal.get("Earnings Date")
                        if isinstance(earn_date, list) and earn_date:
                            earn_date = earn_date[0]
                    if earn_date:
                        d = earn_date.date() if hasattr(earn_date, "date") else earn_date
                        if window_start <= d <= window_end:
                            earnings.append({
                                "ticker": t,
                                "date": d.isoformat(),
                                "is_holding": t in open_tickers,
                                "source": "calendar",
                            })
            except Exception:
                pass

        except Exception:
            continue

        # 진행률 표시 (20개마다)
        if (i + 1) % 20 == 0 or i + 1 == total:
            print(f"  [{i + 1}/{total}] 수집 중... ({len(earnings)}건)")

    earnings.sort(key=lambda x: x["date"])
    return earnings


def save_earnings(earnings: list, pool: str) -> None:
    """earnings_calendar.json에 저장."""
    EARNINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "pool": pool,
        "total_tickers": 0,
        "total_earnings": len(earnings),
        "earnings": earnings,
    }

    with open(EARNINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n[INFO] 저장 완료: {EARNINGS_FILE}")
    print(f"  수집 종목: {data['total_tickers']}개")
    print(f"  어닝 일정: {len(earnings)}건")

    # 보유 종목 경고
    holding = [e for e in earnings if e["is_holding"]]
    if holding:
        print(f"\n  ⚠️  보유 종목 실적 발표 예정:")
        for e in holding:
            print(f"     {e['date']}  {e['ticker']}")


def main():
    parser = argparse.ArgumentParser(description="실적 발표 캘린더 수집")
    parser.add_argument("--pool", type=str, default=None,
                        help="종목 풀 (sp500 | nasdaq100, 미지정 시 전략 설정 사용)")
    parser.add_argument("--days", type=int, default=60,
                        help="수집 범위 (일, 기본 60)")
    args = parser.parse_args()

    # 종목 풀 결정
    pool = args.pool or get_strategy_pool()
    print(f"[INFO] 종목 풀: {pool}")

    # 종목 목록
    tickers = get_pool_tickers(pool)
    open_tickers = get_open_tickers()

    # 보유 종목이 유니버스에 없으면 추가
    for t in open_tickers:
        if t not in tickers:
            tickers.append(t)

    print(f"[INFO] 대상: {len(tickers)}개 종목 (보유 {len(open_tickers)}개 포함)")

    # 수집
    earnings = collect_earnings(tickers, open_tickers, days_range=args.days)

    # 저장
    save_earnings(earnings, pool)
    # total_tickers 업데이트
    with open(EARNINGS_FILE, "r") as f:
        data = json.load(f)
    data["total_tickers"] = len(tickers)
    with open(EARNINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 완료! data/earnings_calendar.json 생성됨")


if __name__ == "__main__":
    main()
