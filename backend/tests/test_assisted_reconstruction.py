import numpy as np
import pytest
from backend.assisted_reconstruction import camera_prior, filter_supported_model


def test_camera_prior_preserves_world_to_camera_and_scales_focal():
    import pycolmap as c
    prediction={'extrinsics':np.array([np.c_[np.eye(3),[1,2,3]]]*3),'intrinsics':np.array([[[250,0,168],[0,250,98],[0,0,1]]]*3),'imageSize':np.array([336,196])}
    cameras=[c.Camera(camera_id=1,model='SIMPLE_RADIAL',width=672,height=392,params=[600,336,196,0])]
    images=[c.Image(image_id=i+1,camera_id=1,name=f'{i}.jpg') for i in range(3)]
    model=camera_prior(cameras,images,prediction)
    assert model.num_reg_images()==3
    assert model.cameras[1].params[0]==pytest.approx(500)
    assert model.images[1].cam_from_world().translation==pytest.approx([1,2,3])


def test_unsupported_views_are_not_retained():
    import pycolmap as c
    model=c.Reconstruction();model.add_camera_with_trivial_rig(c.Camera(camera_id=1,model='SIMPLE_RADIAL',width=672,height=392,params=[500,336,196,0]))
    for i in range(3):
        image=c.Image(image_id=i+1,camera_id=1,name=f'{i}.jpg');model.add_image_with_trivial_frame(image,c.Rigid3d())
    filter_supported_model(model)
    assert model.num_reg_images()==0


def test_written_model_excludes_rejected_views(tmp_path):
    import pycolmap as c
    model=c.Reconstruction();model.add_camera_with_trivial_rig(c.Camera(camera_id=1,model='SIMPLE_RADIAL',width=672,height=392,params=[500,336,196,0]))
    for i in range(3):model.add_image_with_trivial_frame(c.Image(image_id=i+1,camera_id=1,name=f'{i}.jpg'),c.Rigid3d())
    model.deregister_frame(model.images[3].frame_id);model.write(tmp_path)
    restored=c.Reconstruction(tmp_path)
    assert len(restored.images)==restored.num_reg_images()==2


def test_assisted_provenance_keeps_model_identity_and_drops_execution_paths():
    from types import SimpleNamespace
    from backend.formats import validate_reconstruction_provenance
    from backend.depth_runtime import MODEL_ID,MODEL_REVISION,MODEL_SHA256,SOURCE_REVISION
    assets={str(i):SimpleNamespace(role='capture',kind='image',sha256='a'*64) for i in range(3)}
    value={'type':'photogrammetry-gaussian','sourceImages':[{'assetId':i,'sha256':'a'*64} for i in assets], 'quality':'assisted','settings':{'cameraModel':MODEL_ID,'modelRevision':MODEL_REVISION,'modelSha256':MODEL_SHA256,'modelSourceRevision':SOURCE_REVISION,'modelInputResolution':336,'modelInputFrames':3,'modelPath':'/untrusted','downloadUrl':'https://untrusted'},'registeredAssetIds':list(assets),'versions':{},'warnings':[]}
    result=validate_reconstruction_provenance(value,assets)
    assert result['settings']['modelSha256']==MODEL_SHA256
    assert 'modelPath' not in result['settings'] and 'downloadUrl' not in result['settings']
    value['settings']['modelSha256']='bad'
    with pytest.raises(ValueError):validate_reconstruction_provenance(value,assets)


def supported_tracks(tracks):
    import pycolmap as c
    model=c.Reconstruction()
    model.add_camera_with_trivial_rig(c.Camera(camera_id=1,model='SIMPLE_RADIAL',width=672,height=392,params=[500,336,196,0]))
    keypoints={i:[] for ids in tracks for i in ids};points=[]
    for j,ids in enumerate(tracks):
        xyz=np.array([(j%15)*.03,(j//15)*.03,3.]);elements=[]
        for i in ids:
            elements.append(c.TrackElement(i,len(keypoints[i])))
            keypoints[i].append(xyz[:2]/3*500+[336,196])
        points.append((xyz,c.Track(elements)))
    for i in sorted(keypoints):model.add_image_with_trivial_frame(c.Image(image_id=i,camera_id=1,name=f'{i}.jpg',keypoints=np.array(keypoints[i])),c.Rigid3d())
    for xyz,track in points:model.add_point3D(xyz,track)
    return model


def test_camera_support_pruning_reaches_fixed_point():
    model=supported_tracks([(1,2,3)]*120+[(3,4)]*15+[(4,5)]*15+[(5,6)]*15+[(6,7)]*15)
    filter_supported_model(model)
    assert set(model.reg_image_ids())=={1,2,3}
    assert all(model.images[i].num_points3D>=30 for i in model.reg_image_ids())


def test_disconnected_groups_never_form_one_room(tmp_path):
    import pycolmap as c
    from backend.assisted_reconstruction import retain_largest_component
    from backend.reconstruction_worker import select_component
    model=supported_tracks([(1,2,3)]*100+[(4,5,6)]*100)
    filter_supported_model(model);retain_largest_component(model)
    model.write(tmp_path);model=c.Reconstruction(tmp_path)
    _,details=select_component({0:model},[str(i) for i in range(1,7)])
    assert model.num_reg_images()==3
    assert details['registeredPhotos']==3
    assert len(details['unregisteredAssetIds'])==3
    assert details['warnings']


def test_component_selection_prefers_a_usable_component():
    from backend.assisted_reconstruction import retain_largest_component
    model=supported_tracks([(1,2,3,4)]*50+[(5,6,7)]*100)
    filter_supported_model(model);retain_largest_component(model)
    assert set(model.reg_image_ids())=={5,6,7}
    assert model.num_points3D()==100


def test_point_errors_are_rechecked_when_weak_view_is_removed():
    model=supported_tracks([(1,2,3)]*100+[(1,2,4)])
    for i in (1,2):
        point=model.images[i].points2D[-1]
        point.xy=point.xy+np.array([6.,0.])
    filter_supported_model(model)
    model.update_point_3d_errors()
    assert max(p.error for p in model.points3D.values())<=4
    assert model.num_points3D()==100


def test_one_shared_point_cannot_join_independent_camera_groups():
    from backend.assisted_reconstruction import retain_largest_component
    model=supported_tracks([(1,2,3)]*100+[(4,5,6)]*100+[(3,4)])
    filter_supported_model(model);retain_largest_component(model)
    assert model.num_reg_images()==3
    assert model.num_points3D()==100


def test_connectivity_is_rechecked_after_excluding_other_groups():
    from backend.assisted_reconstruction import retain_largest_component
    tracks=[(1,2,3)]*100+[(4,5,6)]*100+[(7,8,9)]*100+[(3,4,7+i%3) for i in range(15)]
    model=supported_tracks(tracks)
    for i in (3,4):
        for index in range(len(model.images[i].points2D)-15,len(model.images[i].points2D)):
            point=model.images[i].points2D[index];point.xy=point.xy+np.array([6.,0.])
    filter_supported_model(model);retain_largest_component(model)
    assert model.num_reg_images()==3
    assert model.num_points3D()==100
