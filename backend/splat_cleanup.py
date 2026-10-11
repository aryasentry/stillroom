"""Conservative removal of geometric outliers, preserving raw training exports.

Thresholds are relative to a robust sparse-scene radius; they do not create detail
or estimate real-world units. Thin planar Gaussians are intentionally retained.
"""
from pathlib import Path
from .formats import validate_ply


def scene_envelope(model):
    import numpy as np
    points=np.asarray([p.xyz for p in getattr(model,'points3D',{}).values()],dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or len(points)<20 or not np.isfinite(points).all():return None
    center=np.median(points,axis=0)
    radius=float(np.percentile(np.linalg.norm(points-center,axis=1),90))
    if not np.isfinite(radius) or radius<=1e-8:return None
    return center,radius


def clean_splats(source:Path,target:Path,*,center,radius):
    import numpy as np
    center=np.asarray(center,dtype=float)
    if center.shape!=(3,) or not np.isfinite(center).all() or not np.isfinite(radius) or radius<=0:
        raise ValueError('Cleanup requires a finite nonzero scene envelope.')
    if source.resolve()==target.resolve():raise ValueError('Preserve the raw splat export separately.')
    data=source.read_bytes();info=validate_ply(data)
    boundary=data.index(b'end_header\n')+11;header=data[:boundary].decode('ascii')
    properties=[parts[2] for line in header.splitlines() if (parts:=line.split()) and parts[0]=='property']
    rows=np.frombuffer(data[boundary:],dtype=[(name,'<f4') for name in properties])
    xyz=np.stack([rows[axis] for axis in ('x','y','z')],axis=1).astype(float)
    # Log-space comparisons avoid overflow from hostile or unstable scales.
    logs=np.sort(np.stack([rows['scale_'+str(i)] for i in range(3)],axis=1).astype(float),axis=1)
    oversized=logs[:,2]>np.log(radius*.5)
    needles=(logs[:,2]-logs[:,1]>np.log(50)) & (logs[:,2]>np.log(radius*.03))
    distant=np.linalg.norm(xyz-center,axis=1)>radius*4
    suspect=oversized|needles|distant
    count=int(np.count_nonzero(suspect));applied=count<=len(rows)*.35
    # A catastrophic scene needs review, not an aggressively erased success.
    keep=~suspect if applied else np.ones(len(rows),dtype=bool)
    remaining=int(np.count_nonzero(keep))
    if applied and count:
        header='\n'.join('element vertex '+str(remaining) if line.split()[:2]==['element','vertex'] else line for line in header.splitlines())+'\n'
        target.write_bytes(header.encode('ascii')+rows[keep].tobytes())
    else:target.write_bytes(data)
    validate_ply(target.read_bytes())
    return {'version':1,'inputSplats':info['splats'],'outputSplats':remaining,'removedSplats':count if applied else 0,
            'suspectSplats':count,'applied':applied,'sceneRadius':float(radius)}
