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


@pytest.fixture(autouse=True)
def isolate_queue_for_existing_native_fixtures(monkeypatch, request):
    # Dedicated queue tests exercise the real service. Older native fixtures
    # test the mutex/ownership layer without starting a user's local service.
    if request.module.__name__.endswith('test_engine_queue'):
        return
    import engine_queue
    from types import SimpleNamespace
    monkeypatch.setattr(engine_queue, 'acquire', lambda **kw: SimpleNamespace(release=lambda: None))
    async def acquire_async(**kw):
        return SimpleNamespace(release=lambda: None)
    monkeypatch.setattr(engine_queue, 'acquire_async', acquire_async)
