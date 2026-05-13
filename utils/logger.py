import sys
from loguru import logger
from config import settings


def setup_logger() -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
    )
    logger.add(
        settings.LOGS_DIR / "bot_{time:YYYY-MM-DD}.log",
        level=settings.LOG_LEVEL,
        rotation="00:00",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} — {message}",
    )
