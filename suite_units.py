"""Narrow electrical-value normalization; never rename nets, pins or paths.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import copy
import re
from typing import Any

VALUE_KEYS = {'value', 'comment', 'parameter_value', 'param_value', 'text_value', 'resistance',
              'capacitance', 'inductance', 'tolerance', 'temperature'}
PARAMETER_KEYS = {'parameters', 'params_map', 'component_parameters'}
REPLACEMENTS = str.maketrans({'µ': 'u', 'μ': 'u', '±': '+/-', '–': '-', '—': '-'})


def electrical_value(text: str) -> str:
    """Translate common units while preserving numeric magnitude and suffixes."""
    text = text.translate(REPLACEMENTS).replace('°C', 'degC')
    # 4k7Ω -> 4k7, 10kΩ -> 10k; 100Ω -> 100R; 4Ω7 -> 4R7.
    text = re.sub(r'(?<=\d)([kKmMgG])([0-9]*)(?:Ω|Ω)', r'\1\2', text)
    text = re.sub(r'(?<=\d)[ΩΩ]', 'R', text)
    return re.sub('[ΩΩ]', 'Ohm', text)


def normalize_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """Copy and normalize typed value fields only, with path-only warnings.

    Plans encoded as JSON strings are decoded only in the documented plan_json
    field. Arbitrary scripts, specification files and opaque payloads are not
    rewritten. The existing native guard remains the final transport authority.
    """
    warnings: list[dict] = []

    def walk(value, path='arguments', key='', parameter_map=False):
        if isinstance(value, str):
            if key == 'plan_json':
                import json
                try:
                    plan = json.loads(value)
                except (ValueError, TypeError):
                    return value
                before = len(warnings)
                converted = walk(plan, path)
                return json.dumps(converted, ensure_ascii=False) if len(warnings) > before else value
            if key.casefold() in VALUE_KEYS or parameter_map:
                converted = electrical_value(value)
                if converted != value:
                    warnings.append({'code': 'ELECTRICAL_UNIT_NORMALIZED', 'field': path,
                                     'message': 'Engineering symbols normalized for the native transport.'})
                bad = sorted({ord(ch) for ch in converted if ord(ch) > 255})
                if bad:
                    raise ValueError(f'{path}: unsupported native text ' + ', '.join(f'U+{c:04X}' for c in bad)
                                     + '; use Latin-1 text or edit this field in Altium. Request not dispatched.')
                return converted
            return value
        if isinstance(value, dict):
            return {k: walk(v, f'{path}.{k}', k, key.casefold() in PARAMETER_KEYS)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f'{path}[{i}]', key, parameter_map) for i, v in enumerate(value)]
        return copy.deepcopy(value)

    return walk(arguments), warnings
