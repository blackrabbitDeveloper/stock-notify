import yaml
import os
import time
import pandas as pd
from typing import Dict, List

from .config import ConfigValidator
from .logger import logger
from .fetch_prices import get_history
from .universe_builder import build_auto_universe
from .ranker import rank_with_news
from .ai_explainer import explain_reason
from .send_discord import send_discord_with_reasons

# Gemini API rate limit 설정
# 무료: 5 req/min, 유료: 1000 req/min
GEMINI_RATE_LIMIT_DELAY = float(os.getenv("GEMINI_RATE_LIMIT_DELAY", "13"))  # 초 (무료: 60/5=12 + 여유 1초)


def load_cfg() -> Dict:
    """설정 파일 로드 및 검증"""
    try:
        with open("config/universe.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        ConfigValidator.validate_config(cfg)
        return cfg
    except FileNotFoundError:
        logger.error("config/universe.yaml 파일을 찾을 수 없습니다.")
        raise
    except Exception as e:
        logger.error(f"설정 파일 로드 실패: {e}")
        raise


def resolve_universe(cfg: Dict) -> List[str]:
    """종목 유니버스 결정"""
    mode = cfg.get("mode", "static").lower()
    if mode == "auto":
        logger.info("Auto 모드: 자동 종목 선별 시작")
        universe = build_auto_universe(cfg.get("auto", {}))
        if not universe:
            logger.warning("자동 선별 실패, static_list 사용")
            return cfg.get("static_list", [])
        return universe
    else:
        logger.info("Static 모드: 고정 종목 사용")
        return cfg.get("static_list", [])


def run_once():
    """메인 실행 로직 v2"""
    try:
        logger.info("=" * 60)
        logger.info("Stock Notify Bot v2 시작")
        logger.info("=" * 60)

        # 1. 환경 변수 검증
        ConfigValidator.validate_env()

        # 2. 설정 로드
        cfg = load_cfg()
        logger.info(f"설정 로드 완료: mode={cfg.get('mode')}")

        # 3. 종목 유니버스 구성
        tickers = resolve_universe(cfg)
        logger.info(f"종목 유니버스: {len(tickers)}개")

        if not tickers:
            logger.warning("종목 유니버스가 비어있습니다.")
            send_discord_with_reasons([], "US Stock Watchlist v2")
            return

        # 4. 가격 데이터 다운로드 (60일 → 90일로 확대, 지표 정확도 향상)
        data_days = int(cfg.get("auto", {}).get("data_days", 90))
        logger.info(f"가격 데이터 다운로드 시작 ({data_days}일)")
        prices = get_history(tickers, days=data_days)

        if prices.empty:
            logger.warning("가격 데이터가 비어있습니다.")
            send_discord_with_reasons([], "US Stock Watchlist v2")
            return

        logger.info(f"가격 데이터: {len(prices)} rows, {prices['ticker'].nunique()} tickers")

        # 5. 종목 분석 및 랭킹
        use_news = bool(cfg.get("auto", {}).get("use_news_bonus", True))
        tech_filter_count = int(cfg.get("auto", {}).get("tech_filter_count", 30))
        min_tech_score = float(cfg.get("auto", {}).get("min_tech_score", 4.0))

        logger.info(f"분석 설정: use_news={use_news}, tech_filter={tech_filter_count}, min_score={min_tech_score}")

        topn = rank_with_news(
            prices,
            tickers,
            use_news=use_news,
            min_bars=5,
            tech_filter_count=tech_filter_count,
            min_tech_score=min_tech_score
        )

        if topn.empty:
            logger.warning("추천 종목 없음 - 데이터 부족 또는 필터 기준 미달")
            send_discord_with_reasons([], "US Stock Watchlist v2")
            return

        logger.info(f"최종 추천 종목: {len(topn)}개")

        # 6. 현재가 정보
        top_symbols = topn["ticker"].tolist()
        from .fetch_prices import get_latest_quotes

        logger.info("현재가 정보 가져오는 중...")
        quotes = get_latest_quotes(top_symbols, prepost=True)
        topn = topn.merge(quotes, on="ticker", how="left")

        # 7. AI 설명 생성
        rows = []
        ai_on = bool(cfg.get("ai_explainer", {}).get("enabled", True))
        logger.info(f"AI 설명 생성: {ai_on}")

        def _num(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        def _ts(x):
            try:
                return x.isoformat() if pd.notna(x) else None
            except Exception:
                return None

        for idx, r in topn.iterrows():
            tech_analysis = r.get("technical_analysis", {})

            reason_obj = {
                "reason": "기술적 분석 기반 선별.",
                "confidence": 0.4,
                "caveat": "투자 자문 아님. 손절 필수."
            }

            if ai_on:
                for attempt in range(3):  # 최대 3회 재시도
                    try:
                        reason_obj = explain_reason(
                            r["ticker"],
                            {
                                "day_ret": float(r["day_ret"]),
                                "vol_x": float(r["vol_x"]),
                                "tech_score": float(r.get("tech_score", 0)),
                                "technical_signals": tech_analysis
                            },
                            r.get("top_news", []),
                        )
                        break  # 성공 시 루프 탈출
                    except Exception as e:
                        err_str = str(e)
                        if "429" in err_str or "quota" in err_str.lower():
                            wait = GEMINI_RATE_LIMIT_DELAY * (attempt + 1)
                            logger.warning(f"Gemini 할당량 초과 ({r['ticker']}), {wait:.0f}초 대기 후 재시도 ({attempt+1}/3)")
                            time.sleep(wait)
                        else:
                            logger.warning(f"AI 설명 생성 실패 ({r['ticker']}): {e}")
                            break  # 429 외 오류는 재시도 안 함

                # API 호출 간 딜레이 (rate limit 방지)
                time.sleep(GEMINI_RATE_LIMIT_DELAY)

            rows.append({
                "ticker": r["ticker"],
                "day_ret": float(r["day_ret"]),
                "vol_x": float(r["vol_x"]),
                "news_n": int(r.get("news_n", 0)),
                "news_bonus": float(r.get("news_bonus", 0.0)),
                "tech_score": float(r.get("tech_score", 0.0)),
                "score": float(r.get("combined_score", 0.0)),
                "top_news": r.get("top_news", []),
                "technical_analysis": tech_analysis,
                "reason_obj": reason_obj,
                "last_price": _num(r.get("last_price")),
                "prev_close": _num(r.get("prev_close")),
                "last_time": _ts(r.get("last_time")),
            })

        # 8. Discord 전송
        send_discord_with_reasons(rows, "US Stock Watchlist v2 (Entry-Timing Focused)")

        logger.info("=" * 60)
        logger.info("Stock Notify Bot v2 완료")
        logger.info("=" * 60)

    except Exception as e:
        logger.exception(f"실행 중 오류 발생: {e}")
        raise


if __name__ == "__main__":
    run_once()
