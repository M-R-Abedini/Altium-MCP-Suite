"""Legacy requests must fail before publishing text native ANSI cannot decode."""
import asyncio
from types import SimpleNamespace
import pytest

from test_review_fixes import legacy_method
from batch_safety import validate_legacy_json_text


@pytest.mark.parametrize('payload', [{'label': 'Ω'}, {'pins': ['你好']}, {'فارسی': 'R1'}])
def test_unsupported_text_never_launches_or_publishes(tmp_path, payload):
    async def launch():
        pytest.fail('Unsupported text reached Altium')
    result = asyncio.run(legacy_method(tmp_path)(SimpleNamespace(run_altium_script=launch), 'modify', payload))
    assert result['success'] is False
    assert 'Latin-1' in result['error']
    assert not list(tmp_path.iterdir())


def test_punctuation_escapes_and_latin1_are_supported():
    validate_legacy_json_text({'label': '10k, "precision"', 'path': r'C:\temp\µ.schlib', 'notes': 'line\nnext\tfield'})
