import copy
import hashlib
import io
import json
import struct
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8765', headers={'X-Stillroom-Request':'local'}) as c:
        yield c

def project(c):
    r=c.post('/api/projects',json={'title':'My room'})
    assert r.status_code==200,r.text
    return r.json()

def glb(external=False):
    binary=struct.pack('<9f',0,0,0,1,0,0,0,1,0)
    doc={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}], 'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}], 'buffers':[{'byteLength':len(binary)}], 'bufferViews':[{'buffer':0,'byteLength':len(binary)}], 'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3','min':[0,0,0],'max':[1,1,0]}]}
    if external:doc['buffers'][0]['uri']='https://evil.invalid/scan.bin'
    js=json.dumps(doc).encode();js+=b' '*((-len(js))%4)
    return struct.pack('<III',0x46546c67,2,28+len(js)+len(binary))+struct.pack('<II',len(js),0x4e4f534a)+js+struct.pack('<II',len(binary),0x004e4942)+binary

def upload(c,p,data=None):
    r=c.post(f"/api/projects/{p['id']}/assets",data={'role':'scan'},files={'file':('room.glb',data or glb(),'model/gltf-binary')})
    assert r.status_code==200,r.text
    return r.json()

def test_revision_conflict_recovery_and_reopen(client,tmp_path):
    p=project(client);old=copy.deepcopy(p);p['title']='Changed'
    r=client.put(f"/api/projects/{p['id']}",json={'expectedRevision':p['revision'],'project':p})
    assert r.status_code==200,r.text
    assert r.json()['revision']==p['revision']+1
    assert client.put(f"/api/projects/{p['id']}",json={'expectedRevision':old['revision'],'project':old}).status_code==409
    restored=client.post(f"/api/projects/{p['id']}/recover").json()
    assert restored['title']=='My room'
    client.app.state.reconstruction.close()  # A restart releases ownership before opening the same data root.
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8765') as reopened:
        assert reopened.get(f"/api/projects/{p['id']}").json()==restored

def test_security(client):
    assert client.post('/api/projects',json={'title':'x'},headers={'X-Stillroom-Request':'bad'}).status_code==403
    assert client.post('/api/projects',json={'title':'x'},headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.get('/api/health',headers={'Host':'evil.invalid'}).status_code==403

def test_csp_allows_embedded_texture_fetch_but_no_remote_connections(client):
    policy=client.get('/api/health').headers['content-security-policy']
    directives={parts[0]:parts[1:] for item in policy.split(';') if (parts:=item.strip().split())}
    assert set(directives['connect-src'])=={"'self'",'blob:'}

def test_assets_immutable_integrity_and_relink(client,tmp_path):
    p=project(client);a=upload(client,p)
    p=client.get(f"/api/projects/{p['id']}").json()
    assert p['assets'][0]['sha256']==hashlib.sha256(glb()).hexdigest()
    changed=copy.deepcopy(p);changed['assets'][0]['path']='../../secrets'
    assert client.put(f"/api/projects/{p['id']}",json={'expectedRevision':p['revision'],'project':changed}).status_code==422
    path=tmp_path/'assets'/p['id']/a['path'];path.unlink()
    assert client.get(f"/api/projects/{p['id']}/integrity").json()['missing']==[a['id']]
    r=client.post(f"/api/projects/{p['id']}/assets/{a['id']}/relink",files={'file':('room.glb',glb())})
    assert r.status_code==200,r.text
    assert client.get(f"/api/projects/{p['id']}/integrity").json()=={'missing':[],'corrupt':[]}

def test_glb_remote_rejected(client):
    p=project(client)
    r=client.post(f"/api/projects/{p['id']}/assets",data={'role':'scan'},files={'file':('room.glb',glb(True))})
    assert r.status_code==422

def test_archive_roundtrip_and_traversal(client):
    p=project(client);a=upload(client,p)
    archive=client.get(f"/api/projects/{p['id']}/export")
    assert archive.status_code==200
    imported=client.post('/api/import',files={'file':('room.zip',archive.content)})
    assert imported.status_code==200,imported.text
    assert imported.json()['id']!=p['id']
    assert imported.json()['assets'][0]['id']==a['id']
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr('../evil',b'bad')
    assert client.post('/api/import',files={'file':('bad.zip',buf.getvalue())}).status_code==422

def test_soft_delete_restore(client):
    p=project(client)
    assert client.delete(f"/api/projects/{p['id']}").status_code==200
    assert client.get('/api/projects').json()[0]['deletedAt']
    assert client.post(f"/api/projects/{p['id']}/restore").json()['deletedAt'] is None

def test_fts_memory_search(client):
    p=project(client);a=upload(client,p);p=client.get(f"/api/projects/{p['id']}").json()
    p['room'].update(scanAssetId=a['id'],scanVersion=a['sha256'],sceneType='mesh')
    p['memories']=[{'id':'e9e3e0e9-0781-4291-a92c-f131841a2c81','title':'Summer afternoon','note':'The quiet yellow chair','date':None,'tags':['family'],'anchor':{'position':[0,0,0],'assetId':a['id'],'scanVersion':a['sha256'],'placement':'surface','needsReview':False},'mediaIds':[],'viewpoint':None,'createdAt':p['createdAt'],'updatedAt':p['createdAt'],'deletedAt':None}]
    r=client.put(f"/api/projects/{p['id']}",json={'expectedRevision':p['revision'],'project':p});assert r.status_code==200,r.text
    assert len(client.get(f"/api/projects/{p['id']}/search",params={'q':'yellow'}).json())==1

def test_archive_checksum_symlink_and_duplicate_rejected(client):
    p=project(client);a=upload(client,p)
    exported=client.get(f"/api/projects/{p['id']}/export").content
    with zipfile.ZipFile(io.BytesIO(exported)) as z:entries={n:z.read(n) for n in z.namelist()}
    for mode in ('checksum','symlink','duplicate'):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            for name,value in entries.items():
                if mode=='checksum' and name.startswith('assets/'):value=b'bad'
                if mode=='symlink' and name.startswith('assets/'):
                    info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o120777<<16;z.writestr(info,value)
                else:z.writestr(name,value)
            if mode=='duplicate':
                with pytest.warns(UserWarning):z.writestr('manifest.json',entries['manifest.json'])
        assert client.post('/api/import',files={'file':('bad.zip',buf.getvalue())}).status_code==422
    assert len(client.get('/api/projects').json())==1

def test_range_and_wrong_relink(client):
    p=project(client);a=upload(client,p)
    r=client.get(f"/api/projects/{p['id']}/assets/{a['id']}",headers={'Range':'bytes=0-11'})
    assert r.status_code==206 and r.content==glb()[:12]
    assert client.post(f"/api/projects/{p['id']}/assets/{a['id']}/relink",files={'file':('a.glb',b'wrong')}).status_code==422

def test_glb_rejects_unreferenced_nan_accessor(client):
    data=bytearray(glb());length=struct.unpack_from('<I',data,12)[0]
    doc=json.loads(data[20:20+length]);binary=bytes(data[28+length:])+struct.pack('<f',float('nan'))
    doc['buffers'][0]['byteLength']=len(binary)
    doc['bufferViews'].append({'buffer':0,'byteOffset':36,'byteLength':4})
    doc['accessors'].append({'bufferView':1,'componentType':5126,'count':1,'type':'SCALAR'})
    js=json.dumps(doc).encode();js+=b' '*((-len(js))%4)
    data=struct.pack('<III',0x46546c67,2,28+len(js)+len(binary))+struct.pack('<II',len(js),0x4e4f534a)+js+struct.pack('<II',len(binary),0x004e4942)+binary
    p=project(client)
    assert client.post(f"/api/projects/{p['id']}/assets",data={'role':'scan'},files={'file':('nan.glb',data)}).status_code==422

def test_host_rejects_url_syntax(client):
    for host in ('localhost?bad','localhost#bad','localhost:8765/path','user@localhost:8765'):
        assert client.get('/api/health',headers={'Host':host}).status_code==403

def test_unknown_media_rejected(client):
    p=project(client)
    assert client.post(f"/api/projects/{p['id']}/assets",data={'role':'media'},files={'file':('photo.png',b'not png')}).status_code==422

@pytest.mark.parametrize('kind',['mesh','ply','spz'])
def test_demo_roundtrip(client,kind):
    r=client.post('/api/demo',params={'kind':kind})
    assert r.status_code==200,r.text
    p=r.json()
    assert p['room']['synthetic'] and p['room']['scanAssetId']
    assert p['memories'][0]['mediaIds']
    assert client.get(f"/api/projects/{p['id']}/integrity").json()=={'missing':[],'corrupt':[]}
    exported=client.get(f"/api/projects/{p['id']}/export")
    imported=client.post('/api/import',files={'file':('demo.zip',exported.content)})
    assert imported.status_code==200,imported.text
    assert imported.json()['memories']==p['memories']
    assert imported.json()['room']==p['room']

def test_concurrent_save_only_one_commits(client):
    from concurrent.futures import ThreadPoolExecutor
    p=project(client)
    def save(title):
        draft=copy.deepcopy(p);draft['title']=title
        return client.put(f"/api/projects/{p['id']}",json={'expectedRevision':p['revision'],'project':draft}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(save,['A','B']))
    assert sorted(statuses)==[200,409]
    assert client.get(f"/api/projects/{p['id']}").json()['revision']==p['revision']+1
