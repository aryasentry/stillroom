import { Group, LoadingManager, Material, Mesh, Object3D, Texture } from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
export function disposeMaterialResources(materials: Iterable<Material>) {
  const unique = new Set(materials); const textures = new Set<Texture>(); const images = new Set<object>();
  for (const material of unique) for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
  for (const texture of textures) { texture.dispose(); const source = texture.source?.data; if (source && typeof source === 'object' && !images.has(source)) { images.add(source); if ('close' in source && typeof source.close === 'function') source.close(); } }
  for (const material of unique) material.dispose();
}

export function validateEmbeddedGlb(buffer: ArrayBuffer): void {
  const v = new DataView(buffer);
  if (buffer.byteLength < 20 || v.getUint32(0, true) !== 0x46546c67 || v.getUint32(4, true) !== 2 || v.getUint32(8, true) !== buffer.byteLength) throw new Error('Invalid or truncated GLB.');
  const length = v.getUint32(12, true);
  if (v.getUint32(16, true) !== 0x4e4f534a || length > buffer.byteLength - 20) throw new Error('Invalid GLB JSON chunk.');
  const json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, length)));
  for (const value of [...(json.images ?? []), ...(json.buffers ?? [])]) {
    if (value.uri) throw new Error('Use a self-contained GLB with embedded buffers and PNG/JPEG textures. External resources are disabled.');
  }
  if (json.animations?.length || json.skins?.length) throw new Error('Animated and skinned GLBs are not supported. Export a static scene.');
  const unsupported = ['KHR_draco_mesh_compression', 'EXT_meshopt_compression', 'KHR_meshopt_compression', 'KHR_texture_basisu', 'EXT_mesh_gpu_instancing'];
  if ([...(json.extensionsUsed ?? []), ...(json.extensionsRequired ?? [])].some(x => unsupported.includes(x))) throw new Error('Compressed or instanced GLBs are not supported. Export an uncompressed static GLB.');
}
export async function loadGlb(bytes: ArrayBuffer) {
  validateEmbeddedGlb(bytes);
  const manager = new LoadingManager();
  const failed = new Set<string>(); const objectUrls = new Set<string>();
  manager.onError = url => { failed.add(url); };
  manager.setURLModifier(url => {
    if (url.startsWith(`blob:${location.origin}/`)) { objectUrls.add(url); return url; }
    failed.add(url);
    throw new Error('External scan resources are disabled.');
  });
  try {
    const result = await new GLTFLoader(manager).parseAsync(bytes, '');
    // GLTFLoader deliberately swallows failed texture loads and resolves a partial scene.
    // A scan missing its appearance is not a successful import for Stillroom.
    if (failed.size) {
      const objects = new Group(); objects.add(...result.scenes); disposeObject(objects);
      throw new Error(`Could not load ${failed.size} embedded scan texture(s). Check that the GLB contains valid PNG/JPEG images and that local blob resources are allowed, then restore the room.`);
    }
    return result.scene;
  } finally {
    // Three only revokes embedded-image URLs on success; also release failed URLs.
    for (const url of objectUrls) URL.revokeObjectURL(url);
  }
}
export function disposeObject(root: Object3D) {
  const materials = new Set<Material>();
  root.traverse(node => {
    if (node instanceof Mesh) {
      node.geometry.dispose();
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        materials.add(material);
      }
    }
  });
  disposeMaterialResources(materials);
}
