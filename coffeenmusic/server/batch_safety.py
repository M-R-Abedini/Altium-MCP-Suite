"""Validation for the legacy pipe/line-delimited circuit specification."""

def validate_batch_fields(value, field='input'):
    if isinstance(value, str):
        if any(ch in value for ch in ('|', '\r', '\n', '\x00')):
            raise ValueError(f'{field}: pipe, line breaks and NUL are not allowed in a batch field')
        try:
            value.encode('cp1252', errors='strict')
        except UnicodeEncodeError as error:
            points = ', '.join(f'U+{ord(ch):04X}' for ch in value[error.start:error.end])
            raise ValueError(f'{field}: unsupported batch text ({points}); no specification was written') from error
    elif isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            validate_batch_fields(key, f'{field}.key[{index}]')
            validate_batch_fields(item, f'{field}.{key}')
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_batch_fields(item, f'{field}[{index}]')
