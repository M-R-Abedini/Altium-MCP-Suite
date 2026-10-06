"""Clear a wedged Altium script executor, without a human in the loop.

A DelphiScript runtime error leaves the script PAUSED IN THE DEBUGGER. While
paused, every later RunScript silently does nothing, so recovery has to be
automatic or every later experiment is a false negative.

Primary mechanism: Altium registers "Stop Debugging" as the process
`EditScript:Stop`, and processes can be dispatched into the already-running
instance from the command line:

    X2.EXE -REditScript:Stop

That is the same channel the sandbox runner already uses to launch scripts
(`-RScriptingSystem:RunScript(...)`), so it needs no window focus and no
synthetic keystrokes - which is exactly why the previous SendKeys approach was
unreliable. Ctrl+F3 only reaches the debugger when the script editor happens to
be the active document, so it silently failed whenever a schematic or library
had focus, while still reporting "sent".

SendKeys is kept as a fallback, and success is VERIFIED by running a trivial
probe script rather than assumed - a wedge is defined by scripts silently doing
nothing, so the only honest test is whether a script produces output.

Usage:
    python dev/unwedge.py            # stop debugger, close dialogs, verify
    python dev/unwedge.py --no-verify
"""
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_window import find_windows, _title, _class

REPO = Path(__file__).resolve().parent.parent
EXCHANGE = Path("C:/Users/Public/altium_mcp")
PROBE_PRJ = REPO / "dev" / "sandbox" / "Sandbox.PrjScr"
PROBE_RESULT = EXCHANGE / "sandbox_result.json"
CONFIG = EXCHANGE / "altium_config.json"


def altium_exe():
    """Resolve X2.EXE the same way the MCP server and sandbox runner do."""
    if CONFIG.is_file():
        try:
            p = json.loads(CONFIG.read_text()).get("altium_exe_path", "")
            if p and Path(p).is_file():
                return p
        except (ValueError, OSError):
            pass
    for base in (r"C:\Program Files\Altium", r"C:\Program Files (x86)\Altium"):
        if Path(base).is_dir():
            for d in sorted(Path(base).iterdir(), reverse=True):
                exe = d / "X2.EXE"
                if exe.is_file():
                    return str(exe)
    return ""


def close_dialogs():
    """Close modal error dialogs. They block input and can mask the wedge."""
    def is_dialog(h):
        cls = _class(h)
        return cls == "#32770" or (
            cls == "TMessageForm" and _title(h) in
            ("Error", "Warning", "Information", "Confirm"))
    hits = find_windows(is_dialog)
    for h in hits:
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
    return len(hits)


def stop_via_process():
    """Dispatch EditScript:Stop into the running instance. No focus needed."""
    exe = altium_exe()
    if not exe:
        return False, "X2.EXE not found"
    try:
        subprocess.Popen(f'"{exe}" -REditScript:Stop', shell=True)
        time.sleep(1.5)
        return True, "EditScript:Stop dispatched"
    except OSError as e:
        return False, str(e)


def stop_via_keys():
    """Fallback: Ctrl+F3, after forcing a script document to the front."""
    hits = find_windows(lambda h: "Altium Designer" in _title(h))
    if not hits:
        return False, "no Altium window"
    hits.sort(key=lambda h: (0 if ".pas" in _title(h)
                             else 1 if ".PrjScr" in _title(h) else 2))
    hwnd = hits[0]
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms
$sig = @'
using System;
using System.Runtime.InteropServices;
public class FG {{
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}}
'@
Add-Type -TypeDefinition $sig
[FG]::ShowWindow([IntPtr]{hwnd}, 9) | Out-Null
[FG]::SetForegroundWindow([IntPtr]{hwnd}) | Out-Null
Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait('^{{F3}}')
Start-Sleep -Milliseconds 400
Write-Output 'sent'
"""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    return "sent" in r.stdout, (r.stdout or r.stderr).strip()[:160]


def executor_alive(timeout=45):
    """Prove the executor runs again by having it write a result file."""
    from sandbox_runner import inject, SANDBOX_PAS
    saved = SANDBOX_PAS.read_text(encoding="utf-8")
    try:
        inject("ResultText := '{\"probe\": \"alive\"}';")
        if PROBE_RESULT.exists():
            PROBE_RESULT.unlink()
        exe = altium_exe()
        subprocess.Popen(
            f'"{exe}" -RScriptingSystem:RunScript('
            f'ProjectName="{PROBE_PRJ}"^|ProcName="Sandbox>Run")', shell=True)
        start = time.time()
        while time.time() - start < timeout:
            if PROBE_RESULT.exists():
                return True
            time.sleep(0.5)
            close_dialogs()
        return False
    finally:
        SANDBOX_PAS.write_text(saved, encoding="utf-8")


def unwedge(verbose=True, verify=True):
    n = close_dialogs()
    if verbose and n:
        print(f"closed {n} dialog(s)")

    ok, info = stop_via_process()
    if verbose:
        print(f"EditScript:Stop -> {info}")
    close_dialogs()

    if not verify:
        return ok

    if executor_alive():
        if verbose:
            print("executor VERIFIED alive")
        return True

    if verbose:
        print("still wedged after process dispatch; trying Ctrl+F3")
    ok, info = stop_via_keys()
    if verbose:
        print(f"Ctrl+F3 -> {info}")
    close_dialogs()
    alive = executor_alive()
    if verbose:
        print("executor VERIFIED alive" if alive
              else "STILL WEDGED - restart Altium (sandbox_runner --auto-restart)")
    return alive


if __name__ == "__main__":
    sys.exit(0 if unwedge(verify="--no-verify" not in sys.argv) else 1)
