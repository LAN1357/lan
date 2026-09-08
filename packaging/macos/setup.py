"""Build with: python packaging/macos/setup.py py2app."""

from pathlib import Path
import os
import subprocess
import sys

from setuptools import setup
from py2app import build_app


ROOT = Path(__file__).resolve().parents[2]
SETUP_DIR = Path(__file__).resolve().parent
os.chdir(SETUP_DIR)
sys.path.insert(0, str(ROOT))


def codesign_adhoc(bundle):
    """Sign rewritten Mach-O files before sealing the application bundle."""
    bundle = Path(bundle)
    macho_magics = {
        b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
        b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
        b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
        b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca',
    }
    binaries = []
    for path in bundle.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            magic = path.open('rb').read(4)
        except OSError:
            continue
        if magic in macho_magics:
            binaries.append(path)
    for path in sorted(binaries, key=lambda item: len(item.parts), reverse=True):
        subprocess.run(
            ['codesign', '--force', '--sign', '-', str(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    subprocess.run(
        ['codesign', '--force', '--deep', '--sign', '-', str(bundle)],
        check=True,
    )


build_app.codesign_adhoc = codesign_adhoc

OPTIONS = {
    'argv_emulation': False,
    'arch': 'arm64',
    'strip': True,
    'includes': ['WebKit', 'Foundation', 'webview'],
    'excludes': [
        'coverage', 'mcp', 'numpy', 'pandas', 'plotly', 'pydantic', 'pytest',
        'setuptools', 'test', 'tkinter', 'yaml',
        'src.adapters', 'src.importers', 'src.models', 'src.server', 'src.tools',
        'src.analysis.mcp_server', 'src.engine.calculator', 'src.engine.metrics',
    ],
    'plist': {
        'CFBundleName': '直播经营复盘工作台',
        'CFBundleDisplayName': '直播经营复盘工作台',
        'CFBundleIdentifier': 'com.local.livecommerce-workbench',
        'CFBundleShortVersionString': '0.1.0',
        'CFBundleVersion': '5',
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
    },
}

setup(
    name='LiveCommerceWorkbench',
    version='0.1.0',
    app=[str(ROOT / 'packaging' / 'macos' / 'launcher.py')],
    options={'py2app': OPTIONS},
)

# py2app signs before its final resource copies are complete on this toolchain.
# Seal the finished directory once more so macOS does not kill it at first load.
if 'py2app' in sys.argv:
    app_bundle = SETUP_DIR / 'dist' / '直播经营复盘工作台.app'
    codesign_adhoc(app_bundle)
    subprocess.run(
        ['codesign', '--verify', '--deep', '--strict', '--all-architectures', str(app_bundle)],
        check=True,
    )
