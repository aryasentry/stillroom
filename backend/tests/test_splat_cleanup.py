import math
import struct
import pytest


def ply(rows):
    names=['x','y','z','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
    header='ply\nformat binary_little_endian 1.0\nelement vertex '+str(len(rows))+'\n'+''.join('property float '+n+'\n' for n in names)+'end_header\n'
    values=[]
    for scales,center in rows:values.extend([*center,.1,.2,.3,1,*[math.log(s) for s in scales],1,0,0,0])
    return header.encode()+struct.pack('<'+'f'*len(values),*values)


def test_cleanup_removes_needles_and_distant_floaters_but_keeps_thin_walls(tmp_path):
    from backend.splat_cleanup import clean_splats
    source=tmp_path/'raw.ply';target=tmp_path/'clean.ply'
    # A flat wall is thin in one axis, unlike a needle which is thin in two.
    rows=[((.2,.2,.2),(0,0,0))]*20+[((2,2,.001),(0,0,0)),((4,.01,.01),(0,0,0)),((.2,.2,.2),(100,0,0)),((9,9,9),(0,0,0))]
    data=ply(rows);source.write_bytes(data)
    details=clean_splats(source,target,center=[0,0,0],radius=10)
    assert details['removedSplats']==3
    assert details['outputSplats']==21
    assert source.read_bytes()==data
    body=target.read_bytes().split(b'end_header\n')[1]
    assert body==data.split(b'end_header\n')[1][:21*14*4]


def test_cleanup_refuses_to_disguise_severely_corrupt_geometry(tmp_path):
    from backend.splat_cleanup import clean_splats
    source=tmp_path/'raw.ply';target=tmp_path/'clean.ply'
    data=ply([((9,9,9),(0,0,0))]*10);source.write_bytes(data)
    result=clean_splats(source,target,center=[0,0,0],radius=10)
    assert result['applied'] is False
    assert result['removedSplats']==0
    assert result['suspectSplats']==10
    assert target.read_bytes()==data


@pytest.mark.parametrize('radius',[0,-1,float('nan'),float('inf')])
def test_cleanup_rejects_unreliable_scene_scale(tmp_path,radius):
    from backend.splat_cleanup import clean_splats
    source=tmp_path/'raw.ply';source.write_bytes(ply([((.2,.2,.2),(0,0,0))]))
    with pytest.raises(ValueError):clean_splats(source,tmp_path/'out.ply',center=[0,0,0],radius=radius)


def test_scene_envelope_uses_robust_radius_and_rejects_sparse_or_degenerate_models():
    import numpy as np
    from types import SimpleNamespace
    from backend.splat_cleanup import scene_envelope
    assert scene_envelope(SimpleNamespace()) is None
    flat=SimpleNamespace(points3D={i:SimpleNamespace(xyz=[0,0,0]) for i in range(20)})
    assert scene_envelope(flat) is None
    points={i:SimpleNamespace(xyz=[math.cos(i),math.sin(i),0]) for i in range(100)}
    points[100]=SimpleNamespace(xyz=[10000,0,0])
    center,radius=scene_envelope(SimpleNamespace(points3D=points))
    assert np.linalg.norm(center)<.1
    assert .9<radius<1.1


@pytest.mark.parametrize('alter',[lambda b:b.replace(b'property float ',b'property\tfloat\t'),lambda b:b.replace(b'element vertex ',b'element  vertex  ')])
def test_cleanup_accepts_valid_ply_header_whitespace(tmp_path,alter):
    from backend.splat_cleanup import clean_splats
    source=tmp_path/'raw.ply';target=tmp_path/'clean.ply'
    source.write_bytes(alter(ply([((.2,.2,.2),(0,0,0))]*20+[((4,.01,.01),(0,0,0))])))
    result=clean_splats(source,target,center=[0,0,0],radius=10)
    assert result['outputSplats']==20
