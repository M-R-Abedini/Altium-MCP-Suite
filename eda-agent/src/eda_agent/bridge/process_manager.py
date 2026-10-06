# Modified 2026-10-06 by M-R-Abedini; see root MODIFICATIONS.md.
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium process detection and management."""

import logging
import sys
import time
import psutil
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("eda_agent.bridge.process")


# ---------------------------------------------------------------------------
# Native Windows process-name scan.
#
# psutil.process_iter -- even fetching only "name" -- measured ~2.2s on the
# target machine, and is_altium_running() is on the hot path of every bridge
# call. The Toolhelp snapshot API enumerates process names straight from the
# kernel snapshot in tens of milliseconds. Falls back to psutil if the
# native path is unavailable (non-Windows, ctypes failure).
# ---------------------------------------------------------------------------

_TH32CS_SNAPPROCESS = 0x00000002
_th_kernel32 = None
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes

        class _PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        _th_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _th_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        _th_kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD, wintypes.DWORD]
        _th_kernel32.Process32FirstW.restype = wintypes.BOOL
        _th_kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        _th_kernel32.Process32NextW.restype = wintypes.BOOL
        _th_kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        _th_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _th_invalid_handle = ctypes.c_void_p(-1).value
    except Exception as _e:  # pragma: no cover - platform dependent
        _th_kernel32 = None
        logger.debug("Toolhelp process scan unavailable: %s", _e)


def _scan_process_names_native(wanted_upper: set) -> Optional[bool]:
    """Return True/False if any wanted process name is running, via the
    Toolhelp snapshot. Returns None if the native path is unavailable."""
    if _th_kernel32 is None:
        return None
    try:
        snap = _th_kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _th_invalid_handle:
            return None
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _th_kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.upper() in wanted_upper:
                    return True
                ok = _th_kernel32.Process32NextW(snap, ctypes.byref(entry))
            return False
        finally:
            _th_kernel32.CloseHandle(snap)
    except Exception as e:
        logger.debug("native process scan failed: %s", e)
        return None


@dataclass
class AltiumProcessInfo:
    """Information about a running Altium process."""

    pid: int
    name: str
    exe_path: str
    version: Optional[str] = None
    cmdline: Optional[list[str]] = None


class AltiumProcessManager:
    """Manages detection and interaction with Altium Designer process."""

    PROCESS_NAMES = ["X2.exe", "DXP.exe"]  # Altium Designer executable names

    # is_altium_running() is on the hot path -- every bridge call hits it
    # (twice: once in _bridge_call, once inside send_command). A full
    # process_iter that fetches exe/cmdline opens every process on Windows
    # and costs seconds; cache the cheap name-only result for this long.
    _RUNNING_TTL = 3.0

    def __init__(self):
        self._running_cache: Optional[tuple[float, bool]] = None

    def find_altium_process(self) -> Optional[AltiumProcessInfo]:
        """Choose the editor, not a transient X2 command-line launcher."""
        candidates = []
        wanted = {n.upper() for n in self.PROCESS_NAMES}
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if (proc.info.get("name") or "").upper() in wanted:
                    candidates.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        pid = self._choose_editor_pid(candidates)
        if pid is None:
            return None
        try:
            proc = psutil.Process(pid)
            info = AltiumProcessInfo(pid=pid, name=proc.name(),
                                     exe_path=proc.exe(), cmdline=proc.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
        self._preferred_pid = pid
        return info

    def _choose_editor_pid(self, candidates) -> Optional[int]:
        if not candidates:
            return None
        if sys.platform != "win32":
            return candidates[0]
        try:
            import win32gui
            import win32process
            editors = set()
            def visit(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "TDocumentForm":
                    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                    if pid in candidates:
                        editors.add(pid)
                return True
            win32gui.EnumWindows(visit, None)
            foreground = win32gui.GetForegroundWindow()
            active = win32process.GetWindowThreadProcessId(foreground)[1] if foreground else None
            if active in editors:
                return active
            preferred = getattr(self, "_preferred_pid", None)
            if preferred in editors:
                return preferred
            if len(editors) == 1:
                return next(iter(editors))
            if len(editors) > 1:
                raise RuntimeError("Multiple Altium editors are open. Focus the intended editor before calling a UI tool.")
            return None
        except ImportError:
            logger.warning("Cannot inspect Altium editor windows; refusing to guess a process")
            return None

    def _scan_running(self) -> bool:
        """Is any Altium process running? Native Toolhelp scan first
        (~tens of ms); psutil name-only scan as the fallback.
        """
        wanted = {n.upper() for n in self.PROCESS_NAMES}
        native = _scan_process_names_native(wanted)
        if native is not None:
            return native
        # Fallback: psutil. Slower, but correct on non-Windows / if the
        # native path failed.
        try:
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").upper()
                if name in wanted:
                    return True
        except Exception as e:
            logger.debug("process scan failed: %s", e)
        return False

    def is_altium_running(self) -> bool:
        """Check if Altium Designer is running.

        Fast path: a name-only process scan, cached for _RUNNING_TTL
        seconds. Altium does not start/stop within a few seconds, so the
        cache is safe and removes seconds of latency from every bridge
        call.
        """
        now = time.monotonic()
        cached = self._running_cache
        if cached is not None and (now - cached[0]) < self._RUNNING_TTL:
            return cached[1]
        val = self._scan_running()
        self._running_cache = (now, val)
        return val

    def get_altium_info(self) -> Optional[AltiumProcessInfo]:
        """Get information about the running Altium process.

        Returns:
            AltiumProcessInfo if Altium is running, None otherwise.
        """
        return self.find_altium_process()

    def get_altium_pid(self) -> Optional[int]:
        """Get the PID of the running Altium process.

        Returns:
            PID if Altium is running, None otherwise.
        """
        process = self.find_altium_process()
        return process.pid if process else None

    def refresh(self) -> None:
        """Re-scan for the Altium process."""
        self.find_altium_process()
