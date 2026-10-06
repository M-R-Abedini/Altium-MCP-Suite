# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Interactive HTML BOM exporter.

Self-contained HTML file (no external CSS or JS) with a sortable,
filterable table of every component, grouped-by-value chips, and a
distinct-parts panel. A single static file with no Altium-side runtime
dependency (we already have the BOM from project.get_bom).

The output is a real HTML page you can email / commit / open offline,
not a live dashboard view.
"""

from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from typing import Any, Iterable


def _h(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def _script_json(value: Any) -> str:
    """Serialise data for embedding INSIDE a <script> element.

    Escaping for JSON is not escaping for HTML, and this blob has to
    survive both parsers. An HTML parser tokenises a script element by
    scanning the raw bytes for "</script"; it has parsed no JSON at that
    point and cannot know the sequence sits inside a string. So a
    component field carrying that text ends the element early, and
    everything after it is read as markup.

    A BOM is exactly where that arrives. The designator will not carry
    it, but a description or supplier string travelling in comment or
    lib_ref holds whatever a vendor typed into a catalogue, and the
    exported page is made to be mailed to a manufacturer, so nobody
    reads it first.

    Escaping "<" as \\u003c fixes it inside the JSON, where it parses
    back to the identical character and no consumer can tell the
    difference. Doing it the other way, by running the blob through
    _h(), turns the quotes into entities and the script stops parsing
    altogether.

    U+2028 and U+2029 need the same treatment for a different reason:
    they are legal raw inside a JSON string and are LINE TERMINATORS to
    a JavaScript parser, so they end the statement rather than the
    element. json.dumps escapes them already while ensure_ascii is on,
    and they are handled here too so that turning that off later cannot
    quietly reopen the hole.
    """
    return (json.dumps(value)
            .replace("<", "\\u003c")
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029"))


def render_bom_html(
    bom: dict[str, Any],
    *,
    title: str = "Bill of Materials",
    project: str = "",
) -> str:
    """Render a BOM dict (as returned by ``project.get_bom``) as a self-
    contained HTML page.

    Expected ``bom`` shape:
        {
          "components": [
            {"designator": "C1", "comment": "10uF", "footprint": "0402",
             "lib_ref": "CAP_NP_0402", "pins": [...] or count int},
            ...
          ],
          "count": <int>,
        }

    Args:
        bom: BOM payload. ``components`` list is required; everything else
            is optional and missing fields render as empty cells.
        title: <title> + H1 heading on the page.
        project: Optional sub-title for the project / board name.

    Returns:
        The full HTML document as a string. Write to file with
        ``Path.write_text(html, encoding='utf-8')``.
    """
    comps = list(bom.get("components") or bom.get("rows") or [])

    # Group by (comment, footprint) -- "distinct line items" in BOM-speak.
    # The same value+footprint shared across N designators consolidates
    # into one BOM line. This mirrors what Altium's bundled BOM template
    # would emit.
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for c in comps:
        key = (
            str(c.get("comment") or c.get("value") or ""),
            str(c.get("footprint") or ""),
            str(c.get("lib_ref") or ""),
        )
        designator = str(c.get("designator") or "")
        if designator:
            groups[key].append(designator)

    # Sort by quantity desc, then comment asc -- the high-count line
    # items live at the top where they should.
    grouped_rows = sorted(
        groups.items(),
        key=lambda kv: (-len(kv[1]), kv[0][0].lower()),
    )

    value_counts = Counter(g[0] for g in grouped_rows for _ in g[1])

    rows_json = _script_json([
        {
            "designators": dlist,
            "value": key[0],
            "footprint": key[1],
            "lib_ref": key[2],
            "qty": len(dlist),
        }
        for key, dlist in grouped_rows
    ])
    flat_json = _script_json([
        {
            "designator": str(c.get("designator") or ""),
            "value": str(c.get("comment") or c.get("value") or ""),
            "footprint": str(c.get("footprint") or ""),
            "lib_ref": str(c.get("lib_ref") or ""),
            "pins": (
                len(c["pins"]) if isinstance(c.get("pins"), list)
                else int(c.get("pins") or 0)
            ),
        }
        for c in comps
    ])

    total_components = len(comps)
    total_lines = len(grouped_rows)
    total_distinct_values = len({k[0] for k in groups.keys() if k[0]})

    # The HTML is intentionally one giant template literal: easier to
    # eyeball than fragmented innerHTML pieces in Python.
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_h(title)}</title>
<style>
:root {{
  --bg: #ffffff; --bg-alt: #f6f7f9; --text: #1c1f23; --muted: #5b6470;
  --accent: #2563eb; --border: #d6dae0; --row-hover: #eff3ff;
  --warn: #ea580c;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; font-size: 13px; }}
.wrap {{ max-width: 1400px; margin: 0 auto; padding: 16px 24px 40px; }}
header h1 {{ margin: 0 0 4px; font-size: 20px; }}
header .sub {{ color: var(--muted); font-size: 12px; }}
.toolbar {{ display: flex; gap: 12px; margin: 16px 0; align-items: center;
  flex-wrap: wrap; }}
.toolbar input, .toolbar select {{
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px;
  font-size: 13px; font-family: inherit; background: var(--bg);
}}
.toolbar input[type=search] {{ flex: 1; min-width: 220px; }}
.toolbar .count {{ color: var(--muted); font-size: 12px; }}
.kpis {{ display: flex; gap: 16px; padding: 10px 14px; background: var(--bg-alt);
  border: 1px solid var(--border); border-radius: 4px; margin-bottom: 14px; }}
.kpis .k {{ display: flex; flex-direction: column; }}
.kpis .k .l {{ font-size: 10px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 1px; }}
.kpis .k .v {{ font-size: 18px; font-weight: 600; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12px;
  table-layout: auto; }}
th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border);
  vertical-align: top; }}
th {{ background: var(--bg-alt); cursor: pointer; user-select: none;
  font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
  color: var(--muted); position: sticky; top: 0; }}
th:hover {{ color: var(--text); }}
th.sorted-asc::after {{ content: " ↑"; }}
th.sorted-desc::after {{ content: " ↓"; }}
tbody tr:hover td {{ background: var(--row-hover); }}
.designators {{ font-family: ui-monospace, monospace; }}
.qty {{ text-align: right; font-feature-settings: "tnum"; }}
.qty.high {{ color: var(--warn); font-weight: 600; }}
.mode-tabs {{ display: flex; gap: 0; }}
.mode-tabs button {{
  background: var(--bg-alt); border: 1px solid var(--border);
  padding: 6px 14px; font-size: 12px; cursor: pointer;
  font-family: inherit;
}}
.mode-tabs button.active {{ background: var(--accent); color: white;
  border-color: var(--accent); }}
.mode-tabs button:first-child {{ border-radius: 4px 0 0 4px; }}
.mode-tabs button:last-child {{ border-radius: 0 4px 4px 0; }}
.empty {{ padding: 30px; text-align: center; color: var(--muted); }}
@media print {{
  .toolbar, .mode-tabs {{ display: none; }}
  th {{ position: static; }}
}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>{_h(title)}</h1>
  <div class="sub">{_h(project)}</div>
</header>

<div class="kpis">
  <div class="k"><span class="l">Components</span><span class="v">{total_components}</span></div>
  <div class="k"><span class="l">BOM lines</span><span class="v">{total_lines}</span></div>
  <div class="k"><span class="l">Distinct values</span><span class="v">{total_distinct_values}</span></div>
</div>

<div class="toolbar">
  <div class="mode-tabs">
    <button data-mode="grouped" class="active">Grouped</button>
    <button data-mode="flat">Per-component</button>
  </div>
  <input type="search" id="filter" placeholder="filter value / designator / footprint / lib_ref...">
  <span class="count" id="count"></span>
</div>

<div id="table-host"></div>

<script>
const ROWS_GROUPED = {rows_json};
const ROWS_FLAT = {flat_json};
let mode = "grouped";
let sortKey = "qty";
let sortDir = "desc";
let filter = "";

function render() {{
  const rows = (mode === "grouped" ? ROWS_GROUPED : ROWS_FLAT).slice();
  const filt = filter.trim().toLowerCase();
  const visible = filt
    ? rows.filter(r => Object.values(r).some(v =>
        Array.isArray(v) ? v.some(x => String(x).toLowerCase().includes(filt))
                          : String(v).toLowerCase().includes(filt)))
    : rows;
  visible.sort((a, b) => {{
    let va = a[sortKey], vb = b[sortKey];
    if (Array.isArray(va)) va = va.length;
    if (Array.isArray(vb)) vb = vb.length;
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();
    if (va < vb) return sortDir === "asc" ? -1 : 1;
    if (va > vb) return sortDir === "asc" ? 1 : -1;
    return 0;
  }});

  const host = document.getElementById("table-host");
  if (visible.length === 0) {{
    host.innerHTML = '<div class="empty">No components match the filter.</div>';
    document.getElementById("count").textContent = "0 of " + rows.length;
    return;
  }}

  const cols = mode === "grouped"
    ? [
        {{key: "qty", label: "Qty", cls: "qty"}},
        {{key: "value", label: "Value"}},
        {{key: "footprint", label: "Footprint"}},
        {{key: "lib_ref", label: "Lib Ref"}},
        {{key: "designators", label: "Designators", cls: "designators"}},
      ]
    : [
        {{key: "designator", label: "Designator", cls: "designators"}},
        {{key: "value", label: "Value"}},
        {{key: "footprint", label: "Footprint"}},
        {{key: "lib_ref", label: "Lib Ref"}},
        {{key: "pins", label: "Pins", cls: "qty"}},
      ];

  let html = "<table><thead><tr>";
  for (const c of cols) {{
    let cls = "";
    if (c.key === sortKey) cls = " class=\\"sorted-" + sortDir + "\\"";
    html += "<th" + cls + " data-key=\\"" + c.key + "\\">" + c.label + "</th>";
  }}
  html += "</tr></thead><tbody>";
  for (const r of visible) {{
    html += "<tr>";
    for (const c of cols) {{
      let v = r[c.key];
      if (Array.isArray(v)) v = v.join(", ");
      let cls = c.cls || "";
      if (c.key === "qty" && r.qty >= 10) cls += " high";
      html += "<td" + (cls ? " class=\\"" + cls + "\\"" : "") + ">" +
              String(v == null ? "" : v).replace(/</g, "&lt;") + "</td>";
    }}
    html += "</tr>";
  }}
  html += "</tbody></table>";
  host.innerHTML = html;
  document.getElementById("count").textContent =
    visible.length + " of " + rows.length;

  host.querySelectorAll("th").forEach(th => {{
    th.addEventListener("click", () => {{
      const key = th.dataset.key;
      if (sortKey === key) sortDir = (sortDir === "asc" ? "desc" : "asc");
      else {{ sortKey = key; sortDir = "asc"; }}
      render();
    }});
  }});
}}

document.querySelectorAll(".mode-tabs button").forEach(b => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll(".mode-tabs button").forEach(x =>
      x.classList.toggle("active", x === b));
    mode = b.dataset.mode;
    sortKey = mode === "grouped" ? "qty" : "designator";
    sortDir = mode === "grouped" ? "desc" : "asc";
    render();
  }});
}});
document.getElementById("filter").addEventListener("input", e => {{
  filter = e.target.value;
  render();
}});
render();
</script>

</div>
</body>
</html>
"""
