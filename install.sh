#!/usr/bin/env bash
# install.sh — Install dependencies for silero-tts-reader (Arch / Debian / Fedora)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== Silero TTS Reader — установка зависимостей ==="
echo

# ── Detect distro ─────────────────────────────────────────────────────────────
if command -v pacman &>/dev/null; then
    DISTRO="arch"
elif command -v apt-get &>/dev/null; then
    DISTRO="debian"
elif command -v dnf &>/dev/null; then
    DISTRO="fedora"
else
    DISTRO="unknown"
fi
ok "Дистрибутив: $DISTRO"

# ── System tools ──────────────────────────────────────────────────────────────
echo
echo "=== Системные инструменты ==="

install_sys_pkg() {
    case "$DISTRO" in
        arch)    sudo pacman -S --noconfirm "$1" ;;
        debian)  sudo apt-get install -y "$2" ;;
        fedora)  sudo dnf install -y "$3" ;;
        *)       err "Установите вручную: $1"; return 1 ;;
    esac
}

if command -v xdotool &>/dev/null; then
    ok "xdotool уже установлен"
else
    warn "Устанавливаю xdotool..."
    install_sys_pkg xdotool xdotool xdotool
    ok "xdotool установлен"
fi

if command -v xclip &>/dev/null; then
    ok "xclip уже установлен"
else
    warn "Устанавливаю xclip..."
    install_sys_pkg xclip xclip xclip
    ok "xclip установлен"
fi

HAS_RUBBERBAND=false
if command -v rubberband &>/dev/null; then
    ok "rubberband уже установлен"
    HAS_RUBBERBAND=true
else
    warn "rubberband не найден (изменение скорости без него недоступно)"
    read -rp "Установить rubberband? [y/N] " answer
    if [[ "${answer,,}" == "y" ]]; then
        install_sys_pkg rubberband rubberband-cli rubberband
        ok "rubberband установлен"
        HAS_RUBBERBAND=true
    else
        warn "Пропускаем rubberband."
    fi
fi

# ── Wayland Global Hotkeys permissions (udev) ──────────────────────────────────
echo
echo "=== Настройка прав для глобальных хоткеев (Wayland) ==="
if [[ "$DISTRO" == "arch" || "$DISTRO" == "debian" || "$DISTRO" == "fedora" ]]; then
    if [[ ! -f /etc/udev/rules.d/99-input-permissions.rules ]]; then
        echo 'KERNEL=="event*", MODE="0666"' | sudo tee /etc/udev/rules.d/99-input-permissions.rules >/dev/null
        sudo udevadm control --reload-rules && sudo udevadm trigger 2>/dev/null || true
        sudo chmod 666 /dev/input/event* 2>/dev/null || true
        ok "Правила udev для глобальных хоткеев добавлены (/etc/udev/rules.d/99-input-permissions.rules)"
    else
        sudo chmod 666 /dev/input/event* 2>/dev/null || true
        ok "Правила udev для глобальных хоткеев уже присутствуют"
    fi
fi

# ── Arch: system Python packages via pacman ────────────────────────────────────
if [[ "$DISTRO" == "arch" ]]; then
    echo
    echo "=== Системные Python-пакеты (pacman) ==="

    PACMAN_PKGS=()
    python3 -c "import PyQt6"     2>/dev/null && ok "python-pyqt6 уже установлен"    || PACMAN_PKGS+=(python-pyqt6)
    python3 -c "import numpy"     2>/dev/null && ok "python-numpy уже установлен"     || PACMAN_PKGS+=(python-numpy)
    python3 -c "import pyperclip" 2>/dev/null && ok "python-pyperclip уже установлен" || PACMAN_PKGS+=(python-pyperclip)

    # python-pytorch is large — check if already installed, offer choice
    if python3 -c "import torch" 2>/dev/null; then
        ok "python-pytorch уже установлен"
    else
        PACMAN_PKGS+=(python-pytorch-opt-cpu)
        warn "Будет установлен python-pytorch-opt-cpu (~500 МБ)"
    fi

    if [[ ${#PACMAN_PKGS[@]} -gt 0 ]]; then
        echo "Устанавливаю через pacman: ${PACMAN_PKGS[*]}"
        sudo pacman -S --noconfirm "${PACMAN_PKGS[@]}"
        ok "Пакеты установлены"
    fi
fi

# ── Virtual environment ───────────────────────────────────────────────────────
echo
echo "=== Виртуальное окружение ==="

if [[ ! -d "$VENV_DIR" ]]; then
    if [[ "$DISTRO" == "arch" ]]; then
        # --system-site-packages: PyQt6, numpy, torch видны внутри venv
        python3 -m venv --system-site-packages "$VENV_DIR"
        ok "venv создан с --system-site-packages: $VENV_DIR"
    else
        python3 -m venv "$VENV_DIR"
        ok "venv создан: $VENV_DIR"
    fi
else
    ok "venv уже существует: $VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python"

"$PIP" install --quiet --upgrade pip

# ── pip packages into venv ────────────────────────────────────────────────────
echo
echo "=== Python-пакеты (pip → venv) ==="

if [[ "$DISTRO" == "arch" ]]; then
    # Only packages NOT in pacman repos
    PIP_PKGS=(pynput sounddevice evdev)
else
    # All via pip for non-Arch
    PIP_PKGS=(PyQt6 numpy torch pynput sounddevice pyperclip evdev)
fi

"$PIP" install --quiet "${PIP_PKGS[@]}"
ok "Установлено: ${PIP_PKGS[*]}"

if [[ "$HAS_RUBBERBAND" == "true" ]]; then
    "$PIP" install --quiet pyrubberband
    ok "pyrubberband установлен (ускорение скорости активно)"
fi

# ── Download Silero TTS model ─────────────────────────────────────────────────
echo
echo "=== Модель Silero TTS ==="

MODEL_DIR="$HOME/.local/share/silero-tts-reader"
MODEL_PATH="$MODEL_DIR/v5_ru.pt"
MODEL_URL="https://models.silero.ai/models/tts/ru/v5_ru.pt"

if [[ -f "$MODEL_PATH" ]]; then
    ok "Модель уже существует: $MODEL_PATH"
else
    echo "Скачиваю русскую модель Silero v5 (~145 МБ)..."
    mkdir -p "$MODEL_DIR"
    if command -v wget &>/dev/null; then
        wget --show-progress -O "$MODEL_PATH" "$MODEL_URL"
    elif command -v curl &>/dev/null; then
        curl -L --progress-bar -o "$MODEL_PATH" "$MODEL_URL"
    else
        err "Нет wget или curl. Скачайте вручную:"
        err "  $MODEL_URL  →  $MODEL_PATH"
        exit 1
    fi
    ok "Модель скачана: $MODEL_PATH"
fi

# Update config with correct model path if needed
CONFIG_FILE="$HOME/.config/silero-tts-reader/config.json"
if [[ -f "$CONFIG_FILE" ]]; then
    # Update model_path in existing config using Python
    "$PYTHON" - <<EOF
import json, pathlib
cfg_path = pathlib.Path("$CONFIG_FILE")
cfg = json.loads(cfg_path.read_text())
cfg.setdefault("tts", {})["model_path"] = "$MODEL_PATH"
cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
print("Конфиг обновлён: model_path →", "$MODEL_PATH")
EOF
fi

# ── Verify ────────────────────────────────────────────────────────────────────
echo
echo "=== Проверка ==="
"$PYTHON" -c "
import PyQt6, numpy, torch, pynput, sounddevice
print('✓ Все модули доступны')
print(f'  PyQt6: {PyQt6.QtCore.PYQT_VERSION_STR}')
print(f'  torch: {torch.__version__}')
"

echo
echo "=== Готово! ==="
ok "Запустить приложение: python run.py"
