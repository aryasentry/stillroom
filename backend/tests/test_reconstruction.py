"""Capture persistence and supervisor tests; controlled workers are NOT reconstruction evidence."""
import copy
import io
import json
import sys
import time
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.models import Project
from backend.storage import Store


def photo(color=0):
    out=io.BytesIO();Image.new('RGB',(32,24),(color,20,30)).save(out,format='PNG');return out.getvalue()

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8849',headers={'X-Stillroom-Request':'local'}) as c:yield c

def new(c):return c.post('/api/projects',json={'title':'Capture tests'}).json()
def capture(c,p,n=0):return c.post(f"/api/projects/{p['id']}/assets",data={'role':'capture'},files={'file':('photo.png',photo(n))})
def get(c,p):return c.get(f"/api/projects/{p['id']}").json()

def test_v1_migrates_without_losing_room_state(tmp_path):
    store=Store(tmp_path);p=store.create(Project(title='Legacy'));raw=p.model_dump();raw['schemaVersion']=1;raw.pop('captureAssetIds',None);raw['room']['floorY']=3
    with store.connect() as db:db.execute('UPDATE projects SET data=? WHERE id=?',(json.dumps(raw),p.id))
    restored=store.get(p.id)
    assert restored.schemaVersion==2
    assert restored.captureAssetIds==[]
    assert restored.room.floorY==3

def test_capture_dedup_remove_immutable_put_and_archive(client):
    p=new(client);response=capture(client,p);assert response.status_code==200,response.text
    a=response.json();assert a['role']=='capture' and a['thumbnailId']
    duplicate=capture(client,p).json();assert duplicate['id']==a['id']
    saved=get(client,p);assert saved['captureAssetIds']==[a['id']] and len(saved['assets'])==2
    bad=copy.deepcopy(saved);bad['captureAssetIds']=[]
    assert client.put(f"/api/projects/{p['id']}",json={'expectedRevision':saved['revision'],'project':bad}).status_code==422
    archive=client.get(f"/api/projects/{p['id']}/export")
    imported=client.post('/api/import',files={'file':('room.zip',archive.content)});assert imported.status_code==200,imported.text
    assert imported.json()['captureAssetIds']==[a['id']]
    thumb=next(x for x in imported.json()['assets'] if x['id']==a['thumbnailId']);assert thumb['metadata']['derivedFrom']==a['id']
    removed=client.delete(f"/api/projects/{p['id']}/captures/{a['id']}");assert removed.status_code==200
    assert removed.json()['captureAssetIds']==[] and len(removed.json()['assets'])==2
    assert client.get(f"/api/projects/{p['id']}/assets/{a['id']}").content==photo()
    assert capture(client,p).json()['id']==a['id']
    assert get(client,p)['captureAssetIds']==[a['id']]

def test_capture_rejects_non_photographs(client):
    p=new(client)
    r=client.post(f"/api/projects/{p['id']}/assets",data={'role':'capture'},files={'file':('movie.mp4',b'0000ftyp00000000mdat0000000')})
    assert r.status_code==422 and get(client,p)['assets']==[]

def test_capture_total_budgets_are_transactional(client,monkeypatch):
    import backend.storage as storage
    monkeypatch.setattr(storage,'MAX_CAPTURE_COUNT',3)
    p=new(client)
    for n in range(3):assert capture(client,p,n).status_code==200
    assert capture(client,p,4).status_code==422
    assert len(get(client,p)['captureAssetIds'])==3
    monkeypatch.setattr(storage,'MAX_CAPTURE_COUNT',200)
    monkeypatch.setattr(storage,'MAX_CAPTURE_BYTES',len(photo())+1)
    p2=new(client);assert capture(client,p2).status_code==200
    assert capture(client,p2,1).status_code==422
    assert len(get(client,p2)['assets'])==2

RUNTIME={'available':True,'installing':False,'supported':True,'platform':'test','message':'Ready','error':None,'versions':{'pycolmap':'4.2.1','brush':'0.3.0'}}

def prepare_worker(client,monkeypatch,code,count=3):
    status=client.get('/api/reconstruction/runtime');assert status.status_code==200,status.text
    import backend.reconstruction as jobs
    monkeypatch.setattr(jobs,'runtime_status',lambda:dict(RUNTIME))
    monkeypatch.setattr(jobs,'worker_command',lambda directory:[sys.executable,'-u','-c',code,str(directory)])
    p=new(client);ids=[capture(client,p,n).json()['id'] for n in range(count)]
    return p,ids

def start(client,p,ids):return client.post(f"/api/projects/{p['id']}/reconstructions",json={'imageIds':ids,'quality':'preview'})
def wait_job(client,p,j,statuses=('ready','failed','needs_photos','cancelled','interrupted')):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        response=client.get(f"/api/projects/{p['id']}/reconstructions/{j['id']}");assert response.status_code==200,response.text
        value=response.json()
        if value['status'] in statuses:return value
        time.sleep(.02)
    pytest.fail(f'Job did not finish: {value}')

READY_WORKER="""
import json,sys,pathlib,struct
root=pathlib.Path(sys.argv[1]);source=json.loads((root/'input.json').read_text())
properties=['x','y','z','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
data=('ply\\nformat binary_little_endian 1.0\\nelement vertex 1\\n'+''.join('property float '+p+'\\n' for p in properties)+'end_header\\n').encode()+struct.pack('<14f',0,0,0,.2,.3,.4,1,-2,-2,-2,1,0,0,0)
(root/'result.ply').write_bytes(data)
print(json.dumps({'stage':'training','message':'Training local scene','registeredPhotos':3,'iteration':3,'totalIterations':3,'components':[3]}),flush=True)
print(json.dumps({'status':'ready','stage':'complete','message':'Ready to preview','resultPath':str(root/'result.ply'),'metadata':{'settings':{'totalSteps':3,'maxSplats':100},'registeredAssetIds':[x['id'] for x in source['images']]}}),flush=True)
"""


def test_supervisor_preserves_validated_video_frame_provenance(client,monkeypatch):
    code="import json,sys,pathlib;p=json.loads((pathlib.Path(sys.argv[1])/'input.json').read_text());assert all(x['videoFrame']['sourceAssetId'] for x in p['images']);print(json.dumps({'status':'needs_photos'}))"
    p,_=prepare_worker(client,monkeypatch,code)
    store=client.app.state.store
    video=store.add_asset(p['id'],'original.mp4',b'0000ftyp00000000mdat0000000','media')
    frames=[(f'frame-{n}.png',photo(20+n),{'sourceAssetId':video.id,'sourceSha256':video.sha256,'timestampSeconds':n,'sharpness':100}) for n in range(3)]
    ids=store.add_video_frames(p['id'],video.id,frames)
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']=='needs_photos',job
    payload=json.loads((store.root/'reconstructions'/job['id']/'input.json').read_text())
    assert [x['videoFrame'] for x in payload['images']]==[frame[2] for frame in frames]


def test_supervisor_rejects_video_provenance_with_wrong_retained_source_hash(client,monkeypatch):
    p,ids=prepare_worker(client,monkeypatch,"raise AssertionError('Invalid inputs must not reach a worker')")
    store=client.app.state.store
    video=store.add_asset(p['id'],'original.mp4',b'0000ftyp00000000mdat0000000','media')
    project=store.get(p['id'])
    for asset in project.assets:
        if asset.id in ids:asset.metadata['videoFrame']={'sourceAssetId':video.id,'sourceSha256':'a'*64,'timestampSeconds':0,'sharpness':100}
    with store.connect() as db:store.write(db,project)
    response=start(client,p,ids)
    assert response.status_code==422
    assert 'checksum' in response.json()['detail']
    assert client.get(f"/api/projects/{p['id']}/reconstructions").json()==[]


def test_supervisor_retains_overlapping_candidate_component_counts(client,monkeypatch):
    code="import json;print(json.dumps({'status':'needs_photos','components':[3,3,2,2]}))"
    p,ids=prepare_worker(client,monkeypatch,code)
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['components']==[3,3,2,2]


@pytest.mark.parametrize('components',[[1]*51,[4],[-1],[True],[0]])
def test_supervisor_bounds_candidate_component_counts(components):
    from backend.reconstruction import ReconstructionManager
    assert 'components' not in ReconstructionManager._progress(None,{'totalPhotos':3,'sourceAssetIds':['a','b','c']},{'components':components})


@pytest.mark.parametrize('calibration,guided',[('auto',False),('shared-video',True)])
def test_reconstruction_settings_survive_archive(client,monkeypatch,calibration,guided):
    settings={'totalSteps':3,'cameraCalibration':calibration,'mappingRandomSeed':0,'guidedMatching':guided,'cleanupVersion':1,'cleanupInputSplats':100,'cleanupRemovedSplats':2,'cleanupSceneRadius':12.5}
    code=READY_WORKER.replace("{'totalSteps':3,'maxSplats':100}",repr(settings))
    p,ids=prepare_worker(client,monkeypatch,code)
    job=wait_job(client,p,start(client,p,ids).json());assert job['status']=='ready',job
    archive=client.get(f"/api/projects/{p['id']}/export").content
    restored=client.post('/api/import',files={'file':('provenance.zip',archive)}).json()
    scan=next(a for a in restored['assets'] if a['id']==job['resultAssetId'])
    assert scan['metadata']['reconstruction']['settings']==settings


def test_reconstruction_rejects_unknown_camera_calibration(client,monkeypatch):
    code=READY_WORKER.replace("{'totalSteps':3,'maxSplats':100}","{'cameraCalibration':'untrusted'}")
    p,ids=prepare_worker(client,monkeypatch,code)
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']=='failed' and job['resultAssetId'] is None


@pytest.mark.parametrize('guided',[0,1,'true',None])
def test_reconstruction_rejects_nonboolean_guided_matching(client,monkeypatch,guided):
    code=READY_WORKER.replace("{'totalSteps':3,'maxSplats':100}",repr({'guidedMatching':guided}))
    p,ids=prepare_worker(client,monkeypatch,code)
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']=='failed' and job['resultAssetId'] is None

def test_jobs_validate_ownership_and_global_single_active_cancel(client,monkeypatch):
    p,ids=prepare_worker(client,monkeypatch,'import time;time.sleep(60)')
    assert start(client,p,ids[:2]).status_code==422
    assert start(client,p,[ids[0]]*3).status_code==422
    other=new(client)
    assert start(client,other,ids).status_code==422
    started=start(client,p,ids);assert started.status_code==200,started.text;j=started.json()
    assert start(client,p,ids).status_code==409
    cancelled=client.post(f"/api/projects/{p['id']}/reconstructions/{j['id']}/cancel");assert cancelled.status_code==200,cancelled.text
    assert cancelled.json()['status']=='cancelled'
    assert all(client.get(f"/api/projects/{p['id']}/assets/{aid}").status_code==200 for aid in ids)
    assert start(client,p,ids).status_code==200


def test_ready_requires_real_gaussian_and_accept_preserves_old_room(client,monkeypatch):
    p,ids=prepare_worker(client,monkeypatch,READY_WORKER)
    from backend.tests.test_backend import glb
    a=client.post(f"/api/projects/{p['id']}/assets",data={'role':'scan'},files={'file':('old.glb',glb())}).json()
    p=get(client,p);p['room'].update(scanAssetId=a['id'],scanVersion=a['sha256'],sceneType='mesh',floorY=2,sourceUnits='metres')
    from backend.models import uid,now
    p['memories']=[{'id':uid(),'title':'Old place','note':'Keep me','date':None,'tags':[],'anchor':{'assetId':a['id'],'scanVersion':a['sha256'],'position':[1,2,3],'placement':'surface','needsReview':False},'mediaIds':[],'viewpoint':None,'createdAt':now(),'updatedAt':now(),'deletedAt':None}]
    saved=client.put(f"/api/projects/{p['id']}",json={'expectedRevision':p['revision'],'project':p});assert saved.status_code==200,saved.text
    j=start(client,p,ids).json();ready=wait_job(client,p,j);assert ready['status']=='ready',ready
    unchanged=get(client,p);assert unchanged['room']['scanAssetId']==a['id'] and unchanged['room']['floorY']==2
    assert ready['registeredPhotos']==3 and ready['iteration']==3
    assert client.post(f"/api/projects/{p['id']}/reconstructions/{j['id']}/accept",json={'expectedRevision':0}).status_code==409
    accepted=client.post(f"/api/projects/{p['id']}/reconstructions/{j['id']}/accept",json={'expectedRevision':unchanged['revision']});assert accepted.status_code==200,accepted.text
    result=accepted.json();assert result['room']['scanAssetId']==ready['resultAssetId'] and result['room']['sourceUnits']=='unknown'
    assert result['room']['floorY'] is None and result['memories'][0]['anchor']['needsReview'] is True
    assert result['memories'][0]['anchor']['assetId']==a['id'] and client.get(f"/api/projects/{p['id']}/assets/{a['id']}").content==glb()
    asset=next(x for x in result['assets'] if x['id']==ready['resultAssetId'])
    provenance=asset['metadata']['reconstruction'];assert [x['assetId'] for x in provenance['sourceImages']]==ids
    assert provenance['versions']==RUNTIME['versions']
    archive=client.get(f"/api/projects/{p['id']}/export")
    imported=client.post('/api/import',files={'file':('room.zip',archive.content)});assert imported.status_code==200,imported.text
    assert next(x for x in imported.json()['assets'] if x['id']==asset['id'])['metadata']['reconstruction']==provenance
    assert client.get('/api/reconstructions').json()[0]['status']=='accepted'
    client.delete(f"/api/projects/{p['id']}");assert client.get('/api/reconstructions').json()==[]

@pytest.mark.parametrize('code',[
    "pass",
    "import json;print(json.dumps({'status':'ready','stage':'complete','resultPath':'/etc/passwd'}))",
    "import pathlib,sys,json;p=pathlib.Path(sys.argv[1])/'bad.ply';p.write_bytes(b'not Gaussian');print(json.dumps({'status':'ready','stage':'complete','resultPath':str(p)}))",
    "import json;print(json.dumps({'status':'ready','stage':'complete','resultPath':'missing.ply'}))",
    "print('x'*65537)",
    READY_WORKER+"\nsys.exit(1)\n",
])
def test_exit_or_claim_alone_never_marks_ready(client,monkeypatch,code):
    p,ids=prepare_worker(client,monkeypatch,code);response=start(client,p,ids);assert response.status_code==200,response.text
    result=wait_job(client,p,response.json());assert result['status']=='failed' and result['resultAssetId'] is None,result
    assert get(client,p)['room']['scanAssetId'] is None

def test_removed_captures_cannot_start_job_but_job_snapshot_retained(client,monkeypatch):
    p,ids=prepare_worker(client,monkeypatch,'import time;time.sleep(60)')
    j=start(client,p,ids).json();client.delete(f"/api/projects/{p['id']}/captures/{ids[0]}")
    client.post(f"/api/projects/{p['id']}/reconstructions/{j['id']}/cancel")
    assert start(client,p,ids).status_code==422
    assert client.get(f"/api/projects/{p['id']}/reconstructions/{j['id']}").json()['sourceAssetIds']==ids

def test_restart_marks_unfinished_jobs_interrupted(client,monkeypatch):
    p,ids=prepare_worker(client,monkeypatch,'import time;time.sleep(60)');j=start(client,p,ids).json()
    manager=client.app.state.reconstruction
    manager.close()
    from backend.reconstruction import ReconstructionManager
    reopened=ReconstructionManager(client.app.state.store)
    try:assert reopened.get(p['id'],j['id'])['status']=='interrupted'
    finally:reopened.close()

def test_runtime_required_and_corrupt_originals_rejected(client,monkeypatch):
    p,ids=prepare_worker(client,monkeypatch,READY_WORKER)
    import backend.reconstruction as jobs
    monkeypatch.setattr(jobs,'runtime_status',lambda:{**RUNTIME,'available':False})
    assert start(client,p,ids).status_code==503
    assert client.get(f"/api/projects/{p['id']}/reconstructions").json()==[]
    monkeypatch.setattr(jobs,'runtime_status',lambda:dict(RUNTIME))
    asset=client.app.state.store.asset(p['id'],ids[0]);client.app.state.store.asset_path(p['id'],asset).write_bytes(b'changed')
    assert start(client,p,ids).status_code==409
    assert client.get(f"/api/projects/{p['id']}/reconstructions").json()==[]


def test_needs_photos_preserves_partial_registration_and_retry(client,monkeypatch):
    code="import json,sys,pathlib;p=json.loads((pathlib.Path(sys.argv[1])/'input.json').read_text());print(json.dumps({'status':'needs_photos','stage':'matching','message':'Add overlapping photos.','registeredPhotos':2,'unregisteredAssetIds':[p['images'][2]['id']],'components':[2],'warnings':['Weak overlap.']}))"
    p,ids=prepare_worker(client,monkeypatch,code);j=start(client,p,ids).json();job=wait_job(client,p,j)
    assert job['status']=='needs_photos' and job['registeredPhotos']==2 and job['unregisteredAssetIds']==[ids[2]] and job['components']==[2]
    assert job['resultAssetId'] is None and len(get(client,p)['assets'])==6
    assert start(client,p,ids).status_code==200


def test_accept_revalidates_result_bytes(client,monkeypatch):
    p,ids=prepare_worker(client,monkeypatch,READY_WORKER);job=wait_job(client,p,start(client,p,ids).json());assert job['status']=='ready'
    store=client.app.state.store;asset=store.asset(p['id'],job['resultAssetId']);store.asset_path(p['id'],asset).write_bytes(b'corrupt')
    current=get(client,p)
    assert client.post(f"/api/projects/{p['id']}/reconstructions/{job['id']}/accept",json={'expectedRevision':current['revision']}).status_code==409
    assert get(client,p)['room']['scanAssetId'] is None


def test_archive_rejects_forged_provenance_sources(client,monkeypatch):
    import zipfile
    p,ids=prepare_worker(client,monkeypatch,READY_WORKER);job=wait_job(client,p,start(client,p,ids).json());assert job['status']=='ready'
    data=client.get(f"/api/projects/{p['id']}/export").content;out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original,zipfile.ZipFile(out,'w') as modified:
        for name in original.namelist():
            payload=original.read(name)
            if name=='manifest.json':
                manifest=json.loads(payload);scan=next(a for a in manifest['assets'] if a['id']==job['resultAssetId'])
                scan['metadata']['reconstruction']['sourceImages'][0]['sha256']='0'*64;payload=json.dumps(manifest).encode()
            modified.writestr(name,payload)
    response=client.post('/api/import',files={'file':('forged.zip',out.getvalue())})
    assert response.status_code==422 and 'source' in response.text


def test_progress_is_bounded_and_logs_capped(client,monkeypatch):
    code="import json;\nfor n in range(100): print('log '+('x'*1000))\nprint(json.dumps({'status':'needs_photos','stage':'matching','registeredPhotos':9999,'components':[9999],'iteration':200,'totalIterations':2,'warnings':['/private/path leak']}))"
    p,ids=prepare_worker(client,monkeypatch,code);job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']=='needs_photos' and job['registeredPhotos']==0 and job['iteration'] is None and job['components']==[]
    assert '/private/' not in json.dumps(job)
    assert (client.app.state.store.root/'reconstructions'/job['id']/'supervisor.log').stat().st_size<=65536

def test_worker_ready_claim_does_not_show_complete_before_validation(client,monkeypatch):
    code=READY_WORKER+"\nimport time;time.sleep(1)\n"
    p,ids=prepare_worker(client,monkeypatch,code);job=start(client,p,ids).json()
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        current=client.get(f"/api/projects/{p['id']}/reconstructions/{job['id']}").json()
        if current['registeredPhotos']==3:break
        time.sleep(.01)
    assert current['status']=='running'
    assert current['stage']!='complete'
    assert 'ready' not in current['message'].lower()
    assert wait_job(client,p,job)['status']=='ready'

@pytest.mark.skipif(sys.platform=='win32',reason='POSIX process-group heartbeat verification')
def test_cancel_stops_descendant_worker_process(client,monkeypatch):
    child="import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile True:\n p.write_text(str(time.monotonic()))\n time.sleep(.03)"
    code=f"import subprocess,sys,pathlib,time\np=pathlib.Path(sys.argv[1])/'heartbeat'\nsubprocess.Popen([sys.executable,'-u','-c',{child!r},str(p)])\ntime.sleep(60)"
    p,ids=prepare_worker(client,monkeypatch,code);job=start(client,p,ids).json()
    marker=client.app.state.store.root/'reconstructions'/job['id']/'heartbeat';deadline=time.monotonic()+5
    while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert marker.exists()
    response=client.post(f"/api/projects/{p['id']}/reconstructions/{job['id']}/cancel");assert response.json()['status']=='cancelled'
    before=marker.read_bytes();time.sleep(.12);assert marker.read_bytes()==before


def test_runtime_get_never_installs_and_post_is_async(client,monkeypatch):
    import threading
    import backend.reconstruction as jobs
    import backend.reconstruction_runtime as runtime
    entered=threading.Event();release=threading.Event()
    def install():entered.set();release.wait(2)
    monkeypatch.setattr(jobs,'runtime_status',lambda:{**RUNTIME,'available':False})
    monkeypatch.setattr(runtime,'install_runtime',install)
    assert client.get('/api/reconstruction/runtime').status_code==200
    assert not entered.is_set()
    try:
        started=time.monotonic();result=client.post('/api/reconstruction/runtime/install')
        assert result.status_code==200 and result.json()['installing'] is True
        assert time.monotonic()-started<1 and entered.wait(.5)
        assert client.get('/api/health').status_code==200
    finally:
        release.set()
        deadline=time.monotonic()+2
        while jobs._installing and time.monotonic()<deadline:time.sleep(.01)


def test_ready_event_supports_200_photos_with_full_metadata_and_warnings(client,monkeypatch):
    code=READY_WORKER+"""
ids=[item['id'] for item in source['images']]
result=root/('local-output-'+'r'*120)/'reconstructed-room.ply'
result.parent.mkdir();(root/'result.ply').rename(result)
warnings=['Inspect capture quality: '+('w'*550) for _ in range(30)]
details={'registeredPhotos':180,'unregisteredAssetIds':ids[180:],'components':[180,20],'warnings':warnings}
metadata={'engines':{'pycolmap':'4.2.1','brush':'0.3.0'},'quality':'preview','settings':{'totalSteps':3000,'maxResolution':960,'maxSplats':300000,'exportEvery':1000,'lpipsLossWeight':0,'normalizedMaxResolution':1600},'sourceAssetIds':ids,'registeredAssetIds':ids[:180],**details,'sparsePoints':25000,'output':{'splats':1,'shDegree':0}}
event={'status':'ready','stage':'complete','message':'The reconstructed room is ready to preview.','resultPath':str(result),'metadata':metadata,**details}
line=json.dumps(event)
(root/'completion-bytes').write_text(str(len(line.encode())+1))
print(line,flush=True)
"""
    p,ids=prepare_worker(client,monkeypatch,code,count=200)
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']=='ready',job
    assert job['totalPhotos']==200 and job['registeredPhotos']==180 and job['unregisteredAssetIds']==ids[180:]
    assert len(job['warnings'])==30
    directory=client.app.state.store.root/'reconstructions'/job['id']
    assert 16384<int((directory/'completion-bytes').read_text())<=65536
    assert (directory/'supervisor.log').stat().st_size<=65536
    result=next(a for a in get(client,p)['assets'] if a['id']==job['resultAssetId'])
    assert len(result['metadata']['reconstruction']['sourceImages'])==200
    assert result['metadata']['reconstruction']['registeredAssetIds']==ids[:180]


@pytest.mark.parametrize(('line_bytes','expected'),[(65536,'needs_photos'),(65537,'failed')])
def test_progress_message_byte_boundary(client,monkeypatch,line_bytes,expected):
    code=f"""
import json
value={{'status':'needs_photos','stage':'matching','message':'Add more overlapping photos.','padding':''}}
value['padding']='x'*({line_bytes}-len(json.dumps(value).encode())-1)
print(json.dumps(value),flush=True)
"""
    p,ids=prepare_worker(client,monkeypatch,code)
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']==expected,job
    if expected=='failed':assert 'progress message limit' in job['error']
    assert (client.app.state.store.root/'reconstructions'/job['id']/'supervisor.log').stat().st_size<=65536


def test_second_manager_cannot_interrupt_live_owner(client,monkeypatch):
    from backend.reconstruction import ReconstructionManager
    p,ids=prepare_worker(client,monkeypatch,'import time;time.sleep(60)')
    job=start(client,p,ids).json()
    assert wait_job(client,p,job,statuses=('running',))['status']=='running'
    with pytest.raises(RuntimeError,match='already owns'):
        ReconstructionManager(client.app.state.store)
    assert client.app.state.reconstruction.get(p['id'],job['id'])['status']=='running'
    assert client.app.state.reconstruction.processes[job['id']].poll() is None
    assert client.post(f"/api/projects/{p['id']}/reconstructions/{job['id']}/cancel").json()['status']=='cancelled'
    client.app.state.reconstruction.close()
    replacement=ReconstructionManager(client.app.state.store)
    try:assert replacement.get(p['id'],job['id'])['status']=='cancelled'
    finally:replacement.close()


def test_owner_lock_is_cross_process_and_released_after_crash(tmp_path):
    import subprocess
    from backend.reconstruction import ReconstructionManager
    code="from backend.storage import Store;from backend.reconstruction import ReconstructionManager;import sys,time;m=ReconstructionManager(Store(sys.argv[1]));print('owned',flush=True);time.sleep(60)"
    process=subprocess.Popen([sys.executable,'-u','-c',code,str(tmp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline()==b'owned\n'
        with pytest.raises(RuntimeError,match='already owns'):ReconstructionManager(Store(tmp_path))
    finally:
        process.kill();process.wait(timeout=5);process.stdout.close();process.stderr.close()
    manager=ReconstructionManager(Store(tmp_path))
    manager.close()


def view_worker(view,rotation=None):
    metadata={'initialView':view}
    if rotation is not None:metadata['displayRotation']=rotation
    return READY_WORKER.replace("'metadata':{'settings':",f"'metadata':{{**json.loads({json.dumps(metadata)!r}),'settings':")


def test_initial_camera_is_preserved_in_provenance_archive_and_accept(client,monkeypatch):
    view={'position':[1.25,-2,3.5],'quaternion':[0,1,0,0],'id':'worker-must-not-select-the-view-id','name':'untrusted label'}
    rotation=[.2,-.1,3.14159]
    p,ids=prepare_worker(client,monkeypatch,view_worker(view,rotation))
    job=wait_job(client,p,start(client,p,ids).json());assert job['status']=='ready',job
    current=get(client,p)
    assert current['room']['entrance'] is None
    expected={'position':[1.25,-2,3.5],'quaternion':[0,1,0,0]}
    scan=next(a for a in current['assets'] if a['id']==job['resultAssetId'])
    assert scan['metadata']['reconstruction']['initialView']==expected
    assert scan['metadata']['reconstruction']['displayRotation']==rotation
    archive=client.get(f"/api/projects/{p['id']}/export").content
    imported=client.post('/api/import',files={'file':('camera.zip',archive)})
    assert imported.status_code==200,imported.text
    imported_provenance=next(a for a in imported.json()['assets'] if a['id']==scan['id'])['metadata']['reconstruction']
    assert imported_provenance['initialView']==expected and imported_provenance['displayRotation']==rotation
    accepted=client.post(f"/api/projects/{p['id']}/reconstructions/{job['id']}/accept",json={'expectedRevision':current['revision']})
    assert accepted.status_code==200,accepted.text
    room=accepted.json()['room'];entrance=room['entrance']
    assert entrance['position']==expected['position'] and entrance['quaternion']==expected['quaternion']
    assert entrance['id']!=view['id'] and entrance['name']!=view['name']
    from uuid import UUID
    UUID(entrance['id'])
    assert room['sourceUnits']=='unknown' and room['floorY'] is None and room['calibration'] is None
    assert room['transform']['rotation']==rotation and room['transform']['scale']==1
    assert 'estimated' in room['alignmentNotes'].lower()


@pytest.mark.parametrize('view',[
    {'position':[1e20,0,0],'quaternion':[0,0,0,1]},
    {'position':[65505,0,0],'quaternion':[0,0,0,1]},
    {'position':[float('nan'),0,0],'quaternion':[0,0,0,1]},
    {'position':[0,0,0],'quaternion':[0,0,0,0]},
    {'position':[0,0],'quaternion':[0,0,0,1]},
    {'position':[0,0,0],'quaternion':[0,0,float('inf'),1]},
])
def test_invalid_initial_camera_cannot_publish_ready(client,monkeypatch,view):
    p,ids=prepare_worker(client,monkeypatch,view_worker(view))
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']=='failed' and job['resultAssetId'] is None,job
    assert get(client,p)['room']['entrance'] is None


@pytest.mark.parametrize('rotation',[[0,0],[float('nan'),0,0],[0,1000001,0],[True,0,0]])
def test_invalid_display_rotation_cannot_publish_ready(client,monkeypatch,rotation):
    p,ids=prepare_worker(client,monkeypatch,view_worker({'position':[0,0,0],'quaternion':[0,0,0,1]},rotation))
    job=wait_job(client,p,start(client,p,ids).json())
    assert job['status']=='failed' and job['resultAssetId'] is None,job


@pytest.mark.parametrize(('field','value'),[
    ('initialView',{'position':[0,0,0],'quaternion':[0,0,0,0]}),
    ('displayRotation',[0,1000001,0]),
])
def test_archive_rejects_invalid_reconstruction_camera_metadata(client,monkeypatch,field,value):
    import zipfile
    p,ids=prepare_worker(client,monkeypatch,view_worker({'position':[0,0,0],'quaternion':[0,0,0,1]},[.1,.2,.3]))
    job=wait_job(client,p,start(client,p,ids).json());assert job['status']=='ready'
    original=client.get(f"/api/projects/{p['id']}/export").content;changed=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source,zipfile.ZipFile(changed,'w') as target:
        for name in source.namelist():
            data=source.read(name)
            if name=='manifest.json':
                manifest=json.loads(data)
                scan=next(a for a in manifest['assets'] if a['id']==job['resultAssetId'])
                scan['metadata']['reconstruction'][field]=value;data=json.dumps(manifest).encode()
            target.writestr(name,data)
    response=client.post('/api/import',files={'file':('invalid-camera.zip',changed.getvalue())})
    assert response.status_code==422


def test_assisted_runtime_is_explicit_and_bounded(client,monkeypatch):
    import backend.reconstruction as reconstruction
    from backend import depth_runtime
    monkeypatch.setattr(reconstruction,'runtime_status',lambda:{'available':True,'versions':{}})
    monkeypatch.setattr(depth_runtime,'runtime_status',lambda:{'available':False,'installing':False,'supported':True})
    assert client.get('/api/reconstruction/assisted-runtime').status_code==200
    p=new(client);ids=[capture(client,p,n).json()['id'] for n in range(3)]
    r=client.post(f"/api/projects/{p['id']}/reconstructions",json={'imageIds':ids,'quality':'assisted'})
    assert r.status_code==503 and 'sparse' in r.text.lower()
    monkeypatch.setattr(depth_runtime,'runtime_status',lambda:{'available':True})
    r=client.post(f"/api/projects/{p['id']}/reconstructions",json={'imageIds':[str(i) for i in range(33)],'quality':'assisted'})
    assert r.status_code==422 and '32' in r.text
