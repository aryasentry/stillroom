# Stillroom

A local room-memory app: create a visual 3D room from overlapping photographs or frames extracted from a room video, revisit it, and attach your own notes, photographs, audio and video to places inside it. Advanced import of textured GLB rooms and Gaussian PLY/SPZ scans remains available. Core viewing, editing, search, saving and portable archives work without an internet connection after setup. Optional OpenRouter suggestions send only content you explicitly select.

![Stillroom synthetic demo room](docs/screenshots/room.jpg)

[App screenshot gallery](docs/screenshots/README.md) · [Verification and limitations](docs/ACCURACY_REPORT.md)

The screenshot shows the built-in synthetic demo. Reconstructed rooms depend on capture quality; this is not a claim that every input produces the same fidelity.

## Run locally

Install Python **3.11–3.13** and Node.js **22.12+ or 24 LTS**. Node/npm are needed for setup and frontend development; the built app runs with Python and a WebGL-capable browser. Dependencies install inside this project. Initial setup needs internet access. Local video decoding uses the pinned PyAV 18.1.0 wheel with bundled FFmpeg libraries; a separate FFmpeg installation is not required.

From the project directory on macOS:

```sh
python3 run.py --setup --setup-reconstruction
python3 run.py --check
python3 run.py
```

On Windows, use PowerShell in the project directory:

```powershell
py run.py --setup --setup-reconstruction
py run.py --check
py run.py
```

Open **http://127.0.0.1:8848**. Stop the server with Ctrl+C. Choose another port with `--port 8851`, or another storage directory with `--data-dir /path/to/data`. The server listens only on loopback. Windows commands are provided but have not been executed on Windows; this delivery was tested on macOS with Apple M3, Python 3.13.13 and Node 24.19.0.

Photo reconstruction uses pinned **PyCOLMAP 4.2.1** and **Brush 0.3.0**, installed into this project's `.venv` and `.tools/brush/v0.3.0`. You can also prepare them later with `python3 run.py --setup-reconstruction` (Windows: `py run.py --setup-reconstruction`) or the photo wizard's local-tool preparation action. Initial tool downloads require internet; photographs are processed locally. No Homebrew, CUDA installation, virtual machine, global tool installation, OpenRouter key or paid reconstruction service is required. The installer has native Apple Silicon, Windows x64 and Linux x64 archive paths; only this Apple M3 host has been exercised. Intel macOS has no pinned Brush release in this build. Installed tools alone do not establish GPU compatibility or reconstruction quality.

To use existing scans without preparing reconstruction tools, run `--setup` on its own. `--check` verifies the Python version, required runtime modules, built frontend and SQLite FTS5, and reports reconstruction-tool installation status. It does not reconstruct a scene or test the browser, GPU, all assets or optional AI access. Exact dependencies are recorded in `requirements-lock.txt`, `requirements-reconstruction-lock.txt` and `frontend/package-lock.json`.

## First visit

1. Choose **Create a room from photos**. Name the place, then add JPEG/PNG/WebP photos or use **Or begin with a video** to choose an MP4, MOV or WebM. For video, choose a frame target (120 by default) and **Extract frames from video**. Review the timestamped thumbnails and remove unwanted frames. Photos and video frames can share a collection. Convert HEIC photos to JPEG first.
2. Copy any selected photographs into the local place; extracted video frames are already saved locally. Prepare local tools if prompted, then build the room. Standard quality is the default; Preview gives a faster first look. Keep the local server running; you can return to the library and reopen its progress. If the result says **More overlap needed**, review unmatched photos, add views that bridge the gaps and try again. Three photos is the minimum input, not a promise of a usable room.
3. Open the reconstructed preview, inspect gaps and artifacts, then choose **Use this room**. It starts from a recovered photo viewpoint, with upright orientation estimated from the cameras; **Reset view** returns there. Previewing does not replace an existing saved room. Acceptance opens **Room setup**: review orientation and optionally calibrate it with a known measured distance. Physical scale and floor height remain unknown.
4. Drag to orbit and scroll to zoom. Choose **Fly**, click the room to focus it, and use WASD or arrow keys to move; Q/E move down/up, Shift moves faster. Drag to look. Optional cursor lock releases with Escape.
5. Choose **Edit**, add a memory, and click the scene. Gaussian placement is approximate. Add a title, note, date, tags and attachments. Wait for **Saved locally** before closing.
6. Return in **View** mode. Select a marker or memory card; **Return to this view** restores its saved perspective. Search notes, titles and tags, or filter the memory list.
7. Use **Place options → Export portable .zip** to make a portable copy. Import that ZIP from the library to create a separate place.

Use **Advanced: import a 3D scan → Import GLB, PLY or SPZ**, or open a labelled **SYNTHETIC DEMO** to explore the memory tools without reconstructing photographs.

For video, a steady **30–90 second walk** around the room is a useful starting point. Use bright, constant light, move slowly and keep neighboring views overlapping. Translate the camera along the walls and around furniture; a stationary pan supplies little depth information. Avoid zoom changes, people moving, mirrors and fast turns. Video is easier to capture, but motion blur and compression can reduce reconstruction quality.

Video extraction accepts up to **256 MiB, five minutes and 4K** (at most 4096 pixels per side and 8,847,360 pixels per frame). It selects clearer samples across time, skips almost-identical neighboring views, and outputs JPEGs capped at 1,600 pixels on the long edge. Frame targets are upper bounds; short or repetitive clips yield fewer. Original video bytes are retained with the project, and each derived frame records the source hash and timestamp. Extraction runs as a cancellable local subprocess, survives navigating away, and can be retried from the saved video after a failure or interruption. Review comes before reconstruction, and the saved room changes only when you accept its preview.

The capture guide covers changing position around recognizable details, about 70–80% overlap, consistent lighting and avoiding blur or moving subjects. This is practical advice, not a measured coverage score. Blur/exposure warnings are advisory. The app reports matched capture views and candidate camera groups (which can overlap), uses one selected connected group, and identifies unused photos; it does not invent unseen walls, a floor plan or completed geometry.

Frames from one retained video with matching dimensions share camera calibration. Standard quality uses guided feature matching to recover more correspondences; Preview uses the faster matching path. Avoid lens changes or zoom during a clip. This follows [COLMAP’s shared-intrinsics guidance](https://colmap.github.io/tutorial).

The review shows partial results explicitly, including over the 3D preview. Its percentage measures matched input views, **not the percentage of the physical room reconstructed**. Original source videos remain playable in capture review after accepting a room. Use them for faithful faces and motion: a static Gaussian reconstruction cannot reliably preserve moving people or recover detail absent from the recording.

Before publication, conservative geometric cleanup removes exceptionally large, needle-shaped or distant splats relative to the recovered scene. Thin planar splats are retained. If more than 35% appear suspect, cleanup is withheld and a warning is shown. Raw training exports remain in the local job directory; removal counts are recorded with the generated scene. Cleanup can reduce extreme streaks but cannot guarantee an artifact-free scene.

Reconstruction runs one attempt at a time across the library. Cancel stops the worker process tree and retains originals. A retry creates a new attempt; an app restart marks unfinished attempts interrupted rather than successful. Progress shows real stages and available iteration counts, without a made-up completion time. A completed engine process is insufficient: the final Gaussian PLY must pass validation, and the preview must load before the UI enables acceptance.

Undo/redo is available while editing, including Cmd/Ctrl+Z and Cmd/Ctrl+Shift+Z outside text fields. Removed memories and places can be restored. Saved views, a room entrance and a library cover are optional.

An exported sample is also included at [fixtures/stillroom-demo.zip](fixtures/stillroom-demo.zip), with its synthetic room, one sample memory and a captured library cover. Import it from the library. The source fixtures can be regenerated with `scripts/generate_demo.py`.

For the actual photo-reconstruction result, import [fixtures/stillroom-photo-demo.zip](fixtures/stillroom-photo-demo.zip). It contains 54 synthetic source photographs, the locally reconstructed 30,557-splat scene and one saved memory. Its 19.0 MiB archive was imported into a separate test store with all checksums, source camera, display transform and memory anchor preserved. This is a synthetic capture example, not a physical-room accuracy demonstration.

For the new video path, import [fixtures/stillroom-video-demo.zip](fixtures/stillroom-video-demo.zip): the original synthetic H.264 clip, 54 timestamped extracted frames, and the actual locally reconstructed scene. The 24.7 MiB archive was separately imported with all 110 asset checksums and source provenance preserved.

## Supported files

| Use | Supported subset |
| --- | --- |
| Textured room mesh | Static, uncompressed GLB 2.0 triangle geometry with embedded PNG/JPEG textures and ordinary node transforms |
| Gaussian splats | Binary little-endian Gaussian PLY with XYZ, opacity, log scales, rotation and SH colour; gzip SPZ **version 2** |
| Collision proxy | Static GLB matching the visible room |
| Capture photos and memory photos | Single-frame PNG, JPEG and WebP; convert HEIC to JPEG first |
| Audio | WAV, MP3, Ogg Opus/Vorbis |
| Video capture and attachments | MP4, MOV and WebM; decoding uses PyAV, while attachment playback separately depends on browser codec support |

Plain point-cloud PLY, ASCII PLY, external GLB resources, glTF extensions/compression, animated or skinned meshes, morph targets and other SPZ versions are rejected. File acceptance does not guarantee every media codec plays. Browser playback was exercised with the bundled WAV and VP8 WebM; see the [accuracy and verification report](docs/ACCURACY_REPORT.md) for the tested scope.

Capture collections allow **200 retained originals / 256 MiB combined**, with at least three active photos required to start. Removing a capture from the active collection retains its original bytes, so it still counts toward these limits. Re-adding identical bytes restores the same original. Use another project for a distinct collection beyond the budget. Images remain limited to **40 MiB / 32 megapixels each**.

Other limits are guardrails, not performance promises: 256 MiB per asset; 1 million splats; GLB aggregate scene budgets of 2 million vertices, 1 million triangles and 4,096 draws; 64 levels of node nesting; 16,777,216 aggregate decoded texture pixels. Images are limited to 40 MiB, 32 megapixels and 16,384 pixels per side. Projects allow 1,000 assets including generated thumbnails. Gaussian centres must remain within the renderer’s packed range of ±65,504 source units. Portable ZIPs are limited to 512 MiB, with at most 1 GiB expanded content and a 4 MiB manifest. Smaller scans are strongly preferable on limited GPUs.

## Scale, anchors and walking

In **Room setup**, orient the room, then declare known metre units or pick two points and enter their real measured distance to calibrate uniform scale. Add a **different** measured distance as an independent check. Detailed imagery does not establish accurate dimensions.

Memory positions and saved viewpoints use the original scan coordinate frame, so display translation, rotation and uniform scale keep them attached to the same source location. Replacing a scan marks old anchors for review; matching filenames are insufficient. Gaussian picks and manually corrected anchors are always labelled approximate. [Coordinate details](docs/COORDINATES.md) explain the transforms and accuracy boundaries.

**Walk** requires known scale, a configured floor, collision geometry and clear standing space. Save an entrance inside the room using Fly. GLB geometry supplies collision directly; splats need a separately imported GLB proxy. Adjust the proxy and inspect its wireframe from several angles before checking the visual-alignment box. This is a visual check, not measured registration. Collision setup rejects meshes above 500,000 triangles; use a simplified proxy. Holes can still expose incomplete scan geometry; falling below the floor resets the walker to its validated spawn.

## Local storage, backup and recovery

The default `.stillroom/` directory contains `stillroom.sqlite3`, `assets/<project-id>/` and reconstruction working directories under `reconstructions/`. Jobs persist in SQLite; normalized images, camera estimates, native logs and training checkpoints are separate from managed originals. Checkpoints can consume substantial extra disk space (up to roughly 223 MiB for preview PLYs or 1.3 GiB for standard PLYs at the configured splat caps, before working images and other files). Each attempt has a two-hour limit. Imported originals are copied to managed immutable files, identified by UUID and SHA-256. Photo thumbnails are separate derivatives. Do not edit managed files directly.

SQLite commits project metadata, the search index and a revision snapshot together, retaining the latest 100 committed metadata revisions. Concurrent edits are checked by revision; a conflicting window gets an error instead of silently overwriting another save. **Save draft & reload saved** downloads the unsaved JSON draft before reopening the committed project. That draft is metadata for recovery, not a portable archive containing media.

**Recover previous save** restores the immediately preceding committed metadata snapshot as a new revision. It does not recreate missing files or repair a damaged SQLite database, and it is not a browsable history. Use export before experimenting. Soft deletion keeps originals, so removing memories does not reclaim their disk space.

A portable export contains `manifest.json` and all managed project assets, including retained capture originals, thumbnails, previous scans and reconstruction provenance. It excludes SQLite revision/job history and disposable engine working files. Schema version 2 adds capture membership; version-1 projects and archives migrate without dropping memories, media or room setup. Provenance preserves source identities/hashes, engine versions, registration information and settings. Retained assets can make a project exceed the 512 MiB portable ZIP limit even when each import was individually valid. Import validates paths, formats, sizes and checksums before creating a new project ID. ZIPs contain personal material without encryption: store them as carefully as the originals. Export is refused if files are missing or corrupt. Relink the exact original in Room setup or the attachment inspector; different bytes require replacing the scan/attachment instead.

For a complete local backup, stop Stillroom and copy the **whole data directory**, including any SQLite sidecar files. To restore it, stop the server, retain the existing directory as a backup, and start Stillroom with `--data-dir` pointing to the recovered copy.

## Optional OpenRouter

Open a memory in Edit mode and expand **Optional AI suggestions**. The default is **`inclusionai/ling-3.1-flash`**, as requested. OpenRouter's catalogue lists $0 input and $0 output pricing as of 2026-10-11. Ling accepts **text only**: select the saved note for grounded tag suggestions. It does not support a JSON response-format parameter, so the app requests JSON in the prompt and validates returned content locally.

For photo suggestions, explicitly select `google/gemma-4-31b-it:free` or `openrouter/free`. Changing models clears selected photos; the app never silently switches models. The free router chooses among available free models supporting the request. Every inference request sets provider maximum prices to zero for prompt tokens, completion tokens, requests and images. Paid model IDs are rejected. If free capacity or the zero-price route is unavailable, the app reports an error; it does not retry with paid models.

Enter an OpenRouter API key in the password field, or set `OPENROUTER_API_KEY` in the server environment. `.env.example` documents the variable; `.env` files are not automatically loaded. UI-entered keys stay in server memory and disappear when the server stops. They are not saved in project files, SQLite, browser storage or exports. Old OpenCode keys are never reused for OpenRouter.

The app sends only explicitly selected content to `https://openrouter.ai/api/v1/chat/completions`. With a vision model, at most three photos are resized to 1024×1024 and stripped of source metadata in memory. Room scans, audio, video and unselected attachments are excluded. Provider data handling follows OpenRouter and the selected provider's policies; free pricing is not a zero-retention guarantee.

Review, edit, apply or discard suggestions. Note-only requests never produce a visual caption. AI cannot position anchors or change geometry. OpenRouter is optional memory assistance and is never part of the photo-to-3D engine. No key or network is needed for core features. Inference transport is tested with mocked responses; no live key or successful provider inference has been verified. Free quotas and availability limits apply.

Official references: [Ling model and pricing](https://openrouter.ai/inclusionai/ling-3.1-flash), [provider price caps](https://openrouter.ai/docs/guides/routing/provider-selection#max-price), [usage limits](https://openrouter.ai/docs/api/reference/limits).

## Development and verification

```sh
.venv/bin/python -m pytest backend/tests -q
npm test --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`. Browser tests require Playwright Chromium installed locally for the user: from `frontend/`, run `npx playwright install chromium`. Alternatively set `STILLROOM_BROWSER_CHANNEL=chrome` to use an installed Chrome. The browser suite starts an isolated server on port 8850 and writes temporary data under `test-results/`. Set `STILLROOM_TEST_NATIVE_RECONSTRUCTION=1` to include the full 54-photo GPU reconstruction; this intentionally takes minutes. Set `STILLROOM_TEST_NATIVE_VIDEO=1` for the full encoded-video → extracted frames → native reconstruction → preview/acceptance test. The ordinary suite skips both long GPU tests. The native insufficient-overlap test also skips when the optional tools are absent.

The latest checks passed **232 backend tests, 37 frontend unit tests and 17 ordinary browser tests**, plus the production build and launcher preflight. The two long synthetic GPU browser tests are opt-in and retain their earlier successful evidence. The final ordinary suite includes partial-result review, accepted-room source-video playback and the 120-frame default. A separate private-room native quality trial is described in the accuracy report. Video extraction also previously passed with OS-level network access denied. Matched views and visual inspection do not establish physical measurement accuracy.

The React/TypeScript viewer uses Three.js and Spark; the Python/FastAPI backend owns validation, SQLite and files. Production scripts, styles, fonts and splat runtime resources are bundled locally. Browser acceptance tests block non-loopback HTTP(S) requests from the app; this is not an OS firewall test and does not inspect unrelated browser background traffic.

See [verification evidence and limits](docs/ACCURACY_REPORT.md), [coordinate contract](docs/COORDINATES.md), [implementation contract](docs/DESIGN.md) and [fixture provenance](fixtures/README.md). Photo capture guidance, local reconstruction jobs, isolated preview and explicit acceptance are implemented and a synthetic native reconstruction was visually inspected. Private real-room recordings have also been inspected; dimensional accuracy still requires independent measurements. Local video-to-frames reconstruction is implemented. Generated missing geometry and guided tours are not implemented.

### Experimental sparse-frame reconstruction

Choose **Sparse · estimated cameras (experimental)** under Reconstruction quality, then **Prepare sparse-frame model** once. This downloads [Depth Anything 3 Base](https://huggingface.co/depth-anything/DA3-BASE) (517 MiB) and its separate PyTorch environment. Standard remains the default and does not need this model. Optional command-line setup, from the repository root: `.venv/bin/python -m scripts.setup_depth` (Windows: `.venv\Scripts\python.exe -m scripts.setup_depth`).

Sparse mode processes at most **32 originals**, spread through the capture; frames from one video favor sharper images within each interval. Every original remains saved. The model initializes camera poses, verified feature matches triangulate points, and bundle adjustment refines the cameras before normal Gaussian training. Views with fewer than 30 supported observations and points above 4-pixel reprojection error are excluded. The matched-view percentage refers only to the selected inputs.

Source and model versions are fixed and SHA-256 checked. Inference runs locally (Apple Metal, CUDA when available, otherwise CPU). Native verification so far covers Apple silicon macOS; the optional runtime has not been executed on Windows or Linux. See the [accuracy report](docs/ACCURACY_REPORT.md) for measured evidence and limits.

This is **camera assistance, not unseen-world generation**. It can help small capture sets, but it cannot make arbitrary blurry footage faithful, restore a moving person's true face, or establish the shape of an unseen wall. Generated completion is not silently mixed into the captured room.
