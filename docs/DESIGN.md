# Stillroom — implementation contract

Local application for building a visual room from overlapping photographs, revisiting it and attaching user-authored memories. The primary flow is photos → capture guidance/review → local reconstruction job → isolated preview → explicit acceptance → room setup and memories. Advanced scan import, navigation, stable anchors, save/reopen and portable projects remain available. Core operations require no external inference, account, remote upload or runtime CDN after setup. Optional OpenRouter suggestions assist memories only; they do not reconstruct or complete geometry.

## Coordinates (one integration owner)

The display frame is right-handed, Y up, with cameras looking along local -Z. A reconstructed source frame has unknown physical upright direction and metric scale. A camera-derived display rotation supplies an initial visual-up estimate for review. A source point is in the GLB scene-root / original splat coordinate frame, after glTF node transforms, before the room display transform. All anchors and saved viewpoints are persisted in this source frame. `room.transform` applies uniform scale, Euler XYZ rotation in radians, then translation. Never change source vertices. Distances are metres only after calibration or explicit source-unit declaration. `floorY` is a display/world-space plane used to validate navigation. Camera orientation is stored as source-space quaternion XYZW.

Anchor IDs are UUIDs, associated with a stable asset ID AND SHA256 `scanVersion`; optional nodePath, normal and triangleIndex are diagnostic associations, never sole identities. Replacing the scan sets old anchors `needsReview=true`. Changed display transforms keep source coordinates fixed. Numeric/manual corrections become `approximate` unless repicked against geometry.

## Stored schema

Authoritative TypeScript contract: `frontend/src/types.ts`. New projects and exports use schema version 2: `captureAssetIds` identifies the active collection and image originals may have role `capture`. Version-1 projects/archives migrate with an empty capture list while preserving existing assets, memories, transforms and calibration. JSON field names match backend exactly. SQLite stores complete validated project JSON, revision, prior committed versions, and FTS5 memory index in one transaction. Asset upload is separately durable and returns immutable metadata; metadata autosave then associates it. Project PUT requires expectedRevision; conflict is HTTP 409, never silent overwrite. Server-managed identity, asset paths/hashes, capture membership and timestamps cannot be modified through PUT.

Managed assets have relative internal paths and are served by project + asset ID. Originals are immutable copies. Thumbnails are derivatives. Identical capture hashes reuse the managed original; removing a capture only removes active collection membership. Retained capture originals still count against the 200-image / 256 MiB project capture budget. Soft deletion preserves records and files. ZIP export includes the schema-2 manifest, all managed assets and validated reconstruction provenance (source IDs/hashes, engine versions, quality/settings and registered sources), excluding job/revision history and engine working files; import rejects traversal, duplicates, symlinks, bad checksums and excessive sizes before committing.

## API

- GET `/api/health` → `{status, schemaVersion}`
- GET `/api/projects` → `ProjectSummary[]`
- POST `/api/projects` JSON `{title}` → `Project`
- GET `/api/projects/{id}` → `Project`
- PUT `/api/projects/{id}` JSON `{expectedRevision, project}` → `Project` (revision +1)
- DELETE `/api/projects/{id}` soft-delete; POST `/api/projects/{id}/restore`
- POST `/api/projects/{id}/assets` multipart `file`, `role` = `scan|collision|media|thumbnail|capture` → `Asset`; upload adds asset to server project, increments revision. Client MUST reload before applying scan/media association.
- DELETE `/api/projects/{id}/captures/{assetId}` → updated `Project`; remove active membership, retain immutable bytes and job snapshots
- GET `/api/projects/{id}/assets/{assetId}` → local file with appropriate MIME/range support
- POST `/api/projects/{id}/assets/{assetId}/relink` multipart `file` → `Asset` (identical hash required; reload after)
- GET `/api/projects/{id}/search?q=...` → `Memory[]` (title/note/tags)
- GET `/api/projects/{id}/export` → ZIP download
- POST `/api/import` multipart `file` → `Project` under NEW project ID, asset IDs preserved
- POST `/api/demo?kind=mesh|ply|spz` → new synthetic `Project` from `fixtures/`
- GET `/api/projects/{id}/integrity` → `{missing: string[], corrupt: string[]}` (asset IDs)
- POST `/api/projects/{id}/recover` → restore previous transactional metadata revision as new revision
- GET `/api/ai/status` → local configuration and supported model choices, without provider access
- POST `/api/ai/configure` → set the process-memory OpenRouter key; DELETE forgets that session key
- POST `/api/projects/{id}/ai/suggest` with `memoryId` → explicitly selected photos and optional note sent to the fixed OpenRouter Chat Completions endpoint, returning editable suggestions

### Reconstruction API

- GET `/api/reconstruction/runtime` → `{available, installing, supported, platform, message, error, versions:{pycolmap,brush}}`; status never downloads tools
- POST `/api/reconstruction/runtime/install` → runtime status; start asynchronous pinned local setup
- GET `/api/reconstructions` → newest-first jobs for non-archived projects, for library progress
- GET `/api/projects/{id}/reconstructions` → newest-first project jobs
- POST `/api/projects/{id}/reconstructions` JSON `{imageIds, quality:'preview'|'standard'}` → new job; require installed runtime and 3–200 unique active capture IDs owned by this project
- GET `/api/projects/{id}/reconstructions/{jobId}` → job
- POST `/api/projects/{id}/reconstructions/{jobId}/cancel` → job; stop worker tree and retain originals
- POST `/api/projects/{id}/reconstructions/{jobId}/accept` JSON `{expectedRevision}` → updated project; stale revision is HTTP 409

A job contains identity/timestamps, `status`, `stage`, `message`, `quality`, source asset IDs, total/registered photo counts, unused asset IDs, component sizes, optional result asset ID, warnings/error and optional iteration/total counters. Statuses are `queued`, `running`, `needs_photos`, `ready`, `accepted`, `cancelled`, `failed`, `interrupted`. Stages are `preparing`, `matching`, `training`, `validating`, `complete`. Progress comes from real bounded counters; there is no synthetic percentage or estimated completion time.

## Local reconstruction contract

PyCOLMAP 4.2.1 and Brush 0.3.0 are pinned in repository-local setup. Native archives and Python packages are hash checked. Setup is explicit through the wizard or `run.py --setup-reconstruction`; normal status checks never download. Platform archive availability is separate from runtime verification: installation and native execution have been exercised on Apple M3 only. Positive native finished-output/quality verification is recorded separately in `ACCURACY_REPORT.md`.

Capture inputs are JPEG, PNG or WebP, at most 40 MiB / 32 MP each. HEIC receives a JPEG-conversion instruction. A video can instead supply derived JPEGs through the local extraction flow. Preserve originals byte-for-byte and prepare EXIF-oriented working JPEGs capped at 1,600 pixels. Blur/exposure/similarity warnings are advice, not a coverage certificate. Estimate camera components, report unused sources, and train only the selected component's undistorted images/cameras in a clean dataset. Fewer than three registered cameras or no triangulated points yields `needs_photos`; preserve the collection and show guidance for overlapping views.

Video extraction accepts retained MP4/MOV/WebM media assets (256 MiB, five minutes, at most 4K). A separate durable SQLite queue permits one active video extraction and supervises a PyAV 18.1.0 subprocess with a ten-minute ceiling and parent-death watchdog. Decode timestamps, not nominal FPS, drive temporal sampling. Within each interval, choose the highest edge-detail score among samples; remove near-identical consecutive small grayscale views. This is a heuristic, not an overlap or sharpness guarantee. Apply display-matrix rotation and cap output long edge at 1,600 pixels. Unknown-duration videos use bounded one-second candidate intervals before even selection. Decode is capped at 36,000 frames and 200 MiB staged JPEGs. Frame batches and thumbnails commit atomically against the retained capture budget. Each frame preserves validated source-video ID, SHA-256, timestamp and score through portable archives. Cancel/failure preserves the original; startup marks unfinished attempts interrupted and removes abandoned frame staging. The UI supports reopening, progress, cancel, retry, frame removal and explicit reconstruction/preview.

A persisted SQLite manager permits one queued/running job globally and supervises `python -m backend.reconstruction_worker JOB_DIRECTORY` outside request execution. A lifetime OS lock gives one server exclusive ownership of each data root before interruption recovery; a duplicate server cannot interrupt another instance's live job. Managed job-local input references owned asset IDs, paths and hashes. The worker checks path isolation and originals before computation, uses argument arrays with fixed tools, and emits JSON lines bounded to 64 KiB. Native logs and supervisor logs are capped; each attempt has a two-hour ceiling. Cancellation stops descendants, retries use a fresh job directory, and unfinished jobs become `interrupted` after application restart. A Windows process handle detects parent death even when the recorded PID stays unchanged. Keep the local server running when leaving the flow for the library.

Brush profiles are preview (3,000 steps / 960 resolution / 300,000 splats) and standard (7,000 / 1,280 / 750,000), with export interval 1,000 and LPIPS loss zero. Successful process exit alone cannot publish `ready`: the expected final PLY must exist inside the job directory and pass the scan validator; the manager independently validates it and associates a managed asset with portable provenance. Engine working data/checkpoints are separate from source originals and excluded from portable ZIPs.

A ready result loads into an isolated temporary project through `RoomViewer`, with editing/markers disabled. The existing saved room remains unchanged until explicit acceptance. The UI enables acceptance only after the preview loads; a stale project revision requires refresh and another review. Acceptance rechecks result bytes and transactionally replaces the room, resets its transform/scale/calibration/checks/floor/entrance/collision setup, clears saved room and memory viewpoints, and marks old anchors for review. Old scans and capture originals remain managed assets. Reconstruction establishes neither metric scale nor world up, a floor plan, collision geometry or reliable occlusion. Orbit/fly and approximate Gaussian anchors are available; Walk needs separately aligned geometry and the usual scale/floor/spawn checks.

All API unsafe methods require `X-Stillroom-Request: local` and an exact allowed loopback Origin when supplied. Restrict Host to loopback. No wildcard CORS. Production backend serves frontend/dist at one address. HTTP errors JSON `{detail: string}`. Default `127.0.0.1:8848`.

## Viewer component contract

`RoomViewer` forwardRef exposes `captureView(): Viewpoint`, `goToView(view)`, `returnToEntrance()`, `captureThumbnail(): Promise<Blob|null>`, `getDiagnostics(): ViewerDiagnostics|null`.

Props: `project`, `mode: orbit|fly|walk`, `editing`, `placing`, `showMarkers`, `selectedMemoryId`, `onPick(PickResult)`, `onSelectMemory(id)`, `onStatus(ViewerStatus)`, optional `onViewChange(view)`, `onMoveAnchor(id, position)` and `speed`. The viewer owns Three.js, clicks, GPU lifecycle, navigation and marker screen projection. Source-space picks and status are passed to UI. Root owns types/store/API, fixtures, setup panel and integration. UI owns App, library, memory panels, toolbar, CSS. Shared schema changes remain coordinated through these contracts.

## Visual system

Room is the main screen, with a warm charcoal frame, quiet ivory panels, muted terracotta accent, serif room titles, system sans UI, compact 52px topbar and slim bottom navigation strip. Collapsible 300px memory rail, 340px edit/setup inspector. No marketing dashboard or fake measurements. Synthetic room is recognizably modelled geometry and labelled as a fixture. Palette: #efede7, #fcfbf8, #262b29, #737872, #ba7455. Local fonts only. On narrow screens panels overlay, with a clear close control.

## Accepted implementation decisions

Keep advanced import of self-contained static uncompressed GLB with embedded PNG/JPEG textures; reject compressed/animated/skinned variants until tested. Start splats with binary little-endian Gaussian PLY and SPZ v2 fixture. Limit scan bytes and splat count, provide honest approximate picking and source-unit labels. Walk is gated on mesh geometry, configured floor and validated spawn; capsule/triangle collision, bounded substeps and reset after holes. Optional proxy registration uses explicit transform and user check, with no invented alignment confidence. Optional OpenRouter suggestions are implemented; live provider access remains unverified. Guided tours are deferred. No external image generation is used; actual synthetic geometry provides the visual scene.
