import pytest
from eda_agent.tools import generic, project


def capture(module, monkeypatch):
    tools = {}
    class MCP:
        def tool(self, *args, **kwargs):
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn
            return decorator
    def unexpected_bridge():
        raise AssertionError('Invalid input reached the bridge')
    monkeypatch.setattr(module, 'get_bridge', unexpected_bridge)
    getattr(module, 'register_generic_tools' if module is generic else 'register_project_tools')(MCP())
    return tools


@pytest.mark.asyncio
@pytest.mark.parametrize('properties,filter', [('Designator,OwnerDesignator',''),
    ('Owner.Designator.Text',''), ('Designator','OwnerDesignator=U1')])
async def test_unsupported_owner_reads_and_filters_are_rejected(monkeypatch, properties, filter):
    tool = capture(generic, monkeypatch)['obj_query']
    with pytest.raises(ValueError, match='pin-owner'):
        await tool(object_type='ePin', properties=properties, filter=filter)


@pytest.mark.asyncio
async def test_negative_query_limits_are_rejected_before_dispatch(monkeypatch):
    query = capture(generic, monkeypatch)['obj_query']
    nets = capture(project, monkeypatch)['proj_get_nets']
    with pytest.raises(ValueError, match='limit'):
        await query(object_type='ePin', properties='Designator', limit=-1)
    with pytest.raises(ValueError, match='limit'):
        await nets(limit=-1)
