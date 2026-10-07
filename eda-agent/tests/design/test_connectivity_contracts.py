import asyncio
import copy

import pytest

from eda_agent.design.connectivity_contracts import snapshot, check_contracts, diff, parity
from eda_agent.tools.connectivity_contracts import register_connectivity_contract_tools
from eda_agent.tools.registry import ToolRegistry


def capture(*rows, complete=True):
    return snapshot({'pins': [dict(component=c, pin=p, net=n) for c, p, n in rows]}, complete)


def test_hierarchy_is_not_merged_and_hash_is_order_independent():
    rows = [('J1', '1', '/Cam0/RESET'), ('J2', '1', '/Cam1/RESET')]
    a, b = capture(*rows), capture(*reversed(rows))
    assert a == b
    result = check_contracts(a, [dict(component='J2', pin='1', net='/Cam0/RESET')])
    assert result['status'] == 'failed'
    assert result['findings'][0]['actual'] == '/Cam1/RESET'


@pytest.mark.parametrize('data', [{}, {'pins': []}, {'pins': [{}]},
    {'pins': [{'component': 'U1', 'pin': '1'}]},
    {'pins': [{'component': 'U1', 'pin': '1', 'net': None}]},
    {'pins': [{'component': 'U1', 'pin': '1', 'net': 'VCC'}], 'truncated': True},
    {'pins': [{'component': 'U1', 'pin': '1', 'net': 'VCC'}], 'total': 2},
    {'pins': [{'component': 'U1', 'pin': '1', 'net': 'VCC'}], 'success': False}])
def test_invalid_or_partial_transport_data_is_rejected(data):
    with pytest.raises(ValueError):
        snapshot(data, True)


def test_repeated_multiunit_pins_deduplicate_but_conflicting_assignments_fail():
    assert capture(('U1', '1', 'VCC'), ('U1', '1', 'VCC'))['count'] == 1
    with pytest.raises(ValueError, match='conflicting'):
        capture(('U1', '1', 'VCC'), ('U1', '1', 'GND'))


def test_contracts_detect_missing_wrong_and_unspecified_pins():
    actual = capture(('U1', '1', 'GND'), ('U1', '2', 'VCC'))
    result = check_contracts(actual, [dict(component='U1', pin='1', net='VCC'),
                                    dict(component='U1', pin='3', net='GND')], True)
    assert {x['code'] for x in result['findings']} == {'MISSING_PIN', 'NET_MISMATCH', 'UNSPECIFIED_PIN'}


def test_nc_is_explicit_and_does_not_hide_a_missing_pin():
    rule = dict(component='U1', pin='1', nc=True, reason='Datasheet reserved pin')
    assert check_contracts(capture(('U1', '1', '?')), [rule])['ok']
    assert not check_contracts(capture(('U1', '1', 'GND')), [rule])['ok']
    assert not check_contracts(capture(('U1', '2', 'GND')), [rule])['ok']
    with pytest.raises(ValueError):
        check_contracts(capture(('U1', '1', '')), [dict(component='U1', pin='1', nc=True)])


def test_empty_duplicate_or_ambiguous_contracts_fail():
    actual = capture(('U1', '1', 'VCC'))
    rule = dict(component='U1', pin='1', net='VCC')
    for rules in ([], [rule, rule], [dict(**rule, nc=True, reason='invalid')]):
        with pytest.raises(ValueError):
            check_contracts(actual, rules)


def test_incomplete_capture_cannot_pass_even_if_it_matches():
    actual = capture(('R1', '1', 'VCC'), complete=False)
    result = check_contracts(actual, [dict(component='R1', pin='1', net='VCC')])
    assert result['status'] == 'incomplete' and result['ok'] is False


def test_diff_reports_connect_disconnect_added_removed_and_net_rename():
    a = capture(('U1', '1', '/A/X'), ('U1', '2', ''), ('R1', '1', 'GND'), ('R2', '1', 'VCC'))
    b = capture(('U1', '1', '/B/X'), ('U1', '2', 'VCC'), ('R2', '1', ''), ('C1', '1', 'VCC'))
    saved = copy.deepcopy(a)
    result = diff(a, b)
    assert result['change_count'] == 5
    assert {x['code'] for x in result['changes']} == {'PIN_ADDED', 'PIN_REMOVED', 'NET_CHANGED'}
    assert a == saved
    assert diff(a, a)['change_count'] == 0


def board(*pads, complete=True):
    return {'components': [{'designator': 'U1', 'pads': [dict(name=p, net=n) for p, n in pads]}],
            'complete': complete}


def test_parity_is_bidirectional_and_includes_unconnected_extra_pads():
    sch = capture(('U1', '1', 'VCC'), ('U1', '2', 'GND'))
    result = parity(sch, board(('1', 'GND'), ('3', '')))
    assert {x['code'] for x in result['findings']} == {
        'PAD_NET_MISMATCH', 'SYMBOL_WITHOUT_PAD', 'PAD_WITHOUT_SYMBOL'}
    assert parity(sch, board(('1', 'VCC'), ('2', 'GND')))['ok']
    assert parity(sch, board(('1', 'VCC'), ('2', 'GND'), complete=False))['status'] == 'incomplete'


def test_split_same_number_pads_and_mechanical_waiver():
    sch = capture(('U1', '1', 'GND'))
    pcb = board(('1', 'GND'), ('1', 'GND'), ('', ''))
    waiver = dict(component='U1', pin='', code='PAD_WITHOUT_SYMBOL', reason='Mechanical anchor')
    result = parity(sch, pcb, [waiver])
    assert result['ok'] and len(result['applied_waivers']) == 1
    with pytest.raises(ValueError, match='conflicting'):
        parity(sch, board(('1', 'VCC'), ('1', 'GND')))
    with pytest.raises(ValueError):
        parity(sch, board(('1', 'VCC')), [dict(waiver, code='PAD_NET_MISMATCH')])
    assert parity(sch, board(('1', 'GND')), [waiver])['findings'][0]['code'] == 'UNUSED_WAIVER'


def test_all_tools_are_registered_and_errors_are_structured():
    registry = ToolRegistry()
    register_connectivity_contract_tools(registry)
    for name in ('design_connectivity_snapshot', 'design_check_pin_contracts',
                 'design_diff_connectivity', 'design_check_schematic_pcb_parity'):
        assert registry.get(name) is not None
    result = asyncio.run(registry.get('design_connectivity_snapshot').fn({}))
    assert result['ok'] is False and result['status'] == 'invalid_input'


def test_extra_thermal_pad_waiver_checks_the_expected_net():
    sch = capture(('U1', '1', 'VCC'))
    waiver = dict(component='U1', pin='EP', code='PAD_WITHOUT_SYMBOL',
                  reason='Exposed pad per manufacturer land pattern', net='GND')
    assert parity(sch, board(('1', 'VCC'), ('EP', 'GND')), [waiver])['ok']
    result = parity(sch, board(('1', 'VCC'), ('EP', 'VCC')), [waiver])
    assert result['findings'][0]['code'] == 'WAIVER_NET_MISMATCH'
