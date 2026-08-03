"""Main application controller — wires all components together."""
from __future__ import annotations

import os
import queue
import threading
from typing import Optional

# Suppress pynput Xlib noise before any imports
os.environ.setdefault("PYNPUT_BACKEND", "xorg")

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from .config.config_manager import ConfigManager
from .core.audio_player import AudioPlayer
from .core.hotkey_listener import HotkeyListener
from .core.text_extractor import TextExtractor
from .core.tts_engine import TTSEngine, TTSLoadError
from .core.tts_worker import TTSWorker
from .core.text_processor import TextProcessor
from .ui.playback_widget import PlaybackWidget
from .ui.tray_icon import TrayIcon
from .ui.settings_window import SettingsWindow
from .ui.theme_manager import ThemeManager


class _Bridge(QObject):
    """Qt signal bridge for cross-thread communication."""
    speak_text = pyqtSignal(str)
    speak_clipboard = pyqtSignal()
    stop = pyqtSignal()
    playback_started = pyqtSignal()
    playback_paused = pyqtSignal()
    playback_resumed = pyqtSignal()
    playback_finished = pyqtSignal()
    playback_stopped = pyqtSignal()
    segment_progress = pyqtSignal(float)
    speed_unavailable = pyqtSignal()
    tts_error = pyqtSignal(str)
    no_text = pyqtSignal()
    clipboard_no_text = pyqtSignal()
    text_too_long = pyqtSignal(str)  # emits full text, user chooses
    rewind_playback = pyqtSignal(float)          # relative seconds (+5.0 or -5.0)
    seek_fraction_playback = pyqtSignal(float)   # 0.0 to 1.0


class Application(QApplication):
    """Top-level application object."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)
        self.setApplicationName("Silero TTS Reader")
        self.setApplicationVersion("1.0.0")

        self._config = ConfigManager()
        self._extractor = TextExtractor()
        self._engine = TTSEngine(
            model_path=self._config.model_path,
            sample_rate=self._config.sample_rate,
        )
        self._hotkey_listener = HotkeyListener()
        self._bridge = _Bridge()
        self._stop_event = threading.Event()
        self._player: Optional[AudioPlayer] = None
        self._worker: Optional[TTSWorker] = None
        self._audio_queue: queue.Queue = queue.Queue()

        # UI
        self._theme_manager = ThemeManager(self)
        self._widget = PlaybackWidget()
        self._widget.restore_position(*self._config.widget_position)

        self._theme_manager.theme_changed.connect(lambda _: self._update_widget_theme())
        self._theme_manager.accent_changed.connect(self._update_widget_theme)

        self._theme_manager.use_system_accent = self._config.use_system_accent
        self._theme_manager.set_mode(self._config.theme)
        self._update_widget_theme()

        self._tray = TrayIcon(
            on_speak_clipboard=self._on_speak_clipboard_hotkey,
            on_settings=self._open_settings,
            on_about=self._show_about,
            on_quit=self._quit,
        )
        self._tray.show()

        print(
            "\n✓ Silero TTS Reader запущен"
            "\n  Горячие клавиши:"
            f"\n    Озвучить выделенное : {self._config.hotkey_speak_selection}"
            f"\n    Озвучить буфер      : {self._config.hotkey_speak_clipboard}"
            f"\n    Стоп                : {self._config.hotkey_stop}"
            "\n  Иконка в трее — правая кнопка мыши для меню"
            "\n  Нажмите Ctrl+C в терминале для выхода\n"
        )

        self._connect_signals()
        self._register_hotkeys()
        self._load_engine()

    # ── Engine loading ────────────────────────────────────────────────────────

    def _load_engine(self) -> None:
        def _load():
            try:
                print("  Загружаю модель TTS...", flush=True)
                self._engine.load()
                speakers = self._engine.get_available_speakers()
                if self._config.speaker not in speakers and speakers:
                    self._config.speaker = speakers[0]
                    self._config.save()
                print(f"  ✓ Модель загружена. Голос: {self._config.speaker}", flush=True)
                print(f"    Доступные голоса: {', '.join(speakers)}", flush=True)
            except TTSLoadError as exc:
                print(f"  ✗ Ошибка: {exc}", flush=True)
                self._bridge.tts_error.emit(str(exc))

        threading.Thread(target=_load, daemon=True, name="ModelLoader").start()

    # ── Signal wiring ─────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        b = self._bridge
        w = self._widget

        b.speak_text.connect(self._start_tts)
        b.speak_clipboard.connect(self._on_speak_clipboard_hotkey)
        b.stop.connect(self._stop_playback)

        b.playback_started.connect(w.fade_in)
        b.playback_started.connect(lambda: w.set_paused(False))
        b.segment_progress.connect(w.set_progress)
        b.playback_paused.connect(lambda: w.set_paused(True))
        b.playback_resumed.connect(lambda: w.set_paused(False))
        b.playback_finished.connect(lambda: w.schedule_hide(2000))
        b.playback_stopped.connect(lambda: w.schedule_hide(500))
        b.tts_error.connect(self._show_tts_error)
        b.no_text.connect(lambda: self._notify("Текст не выделен"))
        b.clipboard_no_text.connect(lambda: self._notify("Буфер обмена не содержит текст"))
        b.text_too_long.connect(self._ask_long_text)

        w.pause_resume_clicked.connect(self._toggle_pause)
        w.stop_clicked.connect(self._stop_playback)
        w.speed_changed.connect(self._on_speed_changed)
        w.rewind_clicked.connect(lambda s: self._bridge.rewind_playback.emit(s))
        w.seek_fraction_clicked.connect(lambda f: self._bridge.seek_fraction_playback.emit(f))

        b.rewind_playback.connect(self._on_rewind_playback)
        b.seek_fraction_playback.connect(self._on_seek_fraction_playback)

        # Save widget position when moved
        QTimer.singleShot(500, self._start_position_tracker)

    def _start_position_tracker(self) -> None:
        """Periodically save widget position to config."""
        timer = QTimer(self)
        timer.setInterval(2000)
        timer.timeout.connect(self._save_widget_position)
        timer.start()

    # ── Hotkeys ───────────────────────────────────────────────────────────────

    def _register_hotkeys(self) -> None:
        try:
            self._hotkey_listener.set_hotkeys(
                speak_selection=self._config.hotkey_speak_selection,
                speak_clipboard=self._config.hotkey_speak_clipboard,
                stop=self._config.hotkey_stop,
                on_speak_selection=lambda: self._bridge.speak_text.emit("__selection__"),
                on_speak_clipboard=lambda: self._bridge.speak_clipboard.emit(),
                on_stop=lambda: self._bridge.stop.emit(),
            )
        except ValueError:
            pass  # Conflict — already handled in settings

    # ── TTS pipeline ──────────────────────────────────────────────────────────

    def _start_tts(self, text_or_marker: str) -> None:
        if text_or_marker == "__selection__":
            text = self._extractor.get_selected_text()
            if not text:
                self._bridge.no_text.emit()
                return
        else:
            text = text_or_marker

        if len(text) > TextExtractor.MAX_CHARS:
            self._bridge.text_too_long.emit(text)
            return

        self._stop_playback_sync()
        self._begin_pipeline(text)

    def _begin_pipeline(
        self,
        text: str,
        speaker: str | None = None,
        speed: float | None = None,
    ) -> None:
        if not self._engine.is_loaded:
            self._notify("Модель TTS ещё загружается, подождите...")
            return

        self._stop_event = threading.Event()
        self._audio_queue = queue.Queue()
        use_speed = speed if speed is not None else self._config.speed
        use_speaker = speaker if speaker is not None else self._config.speaker

        self._player = AudioPlayer(
            audio_queue=self._audio_queue,
            stop_event=self._stop_event,
            sample_rate=self._config.sample_rate,
            speed=use_speed,
            on_started=lambda: self._bridge.playback_started.emit(),
            on_segment_progress=lambda v: self._bridge.segment_progress.emit(v),
            on_paused=lambda: self._bridge.playback_paused.emit(),
            on_resumed=lambda: self._bridge.playback_resumed.emit(),
            on_finished=lambda: self._bridge.playback_finished.emit(),
            on_stopped=lambda: self._bridge.playback_stopped.emit(),
            on_speed_unavailable=lambda: self._bridge.speed_unavailable.emit(),
        )

        self._worker = TTSWorker(
            engine=self._engine,
            text=text,
            speaker=use_speaker,
            config=self._config,
            audio_queue=self._audio_queue,
            stop_event=self._stop_event,
            on_error=lambda e: self._bridge.tts_error.emit(e),
        )

        self._worker.start()
        self._player.start()

    def _stop_playback(self) -> None:
        self._stop_event.set()

    def _stop_playback_sync(self) -> None:
        self._stop_event.set()
        if self._player and self._player.is_alive():
            self._player.join(timeout=1.0)

    def _toggle_pause(self) -> None:
        if self._player is None:
            return
        if self._player.is_paused:
            self._player.resume()
        else:
            self._player.pause()

    def _on_rewind_playback(self, seconds: float) -> None:
        if self._player and self._player.is_alive():
            print(f"[SileroApp] Перемотка аудио на {seconds:+.1f} сек", flush=True)
            self._player.seek_relative(seconds)

    def _on_seek_fraction_playback(self, fraction: float) -> None:
        if self._player and self._player.is_alive():
            print(f"[SileroApp] Переход к позиции {fraction*100:.1f}%", flush=True)
            self._player.seek_fraction(fraction)

    # ── Clipboard ─────────────────────────────────────────────────────────────

    def _on_speak_clipboard_hotkey(self) -> None:
        text = self._extractor.get_clipboard_text()
        if not text:
            self._bridge.clipboard_no_text.emit()
            return
        self._bridge.speak_text.emit(text)

    # ── Speed ─────────────────────────────────────────────────────────────────

    def _on_speed_changed(self, speed: float) -> None:
        self._config.speed = speed
        self._config.save()
        if self._player and self._player.is_alive():
            self._player.set_speed(speed)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        speakers = self._engine.get_available_speakers() if self._engine.is_loaded else []
        dlg = SettingsWindow(
            config=self._config,
            speakers=speakers,
            on_preview_voice=self._preview_voice,
            theme_manager=self._theme_manager,
        )
        dlg.settings_saved.connect(self._apply_settings)
        dlg.exec()

    def _update_widget_theme(self) -> None:
        self._widget.apply_theme(
            is_dark=self._theme_manager.is_dark(),
            accent_color=self._theme_manager.accent_color(),
        )

    def _apply_settings(self) -> None:
        self._theme_manager.use_system_accent = self._config.use_system_accent
        self._theme_manager.set_mode(self._config.theme)
        self._update_widget_theme()
        self._register_hotkeys()
        self._widget.set_speed(self._config.speed)

    def _preview_voice(self, speaker: str, speed: float) -> None:
        self._stop_playback_sync()
        self._begin_pipeline(
            f"Привет! Это голос {speaker} со скоростью {speed:.2g}x.",
            speaker=speaker,
            speed=speed,
        )

    # ── Notifications ─────────────────────────────────────────────────────────

    def _notify(self, message: str, title: str = "Silero TTS Reader") -> None:
        self._tray.showMessage(title, message, self._tray.icon(), 2500)

    def _show_tts_error(self, error_text: str) -> None:
        msg = QMessageBox()
        msg.setWindowTitle("Ошибка TTS")
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setText("<b>Не удалось синтезировать речь</b>")
        msg.setDetailedText(error_text)
        msg.setStyleSheet("QMessageBox { background: #0f172a; color: #e2e8f0; }")
        msg.exec()

    def _ask_long_text(self, text: str) -> None:
        msg = QMessageBox()
        msg.setWindowTitle("Длинный текст")
        msg.setText(
            f"Текст содержит {len(text):,} символов (лимит: {TextExtractor.MAX_CHARS:,}).\n"
            "Что сделать?"
        )
        btn_full = msg.addButton("Озвучить полностью", QMessageBox.ButtonRole.AcceptRole)
        btn_trim = msg.addButton("Обрезать до лимита", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_full:
            self._begin_pipeline(text)
        elif clicked is btn_trim:
            self._begin_pipeline(text[: TextExtractor.MAX_CHARS])

    def _show_about(self) -> None:
        msg = QMessageBox()
        msg.setWindowTitle("О программе")
        msg.setIconPixmap(self._tray.icon().pixmap(48, 48))
        msg.setText(
            "<b>Silero TTS Reader</b> v1.0.0<br><br>"
            "Озвучивание выделенного текста с помощью Silero TTS.<br><br>"
            "Горячие клавиши и голос настраиваются в Настройках."
        )
        msg.exec()

    # ── Position tracking ─────────────────────────────────────────────────────

    def _save_widget_position(self) -> None:
        if self._widget.isVisible():
            pos = self._widget.pos()
            self._config.widget_position = (pos.x(), pos.y())

    # ── Quit ──────────────────────────────────────────────────────────────────

    def _quit(self) -> None:
        self._stop_playback_sync()
        self._hotkey_listener.stop_listening()
        self._config.save()
        self.quit()
