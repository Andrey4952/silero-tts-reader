"""Global hotkey listener — evdev backend (Wayland) with pynput fallback (X11)."""
from __future__ import annotations

import os
import threading
from typing import Callable

# evdev key codes
try:
    from evdev import ecodes as ec
    import evdev
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

from pynput import keyboard as pynput_keyboard


_MODIFIERS = {
    ec.KEY_LEFTCTRL, ec.KEY_RIGHTCTRL,
    ec.KEY_LEFTALT, ec.KEY_RIGHTALT,
    ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT,
    ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA,
} if HAS_EVDEV else set()

_REGULAR_KEY_MAP: dict[str, int] = {
    "a": ec.KEY_A, "b": ec.KEY_B, "c": ec.KEY_C, "d": ec.KEY_D,
    "e": ec.KEY_E, "f": ec.KEY_F, "g": ec.KEY_G, "h": ec.KEY_H,
    "i": ec.KEY_I, "j": ec.KEY_J, "k": ec.KEY_K, "l": ec.KEY_L,
    "m": ec.KEY_M, "n": ec.KEY_N, "o": ec.KEY_O, "p": ec.KEY_P,
    "q": ec.KEY_Q, "r": ec.KEY_R, "s": ec.KEY_S, "t": ec.KEY_T,
    "u": ec.KEY_U, "v": ec.KEY_V, "w": ec.KEY_W, "x": ec.KEY_X,
    "y": ec.KEY_Y, "z": ec.KEY_Z,
    "0": ec.KEY_0, "1": ec.KEY_1, "2": ec.KEY_2, "3": ec.KEY_3,
    "4": ec.KEY_4, "5": ec.KEY_5, "6": ec.KEY_6, "7": ec.KEY_7,
    "8": ec.KEY_8, "9": ec.KEY_9,
    "f1": ec.KEY_F1, "f2": ec.KEY_F2, "f3": ec.KEY_F3, "f4": ec.KEY_F4,
    "f5": ec.KEY_F5, "f6": ec.KEY_F6, "f7": ec.KEY_F7, "f8": ec.KEY_F8,
    "f9": ec.KEY_F9, "f10": ec.KEY_F10, "f11": ec.KEY_F11, "f12": ec.KEY_F12,
    "space": ec.KEY_SPACE, "tab": ec.KEY_TAB, "enter": ec.KEY_ENTER, "return": ec.KEY_ENTER,
    "escape": ec.KEY_ESC, "esc": ec.KEY_ESC, "backspace": ec.KEY_BACKSPACE,
    "delete": ec.KEY_DELETE, "del": ec.KEY_DELETE, "insert": ec.KEY_INSERT, "ins": ec.KEY_INSERT,
    "home": ec.KEY_HOME, "end": ec.KEY_END, "page_up": ec.KEY_PAGEUP, "pgup": ec.KEY_PAGEUP,
    "page_down": ec.KEY_PAGEDOWN, "pgdn": ec.KEY_PAGEDOWN,
    "caps_lock": ec.KEY_CAPSLOCK, "num_lock": ec.KEY_NUMLOCK, "pause": ec.KEY_PAUSE,
} if HAS_EVDEV else {}

_PYNPUT_SPECIAL_KEYS = {
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "space", "tab", "enter", "return", "esc", "escape", "backspace", "delete",
    "del", "insert", "ins", "home", "end", "page_up", "page_down", "pgup", "pgdn",
    "caps_lock", "num_lock", "print_screen", "prtsc", "pause", "up", "down", "left", "right"
}


_SINGLE_MODIFIER_EVDEV = {
    "ctrl": {ec.KEY_LEFTCTRL, ec.KEY_RIGHTCTRL},
    "alt": {ec.KEY_LEFTALT, ec.KEY_RIGHTALT},
    "shift": {ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT},
    "super": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
    "meta": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
    "cmd": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
} if HAS_EVDEV else {}


def normalize_for_pynput(hotkey_str: str) -> str:
    """Format hotkey string for pynput.keyboard.GlobalHotKeys.

    - Modifiers and special keys are wrapped in <>: <ctrl>, <alt>, <f9>, <space>
    - Single character keys stay as is: z, r, 5
    """
    parts = [p.strip().strip("<>").lower() for p in hotkey_str.split("+")]
    result = []
    modifiers = {"ctrl", "alt", "shift", "super", "meta", "cmd"}
    for p in parts:
        if p in ("super", "meta"):
            result.append("<cmd>")
        elif p in modifiers or p in _PYNPUT_SPECIAL_KEYS:
            result.append(f"<{p}>")
        else:
            result.append(p)
    return "+".join(result)


def _parse_evdev_hotkey(hotkey_str: str) -> tuple[frozenset[int], frozenset[int]] | None:
    parts = [p.strip().strip("<>").lower() for p in hotkey_str.split("+")]

    if len(parts) == 1 and parts[0] in _SINGLE_MODIFIER_EVDEV:
        return frozenset(), frozenset(_SINGLE_MODIFIER_EVDEV[parts[0]])

    mods: set[int] = set()
    trigger: int | None = None

    mod_name_to_codes = {
        "ctrl": {ec.KEY_LEFTCTRL, ec.KEY_RIGHTCTRL},
        "alt":  {ec.KEY_LEFTALT, ec.KEY_RIGHTALT},
        "shift":{ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT},
        "super":{ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
        "meta": {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
        "cmd":  {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA},
    }

    for part in parts:
        if part in mod_name_to_codes:
            mods.update(mod_name_to_codes[part])
        else:
            code = _REGULAR_KEY_MAP.get(part)
            if code is not None:
                trigger = code

    if trigger is None:
        return None
    return frozenset(mods), frozenset([trigger])


class EvdevHotkeyListener(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name="EvdevHotkeyListener")
        self._hotkeys: dict[tuple[frozenset[int], frozenset[int]], Callable[[], None]] = {}
        self._stop_event = threading.Event()
        self._held_keys: set[int] = set()
        self._lock = threading.Lock()

    def set_hotkeys(self, hotkey_map: dict[str, Callable[[], None]]) -> None:
        new_hotkeys = {}
        for hk_str, cb in hotkey_map.items():
            parsed = _parse_evdev_hotkey(hk_str)
            if parsed:
                new_hotkeys[parsed] = cb
        with self._lock:
            self._hotkeys = new_hotkeys

    def stop_listening(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        import select
        devices = self.get_keyboards()
        if not devices:
            return

        fds = {d.fd: d for d in devices}

        while not self._stop_event.is_set():
            try:
                r, _, _ = select.select(list(fds.keys()), [], [], 0.2)
            except Exception:
                break

            for fd in r:
                device = fds[fd]
                try:
                    for event in device.read():
                        if event.type == ec.EV_KEY:
                            self._handle_key_event(event.code, event.value)
                except Exception:
                    pass

        for d in devices:
            try:
                d.close()
            except Exception:
                pass

    def _handle_key_event(self, code: int, value: int) -> None:
        if value == 1:
            self._held_keys.add(code)
            self._check_hotkeys(code)
        elif value == 0:
            self._held_keys.discard(code)

    def _check_hotkeys(self, trigger_code: int) -> None:
        with self._lock:
            for (required_mods, allowed_triggers), callback in self._hotkeys.items():
                if trigger_code not in allowed_triggers:
                    continue
                held_mods = self._held_keys & _MODIFIERS
                if self._mods_satisfied(required_mods, held_mods):
                    threading.Thread(target=callback, daemon=True).start()

    @staticmethod
    def _mods_satisfied(required: frozenset[int], held: set[int]) -> bool:
        ctrl_req  = required & {ec.KEY_LEFTCTRL, ec.KEY_RIGHTCTRL}
        alt_req   = required & {ec.KEY_LEFTALT,  ec.KEY_RIGHTALT}
        shift_req = required & {ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT}
        super_req = required & {ec.KEY_LEFTMETA, ec.KEY_RIGHTMETA}

        if ctrl_req  and not (held & {ec.KEY_LEFTCTRL,  ec.KEY_RIGHTCTRL}):
            return False
        if alt_req   and not (held & {ec.KEY_LEFTALT,   ec.KEY_RIGHTALT}):
            return False
        if shift_req and not (held & {ec.KEY_LEFTSHIFT, ec.KEY_RIGHTSHIFT}):
            return False
        if super_req and not (held & {ec.KEY_LEFTMETA,  ec.KEY_RIGHTMETA}):
            return False
        return True

    @staticmethod
    def get_keyboards() -> list:
        if not HAS_EVDEV:
            return []
        devices = []
        try:
            all_devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
        except (PermissionError, OSError):
            return []

        for dev in all_devs:
            try:
                caps = dev.capabilities()
                if ec.EV_KEY in caps and ec.KEY_A in caps.get(ec.EV_KEY, []):
                    devices.append(dev)
                else:
                    dev.close()
            except Exception:
                pass
        return devices


class PynputHotkeyListener:
    def __init__(self) -> None:
        self._listener: pynput_keyboard.GlobalHotKeys | None = None
        self._lock = threading.Lock()

    def set_hotkeys(self, hotkey_map: dict[str, Callable[[], None]]) -> None:
        self.stop_listening()
        normalized_map = {
            normalize_for_pynput(hk): cb for hk, cb in hotkey_map.items()
        }
        with self._lock:
            try:
                self._listener = pynput_keyboard.GlobalHotKeys(normalized_map)
                self._listener.daemon = True
                self._listener.start()
            except Exception as e:
                print(f"  ⚠ pynput error: {e}", flush=True)

    def stop_listening(self) -> None:
        with self._lock:
            if self._listener:
                self._listener.stop()
                self._listener = None


class HotkeyListener:
    """Unified hotkey listener — attempts evdev first (Wayland), falls back to pynput (X11)."""

    def __init__(self) -> None:
        self._evdev_listener: EvdevHotkeyListener | None = None
        self._pynput_listener: PynputHotkeyListener | None = None
        self._backend = "none"

    def set_hotkeys(
        self,
        speak_selection: str,
        speak_clipboard: str,
        stop: str,
        on_speak_selection: Callable[[], None],
        on_speak_clipboard: Callable[[], None],
        on_stop: Callable[[], None],
    ) -> None:
        hotkey_map = {
            speak_selection: on_speak_selection,
            speak_clipboard: on_speak_clipboard,
            stop: on_stop,
        }

        # Check evdev availability
        keyboards = EvdevHotkeyListener.get_keyboards() if HAS_EVDEV else []
        if keyboards:
            self._backend = "evdev"
            print(f"  ✓ Использование evdev (Wayland) — устройств: {len(keyboards)}", flush=True)
            if not self._evdev_listener:
                self._evdev_listener = EvdevHotkeyListener()
            self._evdev_listener.set_hotkeys(hotkey_map)
            if not self._evdev_listener.is_alive():
                self._evdev_listener.start()
        else:
            self._backend = "pynput"
            print("  ⚠ evdev не доступен (требуются права доступа к /dev/input/). Использование pynput (X11 fallback)...", flush=True)
            print("    Для поддежки хоткеев на Wayland выполните: sudo chmod 666 /dev/input/event*", flush=True)
            if not self._pynput_listener:
                self._pynput_listener = PynputHotkeyListener()
            self._pynput_listener.set_hotkeys(hotkey_map)

    def stop_listening(self) -> None:
        if self._evdev_listener:
            self._evdev_listener.stop_listening()
        if self._pynput_listener:
            self._pynput_listener.stop_listening()

    @staticmethod
    def format_hotkey(hotkey_str: str) -> str:
        parts = hotkey_str.split("+")
        result = []
        mapping = {
            "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift",
            "super": "Super", "meta": "Super", "cmd": "Cmd",
        }
        for part in parts:
            part = part.strip().strip("<>")
            result.append(mapping.get(part.lower(), part.upper()))
        return "+".join(result)

    @staticmethod
    def parse_hotkey(human_readable: str) -> str:
        parts = human_readable.split("+")
        result = []
        modifiers = {"ctrl", "alt", "shift", "super", "cmd"}
        for part in parts:
            part = part.strip()
            if part.lower() in modifiers:
                result.append(f"<{part.lower()}>")
            elif part.lower() in _PYNPUT_SPECIAL_KEYS:
                result.append(f"<{part.lower()}>")
            else:
                result.append(part.lower())
        return "+".join(result)
