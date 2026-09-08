from types import SimpleNamespace

from src.desktop.app import run_desktop
from src.desktop.config import load_ledger_path


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(closing=Event())
        self.urls = []
        self.shown = False
        self.destroyed = False
        self.dialogs = []

    def create_confirmation_dialog(self, title, message):
        self.dialogs.append((title, message))
        return False  # first run: create the default ledger

    def create_file_dialog(self, *_args, **_kwargs):
        raise AssertionError('new-ledger flow must not open a file chooser')

    def load_url(self, url):
        self.urls.append(url)

    def show(self):
        self.shown = True

    def destroy(self):
        self.destroyed = True


class FakeMenuItem:
    def __init__(self, *args):
        self.args = args


class FakeWebview:
    class FileDialog:
        OPEN = 'open'

    Menu = FakeMenuItem
    menu = SimpleNamespace(MenuAction=FakeMenuItem, MenuSeparator=FakeMenuItem)

    def __init__(self):
        self.settings = {}
        self.window = FakeWindow()
        self.start_kwargs = None

    def create_window(self, *_args, **_kwargs):
        return self.window

    def start(self, func, args, **kwargs):
        self.start_kwargs = kwargs
        func(args)
        assert self.window.events.closing.handlers[0]() is True


def test_desktop_first_run_creates_configured_ledger_and_stops_cleanly(tmp_path):
    webview = FakeWebview()

    assert run_desktop(config_dir=tmp_path, webview_module=webview) == 0

    assert webview.settings['ALLOW_DOWNLOADS'] is True
    assert webview.settings['ALLOW_FILE_URLS'] is False
    assert webview.window.shown is True
    assert webview.window.urls[-1].endswith('/months')
    assert load_ledger_path(tmp_path) == (tmp_path / 'workbench.db').resolve()
    assert (tmp_path / 'workbench.db').exists()
    assert webview.start_kwargs['debug'] is False
