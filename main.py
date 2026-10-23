#!/usr/bin/env python3
"""SysCast v1.0.0 — PC hardware monitor streamed to a mobile browser."""

from __future__ import annotations
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
        
# --------------------------------------------------------------------------
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


app.mount("/static", StaticFiles(directory="web"), name="static")

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the single-page telemetry dashboard."""
    cfg = load_config()
    body_cls = (
        f"{cfg['layout']} layout-{cfg['layout']} "
        f"theme-{cfg['theme']} accent-{cfg['accent']}"
    )
    
    with open("web/index.html", "r", encoding="utf-8") as file:
        html = file.read()
        
    html = html.replace("__INITIAL_BODY_CLASS__", body_cls)
    html = html.replace("__INITIAL_CLOCK_FORMAT__", cfg.get("clock_format", "12h"))
    
    return HTMLResponse(content=html)


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