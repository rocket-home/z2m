"""
Маленький общий логгер для z2m_manager.

Цель — заменить тихие `except Exception: pass/return` на логируемые ошибки,
не меняя поток управления. Уровень берётся из env `Z2M_LOG_LEVEL` (по умолчанию WARNING),
чтобы в обычном режиме (TUI/CLI) вывод оставался тихим.
"""
import logging
import os

_configured = False


def get_logger(name: str = "z2m") -> logging.Logger:
    global _configured
    if not _configured:
        level_name = os.environ.get("Z2M_LOG_LEVEL", "WARNING").upper()
        level = getattr(logging, level_name, logging.WARNING)
        # basicConfig — идемпотентен; если приложение настроило логирование само, не мешаем.
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        _configured = True
    return logging.getLogger(name)
