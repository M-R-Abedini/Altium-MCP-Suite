# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guard: a refusal must never be reported as an empty, clean result.

Every case here is a defect that shipped, that the previous suite passed
on, and that was found only by driving a live EasyEDA editor.

The shape of the mistake is always the same. A lower layer refuses
correctly and in detail, and the layer above reads past the refusal into
a field that is absent, gets a falsey default, and reports it as an
answer. Zero findings and "nothing was examined" are then indistinguishable,
and the safe-looking one wins.

Measured, on a schematic document with 111 parts:

* ``design.snapshot`` replies with an ERROR saying it needs a PCB
  document. The adapter read ``reply["result"] or {}``, so
  ``review_design`` reported ok:True with 0 parts, 0 nets and 0
  findings.
* ``design.run_erc`` replies ``{"ran": false, "failed": "...This is NOT
  a clean result..."}``. The adapter read ``violations or []`` and
  reported a clean ERC.

Both refusals were already worded correctly by the extension. The bug
was entirely in reading them.
"""

from __future__ import annotations

import pytest

from eda_agent.core.backends import (
    BackendUnavailableError,
    EasyEdaBackend,
    active_backend_name,
    resolve_backend,
    set_active_backend,
)


# --------------------------------------------------------------------
# A reply carrying an error is not a reply carrying no data.
# --------------------------------------------------------------------

#: Verbatim from the live editor, so the test cannot drift from the
#: wording the extension actually produces.
WRONG_DOCUMENT = (
    "design.snapshot needs a PCB document and the active one is a "
    "schematic. The pcb_* classes exist in every runtime, so this would "
    "not fail with \"undefined\": it fails inside EasyEDA with a null, or "
    "does not answer at all."
)


def test_error_reply_raises_rather_than_yielding_an_empty_result():
    with pytest.raises(BackendUnavailableError) as caught:
        EasyEdaBackend._result({"error": WRONG_DOCUMENT}, "design.snapshot")
    # The editor's own explanation has to survive to the caller. A
    # generic "unavailable" would send someone hunting a connection
    # problem when the fix is to open a PCB.
    assert "needs a PCB document" in str(caught.value)


def test_reply_with_neither_result_nor_error_is_refused():
    """Silence is not an empty design either."""
    with pytest.raises(BackendUnavailableError):
        EasyEdaBackend._result({}, "design.snapshot")


def test_empty_but_present_result_is_allowed_through():
    """The opposite direction: a genuine empty answer must NOT raise.

    Without this, the guard above could be satisfied by refusing
    everything, and a board that really has nothing on it would report
    as broken.
    """
    assert EasyEdaBackend._result({"result": {}}, "design.snapshot") == {}


# --------------------------------------------------------------------
# A check that did not run is not a check that passed.
# --------------------------------------------------------------------

DID_NOT_RUN = (
    "the ERC checker answered with the boolean false rather than a "
    "report, so no violation list exists. This is NOT a clean result: "
    "nothing was enumerated."
)


def test_checker_that_did_not_run_is_not_reported_clean():
    out = EasyEdaBackend._checked(
        {"ran": False, "failed": DID_NOT_RUN}, "design.run_erc")
    assert out["ok"] is False
    assert out["ran"] is False
    assert "NOT a clean result" in out["reason"]
    # The number that would have been reported before. Its absence is
    # the point: there is no violation count when nothing was counted.
    assert "violation_count" not in out


def test_checker_that_did_not_run_still_says_so_without_a_reason():
    out = EasyEdaBackend._checked({"ran": False}, "design.run_drc")
    assert out["ok"] is False
    assert "did not run" in out["reason"]


def test_genuinely_clean_check_is_still_reported_clean():
    """The opposite direction, so this cannot be satisfied by pessimism."""
    out = EasyEdaBackend._checked({"violations": []}, "design.run_erc")
    assert out["ok"] is True
    assert out["ran"] is True
    assert out["violation_count"] == 0


def test_violations_are_counted_and_capped():
    out = EasyEdaBackend._checked(
        {"violations": [{"n": i} for i in range(500)]}, "design.run_drc")
    assert out["violation_count"] == 500, "the COUNT must not be capped"
    assert len(out["violations"]) == 200, "the payload is capped at 200"


# --------------------------------------------------------------------
# The registry and the resolver must not be able to disagree.
# --------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_active_backend(monkeypatch):
    """Leave the process-wide active backend as it was found.

    EDA_AGENT_BACKEND is cleared for the duration, because these tests
    are about what REGISTRATION decides and the environment variable
    takes precedence over it. Leaving the variable to whatever the
    machine happens to export would make them pass or fail on a fact
    that has nothing to do with the code under test.
    """
    import eda_agent.core.backends as backends
    monkeypatch.delenv("EDA_AGENT_BACKEND", raising=False)
    before = backends._REGISTERED
    yield
    backends._REGISTERED = before


def test_registering_a_backend_decides_what_resolves():
    """Registration is the single source of truth, not the environment.

    A harness that registered the EasyEDA surface without also exporting
    EDA_AGENT_BACKEND got EasyEDA tools over an ALTIUM resolver, and
    review_design returned a complete, plausible, successful review of a
    different EDA's open document.
    """
    set_active_backend("easyeda")
    assert active_backend_name() == "easyeda"
    assert type(resolve_backend()).__name__ == "EasyEdaBackend"

    set_active_backend("altium")
    assert type(resolve_backend()).__name__ == "AltiumBackend"


def test_unknown_explicit_backend_is_refused_not_silently_altium():
    set_active_backend("easyeda")
    with pytest.raises(BackendUnavailableError) as caught:
        resolve_backend("easyeda-typo")
    message = str(caught.value)
    assert "unknown backend" in message
    # The valid names belong in the error, because the whole failure is
    # someone not knowing them.
    for name in ("altium", "easyeda", "kicad"):
        assert name in message


def test_no_explicit_name_still_falls_back_rather_than_raising():
    """`both`, and any unrecognised ambient value, take the default.

    Only an EXPLICIT unknown name is an error. Raising on the ambient
    case would break a server started in "both" mode.
    """
    import eda_agent.core.backends as backends
    backends._REGISTERED = "both"
    assert type(resolve_backend()).__name__ == "AltiumBackend"
