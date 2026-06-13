import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "app.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] - %(message)s"
LOG_FORMAT_PROD = "%(asctime)s [%(levelname)s] - %(message)s"


def _get_environment() -> str:
    env = (os.getenv("ENV") or "dev").strip().lower()
    if env in {"prod", "production"}:
        return "prod"
    return "dev"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if getattr(logger, "_configured", False):
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    env = _get_environment()
    is_prod = env == "prod"

    log_format = LOG_FORMAT_PROD if is_prod else LOG_FORMAT
    formatter = logging.Formatter(log_format)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    log_level = logging.WARNING if is_prod else logging.INFO
    logger.setLevel(log_level)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    logger._configured = True  # type: ignore[attr-defined]
    return logger
