"""Sheet7 rebuild (USB-C to UART bridge) - layout v2, overlap-driven.

Circuit source: the reference sheet's NETLIST only (dev/netlist.py extract).
Layout: mine. v1 matched electrically but failed the overlap audit with 28
collisions; v2 restructures:

  - DP run moved to y=5100 (was 100 mil above DN at 4500): 100-mil-apart
    parallel runs make it geometrically impossible to hang a grounded cap or
    place labels without collisions. DN stays at 4400; DP descends to U5.19
    at the far end.
  - Shield R20/C18 moved from the crowded top-left to open space BELOW the
    shield bus (rail y=2200), where both get the side clearance their
    harvested text style needs (vertical R text goes right, vertical C text
    goes left - pair them R-left, C-right).
  - Series R18/R19 staggered along their runs so corner text never lands on
    the other's body; labels placed on measured-clear wire stretches.
  - R22 riser moved off R21's text column.

Emits circuit_spec.txt for the bridge and calls build via X2 directly (the
MCP tool gains a ports param only after server restart).
"""
import json
import pathlib
import subprocess
import sys
import time

sp = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(sp))
EX = pathlib.Path("C:/Users/Public/altium_mcp")

db = json.load(open(sp / "sheet7_parts.json"))

PLACE = [  # desig, pn, x, y, orient, mirror
    ("J2", "6112-0120", 2600, 2600, 0, 1),
    ("D1", "5132-0006", 3200, 3400, 0, 0),
    ("U5", "4413-0016", 6300, 3900, 0, 0),
    ("Y1", "4148-0001", 5100, 3300, 0, 0),
    ("C13", "2134-0024", 6000, 5400, 1, 0),
    ("C14", "2164-0011", 5300, 5400, 1, 0),
    ("C15", "2134-0024", 8700, 4500, 1, 0),
    ("C16", "2104-0017", 4300, 4100, 1, 0),
    ("C17", "2104-0017", 4100, 4800, 1, 0),
    ("C18", "2134-0024", 1000, 1900, 1, 0),
    ("R18", "1112-0144", 4200, 5000, 0, 0),
    ("R19", "1112-0144", 4600, 4300, 0, 0),
    ("R20", "1112-0250", 300, 1600, 1, 0),
    ("R21", "1112-0249", 2900, 2000, 1, 0),
    ("R22", "1112-0249", 4000, 1200, 1, 0),
    ("TP1", "9998-0003", 200, 5400, 0, 0),
]

WIRES = [
    # --- VBUS: A4/A9/B4/B9 (x1100, y4500..4200) -> bus x600 -> up to TP1
    [1100, 4500, 600, 4500], [1100, 4400, 600, 4400],
    [1100, 4300, 600, 4300], [1100, 4200, 600, 4200],
    [600, 4200, 600, 4300], [600, 4300, 600, 4400], [600, 4400, 600, 4500],
    [600, 4500, 600, 5000], [600, 5000, 600, 5400], [600, 5400, 500, 5400],
    # --- shield: S1..S4 + A1/A12/B1/B12/M1/M2 -> bus x800 -> down to rail 2200
    [1100, 2600, 800, 2600], [1100, 2700, 800, 2700],
    [1100, 2800, 800, 2800], [1100, 2900, 800, 2900],
    [1100, 3100, 800, 3100], [1100, 3200, 800, 3200],
    [1100, 3300, 800, 3300], [1100, 3400, 800, 3400],
    [1100, 3500, 800, 3500], [1100, 3600, 800, 3600],
    [800, 3600, 800, 3500], [800, 3500, 800, 3400], [800, 3400, 800, 3300],
    [800, 3300, 800, 3200], [800, 3200, 800, 3100], [800, 3100, 800, 2900],
    [800, 2900, 800, 2800], [800, 2800, 800, 2700], [800, 2700, 800, 2600],
    [800, 2600, 800, 2200], [800, 2200, 900, 2200],   # to C18 top pin (end)
    [800, 2200, 600, 2200], [600, 2200, 200, 2200],   # to R20 top pin (end)
    # --- C18 / R20 grounds
    [900, 1900, 900, 1800], [200, 1600, 200, 1500],
    # --- CC nets: A5 (2600,2700) -> R22 ; B5 (2600,2600) -> R21
    [2600, 2600, 2800, 2600], [2800, 2000, 2800, 1900],
    [2600, 2700, 3200, 2700], [3200, 2700, 3200, 1800],
    [3200, 1800, 3900, 1800], [3900, 1200, 3900, 1100],
    # --- D1 ESD grounds
    [3400, 3100, 3400, 3000], [3800, 3100, 3800, 3000],
    # --- USB DN: DN2 (2600,4100) + DN1 (2600,4400) -> run y4400 -> R19 -> U5.18
    [2600, 4100, 3000, 4100], [3000, 4100, 3000, 4400],
    [2600, 4400, 3000, 4400],
    [3000, 4400, 3500, 4400], [3500, 4400, 3600, 4400],
    [3600, 4400, 4200, 4400], [4200, 4400, 4600, 4400],
    [5200, 4400, 6300, 4400],
    # --- USB DP: DP2 (2600,4200) + DP1 (2600,4500) -> riser x2700 -> run y5100
    [2600, 4200, 2700, 4200], [2600, 4500, 2700, 4500],
    [2700, 4200, 2700, 4500], [2700, 4500, 2700, 5100],
    [2700, 5100, 3300, 5100], [3300, 5100, 3400, 5100],
    [3400, 5100, 4000, 5100], [4000, 5100, 4200, 5100],
    # after R18: jog down to 4700 (clear of C13/C14 port labels AND of the
    # RXD row at 4900), across, up-down to U5.19
    [4800, 5100, 5100, 5100], [5100, 5100, 5100, 4700],
    [5100, 4700, 6200, 4700], [6200, 4700, 6200, 4500],
    [6200, 4500, 6300, 4500],
    # --- D1 line stubs: DP pins x3300/3400 up to 5100; DN pins x3500/3600 to 4400
    [3300, 4100, 3300, 5100], [3400, 4100, 3400, 5100],
    [3500, 4100, 3500, 4400], [3600, 4100, 3600, 4400],
    # --- C16 (DN, direct pin tap at 4200,4400) gnd
    [4200, 4100, 4200, 4000],
    # --- C17 (DP, direct pin tap at 4000,5100) gnd
    [4000, 4800, 4000, 4700],
    # --- XTAL
    [6300, 4100, 6100, 4100], [6100, 4100, 6100, 3600],
    [6300, 4200, 5000, 4200], [5000, 4200, 5000, 3600], [5000, 3600, 5100, 3600],
    [5600, 3200, 5600, 3100],
    # --- U5 ground: straight down from the pin, clear of the XTAL-IN label
    [6300, 3900, 6300, 3700],
    # --- C15 / RESET-N ; TXD
    [8100, 4800, 8600, 4800], [8600, 4500, 8600, 4400],
    [8600, 4400, 8900, 4400],
    [8100, 4900, 9000, 4900],
    # RXD (U5.12, left side) out to the UART-MCU2USB port
    [6300, 4900, 5000, 4900],
    # --- 3V3: rail y5700; U5.4 riser x6100; U5.1 riser x8300; U5.17 riser x8500
    [6300, 5300, 6100, 5300], [6100, 5300, 6100, 5700],
    [8100, 5300, 8300, 5300], [8300, 5300, 8300, 5700],
    [8100, 5100, 8500, 5100], [8500, 5100, 8500, 5700],
    [5200, 5700, 5900, 5700], [5900, 5700, 6100, 5700],
    [6100, 5700, 8300, 5700], [8300, 5700, 8500, 5700],
    # C13/C14 grounds
    [5900, 5400, 5900, 5300], [5200, 5400, 5200, 5300],
]

JUNCTIONS = [
    (600, 4300), (600, 4400), (600, 4500),
    (800, 2600), (800, 2700), (800, 2800), (800, 2900),
    (800, 3100), (800, 3200), (800, 3300), (800, 3400), (800, 3500),
    (800, 2200), (600, 2200),
    (3000, 4400), (3500, 4400), (3600, 4400), (4200, 4400),
    (2700, 4500),
    (3300, 5100), (3400, 5100), (4000, 5100),
    (5900, 5700), (6100, 5700), (8300, 5700),
]

LABELS = [
    (600, 4700, "5V0-VBUS"),
    (2750, 4400, "USB-DN"),
    (2800, 5100, "USB-DP"),
    (5500, 4400, "USB-DN-R"),
    (5300, 4700, "USB-DP-R"),
    (6100, 3900, "XTAL-IN"),
    (5400, 4200, "XTAL-OUT"),
]

GND_PORTS = [
    (900, 1800), (200, 1500),          # C18, R20
    (2800, 1900), (3900, 1100),        # R21, R22
    (3400, 3000), (3800, 3000),        # D1
    (4200, 4000),                      # C16
    (4000, 4700),                      # C17
    (5600, 3100),                      # Y1
    (6300, 3700),                      # U5
    (5900, 5300), (5200, 5300),        # C13, C14
]

NOTES = [
    (300, 6200, "USB-C to UART bridge - MCP2200, rebuilt from the reference circuit by connectivity only"),
    (300, 6050, "Shield net (S1-S4, A1/A12/B1/B12/M1/M2) ties to GND only through C18 || R20"),
]


def emit_spec():
    lines = []
    for desig, pn, x, y, o, m in PLACE:
        e = db[pn]
        lines.append(f"PART|{desig}|{e['lib']}|{e['symbol']}|{pn}|{x}|{y}|{o}|{m}")
        lines.append(f"COMMENT|{e['symbol']}")
        if e["footprint"]:
            lines.append(f"FOOTPRINT|{e['footprint']}")
        if e["desc"]:
            lines.append(f"DESCRIPTION|{e['desc']}")
        for k, v in e["params"].items():
            lines.append(f"PARAM|{k}|{v}")
    for w in WIRES:
        lines.append("WIRE|" + "|".join(str(v) for v in w))
    for x, y in JUNCTIONS:
        lines.append(f"JUNCTION|{x}|{y}")
    for x, y, t in LABELS:
        lines.append(f"NETLABEL|{x}|{y}|0|{t}")
    lines.append("POWER|8500|5700|1|2|3V3|1")
    lines.append("SPORT|8900|4400|RESET-N|1|0|900")
    lines.append("SPORT|9000|4900|UART-USB2MCU|1|0|900")
    lines.append("SPORT|4100|4900|UART-MCU2USB|2|2|900")
    for x, y in GND_PORTS:
        lines.append(f"POWER|{x}|{y}|3|5|GND|0")
    for x, y, t in NOTES:
        lines.append(f"NOTE|{x}|{y}|{t}")
    (EX / "circuit_spec.txt").write_text("\n".join(lines) + "\n",
                                         encoding="cp1252", errors="replace")
    return len(lines)


def run_bridge():
    from unwedge import altium_exe
    exe = altium_exe()
    prj = str((sp.parent / "server/AltiumScript/Altium_API.PrjScr").resolve())
    (EX / "response.json").unlink(missing_ok=True)
    (EX / "request.json").write_text(json.dumps(
        {"command": "build_circuit", "parameters": {}}, indent=2))
    cmd = (f'"{exe}" -RScriptingSystem:RunScript(ProjectName="{prj}"'
           f'^|ProcName="Altium_API>Run")')
    subprocess.Popen(cmd, shell=True)
    for _ in range(180):
        if (EX / "response.json").exists():
            return (EX / "response.json").read_text()
        time.sleep(1)
    return "TIMEOUT"


if __name__ == "__main__":
    n = emit_spec()
    print(f"spec lines: {n}")
    print(run_bridge()[:400])
