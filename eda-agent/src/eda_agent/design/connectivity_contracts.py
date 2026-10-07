# SPDX-License-Identifier: MIT
"""Explicit pin contracts, topology snapshots and schematic/PCB parity.

Inspired by KAT's verification workflow; implemented independently for Altium
payloads. Net names remain opaque, including their complete hierarchy.
These checks validate supplied connectivity, not ERC, geometry or freshness.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{label} must be {'a string' if empty else 'a nonempty string'}")
    return value


def _rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get('error') or data.get('ok') is False or data.get('success') is False:
        raise ValueError('connectivity payload is missing or reports failure')
    rows = data.get('pins')
    if not isinstance(rows, list) or not rows:
        raise ValueError('pins must be a nonempty list; empty data cannot pass verification')
    if data.get('truncated'):
        raise ValueError('truncated connectivity cannot be verified')
    for field in ('count', 'total'):
        if field in data and (type(data[field]) is not int or data[field] != len(rows)):
            raise ValueError(f'{field} does not match the supplied pin count')
    return rows


def _index(data: dict[str, Any], *, allow_unnumbered: bool = False) -> dict[tuple[str, str], str]:
    pins: dict[tuple[str, str], str] = {}
    for row in _rows(data):
        if not isinstance(row, dict):
            raise ValueError('every pin must be an object')
        key = (_text(row.get('component'), 'component'),
               _text(row.get('pin'), 'pin', empty=allow_unnumbered))
        net = _text(row.get('net'), 'net', empty=True)
        net = '' if net == '?' else net
        if key in pins and pins[key] != net:
            raise ValueError(f'conflicting assignments for {key[0]}.{key[1]}')
        # Identical repetitions from multi-unit/stacked pins are harmless.
        pins[key] = net
    return pins


def _items(pins: dict[tuple[str, str], str]) -> list[dict[str, str]]:
    return [dict(component=c, pin=p, net=n) for (c, p), n in sorted(pins.items())]


def snapshot(data: dict[str, Any], complete: bool = False) -> dict[str, Any]:
    if type(complete) is not bool:
        raise ValueError('complete must be a boolean')
    pins = _items(_index(data))
    digest = hashlib.sha256(json.dumps(pins, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'ok': True, 'status': 'captured', 'schema_version': 1, 'pins': pins, 'count': len(pins),
            'sha256': digest, 'complete': complete}


def _complete(data: dict[str, Any]) -> bool:
    flag = data.get('complete', False)
    if type(flag) is not bool:
        raise ValueError('complete must be a boolean')
    return flag


def _report(findings: list[dict[str, Any]], complete: bool, **extra: Any) -> dict[str, Any]:
    status = 'failed' if findings else ('passed' if complete else 'incomplete')
    return {'ok': status == 'passed', 'status': status, 'complete': complete,
            'finding_count': len(findings), 'findings': findings, **extra}


def check_contracts(actual: dict[str, Any], contracts: list[dict[str, Any]],
                    require_all_pins: bool = False) -> dict[str, Any]:
    pins = _index(actual)
    if not isinstance(contracts, list) or not contracts:
        raise ValueError('contracts must be a nonempty list')
    if type(require_all_pins) is not bool:
        raise ValueError('require_all_pins must be a boolean')
    seen = set()
    findings = []
    for rule in contracts:
        if not isinstance(rule, dict):
            raise ValueError('every contract must be an object')
        key = (_text(rule.get('component'), 'component'), _text(rule.get('pin'), 'pin'))
        if key in seen:
            raise ValueError(f'duplicate contract for {key[0]}.{key[1]}')
        seen.add(key)
        nc = rule.get('nc', False)
        if type(nc) is not bool:
            raise ValueError('nc must be a boolean')
        if nc:
            if 'net' in rule:
                raise ValueError('NC contract must not also specify a net')
            _text(rule.get('reason'), 'NC reason')
            expected = ''
        else:
            expected = _text(rule.get('net'), 'contract net')
            if expected == '?':
                raise ValueError('use nc=true and a reason for an unconnected pin')
        if key not in pins:
            findings.append(dict(code='MISSING_PIN', component=key[0], pin=key[1], expected=expected))
        elif pins[key] != expected:
            findings.append(dict(code='NET_MISMATCH', component=key[0], pin=key[1],
                                 expected=expected, actual=pins[key]))
    if require_all_pins:
        for c, p in sorted(set(pins) - seen):
            findings.append(dict(code='UNSPECIFIED_PIN', component=c, pin=p))
    return _report(findings, _complete(actual), checked=len(contracts),
                   scope='all_pins' if require_all_pins else 'specified_contracts')


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old, new = _index(before), _index(after)
    changes = []
    for key in sorted(set(old) | set(new)):
        if key not in old:
            changes.append(dict(code='PIN_ADDED', component=key[0], pin=key[1], after=new[key]))
        elif key not in new:
            changes.append(dict(code='PIN_REMOVED', component=key[0], pin=key[1], before=old[key]))
        elif old[key] != new[key]:
            changes.append(dict(code='NET_CHANGED', component=key[0], pin=key[1],
                                before=old[key], after=new[key]))
    return {'ok': True, 'status': 'compared' if _complete(before) and _complete(after) else 'incomplete',
            'complete': _complete(before) and _complete(after), 'change_count': len(changes),
            'changes': changes, 'unchanged_count': sum(old[k] == new[k] for k in set(old) & set(new))}


def _pad_index(board: dict[str, Any]) -> dict[tuple[str, str], str]:
    if not isinstance(board, dict) or board.get('error') or board.get('ok') is False or board.get('success') is False:
        raise ValueError('PCB payload is missing or reports failure')
    components = board.get('components')
    if components is None:
        components = [board]
    if not isinstance(components, list) or not components:
        raise ValueError('PCB components must be a nonempty list')
    rows = []
    for comp in components:
        if not isinstance(comp, dict) or comp.get('error') or comp.get('truncated') or comp.get('success') is False or comp.get('ok') is False:
            raise ValueError('PCB component data is invalid or truncated')
        ref = _text(comp.get('designator'), 'PCB designator')
        pads = comp.get('pads')
        if not isinstance(pads, list) or not pads:
            raise ValueError(f'{ref}: pads must be a nonempty list')
        if 'pad_count' in comp and (type(comp['pad_count']) is not int or comp['pad_count'] != len(pads)):
            raise ValueError(f'{ref}: pad_count does not match')
        for pad in pads:
            if not isinstance(pad, dict):
                raise ValueError('every pad must be an object')
            rows.append(dict(component=ref, pin=pad.get('name'), net=pad.get('net')))
    payload = dict(pins=rows, truncated=board.get('truncated', False))
    return _index(payload, allow_unnumbered=True)


def parity(schematic: dict[str, Any], board: dict[str, Any],
           waivers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sch, pcb = _index(schematic), _pad_index(board)
    waived = {}
    if waivers is not None and not isinstance(waivers, list):
        raise ValueError('waivers must be a list')
    for waiver in waivers or []:
        if not isinstance(waiver, dict):
            raise ValueError('every waiver must be an object')
        key = (_text(waiver.get('component'), 'component'), _text(waiver.get('pin'), 'pin', empty=True))
        code = _text(waiver.get('code'), 'waiver code')
        if code not in {'PAD_WITHOUT_SYMBOL', 'SYMBOL_WITHOUT_PAD'}:
            raise ValueError('waivers only allow an explicit extra pad or schematic-only pin')
        reason = _text(waiver.get('reason'), 'waiver reason')
        if (key, code) in waived:
            raise ValueError('duplicate waiver')
        waived[(key, code)] = (reason, _text(waiver.get('net', ''), 'waiver net', empty=True))
    findings, applied = [], []
    used = set()
    for key in sorted(set(sch) | set(pcb)):
        code = ('PAD_WITHOUT_SYMBOL' if key not in sch else
                'SYMBOL_WITHOUT_PAD' if key not in pcb else
                'PAD_NET_MISMATCH' if sch[key] != pcb[key] else '')
        if not code:
            continue
        item = dict(code=code, component=key[0], pin=key[1],
                    schematic=sch.get(key), pcb=pcb.get(key))
        if (key, code) in waived:
            used.add((key, code))
            reason, expected = waived[(key, code)]
            actual_net = pcb[key] if key in pcb else sch[key]
            if actual_net != expected:
                findings.append(dict(code='WAIVER_NET_MISMATCH', component=key[0], pin=key[1],
                                     expected=expected, actual=actual_net))
            else:
                applied.append(dict(**item, reason=reason))
        else:
            findings.append(item)
    for key, code in sorted(set(waived) - used):
        findings.append(dict(code='UNUSED_WAIVER', component=key[0], pin=key[1], waived_code=code))
    return _report(findings, _complete(schematic) and _complete(board),
                   checked_pins=len(sch), checked_pads=len(pcb), applied_waivers=applied)
