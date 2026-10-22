#!/usr/bin/env python3
"""SysCast v1.0.0 — PC hardware monitor streamed to a mobile browser."""

from __future__ import annotations

import importlib
import subprocess
import sys


def ensure_dependencies() -> None:
    """Verify required third-party packages are installed; install any missing ones."""
    packages = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "websockets": "websockets",
        "psutil": "psutil",
        "qrcode": "qrcode",
        "PIL": "Pillow",
        "pystray": "pystray",
    }
    missing: list[str] = []
    for module_name, pip_name in packages.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return
    print(f"Installing missing packages: {', '.join(missing)}")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", *missing],
        stdout=sys.stdout,
    )


ensure_dependencies()

import asyncio
import ctypes
import inspect
import io
import ipaddress
import json
import math
import mmap
import os
import re
import signal
import socket
import struct
import threading
import time
import webbrowser
from contextlib import asynccontextmanager, suppress
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import pystray
import qrcode
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image, ImageDraw

try:
    import msvcrt  # Windows-only: non-blocking key polling
except ImportError:  # pragma: no cover
    msvcrt = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BROADCAST_INTERVAL = 0.05  # 20 updates/s
PORT_START = 5000
PORT_END = 5099
CONFIG_PATH = Path(os.environ.get("APPDATA", "C:\\Users")) / "SysCast" / "config.json"
FLT_MAX = 3.402823466e38

APP_NAME = "SysCast"
__version__ = "1.0.0-Beta"
APP_VERSION = __version__

BACK_SIGNAL = "__BACK__"

SIG_MAHM = int.from_bytes(b"MAHM", "big")
SIG_MACM = int.from_bytes(b"MACM", "big")
SIG_RTSS = int.from_bytes(b"RTSS", "big")
SIG_DEAD = 0xDEAD

FILE_MAP_READ = 0x0004
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x00000080
MAX_PATH = 260
SRC_GPU_TEMP = 0x00000000
SRC_GPU_USAGE = 0x00000030
SRC_MEMORY_USAGE = 0x00000031
SRC_FRAMERATE = 0x00000050
SRC_FRAMETIME = 0x00000051
SRC_GPU_REL_POWER = 0x00000060
SRC_GPU_ABS_POWER = 0x00000061
SRC_CPU_TEMP = 0x00000080
SRC_CPU_USAGE = 0x00000090
SRC_CPU_POWER = 0x00000100

SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9

ENABLE_PROCESSED_INPUT = 0x0001

ACCENTS = {
    "purple": "#b45cff",
    "green": "#1fff7a",
    "yellow": "#ffd60a",
    "blue": "#2e8bff",
    "red": "#ff3b4e",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "layout": "portrait",
    "theme": "dark",
    "accent": "purple",
    "clock_format": "12h",
    "show_fps": True,
    "show_cpu": True,
    "show_gpu": True,
    "show_ram": True,
}

# ---------------------------------------------------------------------------
# Win32 API Bindings
# ---------------------------------------------------------------------------

if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenFileMappingW.restype = wintypes.HANDLE
    kernel32.MapViewOfFile.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    kernel32.MapViewOfFile.restype = wintypes.LPVOID
    kernel32.UnmapViewOfFile.argtypes = [wintypes.LPCVOID]
    kernel32.UnmapViewOfFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.VirtualQuery.argtypes = [wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
    kernel32.VirtualQuery.restype = ctypes.c_size_t
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
    kernel32.SetConsoleTitleW.restype = wintypes.BOOL
    kernel32.GetConsoleWindow.argtypes = []
    kernel32.GetConsoleWindow.restype = wintypes.HWND

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetShellWindow.argtypes = []
    user32.GetShellWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    _GetWindowLongPtr = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetWindowLongPtr.restype = ctypes.c_ssize_t

    _SetWindowLongPtr = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _SetWindowLongPtr.restype = ctypes.c_ssize_t


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    """Architecture-aware MEMORY_BASIC_INFORMATION structure for VirtualQuery."""

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _fields_ = [
            ("BaseAddress", ctypes.c_uint64),
            ("AllocationBase", ctypes.c_uint64),
            ("AllocationProtect", wintypes.DWORD),
            ("PartitionId", wintypes.WORD),
            ("RegionSize", ctypes.c_uint64),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
        ]
    else:
        _fields_ = [
            ("BaseAddress", ctypes.c_uint32),
            ("AllocationBase", ctypes.c_uint32),
            ("AllocationProtect", wintypes.DWORD),
            ("RegionSize", ctypes.c_uint32),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
        ]


# ---------------------------------------------------------------------------
# Console Window & System Tray Management
# ---------------------------------------------------------------------------

_CONSOLE_HWNDS: set[int] = set()
_CONSOLE_HIDDEN = False
_TRAY_ICON: pystray.Icon | None = None
_TRAY_LOCK = threading.Lock()
_STOP_SERVER_EVENT = threading.Event()
_FORCE_EXIT_EVENT = threading.Event()
_CURRENT_URL = ""


GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GA_ROOTOWNER = 3
GW_OWNER = 4


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


_CLSID_TaskbarList = _GUID(
    0x56FDF344, 0xFD6D, 0x11D0, (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
)
_IID_ITaskbarList = _GUID(
    0x56FDF342, 0xFD6D, 0x11D0, (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
)


def _set_taskbar_tab(hwnd: int, visible: bool) -> None:
    """Add or remove a window tab directly from the Windows Taskbar via ITaskbarList COM."""
    if os.name != "nt":
        return
    with suppress(Exception):
        ole32.CoInitialize(None)
        tbl = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_TaskbarList),
            None,
            1,  # CLSCTX_INPROC_SERVER
            ctypes.byref(_IID_ITaskbarList),
            ctypes.byref(tbl),
        )
        if hr == 0 and tbl.value:
            vtable = ctypes.cast(
                ctypes.cast(tbl, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )
            hr_init = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[3])
            hr_init(tbl)
            idx = 4 if visible else 5  # 4 = AddTab, 5 = DeleteTab
            fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)(
                vtable[idx]
            )
            fn(tbl, wintypes.HWND(hwnd))
            release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[2])
            release(tbl)


def _capture_console_hwnd(include_foreground: bool = False) -> None:
    """Record the top-level console/terminal window handle(s) for hiding/restoring."""
    if os.name != "nt":
        return
    with suppress(Exception):
        shell_w = int(user32.GetShellWindow() or 0)
        if include_foreground:
            fw = int(user32.GetForegroundWindow() or 0)
            if fw and fw != shell_w:
                root_fw = int(user32.GetAncestor(wintypes.HWND(fw), GA_ROOTOWNER) or fw)
                if root_fw and root_fw != shell_w:
                    _CONSOLE_HWNDS.add(root_fw)
        cw = int(kernel32.GetConsoleWindow() or 0)
        if cw and cw != shell_w and not _CONSOLE_HWNDS:
            root_cw = int(user32.GetAncestor(wintypes.HWND(cw), GA_ROOTOWNER) or cw)
            if root_cw and root_cw != shell_w:
                _CONSOLE_HWNDS.add(root_cw)


def hide_console_to_tray(from_keyboard: bool = False) -> None:
    """Hide the console window, remove its taskbar icon, and notify via system tray."""
    global _CONSOLE_HIDDEN
    if os.name != "nt":
        return
    _capture_console_hwnd(include_foreground=from_keyboard)
    for hwnd in list(_CONSOLE_HWNDS):
        with suppress(Exception):
            w = wintypes.HWND(hwnd)
            user32.ShowWindow(w, SW_HIDE)
            _set_taskbar_tab(hwnd, visible=False)
    _CONSOLE_HIDDEN = True
    if _TRAY_ICON is not None:
        with suppress(Exception):
            _TRAY_ICON.notify(
                "Server is running in the background. Click the tray icon to restore.",
                f"{APP_NAME} v{APP_VERSION}",
            )


def show_console_from_tray() -> None:
    """Restore the console window and its taskbar icon from the system tray."""
    global _CONSOLE_HIDDEN
    if os.name != "nt":
        return
    _capture_console_hwnd(include_foreground=False)
    for hwnd in list(_CONSOLE_HWNDS):
        with suppress(Exception):
            w = wintypes.HWND(hwnd)
            user32.ShowWindow(w, SW_SHOW)
            user32.ShowWindow(w, SW_RESTORE)
            _set_taskbar_tab(hwnd, visible=True)
            user32.SetForegroundWindow(w)
    _CONSOLE_HIDDEN = False


def toggle_console_visibility() -> None:
    """Toggle the console window between visible and hidden states."""
    if _CONSOLE_HIDDEN:
        show_console_from_tray()
    else:
        hide_console_to_tray(from_keyboard=False)


def _create_tray_image(accent_hex: str = "#b45cff") -> Image.Image:
    """Generate the system tray icon image dynamically with the active accent color."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    ac = accent_hex.lstrip("#")
    r, g, b = int(ac[0:2], 16), int(ac[2:4], 16), int(ac[4:6], 16)

    draw.rounded_rectangle(
        (4, 4, size - 5, size - 5),
        radius=14,
        fill=(12, 8, 22, 255),
        outline=(r, g, b, 255),
        width=3,
    )
    bolt = [
        (36, 12),
        (20, 34),
        (31, 34),
        (26, 52),
        (44, 28),
        (33, 28),
    ]
    draw.polygon(bolt, fill=(r, g, b, 255))
    return img


def start_tray_icon(url: str, accent_name: str) -> None:
    """Start the background system tray icon thread."""
    global _TRAY_ICON, _CURRENT_URL
    _CURRENT_URL = url
    accent_hex = ACCENTS.get(accent_name, "#b45cff")

    def on_toggle(_icon: Any, _item: Any) -> None:
        toggle_console_visibility()

    def on_open_browser(_icon: Any, _item: Any) -> None:
        if _CURRENT_URL:
            webbrowser.open(_CURRENT_URL)

    def on_copy_url(_icon: Any, _item: Any) -> None:
        if _CURRENT_URL:
            _copy_to_clipboard(_CURRENT_URL)

    def on_stop_server(_icon: Any, _item: Any) -> None:
        show_console_from_tray()
        _STOP_SERVER_EVENT.set()

    def on_exit_app(_icon: Any, _item: Any) -> None:
        show_console_from_tray()
        _FORCE_EXIT_EVENT.set()
        _STOP_SERVER_EVENT.set()

    menu = pystray.Menu(
        pystray.MenuItem("Show / Hide Console", on_toggle, default=True),
        pystray.MenuItem("Open Dashboard in Browser", on_open_browser),
        pystray.MenuItem("Copy Stream Link", on_copy_url),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop Server & Return to Menu", on_stop_server),
        pystray.MenuItem("Exit SysCast", on_exit_app),
    )

    with suppress(Exception), _TRAY_LOCK:
        stop_tray_icon()
        icon = pystray.Icon(
            "SysCast",
            _create_tray_image(accent_hex),
            f"{APP_NAME} v{APP_VERSION} — {url}",
            menu,
        )
        _TRAY_ICON = icon
        threading.Thread(target=icon.run, name="syscast-tray", daemon=True).start()


def stop_tray_icon() -> None:
    """Stop and clean up the active system tray icon."""
    global _TRAY_ICON
    icon = _TRAY_ICON
    _TRAY_ICON = None
    if icon is not None:
        with suppress(Exception):
            icon.stop()


# ---------------------------------------------------------------------------
# Shared Memory (Core Extraction Logic)
# ---------------------------------------------------------------------------


class NamedSection:
    """Read a Windows named file mapping via ctypes and snapshot into mmap."""

    def __init__(self, names: tuple[str, ...], max_copy: int = 1 << 20) -> None:
        self.names = names
        self.max_copy = max_copy
        self.handle: int | None = None
        self.view: int | None = None
        self.size = 0

    def open(self) -> bool:
        """Open the first available named file mapping from `self.names`."""
        self.close()
        if os.name != "nt":
            return False
        for name in self.names:
            handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, name)
            if handle:
                view = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
                if view:
                    self.handle = int(handle)
                    self.view = int(view)
                    self.size = self._region_size(int(view))
                    return True
                kernel32.CloseHandle(handle)
        return False

    def alive(self) -> bool:
        """Return True if the file mapping view is currently open."""
        return bool(self.view)

    def snapshot(self, nbytes: int | None = None) -> mmap.mmap | None:
        """Copy up to `nbytes` from the shared memory view into an in-memory mmap buffer."""
        if not self.view and not self.open():
            return None
        length = min(nbytes or self.size or self.max_copy, self.max_copy)
        if length <= 0:
            length = min(4096, self.max_copy)
        try:
            assert self.view is not None
            blob = ctypes.string_at(self.view, length)
        except OSError:
            self.close()
            return None
        buf = mmap.mmap(-1, length)
        buf.write(blob)
        buf.seek(0)
        return buf

    def close(self) -> None:
        """Unmap any active view and close the file mapping handle."""
        if os.name != "nt":
            return
        if self.view:
            kernel32.UnmapViewOfFile(self.view)
            self.view = None
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None
        self.size = 0

    @staticmethod
    def _region_size(address: int) -> int:
        info = MEMORY_BASIC_INFORMATION()
        got = kernel32.VirtualQuery(address, ctypes.byref(info), ctypes.sizeof(info))
        if got:
            return int(info.RegionSize)
        return 1 << 20


def _with_mutex(names: tuple[str, ...], fn: Any) -> Any:
    """Execute `fn` while holding the first available Windows named mutex."""
    if os.name != "nt":
        return fn()
    mutex = None
    for name in names:
        mutex = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
        if mutex:
            break
    owned = False
    if mutex:
        wait = kernel32.WaitForSingleObject(mutex, 80)
        owned = wait in (WAIT_OBJECT_0, WAIT_ABANDONED)
    try:
        return fn()
    finally:
        if mutex:
            if owned:
                kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)


def _buf_len(buf: mmap.mmap | bytes) -> int:
    return len(buf)


def _fits(buf: mmap.mmap | bytes, offset: int, size: int) -> bool:
    return offset >= 0 and size >= 0 and offset + size <= _buf_len(buf)


def _u32(buf: mmap.mmap | bytes, offset: int) -> int | None:
    if not _fits(buf, offset, 4):
        return None
    return struct.unpack_from("<I", buf, offset)[0]


def _f32(buf: mmap.mmap | bytes, offset: int) -> float | None:
    if not _fits(buf, offset, 4):
        return None
    return struct.unpack_from("<f", buf, offset)[0]


def _cstr(buf: mmap.mmap | bytes, offset: int, length: int = MAX_PATH) -> str:
    if offset < 0 or length <= 0 or offset >= _buf_len(buf):
        return ""
    end = min(offset + length, _buf_len(buf))
    raw = bytes(buf[offset:end])
    encoding = "mbcs" if os.name == "nt" else "latin-1"
    return raw.split(b"\x00", 1)[0].decode(encoding, errors="ignore").strip()


def _valid_float(value: float) -> bool:
    return math.isfinite(value) and abs(value) < FLT_MAX / 2


def _to_gb(value: float, units: str) -> float:
    u = units.lower()
    if "gb" in u:
        return value
    if "mb" in u:
        return value / 1024.0
    if "kb" in u:
        return value / (1024.0 * 1024.0)
    if "%" in u:
        return value
    if value > 256:
        return value / 1024.0
    return value


class HardwareReader:
    """Silent Afterburner / RTSS shared-memory reader (no WMI, no OSD)."""

    def __init__(self) -> None:
        self.macm = NamedSection(
            ("MACMSharedMemory", "Global\\MACMSharedMemory"), 64 * 1024
        )
        self.mahm = NamedSection(
            ("MAHMSharedMemory", "Global\\MAHMSharedMemory"), 2 << 20
        )
        self.rtss = NamedSection(
            ("RTSSSharedMemoryV2", "Global\\RTSSSharedMemoryV2"), 4 << 20
        )

    def close(self) -> None:
        """Close all open shared-memory sections."""
        self.macm.close()
        self.mahm.close()
        self.rtss.close()

    def afterburner_running(self) -> bool:
        """Return True if MSI Afterburner's shared memory is active and alive."""
        def _check() -> bool:
            snap = self.macm.snapshot(32) or self.mahm.snapshot(32)
            if snap is None:
                return False
            try:
                sig = _u32(snap, 0)
                if sig is None:
                    return False
                return sig in (SIG_MACM, SIG_MAHM) and sig != SIG_DEAD
            finally:
                snap.close()

        return bool(
            _with_mutex(
                ("Global\\Access_MACMSharedMemory", "Global\\Access_MAHMSharedMemory"),
                _check,
            )
        )

    def read(self) -> dict[str, Any]:
        """Read current telemetry metrics from Afterburner, RTSS, and system memory."""
        payload: dict[str, Any] = {
            "afterburner": False,
            "fps": None,
            "frametime": None,
            "cpu": {"usage": None, "temp": None, "power": None},
            "gpu": {"usage": None, "temp": None, "vram_gb": None, "power": None},
            "ram": self._ram(),
        }
        if not self.afterburner_running():
            self.close()
            return payload

        payload["afterburner"] = True
        mahm_metrics = (
            _with_mutex(("Global\\Access_MAHMSharedMemory",), self._read_mahm) or {}
        )
        rtss_metrics = self._read_rtss() or {}

        if rtss_metrics.get("fps") is not None and rtss_metrics["fps"] > 0:
            payload["fps"] = rtss_metrics["fps"]
        elif mahm_metrics.get("fps") is not None and mahm_metrics["fps"] > 0:
            payload["fps"] = mahm_metrics["fps"]

        if rtss_metrics.get("frametime") is not None and rtss_metrics["frametime"] > 0:
            payload["frametime"] = rtss_metrics["frametime"]
        elif mahm_metrics.get("frametime") is not None and mahm_metrics["frametime"] > 0:
            payload["frametime"] = mahm_metrics["frametime"]
        elif payload["fps"] is not None and payload["fps"] > 0.1:
            payload["frametime"] = 1000.0 / payload["fps"]

        for key in ("usage", "temp", "power"):
            if mahm_metrics.get("cpu", {}).get(key) is not None:
                payload["cpu"][key] = mahm_metrics["cpu"][key]
            if mahm_metrics.get("gpu", {}).get(key) is not None:
                payload["gpu"][key] = mahm_metrics["gpu"][key]
        if mahm_metrics.get("gpu", {}).get("vram_gb") is not None:
            payload["gpu"]["vram_gb"] = mahm_metrics["gpu"]["vram_gb"]
        return payload

    def _read_mahm(self) -> dict[str, Any] | None:
        header = self.mahm.snapshot(64)
        if header is None:
            return None
        try:
            if _u32(header, 0) != SIG_MAHM:
                return None
            header_size = _u32(header, 8)
            num_entries = _u32(header, 12)
            entry_size = _u32(header, 16)
            if header_size is None or num_entries is None or entry_size is None:
                return None
        finally:
            header.close()

        if header_size < 24 or entry_size < 1324 or num_entries > 4096:
            return None

        need = header_size + num_entries * entry_size
        snap = self.mahm.snapshot(need)
        if snap is None:
            return None

        cpu_usage: list[float] = []
        cpu_temp: list[float] = []
        gpu_usage: list[float] = []
        gpu_temp: list[float] = []
        gpu_power: list[float] = []
        gpu_vram: list[float] = []
        cpu_power: list[float] = []
        fps: float | None = None
        frametime: float | None = None

        data_off = MAX_PATH * 5
        src_id_off = data_off + 20  # data, minLimit, maxLimit, dwFlags, dwGpu, dwSrcId
        try:
            for i in range(num_entries):
                base = header_size + i * entry_size
                if not _fits(snap, base, entry_size):
                    break
                name = _cstr(snap, base)
                units = _cstr(snap, base + MAX_PATH)
                data = _f32(snap, base + data_off)
                src_id = _u32(snap, base + src_id_off)
                if data is None or src_id is None or not _valid_float(data):
                    continue
                low = name.lower()

                if src_id == SRC_FRAMERATE or low == "framerate":
                    if data > 0.0:
                        fps = data
                elif src_id == SRC_FRAMETIME or low == "frametime":
                    if data > 0.0:
                        frametime = data
                elif src_id == SRC_CPU_USAGE or (
                    low.startswith("cpu") and "usage" in low
                ):
                    if (
                        "cpu" in low
                        and any(ch.isdigit() for ch in low)
                        and low not in {"cpu usage", "cpu utilization"}
                    ):
                        continue
                    cpu_usage.append(data)
                elif src_id == SRC_CPU_TEMP or (
                    low.startswith("cpu") and "temp" in low
                ):
                    if (
                        any(ch.isdigit() for ch in low)
                        and "cpu temperature" not in low
                    ):
                        continue
                    cpu_temp.append(data)
                elif src_id == SRC_CPU_POWER or (
                    low.startswith("cpu") and "power" in low
                ):
                    cpu_power.append(data)
                elif src_id == SRC_GPU_USAGE or (
                    low.startswith("gpu") and "usage" in low
                ):
                    gpu_usage.append(data)
                elif src_id == SRC_GPU_TEMP or (
                    low.startswith("gpu") and "temp" in low
                ):
                    gpu_temp.append(data)
                elif src_id in (SRC_GPU_ABS_POWER, SRC_GPU_REL_POWER) or (
                    "power" in low and "gpu" in low
                ):
                    if src_id == SRC_GPU_REL_POWER and "%" in units:
                        continue
                    gpu_power.append(data)
                elif src_id == SRC_MEMORY_USAGE or (
                    ("memory" in low or "vram" in low)
                    and "usage" in low
                    and "cpu" not in low
                ):
                    if "%" in units:
                        continue
                    gpu_vram.append(_to_gb(data, units))
        finally:
            snap.close()

        def first(values: list[float]) -> float | None:
            return values[0] if values else None

        return {
            "fps": fps,
            "frametime": frametime,
            "cpu": {
                "usage": first(cpu_usage),
                "temp": first(cpu_temp),
                "power": first(cpu_power),
            },
            "gpu": {
                "usage": first(gpu_usage),
                "temp": first(gpu_temp),
                "vram_gb": first(gpu_vram),
                "power": first(gpu_power),
            },
        }

    def _read_rtss(self) -> dict[str, Any] | None:
        header = self.rtss.snapshot(64)
        if header is None:
            return None
        try:
            if _u32(header, 0) != SIG_RTSS:
                return None
            version = _u32(header, 4)
            if version is None or version < 0x00020000:
                return None
            app_entry_size = _u32(header, 8)
            app_arr_offset = _u32(header, 12)
            app_arr_size = _u32(header, 16)
            if (
                app_entry_size is None
                or app_arr_offset is None
                or app_arr_size is None
            ):
                return None
        finally:
            header.close()

        if app_entry_size < 284 or app_arr_size > 512:
            return None

        need = app_arr_offset + app_arr_size * app_entry_size
        snap = self.rtss.snapshot(min(need, 4 << 20))
        if snap is None:
            return None

        best_fps = 0.0
        best_ft = None
        found = False
        try:
            for i in range(app_arr_size):
                base = app_arr_offset + i * app_entry_size
                time0_off = base + 4 + MAX_PATH + 4
                if not _fits(snap, base, 284):
                    break
                pid = _u32(snap, base)
                if not pid:
                    continue
                time0 = _u32(snap, time0_off)
                time1 = _u32(snap, time0_off + 4)
                frames = _u32(snap, time0_off + 8)
                frame_us = _u32(snap, time0_off + 12)
                if None in (time0, time1, frames, frame_us):
                    continue
                fps = 0.0
                if time1 > time0 and frames:
                    fps = 1000.0 * frames / (time1 - time0)
                elif frame_us:
                    fps = 1_000_000.0 / frame_us
                if fps <= 0.1:
                    continue
                found = True
                if fps >= best_fps:
                    best_fps = fps
                    if frame_us and frame_us > 0:
                        best_ft = frame_us / 1000.0
                    else:
                        best_ft = 1000.0 / fps
        finally:
            snap.close()

        if not found:
            return None
        return {"fps": best_fps, "frametime": best_ft}

    @staticmethod
    def _ram() -> dict[str, float]:
        vm = psutil.virtual_memory()
        return {
            "used_gb": vm.used / (1024 ** 3),
            "total_gb": vm.total / (1024 ** 3),
            "percent": float(vm.percent),
        }


# ---------------------------------------------------------------------------
# Configuration & Network Discovery
# ---------------------------------------------------------------------------

_CFG_CACHE: dict[str, Any] = {"mtime": None, "cfg": None}
_ADAPTER_CACHE: dict[str, tuple[str, bool | None]] | None = None


def load_config() -> dict[str, Any]:
    """Load configuration from disk, utilizing an mtime-based in-memory cache."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        mtime = -1
    cached = _CFG_CACHE["cfg"]
    if cached is not None and _CFG_CACHE["mtime"] == mtime:
        return dict(cached)
    cfg = _load_config_uncached()
    _CFG_CACHE["mtime"] = mtime
    _CFG_CACHE["cfg"] = cfg
    return dict(cfg)


def _load_config_uncached() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg.update({k: loaded[k] for k in DEFAULT_CONFIG if k in loaded})
        except (OSError, json.JSONDecodeError):
            pass
    cfg["layout"] = (
        cfg["layout"] if cfg["layout"] in {"portrait", "landscape"} else "portrait"
    )
    cfg["theme"] = cfg["theme"] if cfg["theme"] in {"dark", "white"} else "dark"
    cfg["accent"] = cfg["accent"] if cfg["accent"] in ACCENTS else "purple"
    cfg["clock_format"] = (
        cfg.get("clock_format")
        if cfg.get("clock_format") in {"12h", "24h"}
        else "12h"
    )
    for key in ("show_fps", "show_cpu", "show_gpu", "show_ram"):
        cfg[key] = bool(cfg[key])
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist the user configuration dictionary to `CONFIG_PATH`."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass
    _CFG_CACHE["cfg"] = None


_VIRTUAL_ADAPTER_RE = re.compile(
    "|".join(
        [
            r"vpn", r"proton", r"wireguard", r"\bwg\d*\b", r"nordlynx", r"nordvpn",
            r"mullvad", r"expressvpn", r"surfshark", r"windscribe", r"openvpn",
            r"\btap\b", r"tap-windows", r"\btap\d+\b", r"\btun\d*\b", r"wintun",
            r"tailscale", r"zerotier", r"hamachi", r"radmin", r"anyconnect",
            r"globalprotect", r"pangp", r"forticlient", r"fortinet", r"sonicwall",
            r"pulse\s?secure", r"softether", r"\butun\d*\b", r"\bipsec\b",
            r"vmware", r"vmnet", r"virtualbox", r"vbox", r"host-only", r"hyper-v",
            r"vethernet", r"\bwsl\b", r"docker", r"podman", r"qemu", r"\bkvm\b",
            r"parallels", r"\bxen", r"virbr", r"\bveth", r"\bbr-", r"\bvnic",
            r"virtual", r"npcap", r"loopback", r"pseudo", r"bluetooth", r"teredo",
            r"isatap", r"6to4", r"wan\smini", r"\bppp", r"wi-?fi\sdirect",
            r"local\sarea\sconnection\*", r"kernel\sdebug",
        ]
    ),
    re.IGNORECASE,
)

_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

_DEPRIORITIZED_NETWORKS = (
    ipaddress.ip_network("192.168.56.0/24"),
    ipaddress.ip_network("192.168.99.0/24"),
    ipaddress.ip_network("192.168.122.0/24"),
    ipaddress.ip_network("192.168.137.0/24"),
    ipaddress.ip_network("172.17.0.0/16"),
)

_WIFI_RE = re.compile(r"wi-?fi|wlan|wireless|802\.11", re.IGNORECASE)
_WIRED_RE = re.compile(
    r"ethernet|\beth\d|\ben\d|realtek|intel|killer|broadcom|qualcomm|mediatek|atheros|aquantia",
    re.IGNORECASE,
)


def _windows_adapter_info() -> dict[str, tuple[str, bool | None]]:
    """Query PowerShell for physical vs. virtual adapter metadata (cached per session)."""
    global _ADAPTER_CACHE
    if _ADAPTER_CACHE is not None:
        return _ADAPTER_CACHE
    if os.name != "nt":
        _ADAPTER_CACHE = {}
        return _ADAPTER_CACHE

    cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-NetAdapter | Select-Object Name,InterfaceDescription,HardwareInterface | ConvertTo-Json -Compress",
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if isinstance(data, dict):
        data = [data]
    info: dict[str, tuple[str, bool | None]] = {}
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict) or not row.get("Name"):
                continue
            hw = row.get("HardwareInterface")
            info[str(row["Name"])] = (
                str(row.get("InterfaceDescription") or ""),
                hw if isinstance(hw, bool) else None,
            )
    _ADAPTER_CACHE = info
    return info


def _in_lan(ip: ipaddress.IPv4Address) -> bool:
    return any(ip in net for net in _LAN_NETWORKS)


def _default_route_ip() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            return ip if ip and not ip.startswith("127.") else None
        except OSError:
            return None


def true_local_ip() -> str:
    """Determine the primary physical LAN IPv4 address of the host machine."""
    route_ip = _default_route_ip()
    info = _windows_adapter_info()
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:
        addrs, stats = {}, {}

    best: tuple[int, str] | None = None
    for name, entries in addrs.items():
        st = stats.get(name)
        if st is not None and not st.isup:
            continue
        desc, hardware = info.get(name, ("", None))
        label = f"{name} {desc}"
        if _VIRTUAL_ADAPTER_RE.search(label):
            continue
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.IPv4Address(entry.address)
            except ValueError:
                continue
            if not _in_lan(ip):
                continue
            score = 0
            if hardware is True:
                score += 40
            elif hardware is False:
                score -= 50
            if _WIFI_RE.search(label):
                score += 20
            elif _WIRED_RE.search(label):
                score += 15
            if any(ip in net for net in _DEPRIORITIZED_NETWORKS):
                score -= 60
            if route_ip == str(ip):
                score += 25
            if best is None or score > best[0]:
                best = (score, str(ip))

    if best:
        return best[1]
    if route_ip:
        with suppress(ValueError):
            if _in_lan(ipaddress.IPv4Address(route_ip)):
                return route_ip
    return "127.0.0.1"


class _ThreadedServer(uvicorn.Server):
    """Uvicorn server subclass that skips signal handler installation for background threads."""

    def install_signal_handlers(self) -> None:
        pass


def find_free_port(start: int = PORT_START, end: int = PORT_END) -> int:
    """Find and return the first available TCP port in `[start, end]`."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free TCP port in {start}-{end}")


def round_or_none(value: Any, digits: int) -> float | None:
    """Round `value` to `digits` decimal places, or return None if invalid."""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Frontend (Glass dashboard UI + NoSleep.js wake lock)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover, user-scalable=no" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="theme-color" content="#020104" id="meta-theme" />
  <title>SysCast Telemetry</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800&family=JetBrains+Mono:wght@500;700&family=Orbitron:wght@600;700;800;900&display=swap" rel="stylesheet" />
  <style>
    :root {
      --accent: #b45cff;
      --accent-rgb: 180, 92, 255;
      --accent-ink: #c27dff;
      --bg: #020104;
      --card-top: rgba(16, 10, 26, 0.86);
      --card-bot: rgba(5, 3, 9, 0.92);
      --card-inner: rgba(255, 255, 255, 0.03);
      --hair: rgba(255, 255, 255, 0.07);
      --text: #f6f7fb;
      --muted: #8b93a7;
      --track: rgba(255, 255, 255, 0.07);
      --tick: rgba(2, 1, 4, 0.7);
      --wait-bg: rgba(2, 1, 4, 0.92);
      --shadow: 0 20px 44px -14px rgba(0, 0, 0, 0.95), 0 2px 6px rgba(0, 0, 0, 0.5);
      --sheen: rgba(255, 255, 255, 0.08);
      --glow: 1.25;
      --aurora: 1;
      --ok: #22c55e;
      --ok-rgb: 34, 197, 94;
      --warn: #f59e0b;
      --warn-rgb: 245, 158, 11;
      --hot: #ff2d46;
      --hot-rgb: 255, 45, 70;
      --crit: #ff0a2a;
      --crit-rgb: 255, 10, 42;
    }

    body.theme-white {
      --bg: #b9bfcc;
      --card-top: rgba(214, 219, 230, 0.92);
      --card-bot: rgba(196, 202, 215, 0.9);
      --card-inner: rgba(15, 23, 42, 0.06);
      --hair: rgba(15, 23, 42, 0.14);
      --text: #111827;
      --muted: #475569;
      --track: rgba(15, 23, 42, 0.14);
      --tick: rgba(196, 202, 215, 0.9);
      --wait-bg: rgba(185, 191, 204, 0.92);
      --shadow: 0 16px 34px -14px rgba(30, 41, 90, 0.38), 0 2px 6px rgba(30, 41, 90, 0.12);
      --sheen: rgba(255, 255, 255, 0.45);
      --glow: 0.35;
      --aurora: 0.55;
      --ok: #15803d;
      --ok-rgb: 21, 128, 61;
      --warn: #c2640a;
      --warn-rgb: 194, 100, 10;
      --hot: #dc1236;
      --hot-rgb: 220, 18, 54;
      --crit: #c40a2a;
      --crit-rgb: 196, 10, 42;
    }

    body.accent-purple { --accent: #b45cff; --accent-rgb: 180, 92, 255; --accent-ink: #c27dff; }
    body.accent-green  { --accent: #1fff7a; --accent-rgb: 31, 255, 122; --accent-ink: #4dff95; }
    body.accent-yellow { --accent: #ffd60a; --accent-rgb: 255, 214, 10; --accent-ink: #ffe04d; }
    body.accent-blue   { --accent: #2e8bff; --accent-rgb: 46, 139, 255; --accent-ink: #5aa4ff; }
    body.accent-red    { --accent: #ff3b4e; --accent-rgb: 255, 59, 78;  --accent-ink: #ff6b79; }

    body.theme-white.accent-purple { --accent: #8b2be2; --accent-rgb: 139, 43, 226; --accent-ink: #6d1bb8; }
    body.theme-white.accent-green  { --accent: #0a9f4f; --accent-rgb: 10, 159, 79;  --accent-ink: #067a3b; }
    body.theme-white.accent-yellow { --accent: #d19a00; --accent-rgb: 209, 154, 0;  --accent-ink: #8a6200; }
    body.theme-white.accent-blue   { --accent: #1469e6; --accent-rgb: 20, 105, 230; --accent-ink: #0b4fb8; }
    body.theme-white.accent-red    { --accent: #e0182f; --accent-rgb: 224, 24, 47;  --accent-ink: #a8101f; }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
    }

    html, body {
      width: 100%;
      height: 100%;
      overflow: hidden;
      background-color: var(--bg);
      color: var(--text);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      -webkit-font-smoothing: antialiased;
      transition: background-color 0.35s ease, color 0.35s ease;
    }

    body::before {
      content: "";
      position: fixed;
      inset: 0;
      background:
        radial-gradient(circle at 12% 8%, rgba(var(--accent-rgb), calc(0.16 * var(--aurora))), transparent 42%),
        radial-gradient(circle at 90% 92%, rgba(var(--accent-rgb), calc(0.12 * var(--aurora))), transparent 46%),
        linear-gradient(rgba(var(--accent-rgb), 0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(var(--accent-rgb), 0.03) 1px, transparent 1px);
      background-size: 100% 100%, 100% 100%, 28px 28px, 28px 28px;
      pointer-events: none;
      z-index: 0;
    }

    body::after {
      content: "";
      position: fixed;
      inset: -20%;
      background:
        radial-gradient(40% 34% at 28% 30%, rgba(var(--accent-rgb), calc(0.09 * var(--aurora))), transparent 70%),
        radial-gradient(34% 30% at 76% 70%, rgba(var(--accent-rgb), calc(0.07 * var(--aurora))), transparent 70%);
      animation: drift 24s ease-in-out infinite alternate;
      pointer-events: none;
      z-index: 0;
      will-change: transform;
    }

    @keyframes drift {
      to { transform: translate3d(4%, -3%, 0) scale(1.08); }
    }

    @keyframes heat-warn {
      0%, 100% { box-shadow: var(--shadow), inset 0 1px 0 var(--sheen), inset 0 0 0 1px rgba(var(--warn-rgb), 0.55),
                   0 0 14px rgba(var(--warn-rgb), 0.25), inset 0 0 18px rgba(var(--warn-rgb), 0.06); }
      50%      { box-shadow: var(--shadow), inset 0 1px 0 var(--sheen), inset 0 0 0 1px rgba(var(--warn-rgb), 0.85),
                   0 0 26px rgba(var(--warn-rgb), 0.45), inset 0 0 28px rgba(var(--warn-rgb), 0.12); }
    }

    @keyframes heat-hot {
      0%, 100% { box-shadow: var(--shadow), inset 0 1px 0 var(--sheen), inset 0 0 0 1px rgba(var(--hot-rgb), 0.7),
                   0 0 18px rgba(var(--hot-rgb), 0.4), 0 0 44px rgba(var(--hot-rgb), 0.18), inset 0 0 26px rgba(var(--hot-rgb), 0.1); }
      50%      { box-shadow: var(--shadow), inset 0 1px 0 var(--sheen), inset 0 0 0 1px rgba(var(--hot-rgb), 1),
                   0 0 30px rgba(var(--hot-rgb), 0.75), 0 0 70px rgba(var(--hot-rgb), 0.35), inset 0 0 40px rgba(var(--hot-rgb), 0.2); }
    }

    @keyframes heat-crit {
      0%, 100% { box-shadow: var(--shadow), inset 0 1px 0 var(--sheen), inset 0 0 0 1.5px rgba(var(--crit-rgb), 0.85),
                   0 0 24px rgba(var(--crit-rgb), 0.6), 0 0 60px rgba(var(--crit-rgb), 0.3), inset 0 0 34px rgba(var(--crit-rgb), 0.16); }
      50%      { box-shadow: var(--shadow), inset 0 1px 0 var(--sheen), inset 0 0 0 1.5px rgba(var(--crit-rgb), 1),
                   0 0 42px rgba(var(--crit-rgb), 0.95), 0 0 96px rgba(var(--crit-rgb), 0.55), inset 0 0 52px rgba(var(--crit-rgb), 0.32); }
    }

    .shell {
      --gap: clamp(10px, 1.6vmin, 16px);
      position: relative;
      z-index: 1;
      width: 100dvw;
      height: 100dvh;
      padding: max(12px, env(safe-area-inset-top)) max(14px, env(safe-area-inset-right))
               max(12px, env(safe-area-inset-bottom)) max(14px, env(safe-area-inset-left));
      display: flex;
      flex-direction: column;
      gap: 12px;
      transition: transform 0.3s ease;
    }

    @media (orientation: portrait) {
      body.landscape:not(.force-upright) .shell {
        width: 100dvh;
        height: 100dvw;
        position: fixed;
        top: 0;
        left: 100dvw;
        transform-origin: top left;
        transform: rotate(90deg);
        padding: 12px 18px;
      }
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 2px 4px;
      flex: 0 0 auto;
    }

    .brand-wrap {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }

    .live-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--ok);
      box-shadow: 0 0 0 3px rgba(var(--ok-rgb), 0.18), 0 0 12px var(--ok);
      animation: pulse 2s infinite ease-in-out;
      transition: background 0.3s ease, box-shadow 0.3s ease;
    }

    .live-dot.off {
      background: var(--hot);
      box-shadow: 0 0 0 3px rgba(var(--hot-rgb), 0.18), 0 0 12px var(--hot);
      animation: none;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(0.82); }
    }

    .brand {
      font-family: 'Orbitron', sans-serif;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.24em;
      text-transform: uppercase;
      color: var(--text);
      white-space: nowrap;
    }

    .brand span {
      color: var(--accent-ink);
      text-shadow: 0 0 16px rgba(var(--accent-rgb), calc(0.5 * var(--glow)));
    }

    .header-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    #clock {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 700;
      color: var(--text);
      letter-spacing: 0.06em;
      background: linear-gradient(160deg, var(--card-top), var(--card-bot));
      border: 1px solid var(--hair);
      box-shadow: inset 0 1px 0 var(--sheen);
      height: 28px;
      display: inline-flex;
      align-items: center;
      padding: 0 11px;
      border-radius: 999px;
      white-space: nowrap;
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }

    .wake {
      -webkit-appearance: none;
      appearance: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 28px;
      padding: 0 11px;
      border-radius: 999px;
      border: 1px solid rgba(var(--accent-rgb), 0.38);
      background: rgba(var(--accent-rgb), 0.12);
      color: var(--accent-ink);
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      white-space: nowrap;
      cursor: pointer;
      outline: none;
      transition: transform 0.15s ease, background 0.25s ease, color 0.25s ease, border-color 0.25s ease;
    }

    .wake:active { transform: scale(0.94); }
    .wake:focus-visible { box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.35); }

    .wake svg {
      width: 13px;
      height: 13px;
      flex: 0 0 auto;
    }

    .wake[data-state="pending"] { animation: ring 2.2s infinite; }

    @keyframes ring {
      0%   { box-shadow: 0 0 0 0 rgba(var(--accent-rgb), 0.5); }
      70%  { box-shadow: 0 0 0 9px rgba(var(--accent-rgb), 0); }
      100% { box-shadow: 0 0 0 0 rgba(var(--accent-rgb), 0); }
    }

    .wake[data-state="on"] {
      color: var(--ok);
      background: rgba(var(--ok-rgb), 0.13);
      border-color: rgba(var(--ok-rgb), 0.42);
    }

    .wake[data-state="off"] {
      color: var(--muted);
      background: var(--card-inner);
      border-color: var(--hair);
    }

    @media (max-width: 400px) {
      body.portrait .wake:not([data-state="pending"]) .wake-label { display: none; }
      body.portrait .wake:not([data-state="pending"]) { padding: 0 8px; }
    }

    .dash {
      flex: 1;
      min-height: 0;
      display: grid;
      gap: var(--gap);
    }

    body.portrait .dash {
      grid-template-columns: 1fr;
      grid-template-rows: 1.25fr 1.15fr 0.72fr;
    }

    body.portrait .right { display: contents; }

    body.portrait .mid {
      display: flex;
      gap: var(--gap);
      min-height: 0;
      min-width: 0;
    }

    body.landscape .dash {
      grid-template-columns: 1.05fr 1fr;
      grid-template-rows: 1fr;
    }

    body.landscape .right {
      display: grid;
      grid-template-rows: 1.22fr 0.78fr;
      gap: var(--gap);
      min-height: 0;
      min-width: 0;
    }

    body.landscape .mid {
      display: flex;
      gap: var(--gap);
      min-height: 0;
      min-width: 0;
    }

    .mid > .card-cpu { flex: 1 1 0; }
    .mid > .card-gpu { flex: 1.3 1 0; }

    .card {
      position: relative;
      isolation: isolate;
      background: linear-gradient(155deg, var(--card-top), var(--card-bot));
      backdrop-filter: blur(22px) saturate(160%);
      -webkit-backdrop-filter: blur(22px) saturate(160%);
      border: none;
      border-radius: clamp(20px, 2.8vmin, 26px);
      padding: clamp(14px, 2.4vmin, 22px);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
      box-shadow:
        var(--shadow),
        inset 0 1px 0 var(--sheen),
        inset 0 0 0 1px var(--hair);
      transition: transform 0.2s ease, box-shadow 0.6s ease, background-color 0.3s ease;
      animation: rise 0.6s cubic-bezier(0.2, 0.8, 0.2, 1) both;
    }

    .card-fps { animation-delay: 0.02s; }
    .card-cpu { animation-delay: 0.08s; }
    .card-gpu { animation-delay: 0.14s; }
    .card-ram { animation-delay: 0.20s; }

    @keyframes rise {
      from { opacity: 0; transform: translateY(10px) scale(0.985); }
      to   { opacity: 1; transform: none; }
    }

    .card:active { transform: scale(0.992); }

    html[data-ready] .card { animation: none; }

    .card[data-heat="warn"] { animation: heat-warn 2.4s infinite ease-in-out !important; }
    .card[data-heat="hot"]  { animation: heat-hot 1.5s infinite ease-in-out !important; }
    .card[data-heat="crit"] { animation: heat-crit 0.8s infinite ease-in-out !important; }

    .card[data-heat="hot"],
    .card[data-heat="crit"] {
      background:
        radial-gradient(120% 70% at 50% 120%, rgba(var(--hot-rgb), 0.26), transparent 70%),
        linear-gradient(155deg, var(--card-top), var(--card-bot));
    }
    .card[data-heat="crit"] {
      background:
        radial-gradient(130% 80% at 50% 120%, rgba(var(--crit-rgb), 0.4), transparent 70%),
        linear-gradient(155deg, var(--card-top), var(--card-bot));
    }
    .card[data-heat="warn"] {
      background:
        radial-gradient(120% 60% at 50% 120%, rgba(var(--warn-rgb), 0.14), transparent 70%),
        linear-gradient(155deg, var(--card-top), var(--card-bot));
    }

    .card[data-heat="warn"]::before { background: linear-gradient(90deg, transparent, var(--warn), transparent); box-shadow: 0 0 18px rgba(var(--warn-rgb), 0.9); }
    .card[data-heat="hot"]::before  { background: linear-gradient(90deg, transparent, var(--hot), transparent);  box-shadow: 0 0 22px rgba(var(--hot-rgb), 1); height: 3px; }
    .card[data-heat="crit"]::before { background: linear-gradient(90deg, transparent, var(--crit), transparent); box-shadow: 0 0 30px rgba(var(--crit-rgb), 1); height: 3px; left: 6%; right: 6%; }
    .card[data-heat="warn"]::after { background: linear-gradient(145deg, rgba(var(--warn-rgb), 0.75), rgba(var(--warn-rgb), 0.08) 40%, rgba(var(--warn-rgb), 0.08) 60%, rgba(var(--warn-rgb), 0.5)); }
    .card[data-heat="hot"]::after  { background: linear-gradient(145deg, rgba(var(--hot-rgb), 0.95), rgba(var(--hot-rgb), 0.12) 40%, rgba(var(--hot-rgb), 0.12) 60%, rgba(var(--hot-rgb), 0.75)); }
    .card[data-heat="crit"]::after { background: linear-gradient(145deg, rgba(var(--crit-rgb), 1), rgba(var(--crit-rgb), 0.25) 40%, rgba(var(--crit-rgb), 0.25) 60%, rgba(var(--crit-rgb), 0.95)); }

    .card[data-heat="warn"] .value { color: var(--warn); text-shadow: 0 0 22px rgba(var(--warn-rgb), 0.55); }
    .card[data-heat="hot"]  .value { color: var(--hot);  text-shadow: 0 0 24px rgba(var(--hot-rgb), 0.85), 0 0 48px rgba(var(--hot-rgb), 0.4); }
    .card[data-heat="crit"] .value { color: var(--crit); text-shadow: 0 0 26px rgba(var(--crit-rgb), 1), 0 0 60px rgba(var(--crit-rgb), 0.6); }
    .card[data-heat] .unit { color: inherit; opacity: 0.85; }

    .card[data-heat="warn"] .meter { --c: var(--warn); --c-rgb: var(--warn-rgb); }
    .card[data-heat="hot"]  .meter { --c: var(--hot);  --c-rgb: var(--hot-rgb); }
    .card[data-heat="crit"] .meter { --c: var(--crit); --c-rgb: var(--crit-rgb); }
    .card[data-heat="hot"] .meter > i,
    .card[data-heat="crit"] .meter > i { box-shadow: 0 0 18px rgba(var(--c-rgb), 1), 0 0 32px rgba(var(--c-rgb), 0.5); }
    .card[data-heat="crit"] .label { color: var(--crit); }

    .card::before {
      content: "";
      position: absolute;
      top: 0;
      left: 14%;
      right: 14%;
      height: 2px;
      border-radius: 0 0 4px 4px;
      background: linear-gradient(90deg, transparent, var(--accent), transparent);
      box-shadow: 0 0 20px rgba(var(--accent-rgb), calc(0.8 * var(--glow)));
      opacity: 1;
      z-index: 2;
    }

    .card::after {
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
      padding: 1px;
      background: linear-gradient(145deg,
        rgba(var(--accent-rgb), 0.8),
        rgba(var(--accent-rgb), 0.04) 38%,
        rgba(var(--accent-rgb), 0.04) 62%,
        rgba(var(--accent-rgb), 0.5));
      -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
      -webkit-mask-composite: xor;
      mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);
      mask-composite: exclude;
      pointer-events: none;
      z-index: 2;
    }

    .hidden { display: none !important; }

    .card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      z-index: 1;
    }

    .label {
      font-size: clamp(10px, 1.5vmin, 12px);
      font-weight: 800;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
    }

    .label::before {
      content: "";
      display: inline-block;
      width: 3px;
      height: 12px;
      border-radius: 2px;
      background: linear-gradient(var(--accent), rgba(var(--accent-rgb), 0.3));
      box-shadow: 0 0 10px rgba(var(--accent-rgb), calc(0.8 * var(--glow)));
    }

    .badge {
      --c-rgb: var(--accent-rgb);
      --c: var(--accent-ink);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 4px 9px;
      border-radius: 999px;
      background: rgba(var(--c-rgb), 0.14);
      color: var(--c);
      border: 1px solid rgba(var(--c-rgb), 0.34);
      white-space: nowrap;
      transition: color 0.3s ease, background 0.3s ease, border-color 0.3s ease;
    }

    .badge::before {
      content: "";
      width: 5px;
      height: 5px;
      border-radius: 50%;
      background: currentColor;
      box-shadow: 0 0 8px currentColor;
    }

    .badge.idle {
      --c: var(--muted);
      --c-rgb: 120, 135, 160;
    }

    [data-lvl="warn"] { --c: var(--warn); --c-rgb: var(--warn-rgb); }
    [data-lvl="hot"]  { --c: var(--hot);  --c-rgb: var(--hot-rgb); }

    .value {
      font-family: 'Orbitron', sans-serif;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
      line-height: 1;
      color: var(--text);
      letter-spacing: -0.02em;
      z-index: 1;
    }

    .unit {
      font-family: 'Inter', sans-serif;
      font-size: 0.32em;
      font-weight: 800;
      margin-left: 6px;
      color: var(--accent-ink);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      vertical-align: baseline;
    }

    .card-fps {
      background:
        radial-gradient(circle at 88% 16%, rgba(var(--accent-rgb), calc(0.2 * var(--aurora))), transparent 55%),
        linear-gradient(155deg, var(--card-top), var(--card-bot));
    }

    .fps-center {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin: auto 0;
      z-index: 1;
    }

    .fps-main {
      font-size: clamp(58px, 16vmin, 128px);
      text-shadow: 0 0 30px rgba(var(--accent-rgb), calc(0.45 * var(--glow)));
    }

    .fps-footer {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 14px;
      z-index: 1;
      padding-top: 10px;
      border-top: 1px solid var(--hair);
    }

    .frametime-box .sub-label {
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
    }

    .frametime-box .value {
      font-size: clamp(20px, 4.2vmin, 32px);
      color: var(--accent-ink);
    }

    .sparkline {
      flex: 1;
      max-width: 180px;
      height: 30px;
      display: flex;
      align-items: flex-end;
      gap: 3px;
      -webkit-mask-image: linear-gradient(90deg, transparent, #000 45%);
      mask-image: linear-gradient(90deg, transparent, #000 45%);
    }

    .sparkline span {
      flex: 1;
      background: linear-gradient(to top, rgba(var(--accent-rgb), 0.22), var(--accent));
      border-radius: 3px 3px 1px 1px;
      min-height: 15%;
      transition: height 0.1s linear;
    }

    .sparkline span:last-child {
      box-shadow: 0 0 10px rgba(var(--accent-rgb), 0.8);
    }

    .hw-main { margin: auto 0; z-index: 1; }

    .hw .value { font-size: clamp(30px, 7.2vmin, 54px); }

    .meter {
      --c-rgb: var(--accent-rgb);
      --c: var(--accent);
      position: relative;
      width: 100%;
      height: 7px;
      background: var(--track);
      box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.25);
      border-radius: 999px;
      margin-top: 9px;
    }

    .meter > i {
      position: relative;
      display: block;
      height: 100%;
      width: 0%;
      overflow: hidden;
      background: linear-gradient(90deg, rgba(var(--c-rgb), 0.55), var(--c));
      box-shadow: 0 0 14px rgba(var(--c-rgb), calc(0.75 * var(--glow) + 0.1));
      border-radius: inherit;
      transition: width 0.1s linear, background 0.2s ease;
    }

    .meter > i::after {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(100deg, transparent 30%, rgba(255, 255, 255, 0.45) 50%, transparent 70%);
      transform: translateX(-100%);
      animation: shimmer 2.6s linear infinite;
    }

    @keyframes shimmer { to { transform: translateX(100%); } }

    .meter::after {
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
      background: repeating-linear-gradient(90deg, transparent 0 calc(10% - 1px), var(--tick) calc(10% - 1px) 10%);
      pointer-events: none;
    }

    .hw-stats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
      margin-top: 11px;
      z-index: 1;
    }

    .card-gpu .hw-stats {
      grid-template-columns: 1fr 1.6fr;
      grid-template-rows: auto auto;
    }

    .card-gpu .hw-stats .stat-pill:nth-child(1) { grid-column: 1; grid-row: 1; }
    .card-gpu .hw-stats .stat-pill:nth-child(2) { grid-column: 1; grid-row: 2; }
    .card-gpu .hw-stats .stat-pill:nth-child(3) {
      grid-column: 2;
      grid-row: 1 / span 2;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }

    .card-gpu .hw-stats .stat-pill:nth-child(3) .s-val {
      font-size: clamp(13px, 2.8vmin, 18px);
    }

    .stat-pill {
      background: var(--card-inner);
      border: 1px solid var(--hair);
      box-shadow: inset 0 1px 0 var(--sheen);
      border-radius: 12px;
      padding: 6px 7px;
      min-width: 0;
      transition: border-color 0.3s ease, background 0.3s ease;
    }

    .stat-pill[data-lvl="warn"],
    .stat-pill[data-lvl="hot"] {
      border-color: rgba(var(--c-rgb), 0.45);
      background: rgba(var(--c-rgb), 0.1);
    }

    .stat-pill .s-label {
      font-size: 9px;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 2px;
      white-space: nowrap;
    }

    .stat-pill .s-val {
      font-family: 'JetBrains Mono', monospace;
      font-size: clamp(10.5px, 2.1vmin, 14px);
      font-weight: 700;
      color: var(--text);
      white-space: nowrap;
      overflow: visible;
      text-overflow: clip;
      letter-spacing: -0.02em;
    }

    .stat-pill[data-lvl="warn"] .s-val,
    .stat-pill[data-lvl="hot"] .s-val { color: var(--c); }

    .s-val .su {
      font-size: 0.72em;
      font-weight: 700;
      margin-left: 1px;
      color: var(--muted);
      letter-spacing: 0;
    }

    .ram-body {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      margin: auto 0 6px;
      z-index: 1;
    }

    .ram-body .value {
      font-size: clamp(26px, 6.2vmin, 44px);
      white-space: nowrap;
    }

    .ram-meta {
      font-family: 'JetBrains Mono', monospace;
      font-size: clamp(11px, 2vmin, 14px);
      font-weight: 700;
      color: var(--muted);
      white-space: nowrap;
    }

    .wait {
      position: fixed;
      inset: 0;
      display: none;
      place-items: center;
      background: var(--wait-bg);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
      z-index: 50;
      text-align: center;
      padding: 24px;
    }

    .wait.show { display: grid; }

    .wait-box {
      position: relative;
      max-width: 360px;
      background: linear-gradient(155deg, var(--card-top), var(--card-bot));
      border-radius: 26px;
      padding: 30px 26px;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45), inset 0 1px 0 var(--sheen), inset 0 0 0 1px rgba(var(--accent-rgb), 0.35);
      overflow: hidden;
    }

    .wait-box::before {
      content: "";
      position: absolute;
      top: 0;
      left: 18%;
      right: 18%;
      height: 2px;
      background: linear-gradient(90deg, transparent, var(--accent), transparent);
      box-shadow: 0 0 16px var(--accent);
    }

    .spinner {
      width: 40px;
      height: 40px;
      border: 3px solid rgba(var(--accent-rgb), 0.18);
      border-top-color: var(--accent);
      border-right-color: rgba(var(--accent-rgb), 0.55);
      border-radius: 50%;
      margin: 0 auto 18px;
      box-shadow: 0 0 18px rgba(var(--accent-rgb), calc(0.4 * var(--glow)));
      animation: spin 0.9s linear infinite;
    }

    @keyframes spin { to { transform: rotate(360deg); } }

    .wait-box h1 {
      font-family: 'Orbitron', sans-serif;
      font-size: clamp(16px, 3.5vmin, 22px);
      margin-bottom: 8px;
      color: var(--text);
    }

    .wait-box p {
      font-size: 13px;
      line-height: 1.55;
      color: var(--muted);
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: 0.001s !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.001s !important;
      }
    }
  </style>
  <script>setTimeout(function () { document.documentElement.dataset.ready = "1"; }, 1200);</script>
</head>
<body class="__INITIAL_BODY_CLASS__">
  <div class="wait" id="wait">
    <div class="wait-box">
      <div class="spinner"></div>
      <h1>Waiting for MSI Afterburner...</h1>
      <p>Launch MSI Afterburner &amp; RTSS in the background to stream real-time hardware telemetry.</p>
    </div>
  </div>

  <div class="shell">
    <header>
      <div class="brand-wrap">
        <span class="live-dot" id="live-dot" title="Connection status"></span>
        <div class="brand">SYS<span>CAST</span></div>
      </div>
      <div class="header-right">
        <button class="wake" id="wake-btn" type="button" data-state="pending" aria-live="polite">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
          <span class="wake-label" id="wake-label">Keep awake</span>
        </button>
        <div id="clock">--:--</div>
      </div>
    </header>

    <main class="dash" id="dash">
      <section class="card card-fps" id="card-fps">
        <div class="card-head">
          <div class="label">Performance</div>
          <div class="badge idle" id="fps-status">RTSS Ready</div>
        </div>
        <div class="fps-center">
          <div class="value fps-main" id="fps">0<span class="unit">FPS</span></div>
        </div>
        <div class="fps-footer">
          <div class="frametime-box">
            <div class="sub-label">Frametime</div>
            <div class="value" id="frametime">0.0<span class="unit">ms</span></div>
          </div>
          <div class="sparkline" id="sparkline"></div>
        </div>
      </section>

      <div class="right" id="right">
        <div class="mid" id="mid">
          <section class="card card-cpu hw" id="card-cpu">
            <div class="card-head">
              <div class="label">CPU</div>
            </div>
            <div class="hw-main">
              <div class="value" id="cpu-usage">0<span class="unit">%</span></div>
              <div class="meter"><i id="cpu-bar"></i></div>
            </div>
            <div class="hw-stats">
              <div class="stat-pill">
                <div class="s-label">Temp</div>
                <div class="s-val" id="cpu-temp">—</div>
              </div>
              <div class="stat-pill">
                <div class="s-label">Power</div>
                <div class="s-val" id="cpu-power">—</div>
              </div>
            </div>
          </section>

          <section class="card card-gpu hw" id="card-gpu">
            <div class="card-head">
              <div class="label">GPU</div>
            </div>
            <div class="hw-main">
              <div class="value" id="gpu-usage">0<span class="unit">%</span></div>
              <div class="meter"><i id="gpu-bar"></i></div>
            </div>
            <div class="hw-stats">
              <div class="stat-pill">
                <div class="s-label">Temp</div>
                <div class="s-val" id="gpu-temp">—</div>
              </div>
              <div class="stat-pill">
                <div class="s-label">Power</div>
                <div class="s-val" id="gpu-power">—</div>
              </div>
              <div class="stat-pill">
                <div class="s-label">VRAM</div>
                <div class="s-val" id="gpu-vram">—</div>
              </div>
            </div>
          </section>
        </div>

        <section class="card card-ram" id="card-ram">
          <div class="card-head">
            <div class="label">System Memory</div>
            <div class="badge" id="ram-percent">0%</div>
          </div>
          <div class="ram-body">
            <div class="value" id="ram-used">0.0<span class="unit">GB</span></div>
            <div class="ram-meta" id="ram-total">/ 0.0 GB</div>
          </div>
          <div class="meter"><i id="ram-bar"></i></div>
        </section>
      </div>
    </main>
  </div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/nosleep/0.12.0/NoSleep.min.js"></script>
  <script>
    const $ = (id) => document.getElementById(id);
    let clockFormat = "__INITIAL_CLOCK_FORMAT__";

    const SPARK_COUNT = 22;
    const sparkHistory = new Array(SPARK_COUNT).fill(18);
    const sparkContainer = $("sparkline");
    for (let i = 0; i < SPARK_COUNT; i++) {
      const bar = document.createElement("span");
      bar.style.height = "18%";
      sparkContainer.appendChild(bar);
    }

    let lastSpark = 0;
    function updateSparkline(fps, ft) {
      const bars = sparkContainer.children;
      let level = 15;
      if (fps !== null && fps > 0) {
        level = Math.max(15, Math.min(100, (fps / 144) * 100));
      } else if (ft !== null && ft > 0) {
        level = Math.max(15, Math.min(100, (1000 / Math.max(ft, 4)) / 1.44));
      }
      const nowMs = performance.now();
      if (nowMs - lastSpark < 250) return;
      lastSpark = nowMs;
      sparkHistory.push(level);
      sparkHistory.shift();
      for (let i = 0; i < bars.length; i++) {
        bars[i].style.height = `${sparkHistory[i].toFixed(0)}%`;
      }
    }

    const UNITS = {
      "fps": "FPS",
      "frametime": "ms",
      "cpu-usage": "%",
      "gpu-usage": "%",
      "ram-used": "GB"
    };

    function setVal(id, value, digits = 0, fallback = "—") {
      const el = $(id);
      if (!el) return;
      const num = (value === null || value === undefined || Number.isNaN(Number(value)))
        ? fallback
        : Number(value).toFixed(digits);
      el.innerHTML = `${num}<span class="unit">${UNITS[id] || ""}</span>`;
    }

    function setStat(id, value, unit = "", digits = 0, lvl = "") {
      const el = $(id);
      if (!el) return;
      const ok = value !== null && value !== undefined && !Number.isNaN(Number(value));
      el.innerHTML = ok ? `${Number(value).toFixed(digits)}<span class="su">${unit}</span>` : "—";
      const pill = el.closest(".stat-pill");
      if (pill) pill.dataset.lvl = ok ? lvl : "";
    }

    function level(v, warn, hot) {
      if (v === null || v === undefined || Number.isNaN(Number(v))) return "";
      return v >= hot ? "hot" : (v >= warn ? "warn" : "");
    }

    function setBar(id, percent, lvl = "") {
      const el = $(id);
      if (!el) return;
      const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
      el.style.width = `${clamped}%`;
      const meter = el.parentElement;
      if (lvl) meter.dataset.lvl = lvl; else delete meter.dataset.lvl;
    }

    function applySettings(s) {
      const layout = s.layout === "landscape" ? "landscape" : "portrait";
      const theme = s.theme === "white" ? "white" : "dark";
      const accent = s.accent || "purple";
      if (s.clock_format) {
        clockFormat = s.clock_format;
        tickClock();
      }

      document.body.className = `${layout} layout-${layout} theme-${theme} accent-${accent}`;
      const metaTheme = $("meta-theme");
      if (metaTheme) metaTheme.setAttribute("content", theme === "white" ? "#b9bfcc" : "#020104");

      $("card-fps").classList.toggle("hidden", !s.show_fps);
      $("card-cpu").classList.toggle("hidden", !s.show_cpu);
      $("card-gpu").classList.toggle("hidden", !s.show_gpu);
      $("card-ram").classList.toggle("hidden", !s.show_ram);

      const showMid = s.show_cpu || s.show_gpu;
      const showRight = showMid || s.show_ram;
      $("mid").classList.toggle("hidden", !showMid);
      $("right").classList.toggle("hidden", !showRight);

      const dash = $("dash");
      const right = $("right");
      if (layout === "landscape") {
        dash.style.gridTemplateRows = "";
        right.style.gridTemplateRows = (showMid && s.show_ram) ? "1.22fr 0.78fr" : "1fr";
        if (!s.show_fps) {
          dash.style.gridTemplateColumns = "1fr";
        } else if (!showRight) {
          dash.style.gridTemplateColumns = "1fr";
        } else {
          dash.style.gridTemplateColumns = "1.05fr 1fr";
        }
      } else {
        right.style.gridTemplateRows = "";
        dash.style.gridTemplateColumns = "1fr";
        const rows = [];
        if (s.show_fps) rows.push("1.25fr");
        if (showMid) rows.push("1.15fr");
        if (s.show_ram) rows.push("0.72fr");
        dash.style.gridTemplateRows = rows.join(" ") || "1fr";
      }
    }

    function render(msg) {
      if (msg.settings) applySettings(msg.settings);

      const alive = Boolean(msg.afterburner);
      $("wait").classList.toggle("show", !alive);

      if (!alive) {
        setVal("fps", null, 0, "—");
        setVal("frametime", null, 1, "—");
        setVal("cpu-usage", null, 0, "—");
        setVal("gpu-usage", null, 0, "—");
        setBar("cpu-bar", 0);
        setBar("gpu-bar", 0);
        delete $("card-cpu").dataset.heat;
        delete $("card-gpu").dataset.heat;
        ["cpu-temp", "cpu-power", "gpu-temp", "gpu-power", "gpu-vram"].forEach((id) => setStat(id, null));
        return;
      }

      const cpuUsage = msg.cpu && msg.cpu.usage != null ? Math.round(msg.cpu.usage) : 0;
      const cpuTemp = (msg.cpu && msg.cpu.temp != null) ? Math.round(msg.cpu.temp) : null;
      const gpuUsage = msg.gpu && msg.gpu.usage != null ? Math.round(msg.gpu.usage) : 0;
      const gpuTemp = (msg.gpu && msg.gpu.temp != null) ? Math.round(msg.gpu.temp) : null;

      function heatTier(usage, temp, tWarn, tHot, tCrit) {
        const t = temp || 0;
        if (usage >= 95 || t >= tCrit) return "crit";
        if (usage >= 80 || t >= tHot) return "hot";
        if (usage >= 65 || t >= tWarn) return "warn";
        return "";
      }
      function setHeat(id, tier) {
        const el = $(id);
        if (tier) { if (el.dataset.heat !== tier) el.dataset.heat = tier; }
        else delete el.dataset.heat;
      }
      setHeat("card-cpu", heatTier(cpuUsage, cpuTemp, 80, 90, 95));
      setHeat("card-gpu", heatTier(gpuUsage, gpuTemp, 75, 85, 90));

      const hasFps = msg.fps !== null && msg.fps !== undefined && Number(msg.fps) > 0;
      const ftVal = (msg.frametime !== null && msg.frametime !== undefined && Number(msg.frametime) > 0)
        ? Number(msg.frametime)
        : (hasFps ? (1000 / Number(msg.fps)) : 0.0);

      setVal("fps", hasFps ? Math.round(msg.fps) : 0, 0);
      setVal("frametime", ftVal, 1);
      const status = $("fps-status");
      status.textContent = hasFps ? "3D Active" : "Desktop / Idle";
      status.classList.toggle("idle", !hasFps);
      updateSparkline(msg.fps, ftVal);

      setVal("cpu-usage", cpuUsage, 0);
      setBar("cpu-bar", cpuUsage);
      setStat("cpu-temp", cpuTemp, "°C", 0, level(cpuTemp, 80, 90));
      setStat("cpu-power", (msg.cpu && msg.cpu.power != null) ? Math.round(msg.cpu.power) : null, "W");

      setVal("gpu-usage", gpuUsage, 0);
      setBar("gpu-bar", gpuUsage);
      setStat("gpu-temp", gpuTemp, "°C", 0, level(gpuTemp, 75, 85));
      setStat("gpu-power", (msg.gpu && msg.gpu.power != null) ? Math.round(msg.gpu.power) : null, "W");
      setStat("gpu-vram", (msg.gpu && msg.gpu.vram_gb != null) ? Number(msg.gpu.vram_gb) : null, "GB", 2);

      if (msg.ram) {
        const ramPct = Math.round(msg.ram.percent || 0);
        const ramLvl = level(ramPct, 80, 92);
        setVal("ram-used", msg.ram.used_gb, 1);
        $("ram-percent").textContent = `${ramPct}%`;
        if (ramLvl) $("ram-percent").dataset.lvl = ramLvl; else delete $("ram-percent").dataset.lvl;
        $("ram-total").textContent = `/ ${Number(msg.ram.total_gb || 0).toFixed(1)} GB`;
        setBar("ram-bar", msg.ram.percent || 0, ramLvl);
      }
    }

    function tickClock() {
      const now = new Date();
      const use12 = (clockFormat === "12h");
      $("clock").textContent = now.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        hour12: use12
      });
    }
    tickClock();
    setInterval(tickClock, 1000);

    let noSleep = null;
    try {
      if (typeof NoSleep !== "undefined") noSleep = new NoSleep();
    } catch (_) {}

    let nativeLock = null;
    let wakeOn = false;
    let wakeOptOut = false;
    let wakeBusy = false;

    function paintWake() {
      const btn = $("wake-btn");
      const label = $("wake-label");
      const state = wakeOn ? "on" : (wakeOptOut ? "off" : "pending");
      btn.dataset.state = state;
      label.textContent = state === "on" ? "Awake" : (state === "off" ? "Sleep ok" : "Keep awake");
    }

    async function wakeEnable() {
      if (wakeOn || wakeOptOut || wakeBusy) return;
      wakeBusy = true;
      try {
        if (noSleep) {
          await Promise.resolve(noSleep.enable());
        } else if ("wakeLock" in navigator) {
          nativeLock = await navigator.wakeLock.request("screen");
        } else {
          throw new Error("wake lock unsupported");
        }
        wakeOn = true;
      } catch (_) {
        wakeOn = false;
      }
      wakeBusy = false;
      paintWake();
    }

    function wakeDisable() {
      try {
        if (noSleep) noSleep.disable();
        if (nativeLock) { nativeLock.release(); nativeLock = null; }
      } catch (_) {}
      wakeOn = false;
      paintWake();
    }

    ["click", "touchstart", "touchend"].forEach((evt) => {
      document.addEventListener(evt, wakeEnable, { passive: true });
    });

    const wakeBtn = $("wake-btn");
    ["touchstart", "touchend"].forEach((evt) => {
      wakeBtn.addEventListener(evt, (e) => e.stopPropagation(), { passive: true });
    });
    wakeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (wakeOn) {
        wakeOptOut = true;
        wakeDisable();
      } else {
        wakeOptOut = false;
        wakeEnable();
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && wakeOn && !wakeOptOut) {
        wakeOn = false;
        wakeEnable();
      }
    });
    paintWake();

    const liveDot = $("live-dot");
    function setLink(ok) { liveDot.classList.toggle("off", !ok); }

    let pending = null;
    let rafQueued = false;
    function flush() {
      rafQueued = false;
      const msg = pending;
      pending = null;
      if (msg) { try { render(msg); } catch (_) {} }
    }

    function connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);

      ws.onopen = () => {
        setLink(true);
      };

      ws.onmessage = (ev) => {
        try { pending = JSON.parse(ev.data); } catch (_) { return; }
        if (!rafQueued) {
          rafQueued = true;
          requestAnimationFrame(flush);
        }
      };

      ws.onclose = () => {
        setLink(false);
        setTimeout(connect, 500);
      };
      ws.onerror = () => ws.close();
    }
    connect();
  </script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# CLI Style & Logging
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
ACCENT = "\033[38;5;75m"
TEXT = "\033[38;5;252m"
MUTED = "\033[38;5;243m"
OK = "\033[38;5;41m"
WARN = "\033[38;5;214m"
ERR = "\033[38;5;203m"
ACCENT_ANSI = {
    "purple": "\033[38;5;135m",
    "green": "\033[38;5;48m",
    "yellow": "\033[38;5;220m",
    "blue": "\033[38;5;39m",
    "red": "\033[38;5;203m",
}


def apply_accent(name: str) -> None:
    """Update the terminal ANSI accent color to match the chosen setting."""
    global ACCENT
    ACCENT = ACCENT_ANSI.get(name, ACCENT_ANSI["purple"])


BOX_W = 54
OUTER_W = BOX_W + 4
INDENT = "   "
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

LOG_LOCK = threading.Lock()
SERVER_ACTIVE = False
PEAK_CLIENTS = 0


def log_device_event(ip: str, connected: bool, active_count: int) -> None:
    """Print a live connection or disconnection event to the server console log."""
    global PEAK_CLIENTS
    if not SERVER_ACTIVE:
        return
    PEAK_CLIENTS = max(PEAK_CLIENTS, active_count)
    ts = datetime.now().strftime("%H:%M:%S")
    with LOG_LOCK:
        badge = f"{OK}● CONNECTED   {RESET}" if connected else f"{ERR}○ DISCONNECTED{RESET}"
        print(
            f"{INDENT}{MUTED}│{RESET} {MUTED}{ts}{RESET}  {badge}  "
            f"{TEXT}{ip:<15}{RESET}  {ACCENT}{active_count}{RESET}",
            flush=True,
        )


# ---------------------------------------------------------------------------
# FastAPI Application & WebSocket Broadcaster
# ---------------------------------------------------------------------------

READER = HardwareReader()
READER_LOCK = threading.Lock()
CLIENTS: set[WebSocket] = set()
CLIENTS_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
LATEST: dict[str, Any] = {}


def collect_payload() -> dict[str, Any]:
    """Read hardware sensors and assemble the JSON-ready telemetry dictionary."""
    with READER_LOCK:
        metrics = READER.read()
    cfg = load_config()
    return {
        "afterburner": metrics["afterburner"],
        "fps": round_or_none(metrics["fps"], 1),
        "frametime": round_or_none(metrics["frametime"], 2),
        "cpu": {
            "usage": round_or_none(metrics["cpu"]["usage"], 1),
            "temp": round_or_none(metrics["cpu"]["temp"], 1),
            "power": round_or_none(metrics["cpu"]["power"], 1),
        },
        "gpu": {
            "usage": round_or_none(metrics["gpu"]["usage"], 1),
            "temp": round_or_none(metrics["gpu"]["temp"], 1),
            "vram_gb": round_or_none(metrics["gpu"]["vram_gb"], 2),
            "power": round_or_none(metrics["gpu"]["power"], 1),
        },
        "ram": {
            "used_gb": round_or_none(metrics["ram"]["used_gb"], 2),
            "total_gb": round_or_none(metrics["ram"]["total_gb"], 2),
            "percent": round_or_none(metrics["ram"]["percent"], 1),
        },
        "settings": cfg,
        "ts": time.time(),
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage the background telemetry polling and WebSocket broadcast loop."""
    stop = asyncio.Event()

    async def pump() -> None:
        loop = asyncio.get_running_loop()
        next_tick = time.perf_counter() + BROADCAST_INTERVAL
        while not stop.is_set():
            payload = await loop.run_in_executor(None, collect_payload)
            with STATE_LOCK:
                LATEST.clear()
                LATEST.update(payload)
            text = json.dumps(payload, separators=(",", ":"))
            with CLIENTS_LOCK:
                peers = list(CLIENTS)

            async def _send(ws: WebSocket) -> WebSocket | None:
                try:
                    await asyncio.wait_for(ws.send_text(text), timeout=1.0)
                    return None
                except Exception:
                    return ws

            if peers:
                results = await asyncio.gather(*(_send(w) for w in peers))
                stale = [w for w in results if w is not None]
                if stale:
                    with CLIENTS_LOCK:
                        for ws in stale:
                            CLIENTS.discard(ws)

            delay = next_tick - time.perf_counter()
            if delay > 0:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=delay)
            next_tick = max(next_tick + BROADCAST_INTERVAL, time.perf_counter())

    task = asyncio.create_task(pump())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with READER_LOCK:
            READER.close()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve the single-page telemetry dashboard."""
    cfg = load_config()
    body_cls = (
        f"{cfg['layout']} layout-{cfg['layout']} "
        f"theme-{cfg['theme']} accent-{cfg['accent']}"
    )
    html = DASHBOARD_HTML.replace("__INITIAL_BODY_CLASS__", body_cls)
    return html.replace("__INITIAL_CLOCK_FORMAT__", cfg.get("clock_format", "12h"))


@app.get("/api/settings")
async def api_settings() -> JSONResponse:
    """Return the active user configuration as JSON."""
    return JSONResponse(load_config())


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Stream real-time telemetry updates over a WebSocket connection."""
    await ws.accept()

    client_ip = ws.client.host if ws.client and ws.client.host else "Unknown"
    with CLIENTS_LOCK:
        CLIENTS.add(ws)
        active_now = len(CLIENTS)
    log_device_event(client_ip, connected=True, active_count=active_now)

    with STATE_LOCK:
        snapshot = dict(LATEST) if LATEST else None
    if snapshot is None:
        snapshot = await asyncio.get_running_loop().run_in_executor(
            None, collect_payload
        )
    try:
        await ws.send_text(json.dumps(snapshot))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        with CLIENTS_LOCK:
            CLIENTS.discard(ws)
            active_now = len(CLIENTS)
        log_device_event(client_ip, connected=False, active_count=active_now)


# ---------------------------------------------------------------------------
# CLI Interface (Backspace / Ctrl+C = Back; '0' is disabled and ignored)
# ---------------------------------------------------------------------------

TITLE_LINES: list[tuple[str, str]] = [
    ("███████╗██╗   ██╗███████╗", " ██████╗ █████╗ ███████╗████████╗"),
    ("██╔════╝╚██╗ ██╔╝██╔════╝", "██╔════╝██╔══██╗██╔════╝╚══██╔══╝"),
    ("███████╗ ╚████╔╝ ███████╗", "██║     ███████║███████╗   ██║   "),
    ("╚════██║  ╚██╔╝  ╚════██║", "██║     ██╔══██║╚════██║   ██║   "),
    ("███████║   ██║   ███████║", "╚██████╗██║  ██║███████║   ██║   "),
    ("╚══════╝   ╚═╝   ╚══════╝", " ╚═════╝╚═╝  ╚═╝╚══════╝   ╚═╝   "),
]


def enable_ansi() -> None:
    """Enable virtual terminal ANSI escape sequences on Windows consoles."""
    if os.name != "nt":
        return
    with suppress(Exception):
        out_handle = kernel32.GetStdHandle(wintypes.DWORD(-11).value)
        out_mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(out_handle, ctypes.byref(out_mode)):
            kernel32.SetConsoleMode(out_handle, out_mode.value | 0x0004)
        kernel32.SetConsoleTitleW(f"{APP_NAME} v{APP_VERSION}")
        _capture_console_hwnd(include_foreground=True)


def clear() -> None:
    """Clear the terminal screen and scrollback buffer."""
    sys.stdout.write("\033[2J\033[3J\033[H")
    sys.stdout.flush()


def _vlen(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _fit(text: str, width: int) -> str:
    return text + " " * max(0, width - _vlen(text))


def _center(text: str, width: int = OUTER_W) -> str:
    return " " * max(0, (width - _vlen(text)) // 2) + text


def _frame_top(title: str = "", width: int = BOX_W) -> str:
    total = width + 4
    if not title:
        return f"{ACCENT}╔{'═' * (total - 2)}╗{RESET}"
    parts = title.split(" › ")
    last = len(parts) - 1
    styled = f"{MUTED} › {RESET}".join(
        f"{BOLD}{ACCENT}{p}{RESET}" if i == last else f"{MUTED}{p}{RESET}"
        for i, p in enumerate(parts)
    )
    label_len = sum(len(p) for p in parts) + 3 * last + 2
    fill = max(0, total - 2 - label_len - 1)
    return f"{ACCENT}╔─{RESET} {styled} {ACCENT}{'═' * fill}╗{RESET}"


def _frame_bottom(width: int = BOX_W) -> str:
    return f"{ACCENT}╚{'═' * (width + 2)}╝{RESET}"


def _frame_sep(width: int = BOX_W) -> str:
    return f"{ACCENT}╠{'═' * (width + 2)}╣{RESET}"


def _frame_row(content: str = "", width: int = BOX_W) -> str:
    return f"{ACCENT}║{RESET} {_fit(content, width)} {ACCENT}║{RESET}"


def _build_box(
    title: str,
    rows: list[str | None],
    width: int = BOX_W,
    footer: str | None = None,
) -> list[str]:
    out = [_frame_top(title, width)]
    for row in rows:
        out.append(_frame_sep(width) if row is None else _frame_row(row, width))
    if footer is not None:
        out.append(_frame_sep(width))
        out.append(_frame_row(footer, width))
    out.append(_frame_bottom(width))
    return out


def _print_box(
    title: str,
    rows: list[str | None],
    width: int = BOX_W,
    footer: str | None = None,
) -> None:
    for line in _build_box(title, rows, width, footer):
        print(f"{INDENT}{line}")


def _vcenter(rows: list[str | None], total: int) -> list[str | None]:
    top = max(0, (total - len(rows)) // 2)
    bottom = max(0, total - len(rows) - top)
    return [""] * top + list(rows) + [""] * bottom


def _key(k: str) -> str:
    return f"{ACCENT}[{k}]{RESET}"


def _footer(target: str) -> str:
    """Render the navigation footer indicating Backspace and Ctrl+C shortcuts."""
    return f"{ACCENT}⌫ / Ctrl+C{RESET} {TEXT}Back{RESET}  {ACCENT}· {target}{RESET}"


def _notice(message: str, ok: bool = True) -> str:
    if ok:
        return f"{INDENT}{OK}✓{RESET} {TEXT}{message}{RESET}"
    return f"{INDENT}{WARN}!{RESET} {TEXT}{message}{RESET}"


def _menu_item(key: str, title: str, hint: str = "") -> list[str]:
    rows = [f"{_key(key)}  {BOLD}{TEXT}{title}{RESET}"]
    if hint:
        rows.append(f"     {MUTED}{hint}{RESET}")
    return rows


def _setting_row(key: str, label: str, value: str) -> str:
    return f"{_key(key)}  {TEXT}{_fit(label, 22)}{RESET}{value}"


def _toggle_chip(name: str, state: bool) -> str:
    if state:
        return f"{TEXT}{name}{RESET} {OK}●{RESET}"
    return f"{MUTED}{name} ○{RESET}"


def _pill(state: bool) -> str:
    return f"{OK}● ON {RESET}" if state else f"{MUTED}○ OFF{RESET}"


def _toggle_row(key: str, label: str, state: bool) -> str:
    return f"{_key(key)}  {TEXT}{_fit(label, 24)}{RESET}{_pill(state)}"


def banner() -> None:
    """Clear screen and render the ASCII art header."""
    clear()
    print()
    for sys_part, cast_part in TITLE_LINES:
        print(f"{INDENT}{ACCENT}{sys_part}{TEXT}{cast_part}{RESET}")
    print(f"{INDENT}{_center(f'{BOLD}{ACCENT}v{APP_VERSION}{RESET}', 58)}")
    print(f"{INDENT}{_center(f'{MUTED}Live PC telemetry on your phone  ·  by Hussein{RESET}', 58)}")
    print()


def prompt(label: str) -> str:
    """
    Read user menu input.
    - Pressing Backspace (on an empty buffer) or Ctrl+C immediately returns `BACK_SIGNAL`.
    - Pressing '0' when the buffer is empty is completely suppressed (neither shown nor typed).
    """
    sys.stdout.write(f"{INDENT}{ACCENT}›{RESET} {TEXT}{label}{RESET} {MUTED}:{RESET} ")
    sys.stdout.flush()

    if msvcrt is None:
        try:
            res = input().strip()
            return "" if res == "0" else res
        except (EOFError, KeyboardInterrupt):
            print()
            return BACK_SIGNAL

    buffer: list[str] = []
    while True:
        try:
            char = msvcrt.getwch()
        except KeyboardInterrupt:
            print()
            return BACK_SIGNAL
        except Exception:
            return BACK_SIGNAL

        # Ctrl+C (\x03) always goes back a step or exits
        if char == "\x03":
            print()
            return BACK_SIGNAL

        # Backspace (\b, \x08, \x7f) goes back when buffer is empty, or deletes last char
        if char in ("\b", "\x08", "\x7f"):
            if not buffer:
                print()
                return BACK_SIGNAL
            buffer.pop()
            sys.stdout.write("\b \b")
            sys.stdout.flush()
            continue

        # Ignore special/arrow key prefixes
        if char in ("\x00", "\xe0"):
            with suppress(Exception):
                msvcrt.getwch()
            continue

        # Enter key submits the current buffer
        if char in ("\r", "\n"):
            print()
            return "".join(buffer).strip()

        # Block '0' from being typed or displayed as an option
        if char == "0" and not buffer:
            continue

        # Ignore non-printable control characters
        if not char.isprintable():
            continue

        sys.stdout.write(char)
        sys.stdout.flush()
        buffer.append(char)


def _invalid(choice: str, last: int) -> str:
    rng = "1" if last == 1 else f"1-{last}"
    if not choice:
        return _notice(f"Type {rng} and press Enter, or press ⌫ / Ctrl+C to go back", ok=False)
    return _notice(f"Invalid option '{choice}': choose {rng}", ok=False)


def _info_screen(title: str, rows: list[str | None], parent: str) -> None:
    """Render a static information screen until the user presses Backspace or Ctrl+C."""
    notice = ""
    while not _FORCE_EXIT_EVENT.is_set():
        banner()
        _print_box(title, rows, footer=_footer(f"Return to {parent}"))
        print(notice)
        notice = ""
        if prompt("Back") == BACK_SIGNAL:
            return
        notice = _notice("Press ⌫ or Ctrl+C to go back", ok=False)


def _sensor_status() -> str:
    """Return a formatted status string indicating whether MSI Afterburner is running."""
    try:
        with READER_LOCK:
            up = bool(READER.afterburner_running())
    except Exception:
        up = False
    if up:
        return f"{OK}● Afterburner detected{RESET}"
    return f"{WARN}○ Afterburner not running{RESET}"


# --- QR & Server Status Cards -----------------------------------------------


def get_qr_lines(url: str) -> list[str]:
    """Generate ASCII QR code lines for `url`."""
    qr = qrcode.QRCode(border=2, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.StringIO()
    try:
        qr.print_ascii(out=buf, invert=True)
    except Exception:
        buf = io.StringIO()
        qr.print_ascii(out=buf)
    return [
        line.rstrip("\r\n") for line in buf.getvalue().splitlines() if line.strip()
    ]


def print_side_by_side_server_panel(url: str, cfg: dict[str, Any]) -> int:
    """Print the QR card (left) and server status card (right); return total width."""
    qr_lines = get_qr_lines(url)
    qr_width = max((len(line) for line in qr_lines), default=29)
    clock_label = "12-Hour" if cfg.get("clock_format") == "12h" else "24-Hour"
    info_w = 50

    def kv(label: str, value: str) -> str:
        return f"{MUTED}{_fit(label, 9)}{RESET} {value}"

    qr_rows: list[str | None] = [
        f"\033[97m{line.ljust(qr_width)}{RESET}" for line in qr_lines
    ]
    profile_str = (
        f"{TEXT}{cfg['layout'].upper()} · {cfg['theme'].upper()} · "
        f"{cfg['accent'].upper()} · {clock_label}{RESET}"
    )
    info_rows: list[str | None] = [
        f"{OK}● ONLINE{RESET}  {TEXT}Live Telemetry Streaming Server{RESET}",
        "",
        f"{TEXT}Scan the QR code with your phone, or open:{RESET}",
        "",
        f"{ACCENT}{url}{RESET}",
        "",
        None,
        kv("Interval", f"{TEXT}{BROADCAST_INTERVAL}s{RESET}"),
        kv("Profile", profile_str),
        kv("Network", f"{TEXT}Phone & PC on the same Wi-Fi{RESET}"),
        kv("Sensors", _sensor_status()),
        kv("Copy", f"{TEXT}Press {ACCENT}C{RESET}{TEXT} to copy the link{RESET}"),
        kv("Open", f"{TEXT}Press {ACCENT}O{RESET}{TEXT} to open it on this PC{RESET}"),
        kv("Hide", f"{TEXT}Press {ACCENT}H{RESET}{TEXT} to hide{RESET}"),
        kv("Back", f"{TEXT}Press {ACCENT}⌫ / Ctrl+C{RESET}{TEXT} to stop & return{RESET}"),
    ]

    total = max(len(qr_rows), len(info_rows))
    qr_box = _build_box("SCAN TO CONNECT", _vcenter(qr_rows, total), qr_width)
    info_box = _build_box("HOME › SERVER", _vcenter(info_rows, total), info_w)
    for left, right in zip(qr_box, info_box):
        print(f"{INDENT}{left}   {right}")
    return 3 + (qr_width + 4) + 3 + (info_w + 4)


def print_log_header(width: int) -> None:
    """Print the header table for live WebSocket connection logs."""
    title = "LIVE DEVICE CONNECTIONS"
    print()
    bar = "─" * max(4, width - 8 - len(title))
    print(f"{INDENT}{ACCENT}╭─{RESET} {BOLD}{TEXT}{title}{RESET} {ACCENT}{bar}╮{RESET}")
    cols = "TIME".ljust(10) + "EVENT".ljust(16) + "CLIENT IP".ljust(17) + "ACTIVE"
    print(f"{INDENT}{ACCENT}│{RESET} {cols}{RESET}")
    print(f"{INDENT}{ACCENT}├{'─' * max(4, width - 5)}┤{RESET}")
    print(f"{INDENT}{ACCENT}│{RESET} ◌ Waiting for devices to connect...{RESET}")


# --- Pickers & Menus --------------------------------------------------------


def _choose(
    title: str,
    options: list[tuple[str, str, str, str]],
    current: str,
    parent: str,
) -> str | None:
    """Render a numbered option picker. Returns the chosen value or None on Back."""
    notice = ""
    while not _FORCE_EXIT_EVENT.is_set():
        banner()
        rows: list[str | None] = []
        for i, (value, label, hint, color) in enumerate(options, 1):
            mark = f"{OK}●{RESET}" if value == current else f"{MUTED}○{RESET}"
            swatch = f"{color}■{RESET} " if color else "  "
            rows.append(
                f"{_key(str(i))}  {mark} {swatch}"
                f"{TEXT}{_fit(label, 11)}{RESET}  {MUTED}{hint}{RESET}"
            )
        rows.append(None)
        rows.append(f"{OK}●{RESET} {MUTED}marks your current choice{RESET}")
        _print_box(title, rows, footer=_footer(f"Return to {parent}"))
        print(notice)
        notice = ""
        sub = prompt("Select")
        if sub == BACK_SIGNAL:
            return None
        if sub.isdigit() and 1 <= int(sub) <= len(options):
            return options[int(sub) - 1][0]
        notice = _invalid(sub, len(options))
    return None


def toggles_menu(cfg: dict[str, Any]) -> None:
    """Interactive menu for toggling individual telemetry cards on or off."""
    labels = {
        "1": ("show_fps", "FPS counter"),
        "2": ("show_cpu", "CPU telemetry"),
        "3": ("show_gpu", "GPU telemetry"),
        "4": ("show_ram", "System RAM"),
    }
    notice = ""
    while not _FORCE_EXIT_EVENT.is_set():
        banner()
        rows: list[str | None] = [
            _toggle_row(k, f"Show {name}", cfg[key])
            for k, (key, name) in labels.items()
        ]
        rows.append(None)
        rows.append(f"{MUTED}Pick a number to switch it ON or OFF{RESET}")
        _print_box(
            "HOME › SETTINGS › DISPLAY TOGGLES",
            rows,
            footer=_footer("Return to Settings"),
        )
        print(notice)
        notice = ""
        sub = prompt("Toggle")
        if sub == BACK_SIGNAL:
            return
        if sub in labels:
            key, name = labels[sub]
            cfg[key] = not cfg[key]
            save_config(cfg)
            notice = _notice(f"{name} is now {'ON' if cfg[key] else 'OFF'}")
        else:
            notice = _invalid(sub, len(labels))


def settings_menu() -> None:
    """Interactive menu for configuring layout, theme, accent, clock, and toggles."""
    cfg = load_config()
    notice = ""
    while not _FORCE_EXIT_EVENT.is_set():
        banner()
        clock_label = "12-HOUR" if cfg.get("clock_format") == "12h" else "24-HOUR"
        accent_col = ACCENT_ANSI.get(cfg["accent"], TEXT)
        toggles = "  ".join(
            _toggle_chip(name, cfg[key])
            for name, key in (
                ("FPS", "show_fps"),
                ("CPU", "show_cpu"),
                ("GPU", "show_gpu"),
                ("RAM", "show_ram"),
            )
        )
        rows: list[str | None] = [
            _setting_row(
                "1", "Layout Orientation", f"{ACCENT}{cfg['layout'].upper()}{RESET}"
            ),
            _setting_row(
                "2", "Base Theme", f"{ACCENT}{cfg['theme'].upper()} MODE{RESET}"
            ),
            _setting_row(
                "3",
                "Accent Color",
                f"{accent_col}■{RESET} {ACCENT}{cfg['accent'].upper()}{RESET}",
            ),
            _setting_row("4", "Clock Format", f"{ACCENT}{clock_label}{RESET}"),
            _setting_row("5", "Display Toggles", toggles),
            _setting_row(
                "6", "Reset to Defaults", f"{MUTED}restore the original look{RESET}"
            ),
        ]
        _print_box("HOME › SETTINGS", rows, footer=_footer("Return to Main Menu"))
        print(notice)
        notice = ""

        choice = prompt("Select")
        if choice == BACK_SIGNAL:
            return
        elif choice == "1":
            value = _choose(
                "HOME › SETTINGS › LAYOUT",
                [
                    ("portrait", "Portrait", "Vertical phone view", ""),
                    ("landscape", "Landscape", "Horizontal widescreen view", ""),
                ],
                cfg["layout"],
                "Settings",
            )
            if value:
                cfg["layout"] = value
                save_config(cfg)
                notice = _notice(f"Layout set to {value.upper()}")
        elif choice == "2":
            value = _choose(
                "HOME › SETTINGS › THEME",
                [
                    ("dark", "Dark Mode", "#0b0514 · Deep cyber glass", ""),
                    ("white", "White Mode", "#ffffff · Clean frosted glass", ""),
                ],
                cfg["theme"],
                "Settings",
            )
            if value:
                cfg["theme"] = value
                save_config(cfg)
                notice = _notice(f"Theme set to {value.upper()} MODE")
        elif choice == "3":
            value = _choose(
                "HOME › SETTINGS › ACCENT COLOR",
                [
                    (name, name.title(), ACCENTS[name], ACCENT_ANSI[name])
                    for name in ("purple", "green", "yellow", "blue", "red")
                ],
                cfg["accent"],
                "Settings",
            )
            if value:
                cfg["accent"] = value
                save_config(cfg)
                apply_accent(value)
                notice = _notice(f"Accent set to {value.upper()}")
        elif choice == "4":
            value = _choose(
                "HOME › SETTINGS › CLOCK FORMAT",
                [
                    ("12h", "12-Hour", "e.g. 07:30 PM", ""),
                    ("24h", "24-Hour", "e.g. 19:30", ""),
                ],
                cfg["clock_format"],
                "Settings",
            )
            if value:
                cfg["clock_format"] = value
                save_config(cfg)
                label_str = "12-HOUR" if value == "12h" else "24-HOUR"
                notice = _notice(f"Clock set to {label_str}")
        elif choice == "5":
            toggles_menu(cfg)
        elif choice == "6":
            if _confirm(
                "HOME › SETTINGS › RESET",
                "Reset all settings to defaults?",
                "Layout, theme, accent, clock and display toggles.",
                "Yes, reset everything",
                "Keep my settings",
            ):
                cfg.update(dict(DEFAULT_CONFIG))
                save_config(cfg)
                apply_accent(cfg["accent"])
                notice = _notice("Settings restored to defaults")
        else:
            notice = _invalid(choice, 6)


def how_to_use() -> None:
    """Display the setup and usage guide for MSI Afterburner & RTSS."""
    rows: list[str | None] = [
        f"{_key('1')}  {BOLD}{TEXT}Install the tools{RESET}",
        f"     {MUTED}MSI Afterburner + RTSS (RivaTuner Server){RESET}",
        "",
        f"{_key('2')}  {BOLD}{TEXT}Keep both running while you play{RESET}",
        f"     {MUTED}No overlay needed: SysCast reads shared memory.{RESET}",
        "",
        f"{_key('3')}  {BOLD}{TEXT}Use the same Wi-Fi network{RESET}",
        f"     {MUTED}Connect your PC and phone/tablet to the same Wi-Fi.{RESET}",
        None,
        f"{WARN}! CRUCIAL STEP{RESET}",
        f"  {TEXT}Afterburner › Settings › Monitoring.{RESET}",
        f"  {TEXT}You {ERR}MUST{RESET}{TEXT} tick the box next to every counter you{RESET}",
        f"  {TEXT}want ({ACCENT}Framerate{RESET}{TEXT}, {ACCENT}Frametime{RESET}{TEXT}, ...). Ticking it{RESET}",
        f"  {TEXT}publishes that counter to shared memory.{RESET}",
    ]
    _info_screen("HOME › HOW TO USE", rows, "Main Menu")


def _drain_keys() -> None:
    """Flush any pending keystrokes from the Windows console input buffer."""
    if msvcrt is None:
        return
    while msvcrt.kbhit():
        msvcrt.getwch()


def _poll_keys() -> list[str]:
    """
    Poll non-blocking keystrokes while on the live server screen.
    - Backspace and Ctrl+C ('\\x03') emit `BACK_SIGNAL`.
    - '0' is ignored completely.
    """
    keys: list[str] = []
    if msvcrt is None:
        return keys
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\b", "\x08", "\x7f", "\x03"):
            keys.append(BACK_SIGNAL)
        elif ch in ("\x00", "\xe0"):
            with suppress(Exception):
                msvcrt.getwch()
        elif ch == "0":
            continue
        else:
            keys.append(ch.lower())
    return keys


def _copy_to_clipboard(text: str) -> bool:
    """Copy `text` to the Windows clipboard safely without shell=True."""
    try:
        subprocess.run(
            ["clip"],
            input=text.encode("utf-16le"),
            check=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:
        return False


def _log_note(message: str) -> None:
    """Print a status note inside the live server log stream."""
    with LOG_LOCK:
        print(f"{INDENT}{MUTED}│{RESET} {message}", flush=True)


def start_server() -> None:
    """Start the FastAPI/Uvicorn telemetry server and handle live hotkeys."""
    global SERVER_ACTIVE, PEAK_CLIENTS
    banner()

    cfg = load_config()

    try:
        port = find_free_port()
    except RuntimeError as exc:
        _info_screen(
            "HOME › SERVER",
            [
                f"{ERR}Could not start the server{RESET}",
                f"{MUTED}{str(exc)[:BOX_W]}{RESET}",
            ],
            "Main Menu",
        )
        return

    print(f"{INDENT}{MUTED}◌ Detecting network adapter...{RESET}")
    ip = true_local_ip()
    url = f"http://{ip}:{port}"

    banner()
    panel_w = print_side_by_side_server_panel(url, cfg)
    print()
    print(f"{INDENT}{_footer('Stop the server and return to Main Menu')}")
    print_log_header(panel_w)

    PEAK_CLIENTS = 0
    started = time.time()
    SERVER_ACTIVE = True
    _STOP_SERVER_EVENT.clear()
    _drain_keys()

    start_tray_icon(url, cfg.get("accent", "purple"))

    cfg_kwargs: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": port,
        "log_level": "error",
        "access_log": False,
        "lifespan": "on",
    }
    sig_params = inspect.signature(uvicorn.Config.__init__).parameters
    if "ws_per_message_deflate" in sig_params:
        cfg_kwargs["ws_per_message_deflate"] = False
    config = uvicorn.Config(app, **cfg_kwargs)
    server = _ThreadedServer(config)
    thread = threading.Thread(target=server.run, name="syscast-uvicorn", daemon=True)
    thread.start()

    try:
        while thread.is_alive() and not _STOP_SERVER_EVENT.is_set():
            try:
                keys = _poll_keys()
            except KeyboardInterrupt:
                break
            if BACK_SIGNAL in keys:
                break
            if "h" in keys:
                _log_note(f"{OK}✓{RESET} {TEXT}Hidden to system tray{RESET}")
                hide_console_to_tray(from_keyboard=True)
            if "c" in keys:
                if _copy_to_clipboard(url):
                    _log_note(f"{OK}✓{RESET} {TEXT}Link copied to clipboard{RESET}")
                else:
                    _log_note(f"{WARN}!{RESET} {TEXT}Could not copy the link{RESET}")
            if "o" in keys:
                webbrowser.open(url)
                _log_note(f"{OK}✓{RESET} {TEXT}Opened in your browser{RESET}")
            thread.join(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        SERVER_ACTIVE = False
        server.should_exit = True
        stop_tray_icon()
        show_console_from_tray()
        thread.join(timeout=4)
        with READER_LOCK:
            READER.close()
        _drain_keys()

    if _FORCE_EXIT_EVENT.is_set():
        return

    mins, secs = divmod(int(time.time() - started), 60)
    print(
        f"\n{INDENT}{MUTED}◼ Server stopped.{RESET}  "
        f"{TEXT}Session {mins}m {secs:02d}s{RESET} {MUTED}·{RESET} "
        f"{TEXT}peak {PEAK_CLIENTS} device(s){RESET}"
    )
    print(f"{INDENT}{MUTED}Returning to Main Menu...{RESET}")
    time.sleep(1.6)


def _confirm(
    title: str,
    question: str,
    detail: str,
    yes_label: str,
    stay_label: str,
) -> bool:
    """Render a confirmation screen: 1 = confirm, Backspace / Ctrl+C = cancel/back."""
    notice = ""
    while not _FORCE_EXIT_EVENT.is_set():
        banner()
        rows: list[str | None] = [
            f"{BOLD}{TEXT}{question}{RESET}",
            f"{MUTED}{detail}{RESET}",
            "",
            f"{_key('1')}  {BOLD}{TEXT}{yes_label}{RESET}",
        ]
        _print_box(title, rows, footer=_footer(stay_label))
        print(notice)
        notice = ""
        choice = prompt("Confirm")
        if choice == "1":
            return True
        if choice == BACK_SIGNAL:
            return False
        notice = _invalid(choice, 1)
    return False


def confirm_exit() -> bool:
    """Prompt the user to confirm exiting SysCast when pressing Backspace / Ctrl+C at the root menu."""
    return _confirm(
        "HOME › EXIT",
        "Exit SysCast?",
        "Open sessions are closed cleanly before quitting.",
        "Yes, exit SysCast",
        "Stay in SysCast",
    )


def main() -> None:
    """Application entry point and root CLI menu loop."""
    with suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, signal.default_int_handler)

    enable_ansi()
    cfg = load_config()
    save_config(cfg)
    apply_accent(cfg["accent"])
    notice = ""
    while not _FORCE_EXIT_EVENT.is_set():
        banner()
        rows: list[str | None] = []
        for key, title, hint in (
            ("1", "Start Server", "Launch the live stream & QR code"),
            ("2", "Settings", "Layout, theme, accent, clock & display toggles"),
            ("3", "How to Use", "MSI Afterburner setup & monitoring guide"),
        ):
            rows.extend(_menu_item(key, title, hint))
            rows.append("")
        rows.pop()
        cfg = load_config()
        clock_label = "12-HOUR" if cfg["clock_format"] == "12h" else "24-HOUR"
        swatch = ACCENT_ANSI.get(cfg["accent"], ACCENT)
        rows.append(None)
        rows.append(f"{MUTED}{_fit('Sensors', 9)}{RESET} {_sensor_status()}")
        rows.append(
            f"{MUTED}{_fit('Profile', 9)}{RESET} "
            f"{TEXT}{cfg['layout'].upper()} · {cfg['theme'].upper()} · "
            f"{swatch}■{RESET} {TEXT}{cfg['accent'].upper()} · {clock_label}{RESET}"
        )
        _print_box(
            "HOME › MAIN MENU",
            rows,
            footer=_footer("Exit SysCast"),
        )
        print(notice)
        notice = ""

        choice = prompt("Select")
        if choice == "1":
            start_server()
        elif choice == "2":
            settings_menu()
        elif choice == "3":
            how_to_use()
        elif choice == BACK_SIGNAL:
            if confirm_exit():
                break
        else:
            notice = _invalid(choice, 3)

    stop_tray_icon()
    show_console_from_tray()
    with READER_LOCK:
        READER.close()
    banner()
    print(
        f"{INDENT}{OK}✓{RESET} {TEXT}Goodbye!{RESET} "
        f"{MUTED}SysCast v{APP_VERSION} closed cleanly.{RESET}\n"
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        stop_tray_icon()
        show_console_from_tray()
        with READER_LOCK:
            READER.close()