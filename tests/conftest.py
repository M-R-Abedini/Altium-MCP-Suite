"""Make repository modules available to pytest entry points and IDE runners."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolate_editor_binding_in_legacy_unit_fixtures(monkeypatch, request):
    """Old guard fixtures simulate PIDs; never bind them to the real user profile.

    The dedicated engine-identity suite exercises the real binding code using
    temporary roots, and subprocess lock tests exercise the Windows mutex.
    """
    if request.module.__name__.endswith('test_engine_identity'):
        return
    import bridge_coordination as coord
    monkeypatch.setattr(coord, '_bind_editor_runtime', lambda state: None)
    monkeypatch.setattr(coord, '_reset_editor_runtime', lambda state: None)
