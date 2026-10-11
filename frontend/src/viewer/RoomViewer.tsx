import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { AmbientLight, Box3, Color, DirectionalLight, Euler, Group, Matrix3, Mesh, MeshBasicMaterial, PerspectiveCamera, Raycaster, Scene, Vector2, Vector3, WebGLRenderer } from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import type { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';
import type { Transform, Vec3, ViewerDiagnostics, ViewerHandle, ViewerProps, ViewerStatus, Viewpoint } from '../types';
import { disposeMaterialResources, disposeObject, loadGlb } from './loading';
import { buildCollisionWorld, WalkController } from './navigation';
import { captureSourceView, restoreSourceView } from './viewpoint';
import { FrameMeter } from './frameMeter';
import './viewer.css';

interface Marker { id: string; title: string; x: number; y: number; selected: boolean; approximate: boolean; review: boolean }
interface Runtime extends ViewerHandle { sync(): void }
const fallbackView = (): Viewpoint => ({ id: crypto.randomUUID(), name: 'View', position: [0, 1.65, 5], quaternion: [0, 0, 0, 1] });
const applyTransform = (node: Group, t: Transform) => {
  node.position.fromArray(t.position); node.rotation.set(...t.rotation, 'XYZ'); node.scale.setScalar(t.scale); node.updateMatrixWorld(true);
};
const typing = (target: EventTarget | null) => target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName));
const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

/** All persisted anchors and views use the original scene-root frame. */
export const RoomViewer = forwardRef<ViewerHandle, ViewerProps>(function RoomViewer(props, ref) {
  const host = useRef<HTMLDivElement>(null); const latest = useRef(props); latest.current = props;
  const runtime = useRef<Runtime | null>(null);
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [status, setStatus] = useState<ViewerStatus>({ loading: true, progress: null, error: null, canWalk: false, message: 'Opening room…' });
  const [retry, setRetry] = useState(0);
  useImperativeHandle(ref, () => ({
    captureView: () => runtime.current?.captureView() ?? fallbackView(),
    goToView: view => runtime.current?.goToView(view),
    returnToEntrance: () => runtime.current?.returnToEntrance(),
    captureThumbnail: () => runtime.current?.captureThumbnail() ?? Promise.resolve(null),
    getDiagnostics: () => runtime.current?.getDiagnostics() ?? null,
  }), []);

  useEffect(() => {
    if (!host.current) return;
    const container = host.current; const project = latest.current.project;
    const asset = project.assets.find(a => a.id === project.room.scanAssetId);
    let active = true; let loaded = false; let frame = 0; let contextLost = false;
    let renderer: WebGLRenderer | null = null; let controls: OrbitControls | null = null;
    let gizmo: TransformControls | null = null; let gizmoMemoryId: string | null = null; let gizmoMoved = false;
    const anchorHandle = new Group(); let dirty = true; let markersDirty = true;
    let scan: Group | SplatMesh | null = null; let spark: SparkRenderer | null = null;
    let proxy: Group | null = null; let walker: WalkController | null = null;
    let canWalk = false; let walkReason = 'Set source units and floor before walking.';
    const abort = new AbortController(); const started = performance.now(); let loadMs = 0;
    const meter = new FrameMeter(); let lastFrame = performance.now(); let lastMarkers = 0; let lastView = 0;
    let lastTransform = ''; let lastNavigation = ''; let lastCollision = '';
    let statusValue: ViewerStatus = { loading: !!asset, progress: null, error: null, canWalk: false, message: asset ? 'Opening room…' : 'Import a scan to enter this room.' };
    const report = (patch: Partial<ViewerStatus>) => { if (!active) return; statusValue = { ...statusValue, ...patch }; setStatus(statusValue); latest.current.onStatus(statusValue); };
    report(statusValue); setMarkers([]);
    const scene = new Scene(); scene.background = new Color('#dad7ce');
    const root = new Group(); scene.add(root);
    root.add(anchorHandle);
    applyTransform(root, project.room.transform);
    const camera = new PerspectiveCamera(58, 1, .02, 2000); camera.position.set(0, 1.65, 6);
    scene.add(new AmbientLight(0xffffff, 1.8));
    const sunlight = new DirectionalLight(0xfff1de, 2.4); sunlight.position.set(2, 7, 5); scene.add(sunlight);
    const raycaster = new Raycaster(); const keys = new Set<string>();
    let pointer: { x: number; y: number; moved: boolean } | null = null;
    let orbitSettleFrames = 0;
    let defaultView = fallbackView();

    const captureView = (): Viewpoint => captureSourceView(root, camera, controls?.target);
    const goToView = (view: Viewpoint, validateWalk = true) => {
      const position = root.localToWorld(new Vector3().fromArray(view.position));
      if (validateWalk && latest.current.mode === 'walk' && walker && !walker.setSpawn(position)) {
        report({ message: 'That view is not a safe walking position. Use Orbit or Fly to visit it.' }); return;
      }
      restoreSourceView(root, camera, view);
      if (validateWalk && latest.current.mode === 'walk' && walker) camera.position.copy(walker.eye);
      if (controls) {
        const distance = view.target ? camera.position.distanceTo(root.localToWorld(new Vector3().fromArray(view.target))) : 3;
        controls.target.copy(camera.position).add(camera.getWorldDirection(new Vector3()).multiplyScalar(Math.max(.1, distance)));
        // Saved views teleport; no interpolation through walls.
        // Keep the stored quaternion authoritative (including source-space roll).
        const orientation = camera.quaternion.clone(); controls.enableDamping = false; controls.update(); controls.enableDamping = true; camera.quaternion.copy(orientation); orbitSettleFrames = 0;
      }
      camera.updateMatrixWorld(true); dirty = true; latest.current.onViewChange?.(captureView());
    };
    const getDiagnostics = (): ViewerDiagnostics | null => {
      if (!renderer) return null;
      const gl = renderer.getContext(); const info = gl.getExtension('WEBGL_debug_renderer_info');
      const sorted = [...meter.samples].sort((a, b) => a - b); let triangles = 0;
      scan?.traverse(node => { if (node instanceof Mesh) triangles += (node.geometry.index?.count ?? node.geometry.getAttribute('position')?.count ?? 0) / 3; });
      return { browser: navigator.userAgent, gpu: String(info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)), width: renderer.domElement.width, height: renderer.domElement.height,
        pixelRatio: renderer.getPixelRatio(), loadMs, frameTimeMedianMs: sorted[Math.floor(sorted.length * .5)] ?? 0,
        frameTimeP95Ms: sorted[Math.floor(sorted.length * .95)] ?? 0, frames: meter.frames, triangles,
        splats: scan && 'numSplats' in scan ? scan.numSplats : 0, geometries: renderer.info.memory.geometries, textures: renderer.info.memory.textures };
    };
    const configureWalking = () => {
      const room = latest.current.project.room;
      walker = null; canWalk = false;
      if (room.sourceUnits !== 'metres' && !room.calibration) { walkReason = 'Declare source units or calibrate scale to enable Walk.'; return; }
      if (room.floorY === null) { walkReason = 'Set the floor height to enable Walk.'; return; }
      const geometry = room.collisionAligned && proxy ? proxy : scan instanceof Group ? scan : null;
      if (!geometry) { walkReason = 'Walk needs a mesh or an aligned collision proxy.'; return; }
      let triangles = 0; geometry.traverse(n => { if (n instanceof Mesh) triangles += (n.geometry.index?.count ?? n.geometry.getAttribute('position')?.count ?? 0) / 3; });
      if (!triangles || triangles > 500000) { walkReason = triangles ? 'This mesh is too large for safe collision setup. Use a simplified proxy.' : 'No usable collision geometry.'; return; }
      const candidate = new WalkController(buildCollisionWorld(geometry), room.floorY);
      const entrance = room.entrance ? root.localToWorld(new Vector3().fromArray(room.entrance.position)) : camera.position.clone();
      if (!candidate.setSpawn(entrance)) { walkReason = 'Entrance needs clear standing room above the configured floor.'; return; }
      if (latest.current.mode === 'walk') candidate.setSpawn(camera.position);
      walker = candidate; canWalk = true; walkReason = 'Walk ready. WASD to move; drag to look. Esc releases the cursor.';
    };
    const sync = () => {
      if (!loaded) return;
      dirty = true;
      if (renderer) renderer.setPixelRatio(Math.min(devicePixelRatio, latest.current.project.settings.quality === 'high' ? 2 : 1.5));
      const room = latest.current.project.room;
      const transform = JSON.stringify(room.transform);
      if (transform !== lastTransform) {
        const before = captureView(); applyTransform(root, room.transform); lastTransform = transform;
        goToView(before, false);
      }
      const collision = JSON.stringify([room.transform, room.collisionTransform, room.floorY, room.sourceUnits, room.calibration, room.collisionAligned, room.entrance]);
      if (collision !== lastCollision) { if (proxy) { applyTransform(proxy, room.collisionTransform); proxy.visible = !room.collisionAligned; } configureWalking(); lastCollision = collision; }
      const mode = latest.current.mode;
      if (controls) controls.enabled = mode === 'orbit' && !latest.current.placing && !gizmo?.dragging;
      const selected = latest.current.project.memories.find(memory => memory.id === latest.current.selectedMemoryId && !memory.deletedAt && memory.anchor.assetId === asset?.id && memory.anchor.scanVersion === asset?.sha256);
      if (gizmo && !gizmo.dragging) {
        if (selected?.anchor.placement === 'approximate' && latest.current.editing && !latest.current.placing && latest.current.onMoveAnchor) {
          gizmoMemoryId = selected.id; anchorHandle.position.fromArray(selected.anchor.position); anchorHandle.updateMatrixWorld(true); gizmo.attach(anchorHandle);
        } else { gizmoMemoryId = null; gizmo.detach(); }
      }
      if (mode !== lastNavigation) {
        keys.clear();
        if (mode === 'walk' && walker) {
          if (!walker.setSpawn(camera.position)) walker.setSpawn(root.localToWorld(new Vector3().fromArray(room.entrance?.position ?? defaultView.position)));
          camera.position.copy(walker.eye);
        }
        lastNavigation = mode;
      }
      report({ canWalk, message: mode === 'walk' ? walkReason : room.sceneType === 'splat' ? 'Gaussian splat · placement is approximate · markers show through surfaces' : latest.current.placing ? 'Click a surface to place your memory.' : 'Drag to look around. Scroll to move closer.' });
    };
    runtime.current = { captureView, goToView, getDiagnostics, sync,
      returnToEntrance: () => goToView(latest.current.project.room.entrance ?? defaultView),
      captureThumbnail: async () => {
        if (!renderer || !loaded || contextLost) return null;
        if (spark) await spark.update({ scene, camera });
        const helper = gizmo?.getHelper(); const helperVisible = helper?.visible;
        if (helper) helper.visible = false;
        renderer.render(scene, camera);
        const source = renderer.domElement; const canvas = document.createElement('canvas');
        canvas.width = 640; canvas.height = Math.max(1, Math.round(640 * source.height / source.width));
        canvas.getContext('2d')?.drawImage(source, 0, 0, canvas.width, canvas.height);
        if (helper) helper.visible = helperVisible ?? false;
        dirty = true;
        return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .82));
      },
    };
    if (!asset) { loaded = false; return () => { active = false; runtime.current = null; }; }
    try { renderer = new WebGLRenderer({ antialias: asset.kind === 'glb', alpha: false, preserveDrawingBuffer: true, powerPreference: 'high-performance' }); }
    catch { report({ loading: false, error: 'WebGL2 is unavailable. Enable hardware acceleration or use a WebGL2-capable browser.', message: 'The room could not open.' }); return () => { active = false; runtime.current = null; }; }
    const canvas = renderer.domElement; canvas.tabIndex = 0; canvas.setAttribute('aria-label', 'Interactive room. Orbit with drag. Fly or walk with WASD and drag to look.');
    container.prepend(canvas); controls = new OrbitControls(camera, canvas); controls.enableDamping = true; controls.dampingFactor = .09;
    const orbitStart = () => { orbitSettleFrames = 120; }; const orbitEnd = () => { orbitSettleFrames = 60; };
    controls.addEventListener('start', orbitStart); controls.addEventListener('end', orbitEnd);
    const invalidate = () => { dirty = true; };
    controls.addEventListener('change', invalidate);
    gizmo = new TransformControls(camera, canvas); gizmo.setMode('translate'); gizmo.setSpace('local'); gizmo.setSize(.72);
    gizmo.setColors('#b36b50', '#64836b', '#627f9f', '#d1a15d'); scene.add(gizmo.getHelper());
    const gizmoDrag = (event: { value: unknown }) => {
      keys.clear(); orbitSettleFrames = 0;
      if (controls) controls.enabled = !event.value && latest.current.mode === 'orbit' && !latest.current.placing;
      if (event.value) gizmoMoved = false;
      else if (gizmoMoved && gizmoMemoryId) { latest.current.onMoveAnchor?.(gizmoMemoryId, anchorHandle.position.toArray() as Vec3); gizmoMoved = false; }
      dirty = true;
    };
    const gizmoChange = () => { gizmoMoved = true; dirty = true; };
    gizmo.addEventListener('dragging-changed', gizmoDrag); gizmo.addEventListener('objectChange', gizmoChange); gizmo.addEventListener('change', invalidate);
    const resize = () => { if (!renderer) return; const { width, height } = container.getBoundingClientRect(); renderer.setPixelRatio(Math.min(devicePixelRatio, latest.current.project.settings.quality === 'high' ? 2 : 1.5)); renderer.setSize(Math.max(1, width), Math.max(1, height)); camera.aspect = width / Math.max(1, height); camera.updateProjectionMatrix(); dirty = true; };
    const observer = new ResizeObserver(resize); observer.observe(container); resize();
    const clearKeys = () => { keys.clear(); pointer = null; };
    const down = (event: PointerEvent) => { if (event.button !== 0) return; canvas.focus({ preventScroll: true }); pointer = { x: event.clientX, y: event.clientY, moved: false }; };
    const move = (event: PointerEvent) => {
      if (pointer && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) > 4) pointer.moved = true;
      if (gizmo?.dragging || latest.current.mode === 'orbit' || latest.current.placing || (!(event.buttons & 1) && document.pointerLockElement !== canvas)) return;
      const rotation = new Euler().setFromQuaternion(camera.quaternion, 'YXZ');
      rotation.y -= event.movementX * .0025; rotation.x = Math.max(-1.48, Math.min(1.48, rotation.x - event.movementY * .0025)); camera.quaternion.setFromEuler(rotation);
    };
    const up = (event: PointerEvent) => {
      const was = pointer; pointer = null;
      if (!was || was.moved || !latest.current.placing || !latest.current.editing || !scan || !loaded) return;
      const rect = canvas.getBoundingClientRect(); camera.updateMatrixWorld(true);
      raycaster.setFromCamera(new Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
      // Spark's synchronous WASM raycast is used only on an explicit placement click.
      const hit = raycaster.intersectObject(scan, true)[0];
      if (!hit) { report({ message: 'No surface at that point. Try a clearer part of the scan.' }); return; }
      const point = root.worldToLocal(hit.point.clone()).toArray() as Vec3;
      const normal = hit.face?.normal.clone().applyMatrix3(new Matrix3().getNormalMatrix(hit.object.matrixWorld)).transformDirection(root.matrixWorld.clone().invert()).normalize();
      const path: string[] = []; let node = hit.object;
      while (node !== root && node.parent) { path.unshift(`${node.name || node.type}[${node.parent.children.indexOf(node)}]`); node = node.parent; }
      latest.current.onPick({ anchor: { position: point, ...(normal ? { normal: normal.toArray() as Vec3 } : {}), assetId: asset.id, scanVersion: asset.sha256,
        placement: asset.kind === 'glb' ? 'surface' : 'approximate', nodePath: path.join('/'), triangleIndex: hit.faceIndex ?? undefined, needsReview: false }, viewpoint: captureView() });
    };
    const keydown = (event: KeyboardEvent) => {
      if (gizmo?.dragging || typing(event.target) || document.activeElement !== canvas || latest.current.mode === 'orbit') return;
      if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE', 'ShiftLeft', 'ShiftRight', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.code)) { event.preventDefault(); keys.add(event.code); }
    };
    const keyup = (event: KeyboardEvent) => keys.delete(event.code);
    const lost = (event: Event) => { event.preventDefault(); contextLost = true; cancelAnimationFrame(frame); report({ error: 'Graphics context lost. Restore the room to continue.', loading: false }); };
    const restored = () => { if (active) setRetry(n => n + 1); };
    canvas.addEventListener('pointerdown', down); canvas.addEventListener('pointermove', move); canvas.addEventListener('pointerup', up); canvas.addEventListener('blur', clearKeys);
    canvas.addEventListener('webglcontextlost', lost); canvas.addEventListener('webglcontextrestored', restored);
    window.addEventListener('keydown', keydown); window.addEventListener('keyup', keyup); window.addEventListener('blur', clearKeys);
    const fetchAsset = async (id: string) => {
      const response = await fetch(`/api/projects/${encodeURIComponent(project.id)}/assets/${encodeURIComponent(id)}`, { signal: abort.signal });
      if (!response.ok) throw new Error(`Could not load scan (${response.status}). Check or relink the asset in project settings.`);
      const length = Number(response.headers.get('content-length')); const reader = response.body?.getReader();
      if (!reader) return response.arrayBuffer();
      const chunks: Uint8Array[] = []; let total = 0;
      while (true) { const { done, value } = await reader.read(); if (done) break; total += value.length; chunks.push(value); report({ progress: length ? Math.min(.95, total / length * .95) : null }); }
      const bytes = new Uint8Array(total); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; } return bytes.buffer;
    };
    void (async () => {
      try {
        const bytes = await fetchAsset(asset.id); if (!active) return;
        if (asset.kind === 'glb') {
          const object = await loadGlb(bytes); if (!active) { disposeObject(object); return; } scan = object;
        } else {
          const { SplatMesh: MeshClass, SparkRenderer: RendererClass } = await import('@sparkjsdev/spark');
          if (!active || !renderer) return;
          spark = new RendererClass({ renderer, enableLod: false }); scene.add(spark);
          const object = new MeshClass({ fileBytes: bytes, fileName: asset.originalName, lod: false, raycastable: true });
          scan = object; await object.initialized; if (!active) return;
        }
        applyTransform(root, latest.current.project.room.transform); root.add(scan); root.updateMatrixWorld(true);
        const collisionId = latest.current.project.room.collisionAssetId;
        if (collisionId) {
          try { const object = await loadGlb(await fetchAsset(collisionId)); if (!active) { disposeObject(object); return; } proxy = object;
            const oldMaterials = new Set<import('three').Material>();
            proxy.traverse(node => { if (node instanceof Mesh) {
              for (const material of Array.isArray(node.material) ? node.material : [node.material]) oldMaterials.add(material);
              node.material = new MeshBasicMaterial({ color: '#aa5638', wireframe: true, transparent: true, opacity: .6, depthTest: false }); node.renderOrder = 10;
            } });
            disposeMaterialResources(oldMaterials);
            proxy.visible = !latest.current.project.room.collisionAligned; root.add(proxy); applyTransform(proxy, latest.current.project.room.collisionTransform); }
          catch (error) { if (!active) return; report({ message: `Collision proxy unavailable: ${errorMessage(error)}` }); }
        }
        const bounds = 'getBoundingBox' in scan ? scan.getBoundingBox().applyMatrix4(root.matrixWorld) : new Box3().setFromObject(scan);
        if (!bounds.isEmpty()) { const center = bounds.getCenter(new Vector3()); const size = bounds.getSize(new Vector3()); const distance = Math.max(size.x, size.y, size.z, 1) * 1.05; camera.position.copy(center).add(new Vector3(distance * .45, distance * .3, distance)); camera.lookAt(center); controls?.target.copy(center); camera.far = Math.max(1000, distance * 30); camera.updateProjectionMatrix(); }
        defaultView = captureView(); lastTransform = JSON.stringify(latest.current.project.room.transform);
        if (latest.current.project.room.entrance) goToView(latest.current.project.room.entrance!);
        loaded = true; loadMs = performance.now() - started; sync(); report({ loading: false, progress: 1, error: null, diagnostics: getDiagnostics() ?? undefined });
      } catch (error) { if (active && !abort.signal.aborted) report({ loading: false, error: errorMessage(error), message: 'The scan could not open.' }); }
    })();
    const lastPosition = camera.position.clone(); const lastQuaternion = camera.quaternion.clone();
    const animate = (now: number) => {
      if (!active || contextLost || !renderer) return;
      const dt = Math.min((now - lastFrame) / 1000, .05); lastFrame = now;
      if (loaded) {
        const mode = latest.current.mode;
        if (mode === 'orbit') { if (orbitSettleFrames > 0) { controls?.update(); orbitSettleFrames--; } }
        else if (!gizmo?.dragging && !typing(document.activeElement)) {
          const forward = camera.getWorldDirection(new Vector3()); if (mode === 'walk') { forward.y = 0; forward.normalize(); }
          const right = new Vector3().crossVectors(forward, camera.up).normalize(); const velocity = new Vector3();
          if (keys.has('KeyW') || keys.has('ArrowUp')) velocity.add(forward); if (keys.has('KeyS') || keys.has('ArrowDown')) velocity.sub(forward);
          if (keys.has('KeyD') || keys.has('ArrowRight')) velocity.add(right); if (keys.has('KeyA') || keys.has('ArrowLeft')) velocity.sub(right);
          if (mode === 'fly') { if (keys.has('KeyE')) velocity.y++; if (keys.has('KeyQ')) velocity.y--; }
          velocity.normalize().multiplyScalar(Math.max(.1, Math.min(10, latest.current.speed ?? latest.current.project.settings.navigationSpeed)) * (keys.has('ShiftLeft') || keys.has('ShiftRight') ? 2 : 1));
          if (mode === 'walk' && walker && canWalk) { if (walker.step(velocity, dt)) report({ message: 'Returned to the safe entrance after leaving the floor.' }); camera.position.copy(walker.eye); }
          else if (mode === 'fly') camera.position.addScaledVector(velocity, dt);
          controls?.target.copy(camera.position).add(camera.getWorldDirection(new Vector3()).multiplyScalar(3));
        }
        camera.updateMatrixWorld(true);
        if (dirty || lastPosition.distanceToSquared(camera.position) > 1e-12 || lastQuaternion.angleTo(camera.quaternion) > 1e-7) markersDirty = true;
        if (markersDirty && now - lastMarkers > 120) {
          markersDirty = false; lastMarkers = now; const rect = container.getBoundingClientRect(); const next: Marker[] = [];
          if (latest.current.showMarkers && scan) for (const memory of latest.current.project.memories) {
            if (memory.deletedAt || memory.anchor.assetId !== asset.id || memory.anchor.scanVersion !== asset.sha256) continue;
            const sourcePosition = gizmo?.dragging && memory.id === gizmoMemoryId ? anchorHandle.position.clone() : new Vector3().fromArray(memory.anchor.position);
            const world = root.localToWorld(sourcePosition); const projected = world.clone().project(camera);
            if (projected.z < -1 || projected.z > 1 || Math.abs(projected.x) > 1.05 || Math.abs(projected.y) > 1.05) continue;
            if (asset.kind === 'glb') { const delta = world.clone().sub(camera.position); const distance = delta.length(); raycaster.set(camera.position, delta.normalize()); raycaster.far = distance - .035 * latest.current.project.room.transform.scale; const hit = raycaster.intersectObject(scan, true)[0]; raycaster.far = Infinity; if (hit) continue; }
            next.push({ id: memory.id, title: memory.title, x: (projected.x * .5 + .5) * rect.width, y: (-projected.y * .5 + .5) * rect.height, selected: memory.id === latest.current.selectedMemoryId, approximate: memory.anchor.placement === 'approximate', review: memory.anchor.needsReview });
          }
          setMarkers(next);
        }
        if (now - lastView > 1200) { lastView = now; latest.current.onViewChange?.(captureView()); }
      }
      const cameraChanged = lastPosition.distanceToSquared(camera.position) > 1e-12 || lastQuaternion.angleTo(camera.quaternion) > 1e-7;
      const shouldRender = !!spark || dirty || cameraChanged;
      if (shouldRender) {
        try { renderer.render(scene, camera); } catch (error) { report({ error: `Rendering stopped: ${errorMessage(error)}` }); return; }
        dirty = false; lastPosition.copy(camera.position); lastQuaternion.copy(camera.quaternion);
      }
      // Cadence is measured only between consecutive rendered rAF ticks, never across idle gaps.
      meter.tick(now, loaded && shouldRender);
      frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => {
      active = false; abort.abort(); cancelAnimationFrame(frame); observer.disconnect(); clearKeys();
      if (document.pointerLockElement === canvas) document.exitPointerLock();
      canvas.removeEventListener('pointerdown', down); canvas.removeEventListener('pointermove', move); canvas.removeEventListener('pointerup', up); canvas.removeEventListener('blur', clearKeys);
      canvas.removeEventListener('webglcontextlost', lost); canvas.removeEventListener('webglcontextrestored', restored);
      window.removeEventListener('keydown', keydown); window.removeEventListener('keyup', keyup); window.removeEventListener('blur', clearKeys);
      gizmo?.removeEventListener('dragging-changed', gizmoDrag); gizmo?.removeEventListener('objectChange', gizmoChange); gizmo?.removeEventListener('change', invalidate); gizmo?.dispose();
      controls?.removeEventListener('change', invalidate); controls?.removeEventListener('start', orbitStart); controls?.removeEventListener('end', orbitEnd); controls?.dispose(); if (scan) { if ('numSplats' in scan) { const splat = scan; if (splat.isInitialized) splat.dispose(); else void splat.initialized.then(() => splat.dispose(), () => splat.dispose()); } else disposeObject(scan); } if (proxy) disposeObject(proxy);
      // Spark dispose terminates its sort/LoD workers and releases GPU accumulators.
      spark?.dispose(); renderer?.dispose(); renderer?.forceContextLoss(); canvas.remove(); runtime.current = null;
    };
  }, [props.project.id, props.project.room.scanAssetId, props.project.room.scanVersion, props.project.room.collisionAssetId, retry]);
  useEffect(() => { runtime.current?.sync(); }, [props.project.room, props.project.memories, props.project.settings.quality, props.selectedMemoryId, props.showMarkers, props.editing, props.mode, props.placing]);

  return <div className="room-viewer" ref={host} data-testid="room-viewer">
    <div className="room-marker-layer" aria-label="Memories in this room">{markers.map(marker => <button key={marker.id} className={`room-marker${marker.selected ? ' selected' : ''}${marker.approximate ? ' approximate' : ''}`} style={{ left: marker.x, top: marker.y }}
      title={`${marker.title}${marker.approximate ? ' · approximate position; visible through splats' : ''}${marker.review ? ' · needs review' : ''}`} aria-label={`Open memory: ${marker.title}`} onClick={() => latest.current.onSelectMemory(marker.id)}><span className="room-marker-dot"/><span className="room-marker-label">{marker.title}</span></button>)}</div>
    {(status.loading || status.error) && <div className="room-viewer-message" role={status.error ? 'alert' : 'status'}><strong>{status.error ? 'The room is paused' : 'Opening your room'}</strong><p>{status.error ?? (status.progress === null ? 'Reading the local scan…' : `${Math.round(status.progress * 100)}% loaded`)}</p>{status.error && <button onClick={() => setRetry(n => n + 1)}>Restore room</button>}</div>}
    {props.mode !== 'orbit' && !status.loading && !status.error && <button className="room-pointer-lock" title="Optional: lock cursor for mouse look. Escape releases it." onClick={() => { const canvas = host.current?.querySelector('canvas'); canvas?.focus(); void canvas?.requestPointerLock()?.catch(() => {}); }}>Lock cursor · Esc to release</button>}
    {props.placing && !status.loading && <div className="room-placement-hint">Click a surface to place a memory{props.project.room.sceneType === 'splat' ? ' · approximate' : ''}</div>}
    {props.editing && !props.placing && props.onMoveAnchor && props.project.memories.some(memory => memory.id === props.selectedMemoryId && !memory.deletedAt && memory.anchor.placement === 'approximate' && memory.anchor.assetId === props.project.room.scanAssetId && memory.anchor.scanVersion === props.project.room.scanVersion) && <div className="room-placement-hint">Drag an axis to adjust position or depth · approximate</div>}
  </div>;
});
export default RoomViewer;
