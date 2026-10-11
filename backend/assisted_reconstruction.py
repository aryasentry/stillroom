"""Learned camera priors refined exclusively with verified image feature tracks."""
from pathlib import Path
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
    model.update_point_3d_errors()
    for identifier,point in list(model.points3D.items()):
        if not np.isfinite(point.xyz).all() or not math.isfinite(point.error) or point.error>4 or point.track.length()<2:
            model.delete_point3D(identifier)
    for identifier in list(model.reg_image_ids()):
        image=model.images[identifier]
        if image.num_points3D<min_observations: model.deregister_frame(image.frame_id)
    # Deregistration can leave tracks with fewer than two remaining views.
    for identifier,point in list(model.points3D.items()):
        if point.track.length()<2: model.delete_point3D(identifier)


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
    # Disk COLMAP models contain registered views only. Reload that bounded model
    # so coverage and downstream view selection cannot count rejected images.
    supported=output/'assisted-supported';supported.mkdir();model.write(supported)
    model=c.Reconstruction(supported)
    if model.num_reg_images()<3 or model.num_points3D()<100: return {}
    return {0:model}
