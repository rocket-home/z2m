#!/usr/bin/env python3
"""
Z2M Manager - Точка входа
Запуск: python z2m.py [--cli]
"""

import sys
import os
from pathlib import Path

# Переходим в директорию z2m
script_dir = Path(__file__).parent
os.chdir(script_dir)

# Добавляем путь к модулям
sys.path.insert(0, str(script_dir))

try:
    from z2m_manager.cli import main
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print()
    print("Установите зависимости:")
    print("  pip install -r z2m_manager/requirements.txt")
    print()
    print("Или используйте лаунчер (рекомендуется):")
    print("  ./z2m [--cli]")
    sys.exit(1)

if __name__ == "__main__":
    main()

