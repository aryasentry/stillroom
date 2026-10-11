"""Untrusted GLBs cannot amplify recursion, CPU work or scene allocation."""
import copy
import io
import json
import struct
import pytest
from PIL import Image
from backend.formats import InvalidAsset,validate_asset


def scene():
    return {'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],
            'nodes':[{'mesh':0}], 'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}],
            'buffers':[{'byteLength':36}], 'bufferViews':[{'buffer':0,'byteLength':36}],
            'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3'}]}


def pack(doc,binary=None):
    if binary is None:binary=struct.pack('<9f',0,0,0,1,0,0,0,1,0)
    doc['buffers'][0]['byteLength']=len(binary)
    binary+=b'\0'*(-len(binary)%4)
    js=json.dumps(doc).encode();js+=b' '*(-len(js)%4)
    return struct.pack('<III',0x46546c67,2,28+len(js)+len(binary))+struct.pack('<II',len(js),0x4e4f534a)+js+struct.pack('<II',len(binary),0x004e4942)+binary


def check(doc,binary=None):return validate_asset('room.glb',pack(doc,binary),'scan')


def test_reverse_id_chain_is_rejected_by_actual_depth():
    doc=scene();doc['nodes']=[{'mesh':0}]+[{'children':[i-1]} for i in range(1,5000)];doc['scenes'][0]['nodes']=[4999]
    with pytest.raises(InvalidAsset,match='depth'):check(doc)


def test_accessor_budget_precedes_any_binary_iteration(monkeypatch):
    doc=scene();doc['accessors']*=1000
    for accessor in doc['accessors']:accessor['count']=6000
    doc['bufferViews'][0]['byteLength']=72000
    binary=bytes(72000)
    original=struct.unpack_from
    def guarded(fmt,*args,**kwargs):
        assert 'f' not in fmt,'Must reject aggregate work before decoding float values'
        return original(fmt,*args,**kwargs)
    monkeypatch.setattr(struct,'unpack_from',guarded)
    with pytest.raises(InvalidAsset,match='aggregate'):check(doc,binary)


@pytest.mark.parametrize('mutation',[
    lambda d:d['nodes'].extend([{'children':[0]},{'children':[0]}]),
    lambda d:d['nodes'][0].update(children=[0]),
    lambda d:d['scenes'][0].update(nodes=[0,0]),
    lambda d:d['scenes'][0].update(nodes=[]),
    lambda d:d['meshes'][0]['primitives'][0].update(material=0),
    lambda d:d.update(textures=[{'source':0}]),
    lambda d:d.update(materials=[{'pbrMetallicRoughness':{'baseColorTexture':{'index':0}}}]),
])
def test_invalid_graph_or_resource_reference_rejected(mutation):
    doc=scene();mutation(doc)
    with pytest.raises(InvalidAsset):check(doc)


def test_instanced_geometry_budget():
    doc=scene();doc['accessors'][0]['count']=3000;doc['bufferViews'][0]['byteLength']=36000
    doc['nodes']=[{'mesh':0} for _ in range(1500)];doc['scenes'][0]['nodes']=list(range(1500))
    with pytest.raises(InvalidAsset,match='scene|instanc'):check(doc,bytes(36000))


def test_repeated_primitive_attribute_budget():
    doc=scene();doc['accessors'][0]['count']=3000;doc['bufferViews'][0]['byteLength']=36000
    doc['meshes'][0]['primitives']=[{'attributes':{'POSITION':0}} for _ in range(2000)]
    with pytest.raises(InvalidAsset,match='aggregate'):check(doc,bytes(36000))


def test_repeated_texture_allocation_budget():
    doc=scene();stream=io.BytesIO();Image.new('RGB',(1024,1024)).save(stream,format='PNG');png=stream.getvalue()
    doc['bufferViews'].append({'buffer':0,'byteOffset':36,'byteLength':len(png)})
    doc['images']=[{'bufferView':1,'mimeType':'image/png'}]
    doc['textures']=[{'source':0} for _ in range(40)]
    with pytest.raises(InvalidAsset,match='texture'):check(doc,struct.pack('<9f',0,0,0,1,0,0,0,1,0)+png)


def test_small_shared_mesh_instances_and_scene_are_valid():
    doc=scene();doc['nodes']=[{'children':[1,2]},{'mesh':0},{'mesh':0}]
    assert check(doc)[2]['triangles']>=1


@pytest.mark.parametrize('bounds',[{'min':[],'max':[]},{'min':[100,100,100],'max':[100,100,100]}])
def test_position_bounds_must_match_geometry(bounds):
    doc=scene();doc['accessors'][0].update(bounds)
    with pytest.raises(InvalidAsset,match='bound'):check(doc)


@pytest.mark.parametrize('transform',[
    {'scale':[1e308,1e308,1e308]},
    {'scale':[0,1,1]},
    {'matrix':[1,0,0,0,1,0,0,0,0,0,1,0,0,0,0,1]},
    {'translation':[1e10,0,0]},
])
def test_invalid_transform_magnitudes_and_degeneracy(transform):
    doc=scene();doc['nodes'][0].update(transform)
    with pytest.raises(InvalidAsset):check(doc)


def test_composed_transform_magnitude_is_bounded():
    doc=scene();doc['nodes']=[{'scale':[1e6]*3,'children':[1]},{'mesh':0,'scale':[1e6]*3}]
    with pytest.raises(InvalidAsset,match='Composed'):check(doc)
