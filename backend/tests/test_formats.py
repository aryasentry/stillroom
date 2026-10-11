import gzip
import struct
import pytest
from backend.formats import InvalidAsset,validate_asset

def ply(count=1,gaussian=True):
    props=['x','y','z']+(['f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3'] if gaussian else [])
    header='ply\nformat binary_little_endian 1.0\nelement vertex '+str(count)+'\n'+''.join('property float '+p+'\n' for p in props)+'end_header\n'
    return header.encode()+struct.pack('<'+'f'*len(props),*[0]*len(props))*max(0,count)

def spz(count=1,degree=0,version=2):
    return gzip.compress(struct.pack('<IIIBBBB',0x5053474e,version,count,degree,12,0,0)+bytes(count*(19+3*((degree+1)**2-1))))

def test_gaussian_formats_and_pointcloud_rejection():
    assert validate_asset('scan.ply',ply(),'scan')[2]['splats']==1
    assert validate_asset('scan.spz',spz(),'scan')[2]['splats']==1
    with pytest.raises(InvalidAsset,match='point cloud'):validate_asset('scan.ply',ply(gaussian=False),'scan')

@pytest.mark.parametrize('data',[spz(version=3),spz()[:-4],spz()+spz(),gzip.compress(struct.pack('<IIIBBBB',0x5053474e,2,1000001,0,12,0,0)),gzip.compress(struct.pack('<IIIBBBB',0x5053474e,2,1,0,12,0,0))])
def test_spz_unsupported_and_truncated_rejected(data):
    with pytest.raises(InvalidAsset):validate_asset('scan.spz',data,'scan')

def test_ply_nan_and_trailing_rejected():
    data=bytearray(ply());data[-4:]=struct.pack('<f',float('nan'))
    with pytest.raises(InvalidAsset):validate_asset('scan.ply',bytes(data),'scan')
    with pytest.raises(InvalidAsset):validate_asset('scan.ply',ply()+b'junk','scan')

@pytest.mark.parametrize('coordinates',[(65505,0,0),(-65505,0,0),(0,100000,0),(0,0,-100000)])
def test_ply_rejects_centres_outside_packed_renderer_range(coordinates):
    data=bytearray(ply());start=data.index(b'end_header\n')+11
    struct.pack_into('<3f',data,start,*coordinates)
    with pytest.raises(InvalidAsset,match='centre'):validate_asset('range.ply',bytes(data),'scan')

def test_ply_accepts_finite_packed_centre_boundaries():
    data=bytearray(ply());start=data.index(b'end_header\n')+11
    struct.pack_into('<3f',data,start,65504,-65504,0)
    assert validate_asset('boundary.ply',bytes(data),'scan')[2]['splats']==1

def positioned_spz(integer_positions,bits):
    raw=bytearray(struct.pack('<IIIBBBB',0x5053474e,2,1,0,bits,0,0)+bytes(19))
    for axis,value in enumerate(integer_positions):raw[16+axis*3:19+axis*3]=(value&0xffffff).to_bytes(3,'little')
    return gzip.compress(raw)

@pytest.mark.parametrize('positions,bits',[
    ((65505,0,0),0),((-65505,0,0),0),((0,65505*128,0),7),((0,0,-65505*128),7),
])
def test_spz_rejects_centres_outside_packed_renderer_range(positions,bits):
    with pytest.raises(InvalidAsset,match='centre'):validate_asset('range.spz',positioned_spz(positions,bits),'scan')

@pytest.mark.parametrize('positions,bits',[
    ((65504,-65504,0),0),((65504*128,-65504*128,0),7),((-8388608,8388607,0),8),
])
def test_spz_accepts_finite_packed_centres(positions,bits):
    assert validate_asset('boundary.spz',positioned_spz(positions,bits),'scan')[2]['splats']==1
