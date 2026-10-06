# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fast offline health checks.

These never touch Altium. Use them as a quick precheck in scripts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from eda_agent.config import (
    WORKSPACE_POINTER_FILE,
    workspace_pointer_file,
    get_config,
)
from eda_agent.diag.checks import Check, Severity, Status


def _check_workspace_dir() -> Check:
    cfg = get_config()
    ws = cfg.workspace_dir
    if not ws.exists():
        return Check(
            name="workspace dir exists",
            status=Status.FAIL,
            message=f"{ws} does not exist",
            fix=(
                "Run `eda-agent install-scripts` to create it, or set "
                "EDA_AGENT_WORKSPACE to an existing path."
            ),
        )
    if not os.access(ws, os.W_OK):
        return Check(
            name="workspace dir writable",
            status=Status.FAIL,
            message=f"{ws} not writable",
            fix="Check the directory permissions.",
        )
    return Check(
        name="workspace dir",
        status=Status.PASS,
        message=str(ws),
    )


def _check_pointer_file() -> Check:
    # Resolve through the same helper the writer uses so an
    # EDA_AGENT_POINTER_FILE override is diagnosed, not the real file.
    pointer = workspace_pointer_file()
    if not pointer.exists():
        return Check(
            name="workspace pointer file",
            status=Status.FAIL,
            message=f"{pointer} missing",
            fix=(
                "Run `eda-agent install-scripts` to create the pointer "
                "file. DelphiScript reads it to find the IPC workspace."
            ),
        )
    try:
        contents = pointer.read_text(encoding="ascii").strip()
    except OSError as exc:
        return Check(
            name="workspace pointer file readable",
            status=Status.FAIL,
            message=f"could not read pointer file: {exc}",
            fix="Check file permissions or recreate via `install-scripts`.",
        )

    cfg_path = str(get_config().workspace_dir)
    pointer_path = contents.rstrip("\\")
    cfg_path_norm = cfg_path.rstrip("\\")
    if Path(pointer_path).resolve() != Path(cfg_path_norm).resolve():
        return Check(
            name="workspace pointer matches config",
            status=Status.FAIL,
            message=(
                f"pointer={pointer_path!r} but config={cfg_path_norm!r}, "
                "Python and Pascal will write to different directories"
            ),
            fix="Re-run `eda-agent install-scripts` to refresh the pointer.",
        )
    return Check(
        name="workspace pointer",
        status=Status.PASS,
        message=str(pointer),
    )


def _check_bundled_scripts() -> Check:
    from eda_agent.cli import get_bundled_scripts_path

    scripts = get_bundled_scripts_path()
    prj = scripts / "Altium_API.PrjScr"
    if not prj.exists():
        return Check(
            name="bundled DelphiScript project",
            status=Status.FAIL,
            message=f"{prj} not found",
            fix="Reinstall the package: `pip install --force-reinstall eda-agent`.",
        )
    return Check(
        name="bundled DelphiScript project",
        status=Status.PASS,
        message=str(prj),
    )


def _script_version(main_pas: Path) -> str | None:
    """SCRIPT_VERSION out of a Main.pas, or None if unreadable."""
    try:
        text = main_pas.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"SCRIPT_VERSION\s*=\s*'([^']+)'", text)
    return match.group(1) if match else None


def _pas_differences(bundled_dir: Path, deployed_dir: Path) -> list[str]:
    """Names of .pas files whose deployed content differs from bundled.

    Compared by CONTENT rather than by SCRIPT_VERSION, because the
    version is bumped by hand and the case that produces a stale deploy
    is precisely the case where somebody forgot. A version-only check
    passes whenever the two copies carry the same string, which is the
    default state after an edit, so it cannot detect its own motivating
    scenario.

    Line endings are normalised: the install copies files between
    directories and a CRLF difference is not a code difference.
    """
    differing = []
    for source in sorted(bundled_dir.glob("*.pas")):
        target = deployed_dir / source.name
        if not target.is_file():
            differing.append(source.name)
            continue
        try:
            a = source.read_bytes().replace(b"\r\n", b"\n")
            b = target.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            differing.append(source.name)
            continue
        if a != b:
            differing.append(source.name)
    return differing


def _check_deployed_scripts_current() -> Check:
    """The deployed copy must be the bundled one.

    Altium compiles whatever sits in the install directory, not what is
    in this checkout. So a Pascal fix that was never reinstalled
    reproduces exactly as before, and the only other place that shows is
    ``app_ping``'s version, which needs Altium running to read.

    That makes a stale deploy the one live-session problem worth
    catching offline: it costs a whole session to diagnose from the
    inside, and one file read to spot from the outside.

    Checked two ways, because the version alone is not enough. A
    mismatched SCRIPT_VERSION is the loud case. The quiet one is an edit
    that never bumped it, leaving both copies claiming the same build
    with different code in them, so the files are also compared by
    content.

    WARN rather than FAIL. Nothing offline is affected, and a developer
    who has never installed the scripts is not in a broken state.
    """
    from ..cli import get_bundled_scripts_path, get_default_scripts_dest

    bundled = Path(get_bundled_scripts_path()) / "Main.pas"
    deployed = get_default_scripts_dest() / "Main.pas"

    if not deployed.exists():
        return Check(
            name="deployed scripts",
            status=Status.SKIP,
            message=f"none installed at {deployed.parent}",
            fix="Run `eda-agent install-scripts` before using Altium.",
        )

    want = _script_version(bundled)
    have = _script_version(deployed)
    if want is None or have is None:
        return Check(
            name="deployed scripts",
            status=Status.WARN,
            message="could not read SCRIPT_VERSION from both copies",
            fix="Check that Main.pas is readable in both locations.",
        )
    if want != have:
        return Check(
            name="deployed scripts current",
            status=Status.WARN,
            message=f"installed {have}, this tree has {want}",
            fix=(
                "Run `python -m eda_agent.server install-scripts --force`, "
                "then reload the script project in Altium. Until then "
                "Altium runs the older code and a fixed bug will "
                "reproduce."
            ),
        )

    # Matching versions are not matching code. The version is bumped by
    # hand, so an edit that skipped the bump leaves both copies claiming
    # the same build while the deployed one is behind.
    differing = _pas_differences(bundled.parent, deployed.parent)
    if differing:
        listed = ", ".join(differing[:6])
        if len(differing) > 6:
            listed += f", and {len(differing) - 6} more"
        return Check(
            name="deployed scripts current",
            status=Status.WARN,
            message=(
                f"both copies say {have} but the code differs: {listed}"),
            fix=(
                "SCRIPT_VERSION was not bumped for these edits, so the "
                "version cannot tell the two builds apart and app_ping "
                "will report a match while Altium runs the older code. "
                "Bump SCRIPT_VERSION in scripts/altium/Main.pas, run "
                "`python -m eda_agent.server install-scripts --force`, "
                "then reload the script project in Altium."
            ),
        )
    return Check(
        name="deployed scripts current",
        status=Status.PASS,
        message=f"{have}, matches this tree",
    )


def _check_bridge_constructable() -> Check:
    """Construct the bridge object without sending a request.

    A failure here usually means a missing dependency (psutil, pywin32).
    """
    try:
        from eda_agent.bridge import get_bridge
        get_bridge()
    except Exception as exc:
        return Check(
            name="bridge constructable",
            status=Status.FAIL,
            message=f"bridge import/construct failed: {exc}",
            fix="Reinstall with `pip install --force-reinstall eda-agent`.",
        )
    return Check(name="bridge constructable", status=Status.PASS)


_STAMPED_BUILD_ID = re.compile(r"^const BUILD_ID = '([^']*)';$", re.MULTILINE)


def _stamped_build_id(built: Path) -> str | None:
    """The BUILD_ID a built extension carries, or None if unreadable."""
    try:
        match = _STAMPED_BUILD_ID.search(
            built.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return match.group(1) if match else None


def _easyeda_build_id(source: Path) -> str | None:
    """What the build WOULD stamp for this source.

    The hash is imported from the build script rather than reimplemented
    here. Two copies of a hash definition drift, and the drift would show
    up as a staleness warning nobody could clear by rebuilding.
    """
    build_script = source.parent / "build.py"
    if not build_script.is_file():
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_easyeda_build_for_health", build_script)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_id(source.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001 - diagnostic only
        return None


def _check_easyeda_extension_built() -> Check:
    """Is the editor half of the EasyEDA bridge ready to install?

    Reported even on an Altium install, because the failure it prevents
    is silent and confusing: EasyEDA DIALS OUT to this server, so a
    missing extension looks exactly like a server that is not listening.
    Someone debugging that will check ports and firewalls for a while
    before suspecting the half that lives inside the editor.
    """
    source = (Path(__file__).resolve().parents[3]
              / "extensions" / "easyeda" / "main.js")
    built = source.parent / "dist" / "index.js"

    if not source.is_file():
        # Not an error on a source install that omits the extension.
        return Check(
            name="easyeda extension",
            status=Status.SKIP,
            message="no extensions/easyeda/main.js in this install",
        )

    if not built.is_file():
        return Check(
            name="easyeda extension",
            status=Status.WARN,
            severity=Severity.MINOR,
            message="the extension is not built, so EasyEDA has nothing "
                    "to install and will never connect",
            fix="Run `python extensions/easyeda/build.py`, then install "
                "the folder from EasyEDA Pro: Settings > Extensions.",
        )

    # Compared by BUILD_ID, not by bytes. The build is not a copy: it
    # stamps the id and strips `export`, so the two files ALWAYS differ
    # and a byte comparison warns even seconds after a successful build.
    # BUILD_ID exists for exactly this question, being a hash of the
    # source with its own id line canonicalised out.
    want = _easyeda_build_id(source)
    have = _stamped_build_id(built)
    if want is None or have is None:
        return Check(
            name="easyeda extension",
            status=Status.WARN,
            severity=Severity.MINOR,
            message="could not read BUILD_ID from the extension source or "
                    "its build, so staleness cannot be determined",
            fix="Rebuild with `python extensions/easyeda/build.py`.",
        )
    if want != have:
        return Check(
            name="easyeda extension",
            status=Status.WARN,
            severity=Severity.MINOR,
            message=f"the built extension is stale: it carries {have} and "
                    f"main.js now hashes to {want}, so EasyEDA is running "
                    f"code that no longer matches this server",
            fix="Rebuild with `python extensions/easyeda/build.py` and "
                "reload the extension in EasyEDA.",
        )

    return Check(name="easyeda extension", status=Status.PASS,
                 message=f"built from main.js, {have}")


def run_health_checks() -> list[Check]:
    """Order matters, earlier failures often explain later ones."""
    return [
        _check_workspace_dir(),
        _check_pointer_file(),
        _check_bundled_scripts(),
        _check_deployed_scripts_current(),
        _check_bridge_constructable(),
        _check_easyeda_extension_built(),
    ]
