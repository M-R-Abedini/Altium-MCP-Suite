"""Read-only Windows Altium discovery and documented script registration plan.

Altium's Global Projects preference auto-loads script projects. Its persisted
registry format is not a supported cross-version API, so this helper does not
guess keys or enable a permanently running bridge at startup.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable

DOCUMENTATION = 'https://www.altium.com/documentation/altium-designer/scripting/running-scripts'


def registry_install_roots() -> list[Path]:
    """Read standard uninstall registrations in both registry views."""
    if sys.platform != 'win32':
        return []
    import winreg
    roots = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                                    0, winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as item:
                                name = winreg.QueryValueEx(item, 'DisplayName')[0]
                                if isinstance(name, str) and 'altium designer' in name.casefold():
                                    location = winreg.QueryValueEx(item, 'InstallLocation')[0]
                                    if isinstance(location, str) and location:
                                        roots.append(Path(location))
                        except OSError:
                            continue
            except OSError:
                continue
    return roots


def detect_installations(roots: Iterable[Path] | None = None) -> list[dict]:
    """Bounded discovery without starting any editor or traversing entire drives."""
    if roots is None:
        bases = [Path(os.environ.get(name, default)) / 'Altium' for name, default in
                 (('ProgramFiles', 'C:/Program Files'), ('ProgramFiles(x86)', 'C:/Program Files (x86)'))]
        roots = registry_install_roots()
        for base in bases:
            if base.is_dir():
                roots.extend(p for p in base.iterdir() if p.is_dir())
    output, seen = [], set()
    for root in roots:
        executable = Path(root) if Path(root).suffix.casefold() == '.exe' else Path(root) / 'X2.EXE'
        if not executable.is_file():
            continue
        identity = os.path.normcase(str(executable.resolve()))
        if identity in seen:
            continue
        seen.add(identity)
        match = re.search(r'(?:AD|Designer[ _-]*)(2[1-6])\b', str(executable.parent), re.I)
        version = match.group(1) if match else None
        output.append({'executable': str(executable.resolve()), 'version_hint': version,
                       'supported_version_hint': version is not None,
                       'version_verified': False})
    return sorted(output, key=lambda item: item['executable'].casefold())


def startup_plan(script_project: Path) -> dict:
    """Describe supported auto-load setup; loading differs from executing the loop."""
    path = Path(script_project).resolve()
    if not path.is_file() or path.suffix.casefold() != '.prjscr':
        raise ValueError('Select an existing .PrjScr script project')
    return {'script_project': str(path), 'automatic_registration_applied': False,
            'method': 'Altium Preferences > Scripting System > Global Projects > Install',
            'auto_loads_project': True, 'auto_executes_bridge': False,
            'native_execution': 'Existing coordinated MCP workers start the bridge on demand.',
            'reason': 'No vendor-documented cross-version registry writer; use the supported preferences UI.',
            'documentation': DOCUMENTATION}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script-project', type=Path)
    args = parser.parse_args()
    output = {'installations': detect_installations(), 'native_editor_started': False}
    if args.script_project:
        output['startup'] = startup_plan(args.script_project)
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
