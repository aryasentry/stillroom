import { Object3D, PerspectiveCamera, Quaternion, Vector3 } from 'three';
import type { Vec3, Viewpoint } from '../types';
export function captureSourceView(root: Object3D, camera: PerspectiveCamera, target?: Vector3): Viewpoint {
  root.updateMatrixWorld(true);
  const view: Viewpoint = { id: crypto.randomUUID(), name: 'View', position: root.worldToLocal(camera.position.clone()).toArray() as Vec3,
    quaternion: root.getWorldQuaternion(new Quaternion()).invert().multiply(camera.quaternion).normalize().toArray() };
  if (target) view.target = root.worldToLocal(target.clone()).toArray() as Vec3;
  return view;
}
export function restoreSourceView(root: Object3D, camera: PerspectiveCamera, view: Viewpoint) {
  camera.position.copy(root.localToWorld(new Vector3().fromArray(view.position)));
  camera.quaternion.copy(root.getWorldQuaternion(new Quaternion()).multiply(new Quaternion().fromArray(view.quaternion))).normalize();
  camera.updateMatrixWorld(true);
}
