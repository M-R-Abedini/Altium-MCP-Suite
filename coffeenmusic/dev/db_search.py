"""Search the component database behind a .DbLib - read-only.

Why this exists: the Altium API (IDatabaseLibDocument) exposes the schema
fully - GetTableCount/GetTableNameAt/TableEnabled, GetFieldNameAt and
GetParameterNameAt - but it cannot enumerate parts. The tables a DbLib
enables are "<NAME>_Query" views carrying
UserWhereText=[Corp_Part_Number] = '{Corp_Part_Number}', i.e. parameterised
single-part lookups. GetAllComponentKeys against one returns 0 rows because
nothing is bound. Searching therefore has to go to the base tables.

Read-only by construction: only SELECT is issued, and the connection string
comes straight from the .DbLib. The database lives on a share that must
never be written to.

Usage:
    python dev/db_search.py --tables
    python dev/db_search.py --columns INTEGRATED-CIRCUITS
    python dev/db_search.py --find "LED DRIVER" [--table INTEGRATED-CIRCUITS] [--max 40]
    python dev/db_search.py --part 1234-5678
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

DBLIB_ENV = "ALTIUM_DBLIB"
DEFAULT_DBLIB = None  # resolved from the installed libraries, see resolve_dblib()


def resolve_dblib(explicit=None):
    """Locate the .DbLib: explicit path, env var, or Altium's own config."""
    import os
    for cand in (explicit, os.environ.get(DBLIB_ENV)):
        if cand and Path(cand).is_file():
            return Path(cand)
    # Altium records installed libraries in its user preferences
    for pref in Path(os.environ["APPDATA"], "Altium").rglob("*.LibraryPreferences"):
        try:
            txt = pref.read_text(errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"([A-Za-z]:\\[^\r\n=|]+?\.DbLib|\\\\[^\r\n=|]+?\.DbLib)", txt, re.I):
            p = Path(m.group(1))
            if p.is_file():
                return p
    raise SystemExit(
        f"Could not locate a .DbLib. Pass --dblib PATH or set {DBLIB_ENV}."
    )


def dblib_config(dblib):
    txt = dblib.read_text(errors="replace")
    conn = re.search(r"^ConnectionString=(.+)$", txt, re.M).group(1).strip()
    tables = []
    for m in re.finditer(r"\[Table\d+\]\s*\n((?:(?!\[).*\n)*)", txt):
        body = m.group(1)
        name = re.search(r"^TableName=(.*)$", body, re.M)
        enabled = re.search(r"^Enabled=(.*)$", body, re.M)
        if name:
            tables.append((name.group(1).strip(),
                           bool(enabled) and enabled.group(1).strip().lower() == "true"))
    return conn, tables


def run_sql(conn_str, sql, limit=200):
    """Execute a SELECT via OLEDB, returning a list of dict rows.

    Guarded: anything that is not a SELECT is refused outright, so this module
    cannot be used to modify the shared database.
    """
    if not sql.lstrip().lower().startswith("select"):
        raise ValueError("run_sql only issues SELECT statements")
    ps = r"""
$ErrorActionPreference = 'Stop'
$conn = New-Object System.Data.OleDb.OleDbConnection($env:AMCP_CONN)
$conn.Open()
$cmd = $conn.CreateCommand()
$cmd.CommandText = $env:AMCP_SQL
$rdr = $cmd.ExecuteReader()
$n = 0
while ($rdr.Read() -and $n -lt [int]$env:AMCP_LIMIT) {
  $parts = @()
  for ($i = 0; $i -lt $rdr.FieldCount; $i++) {
    $v = $rdr[$i]
    if ($v -is [System.DBNull]) { $v = '' }
    $v = ([string]$v) -replace "[`r`n`t]", ' '
    $parts += ($rdr.GetName($i) + '=' + $v)
  }
  Write-Output ($parts -join "`t")
  $n++
}
$rdr.Close(); $conn.Close()
"""
    import os
    env = dict(os.environ, AMCP_CONN=conn_str, AMCP_SQL=sql, AMCP_LIMIT=str(limit))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.strip()[:1500] + "\n")
        return []
    rows = []
    for line in r.stdout.splitlines():
        if "=" not in line:
            continue
        row = {}
        for field in line.split("\t"):
            if "=" in field:
                k, v = field.split("=", 1)
                row[k.strip()] = v.strip()
        if row:
            rows.append(row)
    return rows


def columns_of(conn, table):
    rows = run_sql(conn, f"SELECT TOP 1 * FROM [{table}]", limit=1)
    return list(rows[0].keys()) if rows else []


def search(conn, table, term, max_rows, cols=None):
    """Find parts whose description or part numbers match `term`."""
    if cols is None:
        cols = columns_of(conn, table)
    if not cols:
        return [], []
    hay = [c for c in cols
           if c.lower() in ("description", "corp_part_number",
                            "mfgr1_part_number", "mfgr2_part_number", "class")]
    if not hay:
        return cols, []
    safe = term.replace("'", "''")
    where = " OR ".join(f"[{c}] LIKE '%{safe}%'" for c in hay)
    sql = f"SELECT TOP {max_rows} * FROM [{table}] WHERE {where}"
    return cols, run_sql(conn, sql, limit=max_rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dblib")
    ap.add_argument("--tables", action="store_true", help="list tables")
    ap.add_argument("--columns", metavar="TABLE")
    ap.add_argument("--find", metavar="TERM")
    ap.add_argument("--part", metavar="CORP_PART_NUMBER")
    ap.add_argument("--table", help="restrict --find to one table")
    ap.add_argument("--max", type=int, default=40)
    a = ap.parse_args()

    dblib = resolve_dblib(a.dblib)
    conn, tables = dblib_config(dblib)
    print(f"# dblib: {dblib}\n")

    if a.tables:
        for name, enabled in tables:
            print(f"{'enabled ' if enabled else '        '}{name}")
        return

    if a.columns:
        for c in columns_of(conn, a.columns):
            print(c)
        return

    # Base tables hold the rows; the "_Query" views are parameterised lookups
    # that return nothing without a bound part number.
    base = [n for n, _ in tables if not n.lower().endswith("_query")]

    if a.part:
        for t in base:
            # Access reports an unknown column as "no value given for one or
            # more required parameters", so check the schema before filtering.
            if "corp_part_number" not in {c.lower() for c in columns_of(conn, t)}:
                continue
            rows = run_sql(conn, f"SELECT TOP 1 * FROM [{t}] WHERE [Corp_Part_Number]='{a.part}'", 1)
            if rows:
                print(f"## {t}")
                for k, v in rows[0].items():
                    if v:
                        print(f"  {k:<26} {v}")
                return
        print("not found")
        return

    if a.find:
        targets = [a.table] if a.table else base
        for t in targets:
            cols, rows = search(conn, t, a.find, a.max)
            if not rows:
                continue
            print(f"## {t}  ({len(rows)} match)")
            for r in rows:
                pn = r.get("Corp_Part_Number", "")
                desc = r.get("Description", "")
                # Base tables use Symbol_Name/Footprint_Name; the _Query views
                # alias the same columns to Altium_Symbol/Altium_Footprint.
                sym = r.get("Symbol_Name") or r.get("Altium_Symbol", "")
                fp = r.get("Footprint_Name") or r.get("Altium_Footprint", "")
                mfg = r.get("Mfgr1_Part_Number", "")
                print(f"  {pn:<12} {desc[:46]:<46} {mfg[:20]:<20} {sym[:20]:<20} {fp}")
            print()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
