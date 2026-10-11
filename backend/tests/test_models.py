import pytest
from pydantic import ValidationError
from backend.models import Room, Transform, Viewpoint, uid


@pytest.mark.parametrize('scale',[0,1e-300,1e-7,1_000_001,float('inf'),float('nan')])
def test_transform_rejects_scales_that_break_coordinate_conversion(scale):
    with pytest.raises(ValidationError):Transform(scale=scale)


@pytest.mark.parametrize('scale',[1e-6,1e6])
def test_transform_accepts_supported_scale_boundaries(scale):
    assert Transform(scale=scale).scale==scale


@pytest.mark.parametrize('coordinate',[1e308,-1e308,1_000_000_001,-1_000_000_001])
def test_room_metadata_rejects_excessive_coordinates(coordinate):
    with pytest.raises(ValidationError):Transform(position=(coordinate,0,0))
    with pytest.raises(ValidationError):Room(floorY=coordinate)
    with pytest.raises(ValidationError):Viewpoint(id=uid(),name='View',position=(0,0,0),quaternion=(0,0,0,1),target=(0,coordinate,0))


@pytest.mark.parametrize('angle',[1_000_001,-1_000_001,1e308])
def test_transform_rejects_excessive_rotation(angle):
    with pytest.raises(ValidationError):Transform(rotation=(0,angle,0))


def test_metadata_numeric_boundaries_remain_usable():
    transform=Transform(position=(1e9,-1e9,0),rotation=(1e6,-1e6,0),scale=1e-6)
    assert transform.position==(1e9,-1e9,0)
    assert Room(floorY=-1e9).floorY==-1e9
