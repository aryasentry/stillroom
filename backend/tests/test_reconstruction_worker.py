import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image


def photo_input(tmp_path):
    assets=tmp_path/'assets';assets.mkdir();items=[]
    for i in range(3):
        p=assets/f'{i}.png';Image.new('RGB',(60,40),(i*70,)*3).save(p)
        items.append({'id':f'image-{i}','path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'originalName':p.name})
    return {'projectId':'project','jobId':'job','assetRoot':str(assets),'images':items,'quality':'preview'}


def test_managed_input_rejects_path_escape_and_changed_original(tmp_path):
    from backend.reconstruction_worker import validate_inputs
    spec=photo_input(tmp_path)
    spec['images'][0]['path']='../outside.png'
    with pytest.raises(ValueError,match='inside'):validate_inputs(spec)
    spec['images'][0]['path']='0.png';spec['images'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='changed'):validate_inputs(spec)


def test_managed_input_rejects_duplicate_ids(tmp_path):
    from backend.reconstruction_worker import validate_inputs
    spec=photo_input(tmp_path);spec['images'][1]['id']=spec['images'][0]['id']
    with pytest.raises(ValueError,match='unique'):validate_inputs(spec)


def test_normalization_preserves_original_and_applies_orientation(tmp_path):
    from backend.reconstruction_worker import normalize_images,validate_inputs
    spec=photo_input(tmp_path);p=Path(spec['assetRoot'])/'0.png'
    im=Image.new('RGB',(2000,1000),'black');exif=Image.Exif();exif[274]=6;im.save(p,exif=exif)
    original=p.read_bytes();spec['images'][0]['sha256']=hashlib.sha256(original).hexdigest()
    warnings=normalize_images(validate_inputs(spec),tmp_path/'normalized')
    assert p.read_bytes()==original
    with Image.open(tmp_path/'normalized/image-0.jpg') as result:assert result.size==(800,1600)
    assert any('dark' in w.lower() for w in warnings)
    assert len(warnings)<=12


def test_missing_final_export_is_not_success(tmp_path):
    from backend.reconstruction_worker import validate_output
    with pytest.raises(ValueError,match='export'):validate_output(tmp_path/'room_3000.ply')


def model(names,points=10):
    return SimpleNamespace(images={i:SimpleNamespace(name=name) for i,name in enumerate(names)},num_reg_images=lambda:len(names),num_points3D=lambda:points)


def test_disconnected_components_report_unused_images():
    from backend.reconstruction_worker import select_component
    selected,details=select_component({0:model(['a.jpg','b.jpg','c.jpg']),1:model(['d.jpg','e.jpg'])},['a','b','c','d','e','f'])
    assert selected==0
    assert details['components']==[3,2]
    assert details['registeredPhotos']==3
    assert details['unregisteredAssetIds']==['d','e','f']
    assert details['warnings']


def test_insufficient_components_do_not_train():
    from backend.reconstruction_worker import select_component
    selected,details=select_component({0:model(['a.jpg','b.jpg'])},['a','b','c'])
    assert selected is None
    assert details['status']=='needs_photos'
    assert details['unregisteredAssetIds']==['c']


def test_managed_input_rejects_symlink_outside_asset_root(tmp_path):
    from backend.reconstruction_worker import validate_inputs
    spec=photo_input(tmp_path)
    external=tmp_path/'outside.png';external.write_bytes((Path(spec['assetRoot'])/'0.png').read_bytes())
    link=Path(spec['assetRoot'])/'linked.png';link.symlink_to(external)
    spec['images'][0]['path']=link.name
    with pytest.raises(ValueError,match='inside'):validate_inputs(spec)


def test_zero_point_component_cannot_become_room():
    from backend.reconstruction_worker import select_component
    selected,details=select_component({0:model(['a.jpg','b.jpg','c.jpg'],points=0)},['a','b','c'])
    assert selected is None
    assert details['status']=='needs_photos'


def test_similar_blurry_inputs_receive_bounded_advice(tmp_path):
    from backend.reconstruction_worker import normalize_images,validate_inputs
    spec=photo_input(tmp_path)
    warnings=normalize_images(validate_inputs(spec),tmp_path/'normalized')
    assert any('similar' in w.lower() for w in warnings)
    assert any('blurry' in w.lower() for w in warnings)
    assert len(warnings)<=12


@pytest.mark.parametrize('video_frames',[False,True])
def test_native_blank_photos_report_needs_photos_without_training(tmp_path,video_frames):
    import json
    import subprocess
    import sys
    pytest.importorskip('pycolmap')
    from backend.reconstruction_runtime import runtime_status
    if not runtime_status()['available']:pytest.skip('Optional local native runtime not installed')
    spec=photo_input(tmp_path)
    if video_frames:
        for index,item in enumerate(spec['images']):item['videoFrame']={'sourceAssetId':'video-1','sourceSha256':'a'*64,'timestampSeconds':index,'sharpness':100}
    job=tmp_path/'job';job.mkdir();(job/'input.json').write_text(json.dumps(spec))
    result=subprocess.run([sys.executable,'-m','backend.reconstruction_worker',str(job)],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    events=[json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]['status']=='needs_photos'
    assert events[-1]['registeredPhotos']==0
    assert events[-1]['unregisteredAssetIds']==['image-0','image-1','image-2']
    assert not (job/'exports').exists()
    import sqlite3
    with sqlite3.connect(job/'database.db') as db:
        assert db.execute('SELECT COUNT(DISTINCT camera_id) FROM images').fetchone()[0]==(1 if video_frames else 3)


@pytest.mark.parametrize('variation,expected',[
    ('same-video','shared-video'),('photo','auto'),('mixed-video','auto'),
    ('different-checksum','auto'),('different-size','auto'),('different-orientation','auto'),
    ('invalid-provenance','auto'),
])
def test_camera_calibration_requires_one_video_and_actual_equal_dimensions(tmp_path,variation,expected):
    from backend.reconstruction_worker import validate_inputs,camera_calibration
    spec=photo_input(tmp_path)
    for index,item in enumerate(spec['images']):
        item['videoFrame']={'sourceAssetId':'video-1','sourceSha256':'a'*64,'timestampSeconds':index,'sharpness':100}
        item['imageSize']=[60,40]  # A caller-supplied size cannot override the bytes.
    item=spec['images'][0]
    if variation=='photo':item.pop('videoFrame')
    if variation=='mixed-video':item['videoFrame']['sourceAssetId']='video-2'
    if variation=='different-checksum':item['videoFrame']['sourceSha256']='b'*64
    if variation=='invalid-provenance':item['videoFrame']['timestampSeconds']=float('nan')
    if variation in ('different-size','different-orientation'):
        path=Path(spec['assetRoot'])/item['path'];im=Image.new('RGB',(80,40) if variation=='different-size' else (60,40))
        exif=Image.Exif()
        if variation=='different-orientation':exif[274]=6
        im.save(path,exif=exif);item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    assert camera_calibration(validate_inputs(spec))==expected


def test_native_log_drain_bounds_bytes_without_blocking_output(tmp_path):
    import io
    from backend.reconstruction_worker import drain_log
    source=io.BytesIO(b'x'*100000)
    target=tmp_path/'native.log'
    drain_log(source,target,limit=1024)
    assert target.stat().st_size==1024
    assert source.tell()==100000


@pytest.mark.parametrize('final_exists',[True,False])
def test_final_progress_requires_validated_final_export(tmp_path,monkeypatch,final_exists):
    import contextlib
    import json
    import sys
    from backend import reconstruction_worker as worker
    spec=photo_input(tmp_path);job=tmp_path/'job';job.mkdir();(job/'input.json').write_text(json.dumps(spec))
    reconstructed=model([item['id']+'.jpg' for item in spec['images']]);reconstructed.write=lambda path:None
    noop=lambda *args,**kwargs:None
    fake_colmap=SimpleNamespace(FeatureExtractionOptions=SimpleNamespace,FeatureMatchingOptions=SimpleNamespace,
        ImageReaderOptions=SimpleNamespace,IncrementalPipelineOptions=SimpleNamespace,CameraMode=SimpleNamespace(AUTO=0),
        Device=SimpleNamespace(cpu=0),extract_features=noop,match_exhaustive=noop,
        incremental_mapping=lambda *args,**kwargs:{0:reconstructed},undistort_images=noop)
    monkeypatch.setitem(sys.modules,'pycolmap',fake_colmap)
    monkeypatch.setattr(worker,'runtime_status',lambda:{'available':True})
    monkeypatch.setattr(worker,'native_log',lambda path:contextlib.nullcontext())
    monkeypatch.setattr(worker,'initial_view',lambda model:None)
    monkeypatch.setattr(worker,'display_rotation',lambda model:None)
    events=[];monkeypatch.setattr(worker,'emit',lambda **event:events.append(event))
    def final_export(dataset,output,settings,log_path):
        output.mkdir()
        # The last process poll can precede the final export. No 3000-step
        # checkpoint event was observed, even though its file may be complete.
        worker.emit(stage='training',iteration=2000,totalIterations=3000)
        if final_exists:
            source=Path(__file__).resolve().parents[2]/'fixtures/synthetic-room.ply'
            (output/'room_3000.ply').write_bytes(source.read_bytes())
    monkeypatch.setattr(worker,'train',final_export)
    if final_exists:
        worker.reconstruct(job)
        assert events[-1]['status']=='ready'
        assert events[-1]['iteration']==events[-1]['totalIterations']==3000
    else:
        with pytest.raises(ValueError,match='export'):worker.reconstruct(job)
        assert not any(event.get('status')=='ready' for event in events)
        assert not any(event.get('iteration')==3000 for event in events)


@pytest.mark.parametrize('severe',[False,True])
@pytest.mark.parametrize('quality,steps,guided',[('preview',3000,False),('standard',7000,True)])
def test_worker_publishes_validated_cleanup_and_preserves_raw_export(tmp_path,monkeypatch,severe,quality,steps,guided):
    import contextlib
    import json
    import math
    import sys
    from backend import reconstruction_worker as worker
    from backend.tests.test_splat_cleanup import ply
    spec=photo_input(tmp_path)
    spec['quality']=quality
    for index,item in enumerate(spec['images']):item['videoFrame']={'sourceAssetId':'video-1','sourceSha256':'a'*64,'timestampSeconds':index,'sharpness':100}
    job=tmp_path/'job';job.mkdir();(job/'input.json').write_text(json.dumps(spec))
    reconstructed=model([item['id']+'.jpg' for item in spec['images']]);reconstructed.write=lambda path:None
    reconstructed.points3D={i:SimpleNamespace(xyz=[10*math.cos(i),10*math.sin(i),0]) for i in range(100)}
    noop=lambda *args,**kwargs:None
    def extract(*args,**kwargs):assert kwargs['camera_mode']==1
    def match(*args,**kwargs):assert kwargs['matching_options'].guided_matching is guided
    def mapping(*args,**kwargs):
        assert kwargs['options'].random_seed==0
        return {0:reconstructed}
    fake_colmap=SimpleNamespace(FeatureExtractionOptions=SimpleNamespace,FeatureMatchingOptions=SimpleNamespace,
        ImageReaderOptions=SimpleNamespace,IncrementalPipelineOptions=SimpleNamespace,CameraMode=SimpleNamespace(AUTO=0,SINGLE=1),
        Device=SimpleNamespace(cpu=0),extract_features=extract,match_exhaustive=match,
        incremental_mapping=mapping,undistort_images=noop)
    monkeypatch.setitem(sys.modules,'pycolmap',fake_colmap)
    monkeypatch.setattr(worker,'runtime_status',lambda:{'available':True})
    monkeypatch.setattr(worker,'native_log',lambda path:contextlib.nullcontext())
    monkeypatch.setattr(worker,'initial_view',lambda model:None)
    monkeypatch.setattr(worker,'display_rotation',lambda model:None)
    events=[];monkeypatch.setattr(worker,'emit',lambda **event:events.append(event))
    data=ply(([((.2,.2,.2),(0,0,0))]*20 if not severe else [])+[((4,.01,.01),(0,0,0))]*10)
    def export(dataset,output,settings,log_path):
        output.mkdir();(output/f'room_{steps}.ply').write_bytes(data)
    monkeypatch.setattr(worker,'train',export)
    worker.reconstruct(job)
    event=events[-1];assert event['status']=='ready'
    assert Path(event['resultPath']).name=='room_cleaned.ply'
    assert (job/'exports'/f'room_{steps}.ply').read_bytes()==data
    result=worker.validate_output(Path(event['resultPath']))
    assert result['splats']==(10 if severe else 20)
    metadata=event['metadata'];cleanup=metadata['cleanup']
    assert cleanup['removedSplats']==(0 if severe else 10)
    assert cleanup['applied'] is (not severe)
    assert metadata['settings']['cameraCalibration']=='shared-video'
    assert metadata['settings']['mappingRandomSeed']==0
    assert metadata['settings']['guidedMatching'] is guided
    assert metadata['settings']['cleanupRemovedSplats']==cleanup['removedSplats']
    assert any(('unstable' if severe else 'removed') in warning for warning in metadata['warnings'])


@pytest.mark.parametrize('wait_results', [[258, 0], [0xFFFFFFFF]])
def test_windows_parent_handle_death_kills_brush_tree_before_worker_exit(monkeypatch, wait_results):
    # Windows keeps getppid() unchanged after parent death. Model the OS wait
    # boundary and assert the real watchdog still performs ordered cleanup.
    from backend import reconstruction_worker as worker
    from unittest.mock import Mock
    events=[]
    class WorkerExit(Exception): pass
    kernel=SimpleNamespace(
        OpenProcess=Mock(return_value=991),
        WaitForSingleObject=Mock(side_effect=wait_results),
        CloseHandle=lambda handle:events.append(('close',handle)),
    )
    child=SimpleNamespace(pid=4321,wait=lambda timeout:events.append(('wait',timeout)),terminate=lambda:events.append(('terminate',)),kill=lambda:events.append(('kill',)))
    monkeypatch.setattr(worker,'_BRUSH',child)
    monkeypatch.setattr(worker.os,'getppid',lambda:1234)
    def taskkill(command,**kwargs):
        assert command==['taskkill','/PID','4321','/T','/F']
        assert kwargs.get('shell',False) is False
        events.append(('tree',));return SimpleNamespace(returncode=0)
    monkeypatch.setattr(worker.subprocess,'run',taskkill)
    def exit_worker(code):
        events.append(('exit',code));raise WorkerExit
    monkeypatch.setattr(worker.os,'_exit',exit_worker)
    with pytest.raises(WorkerExit):worker._watch_windows_parent(1234,kernel32=kernel)
    kernel.OpenProcess.assert_called_once_with(0x00100000,False,1234)
    assert events==[('close',991),('tree',),('wait',3),('exit',1)]
    worker._PARENT_GONE.clear()


def test_windows_parent_cleanup_falls_back_when_taskkill_fails(monkeypatch):
    from backend import reconstruction_worker as worker
    from unittest.mock import Mock
    events=[]
    class WorkerExit(Exception): pass
    kernel=SimpleNamespace(OpenProcess=Mock(return_value=0),WaitForSingleObject=Mock(),CloseHandle=Mock())
    child=SimpleNamespace(pid=4321,wait=lambda timeout:events.append('wait'),terminate=lambda:events.append('terminate'),kill=lambda:events.append('kill'))
    monkeypatch.setattr(worker,'_BRUSH',child)
    def unavailable(*args,**kwargs):raise OSError('taskkill unavailable')
    monkeypatch.setattr(worker.subprocess,'run',unavailable)
    def exit_worker(code):events.append('exit');raise WorkerExit
    monkeypatch.setattr(worker.os,'_exit',exit_worker)
    with pytest.raises(WorkerExit):worker._watch_windows_parent(1234,kernel32=kernel)
    assert events==['terminate','wait','exit']
    kernel.WaitForSingleObject.assert_not_called()
    kernel.CloseHandle.assert_not_called()
    worker._PARENT_GONE.clear()


def posed_image(name,position,rotation=None):
    import numpy as np
    colmap=pytest.importorskip('pycolmap')
    rotation=np.eye(3) if rotation is None else np.array(rotation,dtype=float)
    center=np.array(position,dtype=float)
    pose=colmap.Rigid3d(colmap.Rotation3d(rotation),-rotation@center)
    return SimpleNamespace(name=name,projection_center=lambda:center,cam_from_world=lambda:pose)


def test_initial_view_chooses_median_nearest_camera_with_name_tie():
    from backend.reconstruction_worker import initial_view
    model=SimpleNamespace(images={9:posed_image('z.jpg',[1,0,0]),2:posed_image('b.jpg',[-1,0,0]),3:posed_image('a.jpg',[0,2,0]),4:posed_image('outlier.jpg',[0,-80,0])})
    # Component-wise median is (0,0,0); the two nearest centres tie.
    assert initial_view(model)['position']==[-1.0,0.0,0.0]


def test_initial_view_converts_colmap_forward_and_up_without_scene_flip():
    import numpy as np
    colmap=pytest.importorskip('pycolmap')
    from backend.reconstruction_worker import initial_view
    # World-to-camera -90deg about Y; c2w maps camera forward +Z to world +X.
    model=SimpleNamespace(images={1:posed_image('view.jpg',[4,5,6],[[0,0,-1],[0,1,0],[1,0,0]])})
    view=initial_view(model)
    assert view['position']==[4.0,5.0,6.0]
    rotation=colmap.Rotation3d(np.array(view['quaternion'])).matrix()
    assert rotation@np.array([0.,0.,-1.])==pytest.approx([1.,0.,0.])
    assert rotation@np.array([0.,1.,0.])==pytest.approx([0.,-1.,0.])
    assert np.linalg.det(rotation)==pytest.approx(1.)
    assert np.linalg.norm(view['quaternion'])==pytest.approx(1.)


@pytest.mark.parametrize('position',[[float('nan'),0,0],[float('inf'),0,0],[65505,0,0]])
def test_initial_view_omits_unbounded_or_nonfinite_cameras(position):
    from backend.reconstruction_worker import initial_view
    model=SimpleNamespace(images={1:SimpleNamespace(name='invalid.jpg',projection_center=lambda:position)})
    assert initial_view(model) is None


def xyz_matrix(euler):
    import numpy as np
    x,y,z=euler;cx,sx=np.cos(x),np.sin(x);cy,sy=np.cos(y),np.sin(y);cz,sz=np.cos(z),np.sin(z)
    return np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])@np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])@np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])


@pytest.mark.parametrize('up',[[0,-1,0],[0,1,0],[1,0,0],[1,2,-3],[0,0,-1]])
def test_display_rotation_maps_camera_up_to_world_y_with_three_xyz_convention(up):
    import numpy as np
    from backend.reconstruction_worker import upright_euler_xyz
    normalized=np.asarray(up,dtype=float);normalized/=np.linalg.norm(normalized)
    angles=upright_euler_xyz(normalized)
    rotation=xyz_matrix(angles)
    assert rotation@normalized==pytest.approx([0,1,0],abs=1e-7)
    assert np.linalg.det(rotation)==pytest.approx(1.)
    assert np.isfinite(angles).all() and max(abs(v) for v in angles)<=np.pi


def test_display_rotation_uses_average_recovered_camera_up_and_leaves_source_view():
    import numpy as np
    from backend.reconstruction_worker import display_rotation,initial_view
    model=SimpleNamespace(images={1:posed_image('view-a.jpg',[0,0,0]),2:posed_image('view-b.jpg',[1,0,0])})
    before=initial_view(model)
    rotation=xyz_matrix(display_rotation(model))
    assert rotation@np.array([0.,-1.,0.])==pytest.approx([0.,1.,0.],abs=1e-7)
    assert initial_view(model)==before


def test_display_rotation_omits_cancelling_up_directions():
    from backend.reconstruction_worker import display_rotation
    model=SimpleNamespace(images={1:posed_image('upright.jpg',[0,0,0]),2:posed_image('inverted.jpg',[1,0,0],[[1,0,0],[0,-1,0],[0,0,-1]])})
    assert display_rotation(model) is None
