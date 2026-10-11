"""Media thumbnail behavior, original preservation, and atomic metadata failure."""
import hashlib
import io
import sqlite3
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend.app import create_app


def source_image(mode='RGB',size=(1600,900)):
    stream=io.BytesIO()
    image=Image.new(mode,size,(120,75,42,128) if mode=='RGBA' else (120,75,42))
    image.save(stream,format='PNG')
    return stream.getvalue()


@pytest.mark.parametrize('mode',['RGB','RGBA'])
def test_media_upload_creates_bounded_thumbnail_and_preserves_original(tmp_path,mode):
    app=create_app(tmp_path)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'X-Stillroom-Request':'local'}) as client:
        p=client.post('/api/projects',json={'title':'Photos'}).json()
        original=source_image(mode)
        result=client.post(f"/api/projects/{p['id']}/assets",data={'role':'media'},files={'file':('original.png',original)})
        assert result.status_code==200,result.text
        asset=result.json()
        assert asset['thumbnailId']
        saved=client.get(f"/api/projects/{p['id']}").json()
        assert saved['revision']==p['revision']+1
        assert len(saved['assets'])==2
        thumbnail=next(a for a in saved['assets'] if a['id']==asset['thumbnailId'])
        assert thumbnail['role']=='thumbnail' and thumbnail['kind']=='image'
        assert thumbnail['metadata']['derivedFrom']==asset['id']
        assert thumbnail['thumbnailId'] is None
        assert asset['sha256']==hashlib.sha256(original).hexdigest()
        assert client.get(f"/api/projects/{p['id']}/assets/{asset['id']}").content==original
        image_data=client.get(f"/api/projects/{p['id']}/assets/{thumbnail['id']}").content
        with Image.open(io.BytesIO(image_data)) as image:
            assert image.size==(640,360)
            assert image.width==thumbnail['metadata']['width']
            assert image.height==thumbnail['metadata']['height']
            if mode=='RGBA':assert image.mode=='RGBA'
        assert hashlib.sha256(image_data).hexdigest()==thumbnail['sha256']
        assert client.get(f"/api/projects/{p['id']}/integrity").json()=={'missing':[],'corrupt':[]}


def test_thumbnail_upload_does_not_generate_another_thumbnail(tmp_path):
    store=create_app(tmp_path).state.store
    from backend.models import Project
    p=store.create(Project(title='Direct thumbnail'))
    a=store.add_asset(p.id,'thumb.png',source_image(size=(100,50)),'thumbnail')
    assert a.thumbnailId is None
    assert len(store.get(p.id).assets)==1


def test_thumbnail_failure_keeps_project_revision_assets_and_fts_unchanged(tmp_path,monkeypatch):
    app=create_app(tmp_path);store=app.state.store
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'X-Stillroom-Request':'local'}) as client:
        p=client.post('/api/demo?kind=mesh').json()
        before=store.get(p['id'])
        before_files=set((tmp_path/'assets'/p['id']).iterdir())
        before_search=[m.id for m in store.search(p['id'],'afternoon')]
        write=store.write
        def interrupted_write(db,project):
            write(db,project)
            raise sqlite3.OperationalError('simulated transaction interruption')
        monkeypatch.setattr(store,'write',interrupted_write)
        with pytest.raises(sqlite3.OperationalError):
            store.add_asset(p['id'],'photo.png',source_image(),'media')
        assert store.get(p['id'])==before
        assert [m.id for m in store.search(p['id'],'afternoon')]==before_search
        assert set((tmp_path/'assets'/p['id']).iterdir())==before_files


def test_thumbnail_derivation_roundtrips_and_invalid_source_is_rejected(tmp_path):
    import json
    import zipfile
    app=create_app(tmp_path)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'X-Stillroom-Request':'local'}) as client:
        p=client.post('/api/projects',json={'title':'Portable photos'}).json()
        source=client.post(f"/api/projects/{p['id']}/assets",data={'role':'media'},files={'file':('photo.png',source_image())}).json()
        archive=client.get(f"/api/projects/{p['id']}/export").content
        imported=client.post('/api/import',files={'file':('photos.zip',archive)})
        assert imported.status_code==200,imported.text
        thumbnail=next(a for a in imported.json()['assets'] if a['id']==source['thumbnailId'])
        assert thumbnail['metadata']['derivedFrom']==source['id']
        with zipfile.ZipFile(io.BytesIO(archive)) as z:entries={n:z.read(n) for n in z.namelist()}
        manifest=json.loads(entries['manifest.json'])
        thumbnail=next(a for a in manifest['assets'] if a['id']==source['thumbnailId'])
        thumbnail['metadata']['derivedFrom']='a96495bc-4189-48e3-ac1f-f0e51041133c'
        entries['manifest.json']=json.dumps(manifest).encode()
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            for name,payload in entries.items():z.writestr(name,payload)
        assert client.post('/api/import',files={'file':('forged.zip',buffer.getvalue())}).status_code==422
