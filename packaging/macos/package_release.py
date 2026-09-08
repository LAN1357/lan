"""Package a verified desktop bundle and committed source for GitHub Releases.

Run after committing the release. No local ledger or user settings are copied.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import plistlib
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default='v0.1.0-build5')
    parser.add_argument('--app', type=Path, default=root/'packaging/macos/dist/直播经营复盘工作台.app')
    parser.add_argument('--output', type=Path, default=root/'release-artifacts')
    args = parser.parse_args()
    if not args.tag or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._' for c in args.tag):
        parser.error('tag must be a simple release name')
    info = plistlib.loads((args.app/'Contents/Info.plist').read_bytes())
    expected = f'v{info["CFBundleShortVersionString"]}-build{info["CFBundleVersion"]}'
    if args.tag != expected:
        parser.error(f'application version does not match tag: {expected}')
    subprocess.run(['codesign','--verify','--deep','--strict',str(args.app)],check=True)
    for path in args.app.rglob('*'):
        if path.suffix.lower() in ('.db','.sqlite','.sqlite3','.xlsx') or path.name in ('desktop.json','.env'):
            raise ValueError(f'private data cannot be packaged: {path.name}')
    output = args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    name = f'LiveCommerceWorkbench-{args.tag}'
    source = output/f'{name}-source.zip'
    subprocess.run(['git','archive','--format=zip',f'--prefix={name}-source/',f'--output={source}','HEAD'],cwd=root,check=True)
    desktop = output/f'{name}-macos-arm64.zip'
    with tempfile.TemporaryDirectory(prefix='livecommerce-public-release-') as temporary:
        folder = Path(temporary)/f'{name}-macos-arm64'
        folder.mkdir()
        subprocess.run(['ditto',str(args.app),str(folder/args.app.name)],check=True)
        (folder/source.name).write_bytes(source.read_bytes())
        document_paths = subprocess.check_output(
            ['git','ls-tree','-r','--name-only','-z','HEAD','docs'],cwd=root,text=True).rstrip('\0').split('\0')
        for relative in ['LICENSE','README.md','CHANGELOG.md',*document_paths]:
            destination = folder/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(subprocess.check_output(['git','show',f'HEAD:{relative}'],cwd=root))
        (folder/'开始使用.txt').write_text(
            '直播经营复盘工作台 · '+args.tag+'\n\n'
            '1. 解压后打开“直播经营复盘工作台.app”。当前为 Apple Silicon / macOS 13+ 开发验证包。\n'
            '2. 首次选择已有账本或创建新账本，数据存于应用包外。\n'
            '3. 体验请使用独立试用账本；docs/templates 内有空白模板和合成账例。\n'
            '4. 详细演练见 docs/workbench-quickstart.md；AI 接入见 docs/m3-readonly-analysis.md。\n'
            '5. 本包未做 Apple 开发者签名或公证；若系统阻止运行，请参考 README 的源码浏览器方案。\n'
            '6. 完整源码位于同目录 source.zip 后缀的压缩包，解压后按其 README 安装。\n\n'
            '不包含真实订单、账本、账号凭据或预配置的模型服务。\n',encoding='utf-8')
        subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(folder),str(desktop)],check=True)
    sums = output/'SHA256SUMS.txt'
    sums.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in (source,desktop)),encoding='utf-8')
    for path in (source,desktop,sums):
        print(path)


if __name__=='__main__':
    main()
