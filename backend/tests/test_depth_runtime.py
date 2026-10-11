import json
import pytest
from backend import depth_runtime as runtime


def test_status_never_installs_or_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(runtime, '_download', lambda *a: pytest.fail('status downloaded'))
    assert not runtime.runtime_status()['available']
    assert list(tmp_path.iterdir()) == []


def test_status_requires_pinned_marker_and_complete_install(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'TOOL_ROOT', tmp_path)
    (tmp_path/'installed.json').write_text(json.dumps({'modelSha256':'fake'}))
    assert not runtime.runtime_status()['available']


def test_model_identity_is_fixed():
    assert runtime.MODEL_REVISION == 'f4a6c9b3c95e41c82048423d3493a81ec3fa810e'
    assert runtime.MODEL_SHA256 == 'e01067dc1659613083d9145a9a2547ccdbe6ccbbf83c4fe7b3e8a4e2bdae78b5'
    assert len(runtime.SOURCE_SHA256) == 64


def test_corrupt_weights_fail_before_model_execution(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'TOOL_ROOT',tmp_path)
    monkeypatch.setattr(runtime,'runtime_status',lambda:{'available':True})
    (tmp_path/'model.safetensors').write_bytes(b'corrupt')
    monkeypatch.setattr(runtime.subprocess,'run',lambda *a,**kw:pytest.fail('corrupt model executed'))
    with pytest.raises(ValueError,match='checksum'):runtime.predict([tmp_path/'a.jpg']*3,tmp_path/'out.npz')


def test_model_runtime_never_accepts_more_than_32_inputs(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'runtime_status',lambda:{'available':True})
    with pytest.raises(ValueError,match='32'):runtime.predict([tmp_path/'a.jpg']*33,tmp_path/'out.npz')
