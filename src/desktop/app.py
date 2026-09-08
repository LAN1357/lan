"""pywebview entry point. The existing HTTP workbench remains the application."""

from __future__ import annotations

import argparse
from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path
import sys
import threading

from src.desktop.config import (
    DesktopConfigError,
    application_support_dir,
    default_ledger_path,
    load_ledger_path,
    save_ledger_path,
)
from src.workbench.runtime import (
    InvalidLedger,
    WorkbenchRuntimeError,
    canonical_ledger_path,
    inspect_existing_ledger,
    start_workbench,
)


TITLE = '直播经营复盘工作台'


def configure_logging(directory: str | Path | None = None) -> Path:
    base = Path(directory) if directory else application_support_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / 'desktop.log'
    root = logging.getLogger('livecommerce.desktop')
    root.setLevel(logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(path)
               for handler in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        root.addHandler(handler)
    return path


def _import_webview():
    try:
        import webview
    except ImportError as exc:
        raise WorkbenchRuntimeError(
            '桌面依赖尚未安装；请使用包含 desktop 可选依赖的环境启动'
        ) from exc
    return webview


def _select_ledger(window, webview, config_dir: Path | None) -> tuple[Path, bool] | None:
    """Return (path, create_if_missing) using native pywebview dialogs."""
    default_path = default_ledger_path(config_dir)
    if default_path.exists():
        try:
            inspect_existing_ledger(default_path)
        except InvalidLedger:
            pass
        else:
            use_default = window.create_confirmation_dialog(
                '选择账本',
                f'找到默认账本：\n{default_path}\n\n选择“确定”继续使用；选择“取消”选择其他已有账本。',
            )
            if use_default:
                return default_path, False

    use_existing = window.create_confirmation_dialog(
        '首次使用',
        '选择“确定”打开已有 workbench.db；选择“取消”在应用支持目录创建新账本。',
    )
    if use_existing:
        selected = window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=('SQLite账本 (*.db)',),
        )
        if not selected:
            return None
        ledger = canonical_ledger_path(selected[0])
        inspect_existing_ledger(ledger)
        return ledger, False
    if default_path.exists():
        raise InvalidLedger(f'默认账本路径已经存在，不能覆盖：{default_path}')
    return default_path, True


def _menu(webview, holder: dict, ledger_path: Path | None):
    menu_items = getattr(webview, 'menu', webview)
    def home():
        runtime = holder.get('runtime')
        window = holder.get('window')
        if runtime and window:
            window.load_url(runtime.base_url + '/months')

    def data_location():
        runtime = holder.get('runtime')
        window = holder.get('window')
        path = runtime.ledger_path if runtime else ledger_path
        if window and path:
            window.create_confirmation_dialog(
                '数据位置',
                f'当前账本：\n{path}\n\nCodex／Claude MCP 的 --db 应指向同一绝对路径。',
            )

    def quit_app():
        window = holder.get('window')
        if window:
            window.destroy()

    return [
        webview.Menu('__app__', [
            menu_items.MenuAction('回到月份总览', home),
            menu_items.MenuAction('数据位置', data_location),
            menu_items.MenuSeparator(),
            menu_items.MenuAction('退出', quit_app),
        ])
    ]


def run_desktop(
    ledger_path: str | Path | None = None,
    *,
    config_dir: str | Path | None = None,
    webview_module=None,
) -> int:
    """Run the one-window desktop application; intended to be called on main thread."""
    webview = webview_module or _import_webview()
    log_dir = Path(config_dir) if config_dir else application_support_dir()
    log_path = configure_logging(log_dir)
    logger = logging.getLogger('livecommerce.desktop')
    holder: dict = {'runtime': None, 'window': None}
    config_dir_path = Path(config_dir) if config_dir else None

    try:
        selected = canonical_ledger_path(ledger_path) if ledger_path else load_ledger_path(config_dir_path)
    except DesktopConfigError as exc:
        logger.error('配置读取失败：%s', exc)
        raise WorkbenchRuntimeError(f'{exc}；日志：{log_path}') from exc

    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['ALLOW_FILE_URLS'] = False

    def close_window(*_args):
        runtime = holder.get('runtime')
        window = holder.get('window')
        if runtime is None:
            return True
        if not runtime.prepare_close():
            if window:
                window.create_confirmation_dialog(
                    '操作仍在进行', '导入、确认、关账或导出尚未结束，请稍后再退出。'
                )
            return False
        runtime.stop(prepared=True)
        holder['runtime'] = None
        logger.info('桌面工作台正常退出')
        return True

    def initialize(window):
        nonlocal selected
        try:
            create = False
            if selected is None:
                choice = _select_ledger(window, webview, config_dir_path)
                if choice is None:
                    logger.info('用户取消首次账本选择')
                    window.destroy()
                    return
                selected, create = choice
            runtime = start_workbench(selected, port=0, create_if_missing=create)
            holder['runtime'] = runtime
            save_ledger_path(runtime.ledger_path, config_dir_path)
            logger.info('桌面工作台启动；账本=%s；端口=%s', runtime.ledger_path, runtime.server.server_port)
            window.load_url(runtime.base_url + '/months')
            window.show()
        except Exception as exc:
            logger.exception('桌面工作台启动失败')
            window.create_confirmation_dialog(
                '工作台无法启动', f'{exc}\n\n请检查账本路径后重试。日志：{log_path}'
            )
            window.destroy()

    initial_url = None if selected is None else 'about:blank'
    window = webview.create_window(
        TITLE,
        url=initial_url,
        html='<p style="font:16px sans-serif;padding:24px">正在启动工作台…</p>',
        width=1200,
        height=800,
        min_size=(840, 600),
        resizable=True,
        hidden=True,
        text_select=True,
    )
    holder['window'] = window
    window.events.closing += close_window
    try:
        webview.start(initialize, window, menu=_menu(webview, holder, selected), debug=False)
    finally:
        runtime = holder.get('runtime')
        if runtime is not None:
            runtime.stop()
            holder['runtime'] = None
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='直播经营复盘桌面工作台')
    parser.add_argument('--db', type=Path, help='首次开发验证可明确指定账本路径')
    parser.add_argument('--config-dir', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        return run_desktop(args.db, config_dir=args.config_dir)
    except WorkbenchRuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
