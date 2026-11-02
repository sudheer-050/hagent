"""Build an offline test MSIX from the current Windows Python and installed dependencies.

Run with the Python environment used to test Hagent. No user database, credentials,
logs, or site-packages outside the declared dependency graph are bundled.
"""
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def dependency_names():
    pending = ['fastapi', 'uvicorn[standard]', 'jinja2', 'sqlalchemy', 'click', 'anthropic',
               'httpx', 'python-multipart', 'psutil', 'apscheduler', 'mcp', 'cryptography', 'pywinpty']
    seen = set()
    names = set()
    while pending:
        requirement = Requirement(pending.pop())
        name = canonicalize_name(requirement.name)
        extras = requirement.extras or {''}
        key = (name, tuple(sorted(extras)))
        if key in seen:
            continue
        seen.add(key)
        distribution = metadata.distribution(name)
        if requirement.specifier and not requirement.specifier.contains(distribution.version):
            raise RuntimeError(f'{requirement}: installed {distribution.version} is incompatible')
        names.add(name)
        for value in distribution.requires or []:
            child = Requirement(value)
            if child.marker is None or any(child.marker.evaluate({'extra': extra}) for extra in extras):
                pending.append(value)
    return sorted(names)


def main():
    if os.name != 'nt':
        raise SystemExit('Build this package on Windows x64.')
    stamp = __import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')
    stage = ROOT / 'build' / ('windows-test-' + stamp)
    runtime = stage / 'runtime'
    runtime.mkdir(parents=True)
    base = Path(sys.base_prefix)
    for pattern in ('python*.exe', 'python*.dll', 'vcruntime*.dll', 'LICENSE.txt'):
        for source in base.glob(pattern):
            shutil.copy2(source, runtime / source.name)
    shutil.copytree(base / 'DLLs', runtime / 'DLLs', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(base / 'Lib', runtime / 'Lib', ignore=shutil.ignore_patterns(
        'site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'ensurepip', 'tkinter', 'turtledemo'))
    packages = runtime / 'Lib' / 'site-packages'
    packages.mkdir()
    versions = {}
    for name in dependency_names():
        dist = metadata.distribution(name)
        versions[name] = dist.version
        for record in dist.files or []:
            relative = Path(record)
            if '..' in relative.parts or relative.is_absolute() or relative.suffix == '.pyc':
                continue
            # Never embed editable-install absolute paths or installer provenance.
            if relative.name == 'direct_url.json' or relative.suffix == '.pth':
                continue
            source = Path(dist.locate_file(record))
            if source.is_file():
                target = packages / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    # Copy only source and application resources, never run history or generated uploads.
    for source in (ROOT / 'hagent').rglob('*'):
        if (source.is_file() and '__pycache__' not in source.parts
                and (source.suffix in {'.py', '.html', '.css', '.js', '.png', '.webp', '.svg'} or source.name == 'LICENSE')
                and not source.name.startswith('user_app_icon')):
            target = packages / 'hagent' / source.relative_to(ROOT / 'hagent')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (runtime / f'python{sys.version_info.major}{sys.version_info.minor}._pth').write_text(
        '.\nLib\nDLLs\nLib/site-packages\nLib/site-packages/win32\n'
        'Lib/site-packages/win32/lib\nLib/site-packages/pywin32_system32\nimport site\n', encoding='ascii')
    shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE.txt')
    shutil.copy2(ROOT / 'packaging/windows/START_HERE.txt', stage / 'START_HERE.txt')
    shutil.copy2(ROOT / 'packaging/windows/AppxManifest.xml', stage / 'AppxManifest.xml')
    (stage / 'runtime-versions.json').write_text(json.dumps({
        'python': sys.version, 'dependencies': versions, 'release': 'TEST ONLY'}, indent=2), encoding='utf-8')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
        '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll',
        '/reference:System.Web.Extensions.dll', '/out:' + str(stage / 'Hagent.exe'),
        str(ROOT / 'packaging/windows/Launcher.cs')], check=True)
    # Reuse the application's existing icon. Build-time resize only, no network.
    asset_script = ROOT / 'packaging/windows/assets.ps1'
    subprocess.run(['powershell.exe', '-NoProfile', '-File', str(asset_script),
                    '-Destination', str(stage / 'Assets')], check=True)
    smoke_env = dict(os.environ, HAGENT_DESKTOP_DATA=str(stage.parent / ('smoke-' + stamp)))
    subprocess.run([str(stage / 'Hagent.exe'), '--smoke-test'], env=smoke_env, check=True, timeout=60)
    subprocess.run([str(runtime / 'python.exe'), '-c',
                    'import winpty, hagent.web, mcp; print("Packaged imports OK")'], check=True)
    sdk = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Windows Kits/10/bin'
    candidates = sorted(sdk.glob('10.*/x64/makeappx.exe'), reverse=True)
    if not candidates:
        raise SystemExit('Install the Windows SDK (MakeAppx); staged payload: ' + str(stage))
    dist_dir = ROOT / 'dist'
    dist_dir.mkdir(exist_ok=True)
    output = dist_dir / ('Hagent-Test-' + stamp + '.msix')
    subprocess.run([str(candidates[0]), 'pack', '/d', str(stage), '/p', str(output), '/o'], check=True)
    portable = shutil.make_archive(str(dist_dir / ('Hagent-Portable-Test-' + stamp)), 'zip', stage)
    print('Portable test ZIP:', portable)
    print('Unsigned test installer:', output)
    print('Payload:', stage)
    print('Test only: validate a supported Python runtime, signing and clean-machine installation before release.')


if __name__ == '__main__':
    main()
