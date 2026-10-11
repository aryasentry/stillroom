import { Mesh, Object3D, Ray, Triangle, Vector3 } from 'three';
import { Octree } from 'three/addons/math/Octree.js';
import { Capsule } from 'three/addons/math/Capsule.js';
export function buildCollisionWorld(root: Object3D) {
  const world = new Octree(); root.updateWorldMatrix(true, true);
  root.traverse(node => {
    if (!(node instanceof Mesh)) return;
    const geometry = node.geometry; const positions = geometry.getAttribute('position');
    if (!positions) return;
    const index = geometry.index; const count = index?.count ?? positions.count;
    for (let i = 0; i + 2 < count; i += 3) {
      const p = [0, 1, 2].map(j => new Vector3().fromBufferAttribute(positions, index ? index.getX(i + j) : i + j).applyMatrix4(node.matrixWorld));
      world.addTriangle(new Triangle(p[0], p[1], p[2]));
      world.addTriangle(new Triangle(p[2].clone(), p[1].clone(), p[0].clone()));
    }
  });
  return world.build();
}
export class WalkController {
  eye = new Vector3();
  private spawn = new Vector3();
  private capsule = new Capsule();
  private verticalSpeed = 0;
  private valid = false;
  readonly eyeHeight = 1.65;
  readonly radius = .28;
  constructor(public world: Octree, public floorY: number) {}
  private at(eye: Vector3) {
    return new Capsule(new Vector3(eye.x, eye.y - this.eyeHeight + this.radius, eye.z),
      new Vector3(eye.x, eye.y - .12, eye.z), this.radius);
  }
  setSpawn(position: Vector3) {
    if (!position.toArray().every(Number.isFinite)) return false;
    const floor = this.world.rayIntersect(new Ray(new Vector3(position.x, this.floorY + .5, position.z), new Vector3(0, -1, 0)));
    if (!floor || Math.abs(floor.position.y - this.floorY) > .15) return false;
    const eye = new Vector3(position.x, floor.position.y + this.eyeHeight + .002, position.z);
    const capsule = this.at(eye);
    const overlap = this.world.capsuleIntersect(capsule);
    if (overlap && overlap.depth > .01) return false;
    this.eye.copy(eye); this.spawn.copy(eye); this.capsule.copy(capsule);
    this.verticalSpeed = 0; this.valid = true;
    return true;
  }
  step(velocity: Vector3, dt: number) {
    if (!this.valid) return false;
    dt = Math.max(0, Math.min(dt, .05));
    // Keep each displacement below one quarter capsule radius, including falls.
    const count = Math.max(1, Math.min(64, Math.ceil((velocity.length() + Math.abs(this.verticalSpeed) + 1) * dt / (this.radius / 4))));
    const h = dt / count;
    for (let i = 0; i < count; i++) {
      this.verticalSpeed = Math.max(-25, this.verticalSpeed - 18 * h);
      this.capsule.translate(new Vector3(velocity.x * h, this.verticalSpeed * h, velocity.z * h));
      for (let iteration = 0; iteration < 5; iteration++) {
        const hit = this.world.capsuleIntersect(this.capsule);
        if (!hit || hit.depth < 1e-7) break;
        this.capsule.translate(hit.normal.clone().multiplyScalar(hit.depth + 1e-6));
        if (hit.normal.y > .5) this.verticalSpeed = Math.max(0, this.verticalSpeed);
        if (hit.normal.y < -.5) this.verticalSpeed = Math.min(0, this.verticalSpeed);
      }
    }
    this.eye.copy(this.capsule.start); this.eye.y += this.eyeHeight - this.radius;
    if (this.eye.y < this.floorY - 2 || !this.eye.toArray().every(Number.isFinite)) {
      this.eye.copy(this.spawn); this.capsule.copy(this.at(this.spawn)); this.verticalSpeed = 0;
      return true;
    }
    return false;
  }
}
