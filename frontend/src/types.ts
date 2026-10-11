export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];
export type NavigationMode = 'orbit' | 'fly' | 'walk';
export interface Transform { position: Vec3; rotation: Vec3; scale: number }
export interface Asset {
  id: string; originalName: string; role: 'scan' | 'collision' | 'media' | 'thumbnail' | 'capture';
  kind: 'glb' | 'ply' | 'spz' | 'image' | 'audio' | 'video'; mimeType: string;
  size: number; sha256: string; path: string; createdAt: string;
  metadata: Record<string, unknown>; thumbnailId?: string | null;
}
export interface Viewpoint { id: string; name: string; position: Vec3; quaternion: Quat; target?: Vec3 }
export interface Anchor {
  position: Vec3; normal?: Vec3; assetId: string; scanVersion: string;
  placement: 'surface' | 'approximate'; nodePath?: string; triangleIndex?: number;
  needsReview: boolean;
}
export interface Memory {
  id: string; title: string; note: string; date: string | null; tags: string[];
  anchor: Anchor; mediaIds: string[]; viewpoint: Viewpoint | null;
  createdAt: string; updatedAt: string; deletedAt: string | null;
}
export interface Calibration { id: string; a: Vec3; b: Vec3; knownDistance: number; method: 'two-point'; createdAt: string }
export interface DistanceCheck { id: string; a: Vec3; b: Vec3; knownDistance: number; label: string }
export interface Room {
  scanAssetId: string | null; scanVersion: string | null; sceneType: 'mesh' | 'splat' | null;
  transform: Transform; sourceUnits: 'unknown' | 'metres'; floorY: number | null;
  entrance: Viewpoint | null; calibration: Calibration | null; distanceChecks: DistanceCheck[];
  collisionAssetId: string | null; collisionTransform: Transform; collisionAligned: boolean;
  visualQuality: string; alignmentNotes: string; synthetic: boolean;
}
export interface Project {
  schemaVersion: 1 | 2; captureAssetIds?: string[]; id: string; title: string; createdAt: string; updatedAt: string;
  revision: number; assets: Asset[]; room: Room; memories: Memory[]; viewpoints: Viewpoint[];
  settings: { showMarkers: boolean; navigationSpeed: number; quality: 'balanced' | 'high' };
  thumbnailAssetId: string | null; deletedAt: string | null;
}
export interface ProjectSummary { id: string; title: string; updatedAt: string; createdAt: string; memoryCount: number; sceneType: 'mesh'|'splat'|null; synthetic: boolean; thumbnailAssetId: string|null; deletedAt: string|null }
export interface PickResult { anchor: Anchor; viewpoint: Viewpoint }
export interface ViewerDiagnostics { browser: string; gpu: string; width: number; height: number; pixelRatio: number; loadMs: number; frameTimeMedianMs: number; frameTimeP95Ms: number; frames: number; triangles: number; splats: number; geometries: number; textures: number }
export interface ViewerStatus { loading: boolean; progress: number | null; error: string | null; canWalk: boolean; message: string; diagnostics?: ViewerDiagnostics }
export interface ViewerHandle {
  captureView(): Viewpoint; goToView(view: Viewpoint): void; returnToEntrance(): void;
  captureThumbnail(): Promise<Blob|null>; getDiagnostics(): ViewerDiagnostics|null;
}
export interface ViewerProps {
  project: Project; mode: NavigationMode; editing: boolean; placing: boolean; showMarkers: boolean;
  selectedMemoryId: string | null; speed?: number;
  onPick(result: PickResult): void; onSelectMemory(id: string): void; onStatus(status: ViewerStatus): void;
  onViewChange?(view: Viewpoint): void;
  onMoveAnchor?(id: string, position: Vec3): void;
}

export type ReconstructionStatus = 'queued' | 'running' | 'needs_photos' | 'ready' | 'accepted' | 'cancelled' | 'failed' | 'interrupted';
export interface ReconstructionJob {
  id: string; projectId: string; status: ReconstructionStatus;
  stage: 'preparing' | 'matching' | 'training' | 'validating' | 'complete'; message: string;
  createdAt: string; updatedAt: string; quality: 'preview' | 'standard' | 'assisted'; sourceAssetIds: string[];
  totalPhotos: number; registeredPhotos: number; unregisteredAssetIds: string[]; components: number[];
  resultAssetId: string | null; warnings: string[]; error: string | null;
  iteration: number | null; totalIterations: number | null;
}
export interface ReconstructionRuntime {
  available: boolean; installing: boolean; supported: boolean; platform: string;
  message: string; error: string | null; versions: {pycolmap?: string | null; brush?: string | null; model?:string; revision?:string};
}

export interface VideoExtractionJob {
  id:string; projectId:string; sourceAssetId:string;
  status:'queued'|'running'|'ready'|'failed'|'cancelled'|'interrupted';
  message:string; createdAt:string; updatedAt:string; frameAssetIds:string[];
  frameCount:number; warnings:string[]; error:string|null;
}
