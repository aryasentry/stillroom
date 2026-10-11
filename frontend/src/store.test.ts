import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import type {Asset, Project} from './types';

const api = vi.hoisted(() => ({saveProject: vi.fn(), uploadAsset: vi.fn(), getProject: vi.fn()}));
vi.mock('./api', () => ({api}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return {promise, resolve};
}
function project(id = 'room'): Project {
  return {schemaVersion: 1, id, title: 'Original', revision: 0, createdAt: 'initial', updatedAt: 'initial', assets: [], memories: [], viewpoints: [], thumbnailAssetId: null, deletedAt: null,
    settings: {showMarkers: true, navigationSpeed: 2, quality: 'balanced'},
    room: {scanAssetId: null, scanVersion: null, sceneType: null, transform: {position: [0, 0, 0], rotation: [0, 0, 0], scale: 1}, sourceUnits: 'unknown', floorY: null, entrance: null, calibration: null, distanceChecks: [], collisionAssetId: null, collisionTransform: {position: [0, 0, 0], rotation: [0, 0, 0], scale: 1}, collisionAligned: false, visualQuality: '', alignmentNotes: '', synthetic: false}};
}
function asset(id = 'uploaded'): Asset {
  return {id, originalName: `${id}.png`, role: 'thumbnail', kind: 'image', mimeType: 'image/png', size: 10, sha256: '0'.repeat(64), path: `${id}.png`, createdAt: 'uploaded', metadata: {}};
}

beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks(); vi.useFakeTimers();
  const drafts = new Map<string, string>();
  vi.stubGlobal('localStorage', {getItem: (key: string) => drafts.get(key) ?? null, setItem: (key: string, value: string) => drafts.set(key, value), removeItem: (key: string) => drafts.delete(key)});
});
afterEach(() => {vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();});

describe('project persistence ordering', () => {
  it('keeps edits and an uploaded association when autosave runs during an upload', async () => {
    const {useProjectStore: store} = await import('./store');
    let disk = project(); const added = asset();
    const uploadGate = deferred<void>(); const saveGate = deferred<void>();
    api.uploadAsset.mockImplementation(async () => {await uploadGate.promise; disk = {...disk, assets: [...disk.assets, added], revision: disk.revision + 1, updatedAt: 'upload'}; return added;});
    api.getProject.mockImplementation(async () => structuredClone(disk));
    api.saveProject.mockImplementation(async (draft: Project, revision: number) => {
      if (revision !== disk.revision) throw new Error('409 revision conflict');
      disk = {...structuredClone(draft), revision: revision + 1, updatedAt: 'save'};
      const response = structuredClone(disk); await saveGate.promise; return response;
    });
    store.getState().open(structuredClone(disk));
    const uploading = store.getState().upload(new File(['image'], 'cover.png'), 'thumbnail');
    await vi.advanceTimersByTimeAsync(0);
    store.getState().update(p => {p.title = 'Edited during upload';});
    const saving = store.getState().flush().catch(error => error as Error);
    await vi.advanceTimersByTimeAsync(0);
    uploadGate.resolve(); await uploading;
    store.getState().update(p => {p.thumbnailAssetId = added.id;});
    saveGate.resolve();
    const saveError = await saving;
    let finalError: unknown;
    try {await store.getState().flush();} catch (error) {finalError = error;}
    expect(saveError).toBeUndefined(); expect(finalError).toBeUndefined();
    expect(disk.title).toBe('Edited during upload'); expect(disk.thumbnailAssetId).toBe(added.id);
    expect(store.getState().project).toEqual(disk); expect(store.getState().saveStatus).toBe('saved');
  });

  it('retains both assets when overlapping uploads receive metadata responses out of order', async () => {
    const {useProjectStore: store} = await import('./store');
    let disk = project(), reads = 0; const firstRead = deferred<void>();
    api.uploadAsset.mockImplementation(async (_id: string, file: File) => {
      const added = asset(file.name); disk = {...disk, assets: [...disk.assets, added], revision: disk.revision + 1}; return added;
    });
    api.getProject.mockImplementation(async () => {
      const response = structuredClone(disk); if (++reads === 1) await firstRead.promise; return response;
    });
    store.getState().open(structuredClone(disk));
    const one = store.getState().upload(new File(['one'], 'one'), 'thumbnail');
    const two = store.getState().upload(new File(['two'], 'two'), 'thumbnail');
    await vi.advanceTimersByTimeAsync(0); firstRead.resolve(); await Promise.all([one, two]);
    expect(store.getState().project?.assets.map(a => a.id)).toEqual(['one', 'two']);
    expect(store.getState().project).toEqual(disk);
  });

  it('keeps a new room intact if an old room upload finishes after switching', async () => {
    const {useProjectStore: store} = await import('./store');
    const original = project(), next = project('other'), added = asset(), gate = deferred<void>();
    api.uploadAsset.mockImplementation(async () => {await gate.promise; return added;});
    api.getProject.mockResolvedValue({...original, assets: [added], revision: 1});
    api.saveProject.mockImplementation(async (draft: Project) => ({...draft, revision: draft.revision + 1}));
    store.getState().open(original);
    const uploading = store.getState().upload(new File(['image'], 'cover.png'), 'thumbnail').catch(error => error as Error);
    await vi.advanceTimersByTimeAsync(0); store.getState().open(next); gate.resolve();
    expect(await uploading).toBeInstanceOf(Error);
    expect(store.getState().project).toEqual(next);
    store.getState().update(p => {p.title = 'New room still saves';}); await store.getState().flush();
    expect(store.getState().project?.title).toBe('New room still saves');
    expect(store.getState().project?.id).toBe('other'); expect(store.getState().saveStatus).toBe('saved');
  });

  it('saves pending edits after an upload fails without leaving the request queue blocked', async () => {
    const {useProjectStore: store} = await import('./store');
    let disk = project(); const gate = deferred<void>();
    api.uploadAsset.mockImplementation(async () => {await gate.promise; throw new Error('Import cancelled');});
    api.saveProject.mockImplementation(async (draft: Project, revision: number) => {
      if (revision !== disk.revision) throw new Error('409 revision conflict');
      disk = {...structuredClone(draft), revision: revision + 1}; return structuredClone(disk);
    });
    store.getState().open(structuredClone(disk));
    const uploading = store.getState().upload(new File(['image'], 'cover.png'), 'thumbnail').catch(error => error as Error);
    await vi.advanceTimersByTimeAsync(0); store.getState().update(p => {p.title = 'Keep this edit';});
    const saving = store.getState().flush(); gate.resolve();
    expect(await uploading).toBeInstanceOf(Error); await saving;
    expect(disk.title).toBe('Keep this edit'); expect(store.getState().project).toEqual(disk);
    expect(store.getState().saveStatus).toBe('saved');
  });

  it('preserves a concurrent window save instead of silently rebasing local edits over it', async () => {
    const {useProjectStore: store} = await import('./store');
    let disk = project(); const gate = deferred<void>(), added = asset();
    api.uploadAsset.mockImplementation(async () => {await gate.promise; disk = {...disk, assets: [added], revision: disk.revision + 1}; return added;});
    api.getProject.mockImplementation(async () => structuredClone(disk));
    api.saveProject.mockImplementation(async (draft: Project, revision: number) => {
      if (revision !== disk.revision) throw new Error('409 revision conflict');
      disk = {...structuredClone(draft), revision: revision + 1}; return structuredClone(disk);
    });
    store.getState().open(structuredClone(disk));
    const uploading = store.getState().upload(new File(['image'], 'cover.png'), 'thumbnail').catch(error => error as Error);
    await vi.advanceTimersByTimeAsync(0);
    store.getState().update(p => {p.title = 'My pending title';});
    disk = {...disk, title: 'Committed in the other window', revision: 1};
    gate.resolve(); const result = await uploading;
    const saveResult = await store.getState().flush().catch(error => error as Error);
    expect(result).toBeInstanceOf(Error); expect(saveResult).toBeInstanceOf(Error);
    expect(disk.title).toBe('Committed in the other window'); expect(disk.assets[0].id).toBe(added.id);
    expect(store.getState().project?.title).toBe('My pending title');
    expect(store.getState().saveStatus).toBe('error');
  });
});
