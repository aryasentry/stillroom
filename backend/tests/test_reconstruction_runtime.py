import hashlib
import io
import tarfile
import zipfile
import pytest


def test_checksum_rejects_corrupt_archive(tmp_path):
    from backend.reconstruction_runtime import verify_checksum
    archive=tmp_path/'a.zip';archive.write_bytes(b'altered')
    with pytest.raises(ValueError,match='checksum'):
        verify_checksum(archive,hashlib.sha256(b'original').hexdigest())

@pytest.mark.parametrize('name',['../escape','/absolute','C:/escape','nested/../../escape'])
def test_zip_extraction_rejects_escaping_names(tmp_path,name):
    from backend.reconstruction_runtime import safe_extract
    archive=tmp_path/'a.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr(name,b'bad')
    with pytest.raises(ValueError):safe_extract(archive,tmp_path/'dest')
    assert not (tmp_path/'escape').exists()


def test_tar_extraction_rejects_symlink(tmp_path):
    from backend.reconstruction_runtime import safe_extract
    archive=tmp_path/'a.tar.xz'
    with tarfile.open(archive,'w:xz') as t:
        member=tarfile.TarInfo('bin');member.type=tarfile.SYMTYPE;member.linkname='/tmp';t.addfile(member)
    with pytest.raises(ValueError):safe_extract(archive,tmp_path/'dest')


def test_archive_extracts_regular_files(tmp_path):
    from backend.reconstruction_runtime import safe_extract
    archive=tmp_path/'a.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('bin/brush_app',b'executable')
    safe_extract(archive,tmp_path/'dest')
    assert (tmp_path/'dest/bin/brush_app').read_bytes()==b'executable'
