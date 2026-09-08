"""Small desktop preference file containing only the selected ledger path."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from src.workbench.runtime import canonical_ledger_path


APP_DIR_NAME = 'LiveCommerceWorkbench'
CONFIG_FILENAME = 'desktop.json'


class DesktopConfigError(ValueError):
    pass


def application_support_dir() -> Path:
    override = os.environ.get('LIVECOMMERCE_DESKTOP_CONFIG_DIR')
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return Path.home() / 'Library' / 'Application Support' / APP_DIR_NAME


def config_path(directory: str | Path | None = None) -> Path:
    return Path(directory) / CONFIG_FILENAME if directory else application_support_dir() / CONFIG_FILENAME


def default_ledger_path(directory: str | Path | None = None) -> Path:
    base = Path(directory) if directory else application_support_dir()
    return canonical_ledger_path(base / 'workbench.db')


def load_ledger_path(directory: str | Path | None = None) -> Path | None:
    path = config_path(directory)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DesktopConfigError('桌面配置无法读取；请检查 desktop.json') from exc
    if (not isinstance(payload, dict) or set(payload) != {'ledger_path'}
            or not isinstance(payload['ledger_path'], str)):
        raise DesktopConfigError('桌面配置格式无效；只应包含 ledger_path')
    ledger = Path(payload['ledger_path']).expanduser()
    if not ledger.is_absolute():
        raise DesktopConfigError('桌面配置中的账本路径必须是绝对路径')
    return canonical_ledger_path(ledger)


def save_ledger_path(ledger_path: str | Path, directory: str | Path | None = None) -> Path:
    ledger = canonical_ledger_path(ledger_path)
    destination = config_path(directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({'ledger_path': str(ledger)}, ensure_ascii=False, indent=2) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=destination.parent,
            prefix='.desktop-', suffix='.tmp', delete=False,
        ) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination
