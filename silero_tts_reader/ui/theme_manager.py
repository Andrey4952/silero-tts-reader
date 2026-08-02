"""Theme manager — detects system theme (dark/light) and provides color tokens."""
from __future__ import annotations

import subprocess
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage, QDBusVariant

DEFAULT_ACCENT = "#6366f1"

GNOME_ACCENT_MAP = {
    "blue": "#3584e4",
    "teal": "#129eaf",
    "green": "#2ec27e",
    "yellow": "#e5a50a",
    "orange": "#e66100",
    "red": "#e01b24",
    "pink": "#d56199",
    "purple": "#9141ac",
    "slate": "#62a0ea",
}


class ThemeManager(QObject):
    """Manages dark/light theme resolution, accent colors, and theme change notifications."""

    theme_changed = pyqtSignal(bool)  # emits is_dark: bool
    accent_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = "system"  # "system", "dark", "light"
        self._use_system_accent = True
        self._last_detected_dark: bool = True
        self._last_detected_accent: str = DEFAULT_ACCENT

        app = QApplication.instance()
        if app:
            hints = app.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(lambda _: self._sync_system_theme())
            if hasattr(app, "paletteChanged"):
                app.paletteChanged.connect(lambda _: self._sync_system_theme())

        # Subscribe to Freedesktop Settings DBus signal
        try:
            bus = QDBusConnection.sessionBus()
            if bus.isConnected():
                bus.connect(
                    "org.freedesktop.portal.Desktop",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.Settings",
                    "SettingChanged",
                    self._on_dbus_setting_changed,
                )
        except Exception:
            pass

        # Autostart retry timers (500ms, 1.5s, 3s, 6s, 10s) for desktop portal initialization
        for delay in (500, 1500, 3000, 6000, 10000):
            QTimer.singleShot(delay, self._sync_system_theme)

        # Initial sync
        self._last_detected_dark = self.is_dark()
        self._last_detected_accent = self.accent_color()

    @pyqtSlot(str, str, QDBusVariant)
    def _on_dbus_setting_changed(self, namespace: str, key: str, value: QDBusVariant) -> None:
        if namespace == "org.freedesktop.appearance":
            self._sync_system_theme()

    def _sync_system_theme(self) -> None:
        cur_dark = self.is_dark()
        cur_accent = self.accent_color()

        dark_changed = cur_dark != self._last_detected_dark
        accent_changed = cur_accent != self._last_detected_accent

        self._last_detected_dark = cur_dark
        self._last_detected_accent = cur_accent

        if dark_changed:
            self.theme_changed.emit(cur_dark)
        if accent_changed:
            self.accent_changed.emit()

    def set_mode(self, mode: str) -> None:
        if mode in ("system", "dark", "light"):
            self._mode = mode
            self._sync_system_theme()

    def get_mode(self) -> str:
        return self._mode

    @property
    def use_system_accent(self) -> bool:
        return self._use_system_accent

    @use_system_accent.setter
    def use_system_accent(self, enabled: bool) -> None:
        self._use_system_accent = bool(enabled)
        self._sync_system_theme()

    def accent_color(self) -> str:
        """Return hex color string for current accent (e.g. '#009900' or '#6366f1')."""
        if not self._use_system_accent:
            return DEFAULT_ACCENT

        return self.detect_system_accent_color()

    @staticmethod
    def detect_system_accent_color() -> str:
        # 1. Freedesktop Portal DBus check
        try:
            bus = QDBusConnection.sessionBus()
            if bus.isConnected():
                iface = QDBusInterface(
                    "org.freedesktop.portal.Desktop",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.Settings",
                    bus,
                )
                reply = iface.call("Read", "org.freedesktop.appearance", "accent-color")
                if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
                    val = reply.arguments()[0]
                    if isinstance(val, (tuple, list)) and len(val) == 3:
                        r, g, b = val
                        return QColor.fromRgbF(float(r), float(g), float(b)).name()
        except Exception:
            pass

        # 2. GSettings check
        try:
            res = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "accent-color"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.returncode == 0:
                val = res.stdout.strip().strip("'\"").lower()
                if val in GNOME_ACCENT_MAP:
                    return GNOME_ACCENT_MAP[val]
                if val.startswith("#") and len(val) in (7, 9):
                    return val[:7]
        except Exception:
            pass

        # 3. Qt Palette Highlight
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
        # 1. Freedesktop Portal DBus
        try:
            bus = QDBusConnection.sessionBus()
            if bus.isConnected():
                iface = QDBusInterface(
                    "org.freedesktop.portal.Desktop",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.Settings",
                    bus,
                )
                reply = iface.call("Read", "org.freedesktop.appearance", "color-scheme")
                if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
                    val = reply.arguments()[0]
                    if val == 1:
                        return True
                    if val == 2:
                        return False
        except Exception:
            pass

        # 2. Qt style hints
        app = QApplication.instance()
        if app:
            hints = app.styleHints()
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False

        # 3. GSettings fallback
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

        if app:
            window_color = app.palette().window().color()
            if window_color.lightness() < 128:
                return True

        return True  # Default fallback is Dark

