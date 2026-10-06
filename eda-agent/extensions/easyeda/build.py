# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Produce the ``dist/index.js`` that ``extension.json`` points at.

EasyEDA's manifest names a compiled entry point, and their own SDK gets
there with TypeScript and a bundler. This extension needs neither: it is
one ES module with no imports, so the build is a copy, and this script
exists to make that claim checkable rather than to pretend at a
toolchain.

The check is the point. If ``main.js`` ever grows an import, a copy
stops being a valid bundle and this refuses instead of silently shipping
a file the editor cannot load.

Run: ``python extensions/easyeda/build.py``
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "main.js"
OUT_DIR = HERE / "dist"
OUT = OUT_DIR / "index.js"

#: An import or require means the file depends on something a copy will
#: not bring along. EasyEDA loads the entry point directly, so the
#: missing dependency would surface as a broken extension at load time.
_NEEDS_BUNDLER = re.compile(r"^\s*import\s|^\s*export\s+\*\s+from|require\(",
                            re.MULTILINE)


MANIFEST = HERE / "extension.json"
PACKAGE = HERE / "eda-agent-bridge.eext"


#: The line build_id() rewrites. Left as 'dev' in the repo so main.js
#: stays loadable on its own, which is how the harnesses import it.
_BUILD_ID_LINE = re.compile(r"^const BUILD_ID = '[^']*';$", re.MULTILINE)


def build_id(source: str) -> str:
    """A short hash of the source, ignoring the stamp itself.

    Computed over the source with the stamp normalised back to 'dev', so
    it does not depend on its own value and the Python side can
    recompute it from main.js alone.

    This exists because an extension that is installed, enabled and
    MONTHS OLD is indistinguishable from a current one in EasyEDA's
    Extensions Manager: same name, same uuid, and a size nobody thinks
    to check. Reporting the build over the wire turns "is the editor
    running this code?" from an inference into an answer.
    """
    import hashlib

    canonical = _BUILD_ID_LINE.sub("const BUILD_ID = 'dev';", source)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


#: What the last build shipped. Committed on purpose: the question it
#: answers is "did the version change since the code did?", which needs
#: memory across builds.
STAMP = Path(__file__).resolve().parent / "BUILD_STAMP.json"


def _refuse_unbumped_version(new_build: str) -> None:
    """Stop a build whose code changed but whose version did not.

    EasyEDA installs by VERSION. Re-importing a package whose version
    matches the installed one is a SILENT NO-OP: the dialog behaves
    normally, the extension keeps running the old code, and the only
    symptom is fixes that appear not to work. That cost a full live
    session before the build id made it visible, and the build id
    alone does not prevent it: a rebuilt package with an unchanged
    version is exactly the trap, and it looks perfectly healthy on
    disk.

    So the check belongs HERE rather than in a test: at build time the
    person can fix it in one edit, and nothing broken gets as far as
    an install.
    """
    if not STAMP.exists():
        return
    try:
        previous = json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return                                   # unreadable: do not block
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")

    if previous.get("build_id") == new_build:
        return                                   # code unchanged
    if previous.get("version") != version:
        return                                   # version already bumped

    raise SystemExit(
        f"main.js changed (build {previous.get('build_id')} -> "
        f"{new_build}) but extension.json is still version {version!r}. "
        f"EasyEDA installs by version, so re-importing this package "
        f"would be a silent no-op and the editor would keep running "
        f"the old code. Bump the version, then build again.")


def _write_stamp(new_build: str) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    STAMP.write_text(
        json.dumps({"version": manifest.get("version"),
                    "build_id": new_build}, indent=2) + "\n",
        encoding="utf-8")


def build() -> Path:
    source = SOURCE.read_text(encoding="utf-8")

    if not _BUILD_ID_LINE.search(source):
        raise SystemExit(
            f"{SOURCE.name} has no BUILD_ID line for this script to stamp. "
            f"Without it every install reports the same build and a stale "
            f"one cannot be told from a current one.")
    source = _BUILD_ID_LINE.sub(
        f"const BUILD_ID = '{build_id(source)}';", source)

    offenders = _NEEDS_BUNDLER.findall(source)
    if offenders:
        raise SystemExit(
            f"{SOURCE.name} now has {len(offenders)} import(s), so copying "
            f"it is no longer a valid build. Bundle it with EasyEDA's "
            f"pro-api-sdk and update this script, rather than shipping an "
            f"entry point with unresolved dependencies.")

    # EasyEDA does NOT import the entry as an ES module. Its loader
    # (api.js, the `lm` function the editor's own bug log names) wraps
    # the source in AsyncFunction("sandbox", code) and then resolves
    # registerFn names as `typeof connect === 'function'` inside that
    # body, or on `edaEsbuildExportName` for esbuild bundles. Inside a
    # function body, `export function connect()` is a SyntaxError: the
    # module dies at parse, the menu still shows (it comes from the
    # manifest), and every click is silently dead. EasyEDA's editor bug
    # log recorded 48 of exactly that SyntaxError before this was found.
    #
    # So the shipped entry is the same source with the export keywords
    # stripped, leaving top-level function declarations their loader can
    # see. main.js stays an ES module for the Node harnesses.
    source = re.sub(r"^export (?=(?:async )?function )", "", source,
                    flags=re.MULTILINE)
    if re.search(r"^export ", source, re.MULTILINE):
        raise SystemExit(
            "main.js has an export this build does not know how to "
            "strip (export const/let/default). The shipped entry must "
            "contain no export statements at all: EasyEDA parses it as "
            "a function body, where any of them is a SyntaxError.")

    _refuse_unbumped_version(build_id(SOURCE.read_text(encoding="utf-8")))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(source, encoding="utf-8")

    # Prove the artifact parses the way EASYEDA parses it, not the way
    # Node imports it. Constructing the AsyncFunction compiles the body
    # without running it, which is precisely their loader's first step
    # and the exact place the export bug detonated.
    check = subprocess.run(
        ["node", "-e",
         "const fs=require('fs');"
         "const AF=Object.getPrototypeOf(async()=>{}).constructor;"
         "new AF('sandbox', fs.readFileSync(process.argv[1],'utf8'));"
         "console.log('function-body parse ok');",
         str(OUT)],
        capture_output=True, text=True)
    if check.returncode != 0:
        raise SystemExit(
            f"dist/index.js does not parse as an AsyncFunction body, "
            f"which is how EasyEDA loads it:\n{check.stderr}")
    # Recorded only after the artifact is proven loadable, so a failed
    # build does not claim to have shipped a version.
    _write_stamp(build_id(SOURCE.read_text(encoding="utf-8")))
    return OUT


def package() -> Path:
    """Bundle the extension into the .eext file the installer accepts.

    EasyEDA Pro installs a FILE, not a folder: their own SDK emits a
    ``.eext`` from ``npm run build``. Pointing the installer at a
    directory simply fails, which is how this was found.

    INFERRED, and worth stating: ``.eext`` is treated here as a zip
    holding ``extension.json`` at the root with ``dist/index.js``
    beside it, which is what the documented layout and the
    ``entry: "./dist/index"`` path imply. The archive format is not
    stated outright in the docs I could reach. If the installer rejects
    this, the error text is the thing to report: the fix is the
    container, not the contents.
    """
    import zipfile

    build()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = manifest["entry"]                     # "./dist/index"
    inner = entry[2:] + ".js" if entry.startswith("./") else entry + ".js"

    if PACKAGE.exists():
        PACKAGE.unlink()
    with zipfile.ZipFile(PACKAGE, "w", zipfile.ZIP_DEFLATED) as zf:
        # Paths inside the archive are relative to its root, because
        # `entry` is relative to the manifest. Nesting the whole folder
        # one level deeper would make `./dist/index` resolve to nothing.
        zf.write(MANIFEST, "extension.json")
        zf.write(OUT, inner)
    return PACKAGE


if __name__ == "__main__":
    written = build()
    print(f"Wrote {written} ({written.stat().st_size} bytes)")
    archive = package()
    print(f"Wrote {archive} ({archive.stat().st_size} bytes)")
    print()
    print("Install in EasyEDA Pro: Settings > Extensions, then import")
    print(f"  {archive}")
    sys.exit(0)
