import { describe, expect, it } from 'vitest';
import { Group, PerspectiveCamera, Vector3 } from 'three';
import { captureSourceView, restoreSourceView } from './viewpoint';
describe('saved camera source frame', () => {
  function root() { const g = new Group(); g.position.set(10, 0, 0); g.rotation.y = Math.PI / 2; g.scale.setScalar(2); g.updateMatrixWorld(true); return g; }
  it('removes display translation, uniform scale and rotation when saving', () => {
    const camera = new PerspectiveCamera(); camera.position.set(16, 4, -2); camera.rotation.y = Math.PI / 2;
    const view = captureSourceView(root(), camera, new Vector3(10, 0, 0));
    expect(view.position[0]).toBeCloseTo(1); expect(view.position[1]).toBeCloseTo(2); expect(view.position[2]).toBeCloseTo(3);
    expect(view.quaternion[0]).toBeCloseTo(0); expect(view.quaternion[1]).toBeCloseTo(0); expect(view.quaternion[2]).toBeCloseTo(0); expect(view.quaternion[3]).toBeCloseTo(1);
    expect(view.target).toEqual([0, 0, 0]);
  });
  it('restores saved source orientation through a changed display transform', () => {
    const camera = new PerspectiveCamera();
    restoreSourceView(root(), camera, { id: 'v', name: 'v', position: [1, 2, 3], quaternion: [0, 0, 0, 1] });
    expect(camera.position.x).toBeCloseTo(16); expect(camera.position.y).toBeCloseTo(4); expect(camera.position.z).toBeCloseTo(-2);
    const direction = camera.getWorldDirection(new Vector3()); expect(direction.x).toBeCloseTo(-1); expect(direction.z).toBeCloseTo(0);
  });
});
