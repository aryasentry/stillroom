import numpy as np
import pytest
from backend.depth_worker import validate_prediction


def prediction(n=3):
    ex=np.tile(np.c_[np.eye(3),np.zeros(3)],(n,1,1));ex[:,0,3]=np.arange(n)*.1
    k=np.tile(np.array([[250.,0.,168.],[0.,250.,98.],[0.,0.,1.]]),(n,1,1))
    return {'extrinsics':ex,'intrinsics':k,'imageSize':np.array([336,196])}


def test_valid_camera_prediction():
    out=validate_prediction(prediction(),3)
    assert out['extrinsics'].shape == (3,3,4)


@pytest.mark.parametrize('bad', ['nan','rotation','focal','count','dimensions','translation','principal'])
def test_invalid_camera_predictions_rejected(bad):
    p=prediction()
    if bad=='nan':p['extrinsics'][0,0,3]=np.nan
    if bad=='rotation':p['extrinsics'][0,0,0]=2
    if bad=='focal':p['intrinsics'][0,0,0]=-1
    if bad=='count':p['intrinsics']=p['intrinsics'][:2]
    if bad=='dimensions':p['imageSize']=np.array([99999,0])
    if bad=='translation':p['extrinsics'][0,0,3]=1e10
    if bad=='principal':p['intrinsics'][0,0,2]=-1
    with pytest.raises(ValueError):validate_prediction(p,3)


def test_frame_count_bounded():
    with pytest.raises(ValueError):validate_prediction(prediction(33),33)
