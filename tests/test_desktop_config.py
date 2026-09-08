import json

import pytest

from src.desktop.config import (
    DesktopConfigError,
    default_ledger_path,
    load_ledger_path,
    save_ledger_path,
)


def test_desktop_config_saves_only_absolute_ledger_path(tmp_path):
    ledger = tmp_path / '账本' / 'workbench.db'
    destination = save_ledger_path(ledger, tmp_path / 'preferences')

    assert load_ledger_path(tmp_path / 'preferences') == ledger.resolve()
    assert json.loads(destination.read_text(encoding='utf-8')) == {
        'ledger_path': str(ledger.resolve())
    }


def test_missing_config_and_default_ledger_are_stable(tmp_path):
    assert load_ledger_path(tmp_path) is None
    assert default_ledger_path(tmp_path) == (tmp_path / 'workbench.db').resolve()


@pytest.mark.parametrize('payload', [
    '{broken',
    '{}',
    '[]',
    '{"ledger_path":"relative.db"}',
    '{"ledger_path":"/tmp/a.db","token":"secret"}',
])
def test_invalid_or_expansive_config_is_rejected(tmp_path, payload):
    (tmp_path / 'desktop.json').write_text(payload, encoding='utf-8')
    with pytest.raises(DesktopConfigError):
        load_ledger_path(tmp_path)
