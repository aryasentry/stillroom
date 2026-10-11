"""Learned camera priors refined exclusively with verified image feature tracks."""
from pathlib import Path
from collections import Counter
from itertools import combinations
import math
import numpy as np


def camera_prior(cameras, images, prediction):
    import pycolmap as c
    from .depth_worker import validate_prediction
    p=validate_prediction(prediction,len(images));model=c.Reconstruction()
    # InputProcessor resizes all views to one tensor shape. Different source
    # aspect ratios would involve cropping and need a separate intrinsic transform.
    ratios=[camera.width/camera.height for camera in cameras]
    if max(ratios)-min(ratios)>.01: raise ValueError('Assisted mode needs photos with the same aspect ratio. Use Standard for mixed cameras.')
    for camera in cameras:
        indices=[i for i,image in enumerate(images) if image.camera_id==camera.camera_id]
        if not indices: continue
        focal=float(np.median([(p['intrinsics'][i,0,0]*camera.width/p['imageSize'][0]+p['intrinsics'][i,1,1]*camera.height/p['imageSize'][1])/2 for i in indices]))
        camera.params=np.array([focal,camera.width/2,camera.height/2,0.])
        model.add_camera_with_trivial_rig(camera)
    for i,image in enumerate(images):
        ex=p['extrinsics'][i]
        # DA3 extrinsics already use COLMAP world-to-camera right/down/forward.
        model.add_image_with_trivial_frame(image,c.Rigid3d(c.Rotation3d(ex[:,:3]),ex[:,3]))
    return model


def filter_supported_model(model, min_observations=30):
    """Deregister unsupported cameras and remove nonfinite/high-error tracks."""
    while True:
        model.update_point_3d_errors()
        bad=[identifier for identifier,point in model.points3D.items()
             if not np.isfinite(point.xyz).all() or not math.isfinite(point.error)
             or point.error>4 or point.track.length()<2]
        for identifier in bad: model.delete_point3D(identifier)
        weak=[i for i in model.reg_image_ids() if model.images[i].num_points3D<min_observations]
        if not bad and not weak: break
        for identifier in weak: model.deregister_frame(model.images[identifier].frame_id)
        # Deregistration changes neighboring support and mean track error.
        # Recompute both until the surviving geometry is stable.


def retain_largest_component(model):
    """Keep one group whose relative camera geometry is supported by shared tracks."""
    while True:
        before=(model.num_reg_images(),model.num_points3D())
        graph={i:set() for i in model.reg_image_ids()}
        edges=Counter()
        for point in model.points3D.values():
            ids={e.image_id for e in point.track.elements if e.image_id in graph}
            edges.update(combinations(sorted(ids),2))
        # A lone triangulated point cannot establish relative group geometry.
        for (a,b),support in edges.items():
            if support>=15: graph[a].add(b);graph[b].add(a)
        remaining=set(graph);groups=[]
        while remaining:
            stack=[min(remaining)];group=set()
            while stack:
                identifier=stack.pop()
                if identifier in group: continue
                group.add(identifier);stack.extend(graph[identifier]-group)
            remaining-=group
            points=sum(any(e.image_id in group for e in p.track.elements) for p in model.points3D.values())
            if len(group)>=3 and points>=100: groups.append((group,points))
        groups.sort(key=lambda pair:(-len(pair[0]),-pair[1],min(pair[0])))
        retained=groups[0][0] if groups else set()
        for identifier in list(model.reg_image_ids()):
            if identifier not in retained: model.deregister_frame(model.images[identifier].frame_id)
        filter_supported_model(model)

        if before==(model.num_reg_images(),model.num_points3D()): break


def recover(database, image_dir, output, ids, prediction, threads):
    import pycolmap as c
    output=Path(output)
    with c.Database.open(database) as db:
        cameras=db.read_all_cameras();by_name={image.name:image for image in db.read_all_images()}
        images=[by_name[identifier+'.jpg'] for identifier in ids]
    model=camera_prior(cameras,images,prediction)
    opts=c.IncrementalPipelineOptions();opts.num_threads=threads
    opts.triangulation.create_max_angle_error=8;opts.triangulation.continue_max_angle_error=8
    opts.triangulation.min_angle=.5;opts.triangulation.ignore_two_view_tracks=False
    opts.mapper.filter_max_reproj_error=8
    ba=c.BundleAdjustmentOptions();ba.refine_focal_length=True;ba.refine_extra_params=True
    for iteration in range(3):
        path=output/f'assisted-{iteration}';path.mkdir()
        model=c.triangulate_points(model,database,image_dir,path,options=opts,clear_points=iteration==0)
        c.bundle_adjustment(model,options=ba)
    filter_supported_model(model)
    if model.num_reg_images()>=3 and model.num_points3D()>=100:
        c.bundle_adjustment(model,options=ba);filter_supported_model(model)
    retain_largest_component(model)
    # Disk COLMAP models contain registered views only. Reload that bounded model
    # so coverage and downstream view selection cannot count rejected images.
    supported=output/'assisted-supported';supported.mkdir();model.write(supported)
    model=c.Reconstruction(supported)
    if model.num_reg_images()<3 or model.num_points3D()<100: return {}
    return {0:model}
