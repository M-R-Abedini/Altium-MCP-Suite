# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A project PARAMETER is not a project OPTION, and the names collide.

``proj_set_parameter`` writes through ``DM_AddParameter``: a user-defined
parameter, the kind a title block references. Project OPTIONS are a
different mechanism entirely, reached through their own ``DM_`` accessors
and mostly read-only from a script.

WHY THIS EXISTS. Asking ``proj_set_parameter`` for ``hierarchy_mode``
creates a parameter called ``hierarchy_mode``, reads it back, verifies
it, and reports success, all correctly. The project's actual hierarchy
mode is untouched and still reads Flat. Nothing in the reply says the
two are unrelated, so the call looks like it worked, and the only way to
find out otherwise is to notice the setting did not change.

That is the same shape as the pin root-versus-connection trap in
``pin_hints``: a plausible reply to a question the caller did not mean to
ask. The fix is the same, say so on the reply.
"""
from __future__ import annotations

from typing import Optional

#: Names that are project OPTIONS rather than parameters, with what to do
#: instead. Read from Proj_GetOptions in Project.pas, which is the list of
#: options this bridge can currently see at all.
_OPTIONS = {
    "hierarchy_mode": (
        "the project's Net Identifier Scope, read by proj_get_options as "
        "hierarchy_mode (0 = Flat, 1 = GlobalScope). It is READ-ONLY here: "
        "Project.pas has no setter and no DM_SetHierarchyMode exists in "
        "the reference corpus. Change it in Project > Project Options > "
        "Options, or drive that dialog with app_click_menu plus "
        "app_set_dropdown"),
    "output_path": (
        "the project's output path, read by proj_get_options. Setting a "
        "parameter of this name does not move the output directory"),
    "project_name": (
        "read by proj_get_options and derived from the file name. A "
        "parameter of this name does not rename the project"),
}


def project_option_collision(name: str) -> Optional[str]:
    """The warning to attach when a parameter name is really an option."""
    key = str(name or "").strip().lower()
    meaning = _OPTIONS.get(key)
    if meaning is None:
        return None
    return (
        f"{name!r} was written as a project PARAMETER, which is what this "
        f"tool does, and the read-back confirms that parameter exists. It "
        f"is NOT the project setting of the same name. {name!r} is "
        f"{meaning}. If you wanted the parameter, ignore this."
    )
