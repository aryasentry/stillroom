"""Optional local DA3 Base runtime. No media leaves this computer."""
from pathlib import Path
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
from .reconstruction_runtime import _download, safe_extract, verify_checksum

ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = ROOT / '.tools' / 'depth-anything-3'
SOURCE_REVISION = '3d835ec1a5802d64a8b8b15f817a1ab54809bfe4'
SOURCE_SHA256 = '98aa2dd53ab44b96cef5190ae4841c6ae51797d099b784e08e12a1a55bd3a69b'
MODEL_REVISION = 'f4a6c9b3c95e41c82048423d3493a81ec3fa810e'
MODEL_SHA256 = 'e01067dc1659613083d9145a9a2547ccdbe6ccbbf83c4fe7b3e8a4e2bdae78b5'
MODEL_ID = 'depth-anything-3.DA3-BASE'
_lock = threading.Lock()
_installing = False
_error = None


def python_path():
    return TOOL_ROOT / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def runtime_status():
    available = False
    try:
        marker = json.loads((TOOL_ROOT/'installed.json').read_text())
        available = marker == {'sourceRevision':SOURCE_REVISION, 'modelSha256':MODEL_SHA256} and all(p.is_file() for p in [python_path(), TOOL_ROOT/'model.safetensors', TOOL_ROOT/'source/src/depth_anything_3/configs/da3-base.yaml'])
    except (OSError, ValueError): pass
    supported = platform.system() in ('Darwin', 'Windows', 'Linux') and platform.machine().lower() in ('arm64','aarch64','amd64','x86_64') and (3,11) <= sys.version_info[:2] <= (3,13)
    return {'available':available,'installing':_installing,'supported':supported,'platform':platform.system(),
            'message':'Sparse-frame model is ready.' if available else 'Optional download: 517 MiB model plus PyTorch dependencies. Inference stays local.',
            'error':_error,'versions':{'model':MODEL_ID,'revision':MODEL_REVISION}}


def install_runtime():
    global _installing, _error
    if not _lock.acquire(blocking=False): return
    try:
        _installing=True;_error=None
        if not runtime_status()['supported']: raise RuntimeError('Use Python 3.11–3.13 on a supported 64-bit platform.')
        TOOL_ROOT.mkdir(parents=True,exist_ok=True)
        (TOOL_ROOT/'installed.json').unlink(missing_ok=True)
        cache=TOOL_ROOT/'cache';cache.mkdir(exist_ok=True)
        archive=cache/'source.tar.gz';weights=cache/'model.safetensors'
        for path,url,digest,limit in [
            (archive,f'https://codeload.github.com/ByteDance-Seed/Depth-Anything-3/tar.gz/{SOURCE_REVISION}',SOURCE_SHA256,64*1024**2),
            (weights,f'https://huggingface.co/depth-anything/DA3-BASE/resolve/{MODEL_REVISION}/model.safetensors',MODEL_SHA256,550*1024**2)]:
            try: verify_checksum(path,digest)
            except (OSError,ValueError):
                partial=path.with_suffix('.partial');_download(url,partial,limit);verify_checksum(partial,digest);partial.replace(path)
        with tempfile.TemporaryDirectory(dir=TOOL_ROOT,prefix='source-') as folder:
            unpacked=Path(folder)/'unpacked';safe_extract(archive,unpacked)
            source=unpacked/f'Depth-Anything-3-{SOURCE_REVISION}'
            if not (source/'src/depth_anything_3/configs/da3-base.yaml').is_file(): raise ValueError('Model source archive is incomplete.')
            if (TOOL_ROOT/'source').exists(): shutil.rmtree(TOOL_ROOT/'source')
            shutil.copytree(source,TOOL_ROOT/'source')
        shutil.copyfile(weights,TOOL_ROOT/'model.safetensors')
        if not python_path().is_file(): subprocess.run([sys.executable,'-m','venv',str(TOOL_ROOT/'venv')],check=True,timeout=120,capture_output=True)
        with (TOOL_ROOT/'setup.log').open('wb') as log:
            subprocess.run([str(python_path()),'-m','pip','install','-r',str(ROOT/'requirements-depth.txt')],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
            subprocess.run([str(python_path()),'-c','import torch, torchvision, safetensors, omegaconf, cv2, einops, imageio'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        (TOOL_ROOT/'installed.json').write_text(json.dumps({'sourceRevision':SOURCE_REVISION,'modelSha256':MODEL_SHA256}))
    except Exception as exc:
        _error='Sparse model preparation failed. Check the local model setup log and retry.'
        raise RuntimeError(_error) from exc
    finally:
        _installing=False;_lock.release()


def begin_install():
    global _installing
    if _installing or runtime_status()['available']: return runtime_status()
    def run():
        try: install_runtime()
        except RuntimeError: pass
    # Set before dispatch so the browser sees a pending installation immediately.
    _installing=True
    threading.Thread(target=run,name='stillroom-depth-install',daemon=True).start()
    return runtime_status()


def predict(paths, destination):
    """Bounded subprocess; its parent watchdog handles server shutdown/cancellation."""
    if not runtime_status()['available']: raise RuntimeError('Prepare the sparse-frame model before starting assisted reconstruction.')
    if not 3 <= len(paths) <= 32: raise ValueError('Assisted reconstruction needs 3–32 photos.')
    verify_checksum(TOOL_ROOT/'model.safetensors',MODEL_SHA256)
    destination=Path(destination);request=destination.with_suffix('.json')
    request.write_text(json.dumps({'images':[str(Path(p).resolve()) for p in paths],'output':str(destination.resolve())}))
    env={**os.environ,'PYTHONPATH':str(TOOL_ROOT/'source/src')+os.pathsep+str(ROOT),'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1'}
    with destination.with_suffix('.log').open('wb') as log:
        try: subprocess.run([str(python_path()),'-u','-m','backend.depth_worker',str(request)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc: raise RuntimeError('Sparse-frame camera estimation failed. Try Standard or a smaller, sharper capture set.') from exc
    import numpy as np
    from .depth_worker import validate_prediction
    if not destination.is_file() or destination.stat().st_size > 1024*1024: raise ValueError('Invalid model prediction size.')
    with np.load(destination,allow_pickle=False) as data: return validate_prediction(dict(data),len(paths))
