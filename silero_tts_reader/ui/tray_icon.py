"""System tray icon with context menu."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu


_ASSETS_DIR = Path(__file__).parent.parent / "assets"


class TrayIcon(QSystemTrayIcon):
    """Application system tray icon and context menu."""

    def __init__(
        self,
        on_speak_clipboard: Callable[[], None],
        on_settings: Callable[[], None],
        on_about: Callable[[], None],
        on_quit: Callable[[], None],
        parent=None,
    ) -> None:
        icon_path = _ASSETS_DIR / "icon.png"
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon.fromTheme("audio-headset")
        super().__init__(icon, parent)

        self._on_speak_clipboard = on_speak_clipboard
        self._on_settings = on_settings
        self._on_about = on_about
        self._on_quit = on_quit

        self._build_menu()
        self.setToolTip("Silero TTS Reader")

    def _build_menu(self) -> None:
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background: #1e293b;
                color: #e2e8f0;
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                padding: 4px;
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: rgba(99, 102, 241, 0.6);
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255,255,255,0.1);
                margin: 4px 8px;
            }
        """)

        act_clipboard = menu.addAction("📋  Озвучить буфер обмена")
        act_clipboard.triggered.connect(self._on_speak_clipboard)

        menu.addSeparator()

        act_settings = menu.addAction("⚙️  Настройки")
        act_settings.triggered.connect(self._on_settings)

        act_about = menu.addAction("ℹ️  О программе")
        act_about.triggered.connect(self._on_about)

        menu.addSeparator()

        act_quit = menu.addAction("✖  Выход")
        act_quit.triggered.connect(self._on_quit)

        self.setContextMenu(menu)
