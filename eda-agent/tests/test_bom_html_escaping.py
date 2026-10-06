# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A BOM field cannot be allowed to break out of the exported page.

render_bom_html escapes the title and the project name, and then embeds
the component rows as JSON inside a <script> block. Those are different
problems with different rules, and the second one was not solved.

An HTML parser tokenises a script element by scanning the raw bytes for
"</script". It has not parsed any JSON at that point and has no idea
that the sequence sits inside a string, so JSON quoting does not
protect it. The element ends there, and whatever follows is parsed as
markup.

This is not only a new-code concern. The Altium tool proj_export_bom_html
has shipped on this renderer, and its docstring recommends emailing the
result to a manufacturer and sharing it with a reviewer, which is
exactly the path where nobody looks at the file first. The realistic
carrier is not a designator but a description or supplier string
travelling in comment or lib_ref, which holds whatever text a vendor
put in a catalogue.

The fix belongs INSIDE the JSON, escaping "<" as \\u003c, which parses
back to the identical string. It must not be done by running the blob
through the HTML escaper: inside a script element the escaping rules
are the script element's, and turning the quotes into entities would
stop the JavaScript parsing instead.
"""
from __future__ import annotations

import json
import re

import pytest

from eda_agent.render.bom_html import render_bom_html

#: Sequences that end a script element, or that end a JavaScript line
#: without ending a JSON one. U+2028 and U+2029 are legal raw inside a
#: JSON string and are line terminators to a JavaScript parser, so they
#: truncate the assignment rather than escaping the element.
_BREAKOUTS = [
    "</script>",
    "</SCRIPT>",
    "</script  >",
    "</script\t>",
    "<!--",
    "<script>alert(1)</script>",
    " ",
    " ",
]


def _script_bodies(html: str) -> list[str]:
    return re.findall(r"<script[^>]*>(.*?)</script\s*>", html,
                      re.S | re.I)


@pytest.mark.parametrize("payload", _BREAKOUTS)
@pytest.mark.parametrize("field", ["designator", "comment", "footprint",
                                   "lib_ref"])
def test_no_component_field_can_close_the_script_element(payload, field):
    """The property, stated once and checked over every text field.

    Any field a reviewer never inspects is a carrier, so guarding only
    the designator would leave the realistic route open.
    """
    component = {"designator": "R1", "comment": "10k",
                 "footprint": "0402", "lib_ref": "RES"}
    component[field] = payload

    html = render_bom_html({"components": [component], "count": 1})

    # One script element in, one script element out. A payload that
    # terminated the element early would split it in two.
    opens = len(re.findall(r"<script[^>]*>", html, re.I))
    closes = len(re.findall(r"</script\s*>", html, re.I))
    assert opens == closes, (
        f"{field}={payload!r} left {opens} script opens against "
        f"{closes} closes, so the element structure changed")

    lowered = html.lower()
    # The literal must not survive anywhere outside a comment, because
    # the parser does not care where in the document it sits.
    assert lowered.count("</script") == closes, (
        f"{field}={payload!r} put a raw closing tag into the page")


@pytest.mark.parametrize("payload", _BREAKOUTS)
def test_the_payload_still_arrives_intact_as_data(payload):
    """Escaping must not corrupt the value.

    A fix that dropped or mangled the characters would pass the guard
    above and quietly change somebody's part description, which is a
    worse failure than the one being fixed: it is silent.
    """
    html = render_bom_html(
        {"components": [{"designator": "R1", "comment": payload}],
         "count": 1})

    bodies = _script_bodies(html)
    assert bodies, "no script body was rendered at all"

    match = None
    for body in bodies:
        found = re.search(r"const ROWS_FLAT = (\[.*?\]);", body, re.S)
        if found:
            match = found
            break
    assert match, "the flat row data is no longer in the page"

    rows = json.loads(match.group(1))
    assert rows[0]["value"] == payload, (
        f"the payload came back as {rows[0]['value']!r} rather than "
        f"{payload!r}; escaping changed the data")


def test_the_embedded_json_is_still_valid_json():
    """The blob must remain parseable.

    HTML-escaping it would turn the quotes into entities, which is a
    tempting one-line fix that produces a page whose script does not
    run at all.
    """
    html = render_bom_html(
        {"components": [{"designator": "R1", "comment": "a&b<c>d"}],
         "count": 1})

    body = "\n".join(_script_bodies(html))
    for name in ("ROWS_GROUPED", "ROWS_FLAT"):
        found = re.search(rf"const {name} = (\[.*?\]);", body, re.S)
        assert found, f"{name} is missing from the page"
        json.loads(found.group(1))

    assert "&quot;" not in body, (
        "the row data was HTML-escaped, so the quotes became entities "
        "and the script will not parse")


def test_an_ampersand_in_a_value_is_not_double_escaped():
    """The visible half of the same mistake.

    Part descriptions carry ampersands routinely, and escaping them
    twice renders "a&amp;b" on the page where the value said "a&b".
    """
    html = render_bom_html(
        {"components": [{"designator": "R1", "comment": "a&b"}],
         "count": 1})

    body = "\n".join(_script_bodies(html))
    found = re.search(r"const ROWS_FLAT = (\[.*?\]);", body, re.S)
    rows = json.loads(found.group(1))
    assert rows[0]["value"] == "a&b"


def test_the_title_and_project_are_still_escaped():
    """The half that already worked keeps working.

    These are interpolated into markup rather than into a script, so
    they need the HTML escaping the rows must not get. A fix that
    unified the two paths would break this.
    """
    html = render_bom_html({"components": [], "count": 0},
                           title="<b>T</b>", project="<i>P</i>")

    assert "<b>T</b>" not in html
    assert "<i>P</i>" not in html
    assert "&lt;b&gt;T&lt;/b&gt;" in html
