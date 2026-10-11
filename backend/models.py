"""Validated wire and stored schema, matching frontend/src/types.ts."""
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def uid(): return str(uuid4())

Number = Annotated[float, Field(ge=-1e9,le=1e9,allow_inf_nan=False)]
Angle = Annotated[float, Field(ge=-1e6,le=1e6,allow_inf_nan=False)]
Vec3 = tuple[Number, Number, Number]
Rotation = tuple[Angle, Angle, Angle]
Quat = tuple[Number, Number, Number, Number]
Id = Annotated[str, Field(min_length=36,max_length=36)]
Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Transform(Model):
    position: Vec3=(0,0,0)
    rotation: Rotation=(0,0,0)
    scale: Annotated[float,Field(ge=1e-6,le=1e6,allow_inf_nan=False)]=1

class Asset(Model):
    id: Id
    originalName: str=Field(max_length=255)
    role: Literal['scan','collision','media','thumbnail','capture']
    kind: Literal['glb','ply','spz','image','audio','video']
    mimeType: str=Field(max_length=100)
    size: int=Field(ge=1,le=256*1024*1024)
    sha256: Hash
    path: str=Field(pattern=r'^[a-f0-9-]{36}\.[a-z0-9]+$')
    createdAt: str
    metadata: dict[str,Any]=Field(default_factory=dict)
    thumbnailId: Id|None=None

class Viewpoint(Model):
    id: Id
    name: str=Field(max_length=200)
    position: Vec3
    quaternion: Quat
    target: Vec3|None=None
    @field_validator('quaternion')
    @classmethod
    def quaternion_valid(cls,q):
        if not .98<=sum(v*v for v in q)<=1.02:raise ValueError('Camera quaternion must be normalized')
        return q

class Anchor(Model):
    position: Vec3
    normal: Vec3|None=None
    assetId: Id
    scanVersion: Hash
    placement: Literal['surface','approximate']
    nodePath: str|None=Field(default=None,max_length=1000)
    triangleIndex: int|None=Field(default=None,ge=0)
    needsReview: bool

class Memory(Model):
    id: Id
    title: str=Field(max_length=500)
    note: str=Field(max_length=100000)
    date: str|None
    tags: list[Annotated[str,Field(max_length=100)]]=Field(max_length=100)
    anchor: Anchor
    mediaIds: list[Id]=Field(max_length=100)
    viewpoint: Viewpoint|None
    createdAt: str
    updatedAt: str
    deletedAt: str|None

class Calibration(Model):
    id: Id
    a: Vec3
    b: Vec3
    knownDistance: float=Field(gt=0,le=1e7)
    method: Literal['two-point']
    createdAt: str

class DistanceCheck(Model):
    id: Id
    a: Vec3
    b: Vec3
    knownDistance: float=Field(gt=0,le=1e7)
    label: str=Field(max_length=200)

class Room(Model):
    scanAssetId: Id|None=None
    scanVersion: Hash|None=None
    sceneType: Literal['mesh','splat']|None=None
    transform: Transform=Field(default_factory=Transform)
    sourceUnits: Literal['unknown','metres']='unknown'
    floorY: Number|None=None
    entrance: Viewpoint|None=None
    calibration: Calibration|None=None
    distanceChecks: list[DistanceCheck]=Field(default_factory=list,max_length=100)
    collisionAssetId: Id|None=None
    collisionTransform: Transform=Field(default_factory=Transform)
    collisionAligned: bool=False
    visualQuality: str=Field(default='',max_length=10000)
    alignmentNotes: str=Field(default='',max_length=10000)
    synthetic: bool=False

class Settings(Model):
    showMarkers: bool=True
    navigationSpeed: float=Field(default=2,gt=0,le=50)
    quality: Literal['balanced','high']='balanced'

class Project(Model):
    schemaVersion: Literal[2]=2
    captureAssetIds: list[Id]=Field(default_factory=list,max_length=200)
    id: Id=Field(default_factory=uid)
    title: str=Field(min_length=1,max_length=200)
    createdAt: str=Field(default_factory=now)
    updatedAt: str=Field(default_factory=now)
    revision: int=Field(default=0,ge=0)
    assets: list[Asset]=Field(default_factory=list,max_length=1000)
    room: Room=Field(default_factory=Room)
    memories: list[Memory]=Field(default_factory=list,max_length=10000)
    viewpoints: list[Viewpoint]=Field(default_factory=list,max_length=1000)
    settings: Settings=Field(default_factory=Settings)
    thumbnailAssetId: Id|None=None
    deletedAt: str|None=None
    @model_validator(mode='before')
    @classmethod
    def migrate(cls,value):
        if isinstance(value,dict) and value.get('schemaVersion')==1:
            value={**value,'schemaVersion':2,'captureAssetIds':value.get('captureAssetIds',[])}
        return value
    @model_validator(mode='after')
    def references(self):
        for collection in [self.assets,self.memories,self.viewpoints]:
            ids=[x.id for x in collection]
            if len(set(ids))!=len(ids): raise ValueError('Duplicate object IDs')
            for i in ids:UUID(i)
        assets={a.id:a for a in self.assets}
        for a in self.assets:
            if a.path!=a.id+'.'+a.path.rsplit('.',1)[-1]:raise ValueError('Asset path must use its ID')
            if a.thumbnailId and a.thumbnailId not in assets:raise ValueError('Unknown thumbnail')
        if len(set(self.captureAssetIds))!=len(self.captureAssetIds):raise ValueError('Duplicate capture IDs')
        if any(i not in assets or assets[i].role!='capture' or assets[i].kind!='image' for i in self.captureAssetIds):raise ValueError('Unknown capture image')
        captures=[a for a in self.assets if a.role=='capture']
        if len(captures)>200 or sum(a.size for a in captures)>256*1024*1024:raise ValueError('Capture limit is 200 photographs and 256 MiB of originals')
        r=self.room
        if r.scanAssetId:
            a=assets.get(r.scanAssetId)
            if not a or a.role!='scan' or r.scanVersion!=a.sha256:raise ValueError('Room scan reference/hash mismatch')
            if r.sceneType!=('mesh' if a.kind=='glb' else 'splat'):raise ValueError('Wrong scan scene type')
        elif r.scanVersion or r.sceneType:raise ValueError('Room needs a scan asset')
        if r.collisionAssetId:
            a=assets.get(r.collisionAssetId)
            if not a or a.kind!='glb' or a.role!='collision':raise ValueError('Collision proxy must reference a GLB collision asset')
        if self.thumbnailAssetId and self.thumbnailAssetId not in assets:raise ValueError('Unknown project thumbnail')
        for m in self.memories:
            a=assets.get(m.anchor.assetId)
            if not a or a.role!='scan' or a.sha256!=m.anchor.scanVersion:raise ValueError('Memory anchor asset/hash mismatch')
            if any(i not in assets or assets[i].role!='media' for i in m.mediaIds):raise ValueError('Unknown memory media')
        return self
