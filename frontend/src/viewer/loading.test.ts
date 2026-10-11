import { describe, expect, it, vi } from 'vitest';
import { MeshBasicMaterial, Texture } from 'three';
import { disposeMaterialResources, loadGlb, validateEmbeddedGlb } from './loading';
function glb(json: object, binary?: Uint8Array) {
  const raw = new TextEncoder().encode(JSON.stringify(json));
  const n = Math.ceil(raw.length / 4) * 4;
  const bytes = new Uint8Array(20 + n + (binary ? 8 + binary.length : 0)); bytes.fill(32, 20, 20 + n);
  const v = new DataView(bytes.buffer);
  v.setUint32(0, 0x46546c67, true); v.setUint32(4, 2, true); v.setUint32(8, bytes.length, true);
  v.setUint32(12, n, true); v.setUint32(16, 0x4e4f534a, true); bytes.set(raw, 20);
  if (binary) { v.setUint32(20 + n, binary.length, true); v.setUint32(24 + n, 0x004e4942, true); bytes.set(binary, 28 + n); }
  return bytes.buffer;
}
describe('local GLB loading', () => {
  it('rejects a partially loaded scene when its embedded texture fetches fail', async () => {
    const bytes = glb({ asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 }, material: 0 }] }], buffers: [{ byteLength: 40 }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }, { buffer: 0, byteOffset: 36, byteLength: 4 }],
      accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }],
      images: [{ bufferView: 1, mimeType: 'image/png' }], textures: [{ source: 0 }], materials: [{ pbrMetallicRoughness: { baseColorTexture: { index: 0 } } }],
    }, new Uint8Array(40));
    vi.stubGlobal('self', globalThis); vi.stubGlobal('location', { origin: 'http://127.0.0.1' });
    vi.stubGlobal('createImageBitmap', async () => { throw new Error('Texture decode failed'); });
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:http://127.0.0.1/broken-texture');
    vi.spyOn(console, 'error').mockImplementation(() => {});
    try { await expect(loadGlb(bytes).then(() => undefined)).rejects.toThrow(/texture/i); }
    finally { vi.restoreAllMocks(); vi.unstubAllGlobals(); }
  });
  it('rejects external image and buffer requests before parsing can issue them', () => {
    expect(() => validateEmbeddedGlb(glb({ images: [{ uri: 'https://example.com/image.png' }] }))).toThrow();
    expect(() => validateEmbeddedGlb(glb({ buffers: [{ uri: '../private' }] }))).toThrow();
    expect(() => validateEmbeddedGlb(glb({ images: [{ bufferView: 0, mimeType: 'image/png' }] }))).not.toThrow();
  });
  it('rejects animation, skinning, and unsupported compression', () => {
    expect(() => validateEmbeddedGlb(glb({ animations: [{}] }))).toThrow();
    expect(() => validateEmbeddedGlb(glb({ skins: [{}] }))).toThrow();
    expect(() => validateEmbeddedGlb(glb({ extensionsUsed: ['KHR_draco_mesh_compression'] }))).toThrow();
  });
  it('rejects truncated GLB length', () => {
    const bytes = glb({}); new DataView(bytes).setUint32(8, 100000, true);
    expect(() => validateEmbeddedGlb(bytes)).toThrow();
  });
  it('releases shared textures and bitmap handles once when replacing proxy materials', () => {
    let closed = 0; let released = 0;
    const map = new Texture({ close: () => { closed++; } }); map.addEventListener('dispose', () => { released++; });
    const a = new MeshBasicMaterial({ map }); const b = new MeshBasicMaterial({ map });
    disposeMaterialResources([a, b]);
    expect(closed).toBe(1); expect(released).toBe(1);
  });
});
