"""
환경 변수 검증 및 설정 관리
"""
import os
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from .logger import logger

class ConfigValidator:
    """설정 검증 클래스"""
    
    REQUIRED_ENV_VARS = {
        "DISCORD_WEBHOOK_URL": "Discord Webhook URL (필수)",
        "GOOGLE_API_KEY": "Google Gemini API Key (필수)",
        "FINNHUB_TOKEN": "Finnhub API Token (필수)",
    }
    
    OPTIONAL_ENV_VARS = {
        "GEMINI_MODEL_NAME": "gemini-2.5-flash",
        "GENAI_TRANSPORT": "rest",
        "MAX_TICKERS": "5",
        "AI_EXPLAINER_MAX_TOKENS": "1024",
        "DRY_RUN": "false",
        "SEND_TO_DISCORD": "true",
        "USE_FINBERT": "false",
        "LOG_LEVEL": "INFO",
    }
    
    @classmethod
    def validate_env(cls) -> Dict[str, str]:
        """
        환경 변수 검증
        
        Returns:
            검증된 환경 변수 딕셔너리
        
        Raises:
            EnvironmentError: 필수 환경 변수가 없을 때
        """
        load_dotenv()
        
        missing = []
        for var_name, description in cls.REQUIRED_ENV_VARS.items():
            value = os.getenv(var_name)
            if not value or value.strip() == "":
                missing.append(f"{var_name} ({description})")
        
        if missing:
            error_msg = (
                f"다음 필수 환경 변수가 설정되지 않았습니다:\n" +
                "\n".join(f"  - {m}" for m in missing) +
                "\n\n.env 파일을 확인하거나 GitHub Actions Secrets를 설정하세요."
            )
            logger.error(error_msg)
            raise EnvironmentError(error_msg)
        
        # 선택적 환경 변수 기본값 설정
        for var_name, default_value in cls.OPTIONAL_ENV_VARS.items():
            if not os.getenv(var_name):
                os.environ[var_name] = default_value
        
        logger.info("환경 변수 검증 완료")
        return dict(os.environ)
    
    @classmethod
    def validate_config(cls, cfg: Dict[str, Any]) -> None:
        """
        universe.yaml 설정 검증
        
        Args:
            cfg: 설정 딕셔너리
        
        Raises:
            ValueError: 설정이 유효하지 않을 때
        """
        auto = cfg.get("auto", {})
        
        # 가격 범위 검증
        min_price = auto.get("min_price", 3)
        max_price = auto.get("max_price", 500)
        if min_price >= max_price:
            raise ValueError(
                f"min_price({min_price})가 max_price({max_price})보다 크거나 같습니다."
            )
        
        # 필터링 개수 검증
        tech_filter = auto.get("tech_filter_count", 30)
        max_universe = auto.get("max_final_universe", 150)
        if tech_filter > max_universe:
            raise ValueError(
                f"tech_filter_count({tech_filter})가 max_final_universe({max_universe})보다 클 수 없습니다."
            )
        
        # 풀 검증
        pool = auto.get("pool", "nasdaq100")
        if pool not in ["sp500", "nasdaq100"]:
            logger.warning(f"알 수 없는 pool 타입: {pool}. nasdaq100 또는 sp500을 사용하세요.")
        
        logger.info("설정 검증 완료")


def get_env(key: str, default: Optional[str] = None) -> str:
    """환경 변수 가져오기 (로깅 포함)"""
    value = os.getenv(key, default)
    if value is None:
        logger.warning(f"환경 변수 {key}가 설정되지 않았습니다.")
    return value or ""
