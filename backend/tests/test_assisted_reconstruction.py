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
