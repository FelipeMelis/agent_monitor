"""System-wide keyboard shortcuts via the Carbon hot key API.

Uses `RegisterEventHotKey` (Carbon/HIToolbox). Unlike an `NSEvent`
global monitor, this requires no special macOS permission grant, so
shortcuts work even when running from source rather than a signed,
packaged app.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable

CMD_KEY = 1 << 8
SHIFT_KEY = 1 << 9
OPTION_KEY = 1 << 11
CONTROL_KEY = 1 << 12

# macOS virtual key codes (layout-independent, ANSI positions).
KEYCODE_A = 0x00
KEYCODE_J = 0x26
KEYCODE_P = 0x23

_CARBON_PATH = "/System/Library/Frameworks/Carbon.framework/Carbon"
_NO_ERR = 0


def _four_char_code(value: str) -> int:
    code = 0
    for char in value:
        code = (code << 8) | ord(char)
    return code


_EVENT_CLASS_KEYBOARD = _four_char_code("keyb")
_EVENT_HOTKEY_PRESSED = 5
_EVENT_PARAM_DIRECT_OBJECT = _four_char_code("----")
_TYPE_EVENT_HOTKEY_ID = _four_char_code("hkid")
_SIGNATURE = _four_char_code("amtr")


class _EventHotKeyID(ctypes.Structure):
    _fields_ = (
        ("signature", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
    )


class _EventTypeSpec(ctypes.Structure):
    _fields_ = (
        ("eventClass", ctypes.c_uint32),
        ("eventKind", ctypes.c_uint32),
    )


_EVENT_HANDLER_PROC = ctypes.CFUNCTYPE(
    ctypes.c_int32,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


def _load_carbon() -> ctypes.CDLL | None:
    try:
        carbon = ctypes.cdll.LoadLibrary(_CARBON_PATH)
    except OSError:
        return None
    carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    carbon.GetApplicationEventTarget.argtypes = []
    carbon.RegisterEventHotKey.restype = ctypes.c_int32
    carbon.RegisterEventHotKey.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        _EventHotKeyID,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.UnregisterEventHotKey.restype = ctypes.c_int32
    carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
    carbon.InstallEventHandler.restype = ctypes.c_int32
    carbon.InstallEventHandler.argtypes = [
        ctypes.c_void_p,
        _EVENT_HANDLER_PROC,
        ctypes.c_uint32,
        ctypes.POINTER(_EventTypeSpec),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.GetEventParameter.restype = ctypes.c_int32
    carbon.GetEventParameter.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    return carbon


class GlobalHotKeyManager:
    """Register global hot keys mapped to callbacks; clean up on quit."""

    def __init__(self) -> None:
        self._carbon = _load_carbon()
        self._handlers: dict[int, Callable[[], None]] = {}
        self._hotkey_refs: list[ctypes.c_void_p] = []
        self._handler_installed = False
        self._event_handler_ref = ctypes.c_void_p()
        # Keep a reference so the ctypes callback trampoline is never
        # garbage-collected while Carbon still holds a pointer to it.
        self._callback = _EVENT_HANDLER_PROC(self._handle_event)
        self._next_id = 1

    @property
    def available(self) -> bool:
        return self._carbon is not None

    def register(
        self,
        key_code: int,
        modifiers: int,
        callback: Callable[[], None],
    ) -> bool:
        """Register one global shortcut; return whether it succeeded."""

        if self._carbon is None:
            return False
        if not self._install_handler():
            return False
        hotkey_id = self._next_id
        self._next_id += 1
        hotkey_ref = ctypes.c_void_p()
        status = self._carbon.RegisterEventHotKey(
            ctypes.c_uint32(key_code),
            ctypes.c_uint32(modifiers),
            _EventHotKeyID(_SIGNATURE, hotkey_id),
            self._carbon.GetApplicationEventTarget(),
            ctypes.c_uint32(0),
            ctypes.byref(hotkey_ref),
        )
        if status != _NO_ERR:
            return False
        self._handlers[hotkey_id] = callback
        self._hotkey_refs.append(hotkey_ref)
        return True

    def unregister_all(self) -> None:
        if self._carbon is None:
            return
        for ref in self._hotkey_refs:
            self._carbon.UnregisterEventHotKey(ref)
        self._hotkey_refs.clear()
        self._handlers.clear()

    def _install_handler(self) -> bool:
        if self._handler_installed:
            return True
        if self._carbon is None:
            return False
        spec = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
        status = self._carbon.InstallEventHandler(
            self._carbon.GetApplicationEventTarget(),
            self._callback,
            ctypes.c_uint32(1),
            ctypes.byref(spec),
            None,
            ctypes.byref(self._event_handler_ref),
        )
        self._handler_installed = status == _NO_ERR
        return self._handler_installed

    def _handle_event(
        self,
        _call_ref: int,
        event_ref: int,
        _user_data: int,
    ) -> int:
        hotkey_id_struct = _EventHotKeyID()
        status = self._carbon.GetEventParameter(
            ctypes.c_void_p(event_ref),
            ctypes.c_uint32(_EVENT_PARAM_DIRECT_OBJECT),
            ctypes.c_uint32(_TYPE_EVENT_HOTKEY_ID),
            None,
            ctypes.c_uint32(ctypes.sizeof(_EventHotKeyID)),
            None,
            ctypes.byref(hotkey_id_struct),
        )
        if status == _NO_ERR:
            callback = self._handlers.get(hotkey_id_struct.id)
            if callback is not None:
                callback()
        return _NO_ERR
