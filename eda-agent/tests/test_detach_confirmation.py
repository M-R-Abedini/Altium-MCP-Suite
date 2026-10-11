"""A stop acknowledgment must not masquerade as native engine release."""
from unittest.mock import AsyncMock, Mock
import pytest
from eda_agent.tools import application


def detach_tool(monkeypatch, *, response=None, error=None):
    bridge = Mock()
    bridge.send_command_async = AsyncMock(return_value=response, side_effect=error)
    monkeypatch.setattr(application, 'get_bridge', lambda: bridge)
    tools = {}
    class MCP:
        def tool(self, *args, **kwargs):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register
    application.register_application_tools(MCP())
    return tools['app_detach'], bridge


@pytest.mark.asyncio
async def test_failed_shutdown_does_not_detach_or_claim_release(monkeypatch):
    detach, bridge = detach_tool(monkeypatch, error=TimeoutError('handler still running'))
    out = await detach()
    assert out['success'] is False and out['engine_release_confirmed'] is False
    assert 'handler still running' in out['reason']
    bridge.detach.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [None, {}, {'stopping': True}, {'success': True},
                                     {'engine_release_confirmed': 'true'}])
async def test_stop_acknowledgment_alone_is_insufficient(monkeypatch, response):
    detach, bridge = detach_tool(monkeypatch, response=response)
    out = await detach()
    assert out['success'] is False and out['engine_release_confirmed'] is False
    bridge.detach.assert_not_called()


@pytest.mark.asyncio
async def test_local_detach_follows_confirmed_shutdown_without_save(monkeypatch):
    detach, bridge = detach_tool(monkeypatch, response={'engine_release_confirmed': True})
    out = await detach()
    assert out['success'] is True and out['attached'] is False
    assert out['engine_release_confirmed'] is True
    bridge.send_command_async.assert_awaited_once_with('application.stop_server', timeout=60.0)
    bridge.detach.assert_called_once_with()
