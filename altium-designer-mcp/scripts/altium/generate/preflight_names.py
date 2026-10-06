"""Pre-flight check for GenerateSamples.pas before spending an Altium run.

DelphiScript resolves every property name at COMPILE time, per interface. One
name that the interface does not carry aborts the whole script — every other
footprint and symbol in that run is lost, and `try/except` cannot help, because
nothing has run yet. Two of these cost a full round trip each: `Arc.IsSolid`
(an ISch_Arc is a stroked shape with no fill) and `Pie.Transparent`.

The script engine's identifier table is not enough on its own to prevent that.
It lives in `ScriptingSystem.dll` as UTF-16LE strings and is worth checking --
a name absent from it does not exist at all -- but it is GLOBAL: a hit only
says the name is real somewhere, not that it is real on the interface being
assigned. `IsSolid` is in the table and is genuine on a rectangle.

So this checks the other half, from the file itself: every `(interface,
property)` pair assigned in live code that has no precedent in the committed
version is unproven, and is a compile-abort risk. Discipline that follows from
that: introduce at most ONE unproven family per run, so a failure costs one
round trip and names one identifier.

The committed file is the baseline because a pair only reaches it after a run
that actually compiled -- commit the regenerated binaries and the `.pas`
together, and never commit an assignment Altium has not accepted.

    python scripts/altium/generate/preflight_names.py

Exits 0 when nothing is unproven, 1 otherwise -- a non-zero exit is a prompt to
check the list, not necessarily a defect. Comments (`{ }`, `(* *)`, `//`) are
stripped first, so a staged probe parked in a comment block is correctly
ignored.
"""

import subprocess
import sys

PAS = "scripts/altium/generate/GenerateSamples.pas"

# Scanned rather than matched with regexes: a lazy `\(\*.*?\*\)` over a whole
# file backtracks polynomially on repeated `(*`, and the equivalent name/type
# patterns do the same on long identifier runs. A single left-to-right pass has
# no such cliff and is easier to reason about.


def strip_comments(src: str) -> str:
    """Remove `(* *)`, `{ }` and `//` comments so staged probes are ignored."""
    out = []
    i, n = 0, len(src)
    while i < n:
        two = src[i : i + 2]
        if two == "(*":
            end = src.find("*)", i + 2)
            i = n if end == -1 else end + 2
        elif src[i] == "{":
            end = src.find("}", i + 1)
            i = n if end == -1 else end + 1
        elif two == "//":
            end = src.find("\n", i + 2)
            if end == -1:
                i = n
            else:
                out.append("\n")
                i = end + 1
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _identifier_before(text: str) -> str:
    """The trailing identifier of `text` (`'var Lin'` -> `'Lin'`), else ''."""
    token = text.strip().rsplit(None, 1)[-1] if text.strip() else ""
    return token if token.isidentifier() else ""


def _declarations(routine: str) -> dict:
    """-> {variable: interface_type} from a routine's signature and var block."""
    declared = {}
    # Fragments split on ';' and newlines isolate one declaration each, for both
    # `procedure Foo(Comp : ISch_Component; ...)` and a `var` block's lines.
    for line in routine.replace(";", "\n").splitlines():
        name, sep, rest = line.partition(":")
        if not sep:
            continue
        var = _identifier_before(name.lstrip("(").replace("(", " "))
        kind = rest.strip().split(None, 1)[0].rstrip("),").strip() if rest.strip() else ""
        if var and (kind.startswith("ISch_") or kind.startswith("IPCB_")):
            declared[var] = kind
    return declared


def _assignments(routine: str) -> list:
    """-> [(variable, property)] for every `Var.Prop :=` in `routine`.

    Statements are split on ';' as well as newlines: the `.pas` packs several
    assignments onto one line (`Pad.MidXSize := …;  Pad.MidYSize := …;`), and
    taking only the first per line silently drops the rest.
    """
    found = []
    for statement in routine.replace(";", "\n").splitlines():
        target, sep, _ = statement.partition(":=")
        if not sep:
            continue
        var, dot, prop = target.strip().rpartition(".")
        if dot and var.isidentifier() and prop.strip().isidentifier():
            found.append((var, prop.strip()))
    return found


def assigned_pairs(src: str) -> set:
    """-> {(interface_type, property)} for every live assignment in `src`."""
    pairs = set()
    # Split on routine boundaries so one routine's `var` block cannot be read
    # as declaring another's locals.
    routines, current = [], []
    for line in strip_comments(src).splitlines():
        if line.startswith(("procedure ", "function ")):
            routines.append("\n".join(current))
            current = []
        current.append(line)
    routines.append("\n".join(current))

    for routine in routines:
        declared = _declarations(routine)
        for var, prop in _assignments(routine):
            if var in declared:
                pairs.add((declared[var], prop))
    return pairs


def main() -> int:
    head = subprocess.run(
        ["git", "show", f"HEAD:{PAS}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    with open(PAS, encoding="utf-8") as handle:
        working = handle.read()

    proven = assigned_pairs(head)
    unproven = sorted(assigned_pairs(working) - proven)

    if not unproven:
        print(f"OK: every live assignment has a precedent ({len(proven)} known pairs).")
        return 0

    families = {iface for iface, _ in unproven}
    print(f"{len(unproven)} unproven (interface, property) pair(s) across "
          f"{len(families)} interface(s) -- each risks aborting the run:\n")
    for iface, prop in unproven:
        elsewhere = sorted({i for i, p in proven if p == prop})
        note = (
            f"proven on {', '.join(elsewhere)} -- but NOT on this interface"
            if elsewhere
            else "name never assigned in this file"
        )
        print(f"    {iface:24}.{prop:<20} {note}")

    if len(families) > 1:
        print(
            f"\nMore than one interface is unproven. A failure would not say which "
            f"is at fault; stage all but one behind a `(* *)` comment first."
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
