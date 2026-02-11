"""
통합 로깅 시스템
"""
import logging
import sys
from pathlib import Path

def setup_logger(name: str = "stock-bot", level: str = "INFO") -> logging.Logger:
    """
    로거 설정
    
    Args:
        name: 로거 이름
        level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR)
    
    Returns:
        설정된 Logger 인스턴스
    """
    logger = logging.getLogger(name)
    
    # 이미 핸들러가 있으면 재설정 방지
    if logger.handlers:
        return logger
    
    # 로그 레벨 설정
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)
    
    # 포맷 설정
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 파일 핸들러 (선택적)
    log_dir = Path("logs")
    if log_dir.exists() or True:  # GitHub Actions에서는 logs 디렉토리 없을 수 있음
        try:
            log_dir.mkdir(exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "stock-bot.log", encoding='utf-8')
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass  # 파일 로깅 실패해도 콘솔 로깅은 계속
    
    return logger


# 전역 로거 인스턴스
logger = setup_logger()
