import pytest
from backend import archives
from backend.formats import InvalidAsset
from backend.models import Project
from backend.storage import Store


def test_export_rejects_before_allocating_archive_when_total_exceeds_import_limit(tmp_path,monkeypatch):
    store=Store(tmp_path);p=store.create(Project(title='Portable'))
    monkeypatch.setattr(archives,'MAX_ARCHIVE',128)
    def no_zip_allocation(*args,**kwargs):raise AssertionError('Oversized export allocated its ZIP')
    monkeypatch.setattr(archives.zipfile,'ZipFile',no_zip_allocation)
    with pytest.raises(InvalidAsset,match='limit'):archives.export_project(store,p)


def test_export_boundary_matches_real_stored_zip_size(tmp_path,monkeypatch):
    store=Store(tmp_path);p=store.create(Project(title='A portable room'))
    size=len(archives.export_project(store,p).getvalue())
    monkeypatch.setattr(archives,'MAX_ARCHIVE',size)
    assert len(archives.export_project(store,p).getvalue())==size
    monkeypatch.setattr(archives,'MAX_ARCHIVE',size-1)
    with pytest.raises(InvalidAsset,match='limit'):archives.export_project(store,p)
