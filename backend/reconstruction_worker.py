"""Local photo → CPU COLMAP cameras → undistorted dataset → Brush Gaussian PLY."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from PIL import Image, ImageOps
from .formats import image_info, validate_ply, MAX_IMAGE, MAX_ASSET, MAX_SPLAT_CENTRE
from .reconstruction_runtime import brush_executable, runtime_status, PYCOLMAP_VERSION, BRUSH_VERSION
from .splat_cleanup import clean_splats, scene_envelope

PROFILES = {
    'preview': {'totalSteps': 3000, 'maxResolution': 960, 'maxSplats': 300000, 'exportEvery': 1000},
    'standard': {'totalSteps': 7000, 'maxResolution': 1280, 'maxSplats': 750000, 'exportEvery': 1000},
}
_PROTOCOL = None
_BRUSH = None
_PROCESS_LOCK = threading.Lock()
_PARENT_GONE = threading.Event()


def emit(**event):
    encoded = (json.dumps(event, ensure_ascii=True, allow_nan=False) + '\n').encode()
    if _PROTOCOL is None:
        print(encoded.decode().rstrip(), flush=True)
    else:
        os.write(_PROTOCOL, encoded)


def validate_inputs(spec: dict) -> list[dict]:
    if spec.get('quality') not in PROFILES: raise ValueError('Choose preview or standard reconstruction quality.')
    images = spec.get('images')
    if not isinstance(images, list) or not 3 <= len(images) <= 200: raise ValueError('Choose 3–200 overlapping photographs of one room.')
    root = Path(spec['assetRoot']).resolve(strict=True)
    seen = set(); total = 0; validated = []
    for item in images:
        identifier = item.get('id', '')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier) or identifier in seen:
            raise ValueError('Photo IDs must be unique managed asset IDs.')
        seen.add(identifier)
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root) or path == root: raise ValueError('Photographs must resolve inside the managed asset directory.')
        size = path.stat().st_size
        if not path.is_file() or not 0 < size <= MAX_IMAGE: raise ValueError('Each original photograph must be nonempty and at most 40 MiB.')
        total += size
        if total > 256 * 1024 * 1024: raise ValueError('Source photographs together must not exceed 256 MiB.')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item.get('sha256'): raise ValueError('An original photo changed since upload. Upload it again before reconstructing.')
        info = image_info(data)
        with Image.open(io.BytesIO(data)) as source:
            orientation = source.getexif().get(274)
        size = (info['width'], info['height'])
        if orientation in (5, 6, 7, 8): size = size[::-1]
        validated.append({**item, 'source': path, 'imageSize': size})
    return validated


def camera_calibration(images: list[dict]) -> str:
    """Share intrinsics only for equal-size frames of one validated source video.

    The supervisor validates source identities against retained project assets.
    Worker preflight derives dimensions from verified image bytes, after EXIF
    orientation, and this check conservatively excludes incomplete provenance.
    """
    identities = set(); sizes = set()
    for item in images:
        frame = item.get('videoFrame')
        if not isinstance(frame, dict): return 'auto'
        identifier, digest = frame.get('sourceAssetId'), frame.get('sourceSha256')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier): return 'auto'
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest): return 'auto'
        for field, limit in (('timestampSeconds', 300.1), ('sharpness', 1e9)):
            value = frame.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= limit: return 'auto'
        identities.add((identifier, digest)); sizes.add(tuple(item['imageSize']))
    return 'shared-video' if len(identities) == len(sizes) == 1 else 'auto'


def normalize_images(images: list[dict], directory: Path) -> list[str]:
    import numpy as np
    directory.mkdir(parents=True, exist_ok=False)
    dark = bright = blur = duplicates = 0; hashes = []
    for index, item in enumerate(images):
        # Recheck immediately before use, including when the original changed during preflight.
        data = item['source'].read_bytes()
        if len(data) > MAX_IMAGE or hashlib.sha256(data).hexdigest() != item['sha256']: raise ValueError('An original photo changed during preparation. Upload it again.')
        with Image.open(io.BytesIO(data)) as source:
            normalized = ImageOps.exif_transpose(source).convert('RGB')
            normalized.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            gray = np.asarray(normalized.convert('L').resize((128, 128)), dtype=np.float32)
            dark += float(np.mean(gray < 20)) > .65
            bright += float(np.mean(gray > 240)) > .65
            laplacian = gray[1:-1, :-2] + gray[1:-1, 2:] + gray[:-2, 1:-1] + gray[2:, 1:-1] - 4 * gray[1:-1, 1:-1]
            blur += float(laplacian.var()) < 25
            small = np.asarray(normalized.convert('L').resize((9, 8)), dtype=np.int16)
            fingerprint = np.packbits(small[:, 1:] > small[:, :-1]).tobytes()
            bits = int.from_bytes(fingerprint, 'little')
            duplicates += any((bits ^ old).bit_count() <= 3 for old in hashes)
            hashes.append(bits)
            normalized.save(directory / (item['id'] + '.jpg'), 'JPEG', quality=95, subsampling=0)
        emit(stage='preparing', message=f'Prepared {index + 1} of {len(images)} photos locally.')
    warnings = []
    if dark: warnings.append(f'{dark} photos may be too dark. Even lighting improves matching.')
    if bright: warnings.append(f'{bright} photos may be overexposed. Retake washed-out views.')
    if blur: warnings.append(f'{blur} photos appear blurry or low in texture. Sharp textured views work best.')
    if duplicates: warnings.append(f'{duplicates} photos look very similar. Move sideways between views to add depth.')
    return warnings


def select_component(models: dict, image_ids: list[str]):
    ranked = sorted(models.items(), key=lambda pair: (-pair[1].num_reg_images(), -pair[1].num_points3D(), pair[0]))
    usable = [(key, model) for key, model in ranked if model.num_reg_images() >= 3 and model.num_points3D() > 0]
    selected = usable[0] if usable else (ranked[0] if ranked else (None, None))
    key, model = selected
    registered = {Path(image.name).stem for image in model.images.values()} if model is not None else set()
    unused = [identifier for identifier in image_ids if identifier not in registered]
    warnings = []
    if len(ranked) > 1: warnings.append('Matching produced multiple candidate camera groups, which may share photos. Only the largest usable group is used for this room.')
    if unused: warnings.append(f'{len(unused)} photos could not join the selected room. Add sharp overlapping views around those positions.')
    details = {'registeredPhotos': len(registered), 'unregisteredAssetIds': unused,
               'components': [model.num_reg_images() for _, model in ranked], 'warnings': warnings}
    if not usable: details['status'] = 'needs_photos'
    return (key if usable else None), details


def validate_output(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_ASSET:
        raise ValueError('The training engine did not produce the expected final export. Try again or use a smaller capture set.')
    return validate_ply(path.read_bytes())


def initial_view(model) -> dict | None:
    """Use one recovered camera in source space, independent of splat outliers.

    COLMAP camera axes are right/down/forward; Three cameras are right/up/back.
    This changes the camera basis only and never rotates the reconstructed scene.
    """
    import numpy as np
    import pycolmap
    candidates = []
    for image in model.images.values():
        try:
            center = np.asarray(image.projection_center(), dtype=float)
            if center.shape != (3,) or not np.isfinite(center).all() or np.max(np.abs(center)) > MAX_SPLAT_CENTRE:
                continue
            candidates.append((image.name, center, image))
        except (ValueError, TypeError, RuntimeError):
            continue
    if not candidates: return None
    median = np.median(np.stack([center for _, center, _ in candidates]), axis=0)
    candidates.sort(key=lambda item: (float(np.sum((item[1] - median) ** 2)), item[0]))
    for _, center, image in candidates:
        try:
            camera_to_world = image.cam_from_world().inverse()
            rotation = camera_to_world.rotation.matrix() @ np.diag([1., -1., -1.])
            if not np.isfinite(rotation).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or abs(np.linalg.det(rotation) - 1.) > 1e-6:
                continue
            quaternion = np.asarray(pycolmap.Rotation3d(rotation).quat, dtype=float)
            norm = float(np.linalg.norm(quaternion))
            if not np.isfinite(quaternion).all() or not .999 <= norm <= 1.001:
                continue
            quaternion /= norm
            if quaternion[3] < 0: quaternion = -quaternion
            return {'position': center.tolist(), 'quaternion': quaternion.tolist()}
        except (ValueError, TypeError, RuntimeError):
            continue
    return None


def upright_euler_xyz(up) -> list[float] | None:
    """Minimal rotation of an estimated up vector to +Y, as Three Euler XYZ."""
    import numpy as np
    up = np.asarray(up, dtype=float)
    if up.shape != (3,) or not np.isfinite(up).all(): return None
    length = float(np.linalg.norm(up))
    if length < 1e-6: return None
    up = up / length
    target = np.array([0., 1., 0.])
    dot = float(np.clip(np.dot(up, target), -1., 1.))
    if dot < -1. + 1e-10:
        # The antiparallel case has many minimal rotations; select a stable axis.
        axis = np.cross(up, np.eye(3)[int(np.argmin(np.abs(up)))])
        vector = axis / np.linalg.norm(axis)
        scalar = 0.
    else:
        quaternion = np.append(np.cross(up, target), 1. + dot)
        quaternion /= np.linalg.norm(quaternion)
        vector, scalar = quaternion[:3], float(quaternion[3])
    x, y, z = vector
    skew = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    rotation = np.eye(3) + 2. * scalar * skew + 2. * (skew @ skew)
    # Same XYZ matrix-to-Euler convention as Three.Euler.setFromRotationMatrix.
    ey = float(np.arcsin(np.clip(rotation[0, 2], -1., 1.)))
    if abs(rotation[0, 2]) < .9999999:
        ex = float(np.arctan2(-rotation[1, 2], rotation[2, 2]))
        ez = float(np.arctan2(-rotation[0, 1], rotation[0, 0]))
    else:
        ex = float(np.arctan2(rotation[2, 1], rotation[1, 1]))
        ez = 0.
    result = [ex, ey, ez]
    return result if np.isfinite(result).all() else None


def display_rotation(model) -> list[float] | None:
    """Estimate visual upright from recovered camera ups, not a physical floor."""
    import numpy as np
    ups = []
    for image in sorted(model.images.values(), key=lambda image: image.name):
        try:
            up = image.cam_from_world().inverse().rotation.matrix() @ np.array([0., -1., 0.])
            norm = float(np.linalg.norm(up))
            if np.isfinite(up).all() and .999 <= norm <= 1.001:
                ups.append(up / norm)
        except (ValueError, TypeError, RuntimeError):
            continue
    return upright_euler_xyz(np.mean(ups, axis=0)) if ups else None


@contextmanager
def native_log(path):
    """C++ logging uses file descriptors, so Python redirect_stdout is insufficient."""
    sys.stdout.flush(); sys.stderr.flush()
    old_out, old_err = os.dup(1), os.dup(2)
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, 'rb', buffering=0)
    pump = threading.Thread(target=drain_log, args=(source, path), daemon=True)
    pump.start()
    try:
        os.dup2(write_fd, 1); os.dup2(write_fd, 2)
        os.close(write_fd)
        yield
    finally:
        os.dup2(old_out, 1); os.dup2(old_err, 2)
        os.close(old_out); os.close(old_err)
        pump.join(timeout=5)
        if not pump.is_alive(): source.close()


def drain_log(source, path: Path, limit=8 * 1024 * 1024):
    """Keep bounded diagnostics while always draining the native process pipe."""
    written = min(path.stat().st_size, limit) if path.exists() else 0
    with path.open('ab', buffering=0) as log:
        while chunk := source.read(65536):
            if written < limit:
                chunk = chunk[:limit - written]
                log.write(chunk)
                written += len(chunk)


def build_brush_command(dataset: Path, output: Path, settings: dict) -> list[str]:
    return [str(brush_executable()), str(dataset), '--total-steps', str(settings['totalSteps']),
            '--max-resolution', str(settings['maxResolution']), '--max-splats', str(settings['maxSplats']),
            '--export-every', str(settings['exportEvery']), '--export-path', str(output),
            '--export-name', 'room_{iter}.ply', '--lpips-loss-weight', '0']


def train(dataset: Path, output: Path, settings: dict, log_path: Path):
    global _BRUSH
    output.mkdir(parents=True, exist_ok=False)
    # Brush's non-TTY progress bar does not reliably emit iteration lines. Actual
    # completed checkpoint exports provide honest, bounded progress counters.
    with _PROCESS_LOCK:
        if _PARENT_GONE.is_set(): raise RuntimeError('The reconstruction server stopped before training could start.')
        _BRUSH = subprocess.Popen(build_brush_command(dataset, output, settings), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pump = threading.Thread(target=drain_log, args=(_BRUSH.stdout, log_path), daemon=True)
    pump.start()
    try:
        last = 0
        while _BRUSH.poll() is None:
            checkpoints = [int(p.stem.split('_')[-1]) for p in output.glob('room_*.ply') if re.fullmatch(r'room_\d+', p.stem)]
            progress = max([0, *[n for n in checkpoints if n <= settings['totalSteps']]])
            if progress > last:
                last = progress
                emit(stage='training', message=f'Training checkpoint {progress} of {settings["totalSteps"]} exported.', iteration=progress, totalIterations=settings['totalSteps'])
            time.sleep(.5)
        code = _BRUSH.returncode
    finally:
        pump.join(timeout=5)
        if not pump.is_alive(): _BRUSH.stdout.close()
        _BRUSH = None
    if code: raise RuntimeError('Local training stopped before finishing. Check available memory and graphics support, then retry with preview quality or fewer photos.')


def reconstruct(job: Path):
    import pycolmap as colmap
    spec = json.loads((job / 'input.json').read_text())
    images = validate_inputs(spec)
    calibration = camera_calibration(images)
    if not runtime_status()['available']: raise RuntimeError('Install the local reconstruction runtime before starting a job.')
    ids = [item['id'] for item in images]
    warnings = normalize_images(images, job / 'images')
    emit(stage='matching', message='Finding shared features and camera positions on the CPU.', warnings=warnings)
    threads = min(8, max(1, (os.cpu_count() or 2) - 1))
    extraction = colmap.FeatureExtractionOptions(); extraction.use_gpu = False; extraction.num_threads = threads; extraction.max_image_size = 1600
    matching = colmap.FeatureMatchingOptions(); matching.use_gpu = False; matching.num_threads = threads
    matching.guided_matching = spec['quality'] == 'standard'
    reader = colmap.ImageReaderOptions(); reader.camera_model = 'SIMPLE_RADIAL'
    mapping = colmap.IncrementalPipelineOptions(); mapping.num_threads = threads; mapping.min_model_size = 3; mapping.multiple_models = True; mapping.ba_use_gpu = False; mapping.random_seed = 0
    database = job / 'database.db'; sparse = job / 'sparse'
    sparse.mkdir()
    with native_log(job / 'colmap.log'):
        camera_mode = colmap.CameraMode.SINGLE if calibration == 'shared-video' else colmap.CameraMode.AUTO
        colmap.extract_features(database, job / 'images', camera_mode=camera_mode, reader_options=reader, extraction_options=extraction, device=colmap.Device.cpu)
        emit(stage='matching', message='Matching overlapping views on the CPU.', warnings=warnings)
        colmap.match_exhaustive(database, matching_options=matching, device=colmap.Device.cpu)
        emit(stage='matching', message='Estimating connected camera groups and triangulated points.', warnings=warnings)
        models = colmap.incremental_mapping(database, job / 'images', sparse, options=mapping)
    selected, details = select_component(models, ids)
    details['warnings'] = (warnings + details['warnings'])[:12]
    if selected is None:
        emit(stage='matching', message='Not enough overlapping views could be reconstructed. Add more sharp photos with sideways movement and shared details.', **details)
        return
    model = models[selected]
    # Only this model is passed to undistortion: unrelated camera components must
    # never be combined. Brush consumes PINHOLE cameras and ignores distortion.
    selected_dir = job / 'selected'; selected_dir.mkdir(); model.write(selected_dir)
    dataset = job / 'dataset'
    with native_log(job / 'colmap.log'):
        colmap.undistort_images(dataset, selected_dir, job / 'images', image_names=[image.name for image in model.images.values()], num_threads=threads)
    settings = {**PROFILES[spec['quality']], 'lpipsLossWeight': 0, 'normalizedMaxResolution': 1600,
                'cameraCalibration': calibration, 'mappingRandomSeed': 0, 'guidedMatching': matching.guided_matching}
    emit(stage='training', message='Training the room locally from solved cameras and undistorted photos.', iteration=0, totalIterations=settings['totalSteps'], **details)
    output = job / 'exports'
    train(dataset, output, settings, job / 'brush.log')
    emit(stage='validating', message='Checking the final Gaussian PLY before publishing the room.', **details)
    expected = output / f'room_{settings["totalSteps"]}.ply'
    output_info = validate_output(expected)
    cleanup = None
    envelope = scene_envelope(model)
    if envelope is not None:
        cleaned = output / 'room_cleaned.ply'
        cleanup = clean_splats(expected, cleaned, center=envelope[0], radius=envelope[1])
        output_info = validate_output(cleaned)
        expected = cleaned
        settings.update(cleanupVersion=cleanup['version'], cleanupInputSplats=cleanup['inputSplats'],
                        cleanupRemovedSplats=cleanup['removedSplats'], cleanupSceneRadius=cleanup['sceneRadius'])
        if cleanup['removedSplats']:
            details['warnings'].append(f'{cleanup["removedSplats"]} extreme geometric outlier splats were removed. This does not repair blur or moving people.')
        elif not cleanup['applied']:
            details['warnings'].append('Many unstable splats were detected; this capture needs review. The raw geometry was retained.')
    metadata = {'engines': {'pycolmap': PYCOLMAP_VERSION, 'brush': BRUSH_VERSION}, 'quality': spec['quality'], 'settings': settings,
                'sourceAssetIds': ids, 'registeredAssetIds': [identifier for identifier in ids if identifier not in details['unregisteredAssetIds']],
                **details, 'sparsePoints': model.num_points3D(), 'output': output_info}
    if cleanup is not None: metadata['cleanup'] = cleanup
    view = initial_view(model)
    if view is not None: metadata['initialView'] = view
    upright = display_rotation(model)
    if upright is not None: metadata['displayRotation'] = upright
    (job / 'result.json').write_text(json.dumps(metadata, indent=2))
    emit(status='ready', stage='complete', message='The reconstructed room is ready to preview.', resultPath=str(expected.resolve()), metadata=metadata,
         iteration=settings['totalSteps'], totalIterations=settings['totalSteps'], **details)


def _watch_windows_parent(parent, kernel32=None):
    """Windows getppid stays unchanged after death; wait on the process handle."""
    handle = None
    try:
        if kernel32 is None:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
        # A handle continues to identify this process even if its PID is reused.
        handle = kernel32.OpenProcess(0x00100000, False, parent)  # SYNCHRONIZE
        if handle:
            while kernel32.WaitForSingleObject(handle, 1000) == 258:  # WAIT_TIMEOUT
                pass
        # Signaled, missing, or failed handles all stop this dependent worker.
    finally:
        if handle: kernel32.CloseHandle(handle)
        with _PROCESS_LOCK:
            _PARENT_GONE.set()
            child = _BRUSH
        if child is not None:
            try:
                result = subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                if result.returncode: child.terminate()
            except (OSError, subprocess.TimeoutExpired):
                try: child.terminate()
                except OSError: pass
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    child.kill()
                    child.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired): pass
            except OSError: pass
        os._exit(1)


def _watch_parent(parent):
    if os.name == 'nt':
        _watch_windows_parent(parent)
        return
    while True:
        time.sleep(1)
        if os.getppid() != parent:
            if _BRUSH is not None:
                try: _BRUSH.terminate()
                except OSError: pass
            if os.name != 'nt' and os.getpgrp() == os.getpid(): os.killpg(os.getpgrp(), signal.SIGTERM)
            os._exit(1)


def main():
    global _PROTOCOL
    _PROTOCOL = os.dup(1)
    threading.Thread(target=_watch_parent, args=(os.getppid(),), daemon=True).start()
    try:
        if len(sys.argv) != 2: raise ValueError('Provide one reconstruction job directory.')
        job = Path(sys.argv[1]).resolve(strict=True)
        reconstruct(job)
        return 0
    except Exception as exc:
        # Native logs stay local; user-facing errors never expose absolute paths.
        safe = str(exc) if isinstance(exc, (ValueError, RuntimeError)) and '/' not in str(exc) and '\\' not in str(exc) and len(str(exc)) < 600 else 'Local reconstruction failed. Check that the photos overlap, then retry; diagnostic logs remain in this job.'
        emit(status='failed', stage='complete', message=safe, error=safe)
        return 1


if __name__ == '__main__': raise SystemExit(main())
