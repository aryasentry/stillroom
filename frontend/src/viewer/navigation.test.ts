import { describe, expect, it } from 'vitest';
import { BoxGeometry, Group, Mesh, MeshBasicMaterial, Vector3 } from 'three';
import { WalkController, buildCollisionWorld } from './navigation';

function room() {
  const scene = new Group();
  const floor = new Mesh(new BoxGeometry(6, .1, 5), new MeshBasicMaterial());
  floor.position.y = -.05; scene.add(floor);
  const wall = new Mesh(new BoxGeometry(.02, 3, 5), new MeshBasicMaterial());
  wall.position.set(2, 1.5, 0); scene.add(wall); scene.updateMatrixWorld(true);
  return buildCollisionWorld(scene);
}
describe('walk safety', () => {
  it('validates standing spawn against actual floor and rejects a spawn inside a wall', () => {
    const c = new WalkController(room(), 0);
    expect(c.setSpawn(new Vector3(0, 1.65, 0))).toBe(true);
    expect(c.setSpawn(new Vector3(2, 1.65, 0))).toBe(false);
    expect(c.setSpawn(new Vector3(10, 1.65, 0))).toBe(false);
  });
  it('keeps feet on floor across repeated gravity updates', () => {
    const c = new WalkController(room(), 0); c.setSpawn(new Vector3(0, 1.65, 0));
    for (let i = 0; i < 240; i++) c.step(new Vector3(), 1/60);
    expect(c.eye.y).toBeCloseTo(1.65, 2);
  });
  it('prevents tunnelling through a two centimetre wall even at high speed', () => {
    const c = new WalkController(room(), 0); c.setSpawn(new Vector3(0, 1.65, 0));
    for (let i = 0; i < 60; i++) c.step(new Vector3(20, 0, 0), 1/30);
    expect(c.eye.x).toBeLessThan(1.75);
    expect(c.eye.y).toBeCloseTo(1.65, 2);
  });
  it('resets to a safe spawn after falling beyond the room floor', () => {
    const c = new WalkController(room(), 0); c.setSpawn(new Vector3(0, 1.65, 0));
    let didReset = false;
    for (let i = 0; i < 300; i++) didReset = c.step(new Vector3(-4, 0, 0), 1/60) || didReset;
    expect(didReset).toBe(true);
    expect(c.eye.y).toBeGreaterThan(-2);
  });
});
