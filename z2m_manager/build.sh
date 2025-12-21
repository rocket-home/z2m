#!/bin/bash
# Сборка z2m.pyz - автономного архива приложения

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/.build"
OUTPUT_FILE="$PROJECT_DIR/z2m.pyz"

echo "🔨 Сборка z2m.pyz..."

# Очистка предыдущей сборки
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Копируем модуль
cp -r "$SCRIPT_DIR" "$BUILD_DIR/z2m_manager"

# Создаём __main__.py для запуска из архива
cat > "$BUILD_DIR/__main__.py" << 'EOF'
#!/usr/bin/env python3
import sys
import os
from pathlib import Path

# Определяем директорию, откуда запущен архив
if hasattr(sys, '_MEIPASS'):
    # PyInstaller
    base_dir = Path(sys._MEIPASS)
else:
    # Обычный запуск или .pyz
    base_dir = Path(__file__).parent.parent

# Переходим в директорию z2m
os.chdir(base_dir)

from z2m_manager.cli import main
main()
EOF

# Собираем .pyz
cd "$BUILD_DIR"
python3 -m zipapp . -o "$OUTPUT_FILE" -p "/usr/bin/env python3" -c

# Очистка
rm -rf "$BUILD_DIR"

chmod +x "$OUTPUT_FILE"

echo "✅ Собрано: $OUTPUT_FILE"
echo ""
echo "Запуск:"
echo "  ./z2m.pyz        - TUI режим"
echo "  ./z2m.pyz --cli  - CLI режим"

