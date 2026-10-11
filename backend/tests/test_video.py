"""Actual encoded clips exercise decode, selection, durable jobs and portable provenance."""
import io
import time
from pathlib import Path
import av
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from backend.app import create_app


def clip(tmp_path,static=False,rotation=0):
    path=tmp_path/'capture.mp4'
    with av.open(str(path),'w') as output:
        stream=output.add_stream('libx264',rate=12);stream.width=320;stream.height=240;stream.pix_fmt='yuv420p'
        if rotation:stream.set_display_rotation(rotation)
        for n in range(72):
            im=Image.new('RGB',(320,240),'#d4cfac');draw=ImageDraw.Draw(im)
            for i in range(40):
                x=(i*47+(0 if static else n*4))%310;y=(i*31)%230
                draw.rectangle((x,y,x+8,y+8),fill=(i*6,40,120))
            frame=av.VideoFrame.from_image(im)
            for packet in stream.encode(frame):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    return path

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'data'),base_url='http://127.0.0.1:8849',headers={'X-Stillroom-Request':'local'}) as c:yield c

def upload(c,path):
    p=c.post('/api/projects',json={'title':'Video test'}).json()
    r=c.post(f"/api/projects/{p['id']}/assets",data={'role':'media'},files={'file':(path.name,path.read_bytes())})
    assert r.status_code==200,r.text
    return p,r.json()

def wait(c,p,j):
    for _ in range(200):
        r=c.get(f"/api/projects/{p['id']}/video-extractions/{j['id']}");assert r.status_code==200,r.text
        value=r.json()
        if value['status'] not in ('queued','running'):return value
        time.sleep(.05)
    pytest.fail('Video worker did not finish')

def test_video_route_decodes_real_clip_and_roundtrips_provenance(client,tmp_path):
    path=clip(tmp_path);p,a=upload(client,path)
    response=client.post(f"/api/projects/{p['id']}/video-extractions",json={'assetId':a['id'],'targetFrames':30})
    assert response.status_code==200,response.text
    j=wait(client,p,response.json());assert j['status']=='ready',j
    assert 3<=j['frameCount']<=30
    project=client.get(f"/api/projects/{p['id']}").json();frames=[x for x in project['assets'] if x['role']=='capture']
    times=[f['metadata']['videoFrame']['timestampSeconds'] for f in frames]
    assert times==sorted(times) and times[0]<1 and times[-1]>5
    assert all(f['metadata']['videoFrame']['sourceAssetId']==a['id'] and f['metadata']['videoFrame']['sourceSha256']==a['sha256'] for f in frames)
    assert project['room']['scanAssetId'] is None
    assert client.get(f"/api/projects/{p['id']}/assets/{a['id']}").content==path.read_bytes()
    z=client.get(f"/api/projects/{p['id']}/export")
    imported=client.post('/api/import',files={'file':('room.zip',z.content)});assert imported.status_code==200,imported.text
    restored=[x for x in imported.json()['assets'] if x['role']=='capture']
    assert [x['metadata']['videoFrame'] for x in restored]==[x['metadata']['videoFrame'] for x in frames]

def test_stationary_clip_does_not_invent_viewpoints(client,tmp_path):
    p,a=upload(client,clip(tmp_path,static=True))
    r=client.post(f"/api/projects/{p['id']}/video-extractions",json={'assetId':a['id'],'targetFrames':30});assert r.status_code==200,r.text
    job=wait(client,p,r.json());assert job['status']=='ready',job
    assert job['frameCount']==1 and job['warnings']

def test_video_rejects_wrong_owner_image_and_bad_target(client,tmp_path):
    p,a=upload(client,clip(tmp_path));other=client.post('/api/projects',json={'title':'other'}).json()
    url=f"/api/projects/{other['id']}/video-extractions"
    assert client.post(url,json={'assetId':a['id'],'targetFrames':30}).status_code in (404,422)
    assert client.post(f"/api/projects/{p['id']}/video-extractions",json={'assetId':a['id'],'targetFrames':201}).status_code==422

def test_malformed_video_fails_without_frames(client,tmp_path):
    path=tmp_path/'bad.mp4';path.write_bytes(b'0000ftyp00000000mdat0000000');p,a=upload(client,path)
    r=client.post(f"/api/projects/{p['id']}/video-extractions",json={'assetId':a['id'],'targetFrames':30});assert r.status_code==200,r.text
    j=wait(client,p,r.json());assert j['status']=='failed' and j['error']
    assert client.get(f"/api/projects/{p['id']}").json()['captureAssetIds']==[]

def test_mov_upload_preserves_original(client,tmp_path):
    path=clip(tmp_path);mov=tmp_path/'phone.mov';mov.write_bytes(path.read_bytes())
    p,a=upload(client,mov);assert a['mimeType']=='video/quicktime'
    assert client.get(f"/api/projects/{p['id']}/assets/{a['id']}").content==mov.read_bytes()


def test_cancel_stops_worker_and_retains_video(client,tmp_path,monkeypatch):
    import sys
    import backend.video as video
    monkeypatch.setattr(video,'worker_command',lambda directory:[sys.executable,'-c','import time;time.sleep(60)'])
    p,a=upload(client,clip(tmp_path));url=f"/api/projects/{p['id']}/video-extractions"
    j=client.post(url,json={'assetId':a['id'],'targetFrames':30}).json()
    for _ in range(100):
        if client.get(url+'/'+j['id']).json()['status']=='running':break
        time.sleep(.01)
    assert client.post(url,json={'assetId':a['id'],'targetFrames':30}).status_code==409
    assert client.post(url+'/'+j['id']+'/cancel').json()['status']=='cancelled'
    manager=client.app.state.video
    for _ in range(100):
        if j['id'] not in manager.processes:break
        time.sleep(.02)
    assert j['id'] not in manager.processes
    saved=client.get(f"/api/projects/{p['id']}").json()
    assert saved['captureAssetIds']==[] and len(saved['assets'])==1


def test_frame_budget_failure_is_atomic(client,tmp_path,monkeypatch):
    import backend.storage as storage
    monkeypatch.setattr(storage,'MAX_CAPTURE_COUNT',2)
    p,a=upload(client,clip(tmp_path));url=f"/api/projects/{p['id']}/video-extractions"
    j=wait(client,p,client.post(url,json={'assetId':a['id'],'targetFrames':30}).json())
    assert j['status']=='failed' and 'limit' in j['error']
    saved=client.get(f"/api/projects/{p['id']}").json()
    assert saved['captureAssetIds']==[] and len(saved['assets'])==1
    assert len(list((client.app.state.store.root/'assets'/p['id']).iterdir()))==1


def test_display_rotation_is_applied_before_frames_saved(tmp_path):
    from backend.video_worker import extract
    source=clip(tmp_path,rotation=90)
    result=extract(source,tmp_path/'frames',30)
    im=Image.open(tmp_path/'frames'/result['frames'][0]['file'])
    assert im.size==(240,320)


def test_overlong_video_rejected_without_decoding(client,tmp_path):
    path=tmp_path/'long.mp4'
    with av.open(str(path),'w') as out:
        stream=out.add_stream('libx264',rate=1);stream.width=32;stream.height=32;stream.pix_fmt='yuv420p'
        for n in (0,301):
            frame=av.VideoFrame.from_image(Image.new('RGB',(32,32)));frame.pts=n
            for packet in stream.encode(frame):out.mux(packet)
        for packet in stream.encode():out.mux(packet)
    p,a=upload(client,path);url=f"/api/projects/{p['id']}/video-extractions"
    j=wait(client,p,client.post(url,json={'assetId':a['id'],'targetFrames':30}).json())
    assert j['status']=='failed' and 'five minutes' in j['error']


def test_unknown_duration_webm_extracts_using_real_timestamps(tmp_path):
    from backend.video_worker import extract
    source=tmp_path/'live.webm'
    with av.open(str(source),'w',format='webm',options={'live':'1'}) as output:
        stream=output.add_stream('libvpx',rate=6);stream.width=160;stream.height=120;stream.pix_fmt='yuv420p'
        for n in range(18):
            im=Image.new('RGB',(160,120),'white');draw=ImageDraw.Draw(im)
            draw.rectangle((n*6,20,n*6+20,90),fill='black')
            for packet in stream.encode(av.VideoFrame.from_image(im)):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    with av.open(str(source)) as original:
        assert original.duration is None and original.streams.video[0].duration is None
    result=extract(source,tmp_path/'frames',30)
    assert result['frames'][0]['timestampSeconds']<1
    assert result['frames'][-1]['timestampSeconds']>=2


def test_restart_discards_abandoned_frame_staging(tmp_path):
    from backend.models import now,uid
    root=tmp_path/'restart'
    with TestClient(create_app(root),base_url='http://127.0.0.1:8849',headers={'X-Stillroom-Request':'local'}) as first:
        p=first.post('/api/projects',json={'title':'Recovery'}).json();manager=first.app.state.video
        identifier=uid();stamp=now()
        job={'id':identifier,'projectId':p['id'],'status':'running','message':'Decoding','createdAt':stamp,'updatedAt':stamp}
        with manager.store.connect() as db:manager.write(db,job)
        staging=root/'video-extractions'/identifier/'frames';staging.mkdir(parents=True);(staging/'frame-0000.jpg').write_bytes(b'abandoned')
    with TestClient(create_app(root),base_url='http://127.0.0.1:8849',headers={'X-Stillroom-Request':'local'}) as second:
        job=second.get(f"/api/projects/{p['id']}/video-extractions/{identifier}").json()
        assert job['status']=='interrupted'
        assert not staging.exists()
