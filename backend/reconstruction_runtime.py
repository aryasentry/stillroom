"""Pinned, repository-local reconstruction dependencies; status is read-only."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import ssl
import stat
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYCOLMAP_VERSION = '4.2.1'
BRUSH_VERSION = '0.3.0'
TOOL_ROOT = ROOT / '.tools' / 'brush' / 'v0.3.0'
BASE_URL = 'https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/'
ARCHIVES = {
    ('Darwin', 'arm64'): ('brush-app-aarch64-apple-darwin.tar.xz', '65b2631398c839be3c1d4d7160fe2326389dec87830aac0710985e6690a1048c'),
    ('Windows', 'amd64'): ('brush-app-x86_64-pc-windows-msvc.zip', 'b68e3e9cf052d51bf3ee30776fa5a364de7f2ba13b58443128ff797bb7bcfcd6'),
    ('Linux', 'x86_64'): ('brush-app-x86_64-unknown-linux-gnu.tar.xz', '4f0f9a8785d1951c62df26aae247c02c5bba32b00f40b06df4e1c9b867399e20'),
}
_lock = threading.Lock()
_installing = False
_error = None


def _platform_key():
    system, machine = platform.system(), platform.machine().lower()
    if system == 'Windows' and machine == 'x86_64': machine = 'amd64'
    return system, machine


def brush_executable() -> Path:
    return TOOL_ROOT / ('brush_app.exe' if platform.system() == 'Windows' else 'brush_app')


def runtime_status() -> dict:
    key = _platform_key()
    try: pycolmap = importlib.metadata.version('pycolmap')
    except importlib.metadata.PackageNotFoundError: pycolmap = None
    installed = False
    try:
        marker = json.loads((TOOL_ROOT / 'installed.json').read_text())
        installed = marker.get('version') == BRUSH_VERSION and marker.get('archiveSha256') == ARCHIVES.get(key, ('', ''))[1] and brush_executable().is_file()
    except (OSError, ValueError): pass
    available = installed and pycolmap == PYCOLMAP_VERSION
    supported = key in ARCHIVES
    return {'available': available, 'installing': _installing, 'supported': supported,
            'platform': '/'.join(key), 'message': 'Local reconstruction is ready.' if available else ('Installing local reconstruction tools.' if _installing else 'Install local reconstruction tools to build rooms from photos.' if supported else 'This platform has no supported pinned Brush binary. Use Apple silicon macOS or 64-bit Windows.'),
            'error': _error, 'versions': {'pycolmap': pycolmap, 'brush': BRUSH_VERSION if installed else None}}


def verify_checksum(path: Path, expected: str):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    if digest.hexdigest() != expected: raise ValueError('Native tool checksum mismatch; download was not installed.')


def _safe_name(name: str):
    path = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe archive path')
    return path


def safe_extract(archive: Path, destination: Path):
    """Extract only regular files/directories; reject links and bounded-size escapes."""
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()): raise ValueError('Extraction destination must be empty')
    total = 0
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as handle:
            entries = handle.infolist()
            if len(entries) > 10000: raise ValueError('Archive contains too many files')
            for item in entries:
                _safe_name(item.filename)
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))): raise ValueError('Archive links/devices are forbidden')
                total += item.file_size
            if total > 1024 * 1024 * 1024: raise ValueError('Archive exceeds unpacked size limit')
            for item in entries:
                target = destination / item.filename
                if item.is_dir(): target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with handle.open(item) as source, target.open('wb') as out: shutil.copyfileobj(source, out)
    else:
        with tarfile.open(archive, 'r:*') as handle:
            entries = handle.getmembers()
            if len(entries) > 10000: raise ValueError('Archive contains too many files')
            for item in entries:
                _safe_name(item.name)
                if not (item.isfile() or item.isdir()): raise ValueError('Archive links/devices are forbidden')
                total += item.size
            if total > 1024 * 1024 * 1024: raise ValueError('Archive exceeds unpacked size limit')
            for item in entries:
                target = destination / item.name
                if item.isdir(): target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with handle.extractfile(item) as source, target.open('wb') as out: shutil.copyfileobj(source, out)
                    target.chmod(item.mode & 0o755)


def _download(url: str, target: Path, limit: int):
    # certifi is already an application dependency (httpx); never disable TLS verification.
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError: context = ssl.create_default_context()
    request = urllib.request.Request(url, headers={'User-Agent': 'Stillroom-local-runtime/1'})
    with urllib.request.urlopen(request, timeout=60, context=context) as response, target.open('wb') as out:
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > limit: raise ValueError('Tool download exceeded the expected size limit')
            out.write(chunk)


def install_runtime(emit=None) -> None:
    global _installing, _error
    if not _lock.acquire(blocking=False): return
    report = emit or (lambda message: None)
    try:
        _installing = True; _error = None
        key = _platform_key()
        if key not in ARCHIVES: raise RuntimeError('No pinned native engine is available for this platform.')
        python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not python.is_file(): raise RuntimeError('Create the project .venv with the normal Stillroom setup first.')
        report('Installing pinned camera reconstruction dependencies into the project environment.')
        result = subprocess.run([str(python), '-m', 'pip', 'install', '--require-hashes', '-r', str(ROOT / 'requirements-reconstruction-lock.txt')], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        if result.returncode: raise RuntimeError('Camera dependency installation failed. Check internet access and your supported Python version, then retry.')
        if not runtime_status()['available']:
            name, checksum = ARCHIVES[key]
            TOOL_ROOT.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='brush-install-', dir=TOOL_ROOT.parent) as temporary:
                temporary = Path(temporary); archive = temporary / name; sidecar = temporary / (name + '.sha256')
                report('Downloading the pinned Brush engine and checking its SHA-256 checksum.')
                _download(BASE_URL + name, archive, 200 * 1024 * 1024)
                _download(BASE_URL + name + '.sha256', sidecar, 4096)
                if sidecar.read_text().split()[0].lower() != checksum: raise ValueError('Published tool checksum does not match the pinned release.')
                verify_checksum(archive, checksum)
                unpacked = temporary / 'unpacked'; safe_extract(archive, unpacked)
                binaries = list(unpacked.rglob(brush_executable().name))
                if len(binaries) != 1: raise ValueError('Native release does not contain the expected executable.')
                executable = binaries[0]; executable.chmod(executable.stat().st_mode | 0o700)
                help_result = subprocess.run([str(executable), '--help'], capture_output=True, text=True, timeout=30)
                expected = ('--total-steps', '--max-resolution', '--max-splats', '--export-every', '--export-path', '--export-name', '--lpips-loss-weight')
                if help_result.returncode or not all(flag in help_result.stdout for flag in expected): raise RuntimeError('The pinned engine CLI did not pass its compatibility check.')
                ready = temporary / 'ready'; shutil.copytree(executable.parent, ready)
                (ready / 'installed.json').write_text(json.dumps({'version': BRUSH_VERSION, 'archiveSha256': checksum, 'platform': '/'.join(key)}))
                # Installation is serialized and existing partial installs are replaceable.
                if TOOL_ROOT.exists(): shutil.rmtree(TOOL_ROOT)
                ready.rename(TOOL_ROOT)
        report('Local reconstruction tools are ready.')
    except Exception as exc:
        _error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Local runtime installation failed. Check internet access and retry.'
        raise RuntimeError(_error) from exc
    finally:
        _installing = False; _lock.release()
