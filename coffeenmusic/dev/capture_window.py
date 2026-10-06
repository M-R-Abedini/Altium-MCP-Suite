"""Capture a window's pixels via PrintWindow (works when screen-scraping can't).

CopyFromScreen only captures what is actually rendered on the desktop, so it
returns blank images when the session is remote/disconnected or the window is
obscured. PrintWindow asks the window to render itself into a DC instead.

Usage:
    python dev/capture_window.py <hwnd> <output.png>
    python dev/capture_window.py --find "<title substring>" <output.png>
    python dev/capture_window.py --dialogs <output_prefix>   (all modal dialogs)
"""
import ctypes
import subprocess
import time
import sys
from ctypes import wintypes
from pathlib import Path

PW_RENDERFULLCONTENT = 2


def _title(hwnd):
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 64)
    return buf.value



def focus_and_refresh(hwnd, settle=1.2):
    """Bring a window truly to the front and force it to repaint.

    PrintWindow copies whatever the window last painted. If SetForegroundWindow
    silently fails - Windows refuses it when another process owns the
    foreground - or the window has not repainted since the document changed,
    the capture is a stale image of a different sheet. That produced several
    screenshots of the wrong schematic that looked plausible enough to trust.

    AttachThreadInput lends us the foreground thread's input state so the
    activation is allowed, then RDW_UPDATENOW forces a synchronous repaint
    before anything is copied.
    """
    u = ctypes.windll.user32
    SW_RESTORE, SW_SHOW = 9, 5
    RDW = 0x1 | 0x4 | 0x80 | 0x100     # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW

    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    else:
        u.ShowWindow(hwnd, SW_SHOW)

    fg = u.GetForegroundWindow()
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tgt = u.GetWindowThreadProcessId(hwnd, None)
    fgt = u.GetWindowThreadProcessId(fg, None) if fg else 0
    for t in (fgt, tgt):
        if t and t != cur:
            u.AttachThreadInput(t, cur, True)
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        for t in (fgt, tgt):
            if t and t != cur:
                u.AttachThreadInput(t, cur, False)

    time.sleep(settle)
    u.RedrawWindow(hwnd, None, None, RDW)
    time.sleep(0.4)
    return u.GetForegroundWindow() == hwnd


def capture(hwnd, out_path, flags=PW_RENDERFULLCONTENT, refresh=True):
    """Render a window to PNG using PrintWindow via System.Drawing."""
    if refresh:
        focus_and_refresh(hwnd)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None, f"window has no size ({w}x{h})"

    out_path = str(Path(out_path).resolve())
    ps = f"""
Add-Type -AssemblyName System.Drawing
$sig = @'
using System;
using System.Runtime.InteropServices;
public class PW {{
  [DllImport("user32.dll")]
  public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
}}
'@
Add-Type -TypeDefinition $sig
$bmp = New-Object System.Drawing.Bitmap {w}, {h}
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [PW]::PrintWindow([IntPtr]{hwnd}, $hdc, {flags})
$g.ReleaseHdc($hdc)
$g.Dispose()
$bmp.Save('{out_path}', [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output ("printwindow=" + $ok)
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    p = Path(out_path)
    if p.exists() and p.stat().st_size > 0:
        return out_path, r.stdout.strip()
    return None, (r.stderr or r.stdout).strip()[:300]


def find_windows(pred):
    user32 = ctypes.windll.user32
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd) and pred(hwnd):
            hits.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hits


def find_dialogs():
    def is_dialog(hwnd):
        cls = _class(hwnd)
        if cls == "#32770":
            return True
        return cls == "TMessageForm" and _title(hwnd) in (
            "Error", "Warning", "Information", "Confirm")

    return find_windows(is_dialog)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "--find":
        needle = args[1].lower()
        hits = find_windows(lambda h: needle in _title(h).lower())
        if not hits:
            print("no matching window")
            sys.exit(1)
        path, info = capture(hits[0], args[2])
        print(f"{_title(hits[0])!r} -> {path} ({info})")
    elif args[0] == "--dialogs":
        prefix = args[1] if len(args) > 1 else "dialog"
        hits = find_dialogs()
        if not hits:
            print("no dialogs visible")
            sys.exit(1)
        for i, h in enumerate(hits):
            path, info = capture(h, f"{prefix}_{i}.png")
            print(f"[{_class(h)}] {_title(h)!r} -> {path} ({info})")
    else:
        path, info = capture(int(args[0]), args[1])
        print(f"{path} ({info})")


def altium_window(doc_name=None):
    """The Altium frame actually showing a document - not just the first match.

    Altium keeps several top-level windows whose titles contain "Altium
    Designer" (project frames, hidden helpers). Picking find_windows(...)[0]
    returned one holding stale content, which silently produced screenshots of
    a completely different schematic while every other signal said the right
    document was focused. Prefer the frame whose title names the document, and
    otherwise the largest visible one.
    """
    u = ctypes.windll.user32
    cands = find_windows(lambda h: "Altium Designer" in _title(h) and u.IsWindowVisible(h))
    if not cands:
        return None

    if doc_name:
        for h in cands:
            if doc_name.lower() in _title(h).lower():
                return h

    def area(h):
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        return (r.right - r.left) * (r.bottom - r.top)

    return max(cands, key=area)


def capture_document(doc_name, out_path, zoom_script_runner=None):
    """THE reliable way to screenshot a schematic document. Verified recipe:

    1. Client.ShowDocument the target (the caller does this, typically via
       dev/sandbox_runner.py running a Show+Zoom script) - this switches the
       visible tab AND retitles the owning frame to the document name.
    2. Pick the frame whose TITLE CONTAINS THE DOCUMENT NAME - never [0].
       Altium keeps several top-level frames; grabbing the first match
       produced screenshots of a different document than every API said was
       current, repeatedly and convincingly.
    3. capture() forces foreground + synchronous repaint, then PrintWindow.

    Returns (png_path, frame_title) or (None, reason).
    Screenshots are a standing top priority: if this breaks, fixing it comes
    before whatever else is in flight, because it is the only check that sees
    text overlap - the defect class every geometric audit misses.
    """
    hits = find_windows(lambda h: doc_name.lower() in _title(h).lower()
                        and "Altium Designer" in _title(h))
    if not hits:
        return None, f"no Altium frame titled with {doc_name!r} - was it shown?"
    path, info = capture(hits[0], out_path)
    return path, _title(hits[0])
