"""Headless schematic render - THE reliable screenshot mechanism.

Window capture is a dead end in this environment: Altium only paints a view
that has actually been displayed, and every foreground-acquisition trick is
refused (AttachThreadInput, minimize/restore, TOPMOST, SendInput click,
Alt-key unlock - all measured failures). PrintWindow then returns stale or
black pixels while every API signal says the right document is current.

This route needs no window at all: Select All + Sch:Copy puts a properly
framed enhanced metafile on the clipboard (the same mechanism that pastes
schematics into Word), which is extracted and rasterized here. The result is
a crisp vector-derived render of the CURRENT document state.

Side effect: the user's clipboard is overwritten. Acceptable for autonomous
verification runs; don't run it while the user is mid copy-paste.

Usage:
    python dev/render_sheet.py <sheet-path-or-bare-name> <out.png> [width]
"""
import pathlib
import subprocess
import sys
import tempfile

DEV = pathlib.Path(__file__).resolve().parent

PAS = r"""
Obj3 := Client.GetDocumentByPath('{DOC}');
if (Obj3 = nil) then
    Obj3 := Client.OpenDocument('SCH', '{DOC}');
if (Obj3 = nil) then
begin
    ResultText := '{{"error": "cannot open {DOC}"}}';
    SandboxLog('ABORT nil');
end
else
begin
    Client.ShowDocument(Obj3);
    Sleep(1200);
    Obj1 := SchServer.GetCurrentSchDocument;
    SandboxLog('current: ' + Obj1.DocumentName);
    ResetParameters;
    AddStringParameter('Action', 'All');
    RunProcess('Sch:Select');
    ResetParameters;
    RunProcess('Sch:Copy');
    ResetParameters;
    RunProcess('Sch:DeSelect');
    SandboxLog('copied');
    ResultText := '{{"ok": true}}';
end;
"""

PS = r"""
Add-Type -AssemblyName System.Drawing
$sig = @'
using System;
using System.Runtime.InteropServices;
public class Clip {
  [DllImport("user32.dll")] public static extern bool OpenClipboard(IntPtr h);
  [DllImport("user32.dll")] public static extern bool CloseClipboard();
  [DllImport("user32.dll")] public static extern IntPtr GetClipboardData(uint fmt);
  [DllImport("user32.dll")] public static extern bool IsClipboardFormatAvailable(uint fmt);
  [DllImport("gdi32.dll")] public static extern IntPtr CopyEnhMetaFile(IntPtr h, string f);
  [DllImport("gdi32.dll")] public static extern bool DeleteEnhMetaFile(IntPtr h);
}
'@
Add-Type -TypeDefinition $sig
if (-not [Clip]::IsClipboardFormatAvailable(14)) { Write-Output 'NOEMF'; exit 1 }
[Clip]::OpenClipboard([IntPtr]::Zero) | Out-Null
$h = [Clip]::GetClipboardData(14)
$copy = [Clip]::CopyEnhMetaFile($h, '__EMF__')
[Clip]::DeleteEnhMetaFile($copy) | Out-Null
[Clip]::CloseClipboard() | Out-Null
$img = [System.Drawing.Image]::FromFile('__EMF__')
$w = __W__; $h2 = [int]($w * $img.Height / $img.Width)
$bmp = New-Object System.Drawing.Bitmap $w, $h2
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$g.SmoothingMode = 'AntiAlias'
$g.DrawImage($img, 0, 0, $w, $h2)
$g.Dispose()
$bmp.Save('__PNG__', [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose(); $img.Dispose()
Write-Output ('rendered ' + $w + 'x' + $h2)
"""


def render(doc, out_png, width=2400):
    pas = PAS.replace("{DOC}", str(doc))
    tmp = pathlib.Path(tempfile.gettempdir()) / "render_sheet.pas"
    tmp.write_text(pas, encoding="ascii")
    r = subprocess.run([sys.executable, "-X", "utf8", str(DEV / "sandbox_runner.py"),
                        str(tmp), "180"], capture_output=True, text=True, timeout=400)
    if "copied" not in r.stdout:
        return False, "copy failed:\n" + r.stdout[-800:]
    emf = pathlib.Path(tempfile.gettempdir()) / "render_sheet.emf"
    if emf.exists():
        emf.unlink()
    ps = (PS.replace("__EMF__", str(emf))
            .replace("__W__", str(width))
            .replace("__PNG__", str(out_png)))
    r2 = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                        capture_output=True, text=True, timeout=200)
    if "rendered" not in r2.stdout:
        return False, "rasterize failed:\n" + r2.stdout[-500:] + r2.stderr[-300:]
    return True, r2.stdout.strip()


if __name__ == "__main__":
    ok, info = render(sys.argv[1], sys.argv[2],
                      int(sys.argv[3]) if len(sys.argv) > 3 else 2400)
    print(info)
    sys.exit(0 if ok else 1)
