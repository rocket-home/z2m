#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/z2m"

if [[ ! -f "$SRC" ]]; then
  echo "❌ Не найден лаунчер: $SRC"
  exit 1
fi

chmod +x "$SRC" || true

install_link() {
  local target_bin="$1"
  local target_dir
  target_dir="$(dirname "$target_bin")"
  mkdir -p "$target_dir" 2>/dev/null || true

  if [[ -e "$target_bin" || -L "$target_bin" ]]; then
    echo "ℹ️ $target_bin уже существует, перезаписываю симлинк"
  fi

  ln -sf "$SRC" "$target_bin"
  echo "✅ Установлено: $target_bin -> $SRC"
}

# 1) /usr/local/bin (предпочтительно)
TARGET_BIN="/usr/local/bin/z2m"
if [[ -w "$(dirname "$TARGET_BIN")" ]]; then
  install_link "$TARGET_BIN"
  echo "Теперь можно запускать: z2m"
  exit 0
fi

# 2) пробуем sudo без запроса пароля (sudo -n)
if command -v sudo >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then
    sudo ln -sf "$SRC" "$TARGET_BIN"
    echo "✅ Установлено: $TARGET_BIN -> $SRC"
    echo "Теперь можно запускать: z2m"
    exit 0
  fi
fi

# 3) fallback без sudo: ~/.local/bin
TARGET_BIN="$HOME/.local/bin/z2m"
install_link "$TARGET_BIN"
echo "Теперь можно запускать: z2m"
echo "Если команда не найдена — добавьте в PATH: export PATH=\"$HOME/.local/bin:$PATH\""


