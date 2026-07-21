"""Theme manager — detects system theme (dark/light) and provides color tokens."""
from __future__ import annotations

import subprocess
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

DEFAULT_ACCENT = "#6366f1"


class ThemeManager(QObject):
    """Manages dark/light theme resolution, accent colors, and theme change notifications."""

    theme_changed = pyqtSignal(bool)  # emits is_dark: bool
    accent_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = "system"  # "system", "dark", "light"
        self._use_system_accent = True
        app = QApplication.instance()
        if app:
            hints = app.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

    def set_mode(self, mode: str) -> None:
        if mode in ("system", "dark", "light"):
            self._mode = mode
            self.theme_changed.emit(self.is_dark())

    def get_mode(self) -> str:
        return self._mode

    @property
    def use_system_accent(self) -> bool:
        return self._use_system_accent

    @use_system_accent.setter
    def use_system_accent(self, enabled: bool) -> None:
        self._use_system_accent = bool(enabled)
        self.accent_changed.emit()

    def accent_color(self) -> str:
        """Return hex color string for current accent (e.g. '#009900' or '#6366f1')."""
        if not self._use_system_accent:
            return DEFAULT_ACCENT

        app = QApplication.instance()
        if app:
            palette = app.palette()
            color = palette.color(QPalette.ColorRole.Highlight)
            if color.isValid() and color.name().lower() not in ("#000000", "#ffffff"):
                return color.name()

        return DEFAULT_ACCENT

    def accent_hover_color(self) -> str:
        """Return hex color string for hovered accent elements."""
        base = QColor(self.accent_color())
        if base.lightness() > 140:
            return base.darker(115).name()
        return base.lighter(120).name()

    def accent_pressed_color(self) -> str:
        """Return hex color string for pressed accent elements."""
        base = QColor(self.accent_color())
        if base.lightness() > 140:
            return base.darker(130).name()
        return base.lighter(135).name()

    def is_dark(self) -> bool:
        if self._mode == "dark":
            return True
        if self._mode == "light":
            return False

        # Mode is "system": detect system dark mode
        return self.detect_system_is_dark()

    @staticmethod
    def detect_system_is_dark() -> bool:
        app = QApplication.instance()
        if app:
            hints = app.styleHints()
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False

            # Check Qt window palette lightness
            window_color = app.palette().window().color()
            if window_color.lightness() < 128:
                return True

        # Fallback to gsettings / portal check on Linux
        try:
            res = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if "dark" in res.stdout.lower():
                return True
            if "light" in res.stdout.lower():
                return False
        except Exception:
            pass

        return True  # Default fallback is Dark

    def _on_system_scheme_changed(self) -> None:
        if self._mode == "system":
            self.theme_changed.emit(self.is_dark())
        self.accent_changed.emit()
