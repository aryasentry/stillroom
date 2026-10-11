# Synthetic fixtures

These fixtures were procedurally authored for Stillroom, not reconstructed from a person's room. Source generator: `scripts/generate_demo.py`. All original fixture geometry, textures, image and audio are dedicated to the public domain under **CC0-1.0**. No third-party scan is bundled.

`stillroom-demo.zip` is a portable export of the synthetic mesh example with one sample memory and a captured cover. Its creation timestamps and UUIDs describe the exported project, not a historical memory or physical capture. The geometry and media have the same CC0 dedication as the source fixtures.

Run `.venv/bin/python scripts/generate_demo.py` (Windows: `.venv\Scripts\python scripts\generate_demo.py`). The room is 6 × 5 metres, floor Y=0, back Z=-2.5, Y up. Modelled dimensions test software coordinate handling; they do not establish real scan accuracy. Geometry is static uncompressed GLB2 with embedded PNG textures. Two front/top boundaries are deliberately open for inspection. Collision holes are separately exercised by controller tests.

`synthetic-room.ply` is binary little-endian Gaussian data with XYZ, DC colour, opacity, log scale and quaternion attributes. `synthetic-room.spz` is SPZ v2 containing the same sampled surface centres. These are rendering/coordinate fixtures, not real Gaussian reconstructions and not exact collision surfaces. `unsupported-pointcloud.ply` intentionally lacks Gaussian attributes and must be rejected.

`memory-photo.png` is a labelled synthetic illustration; `memory-audio.wav` is a quiet three-second synthetic chime. Neither represents a real memory. `measurements.json` records independent known fixture distances and anchors.

## Photo reconstruction fixtures

`capture-room/images/` contains 54 CC0 rendered JPEGs at 960×720 from translated viewpoints around the synthetic room fragment. `scripts/generate_capture_fixture.mjs` creates them using the local Three.js renderer; run `STILLROOM_BROWSER_CHANNEL=chrome node scripts/generate_capture_fixture.mjs` after frontend dependencies are installed. The scene deliberately has rich fixed surface details and stable lighting, so this is favourable synthetic input rather than evidence about a physical room. Pixel hashes may differ with graphics hardware.

`capture-room/ground-truth.json` records generating cameras for post-run analysis. The reconstruction worker receives only the JPEG originals and their identities/hashes; it is never given this ground truth or the source GLB. `capture-insufficient/` contains three uniform synthetic PNGs for native insufficient-overlap and recovery tests.

The first completed 54-photo native run produced 31,184 Gaussian splats. Its validated hash, profile, elapsed time and OS-level offline observation are recorded in `docs/evidence/photo-native-verification.json`; derived camera comparisons are in `docs/evidence/photo-camera-accuracy.json`. These distinguish reconstructed output from the directly generated PLY/SPZ rendering fixtures above.

`memory-video.webm` is a two-second, 320×180 synthetic VP8 playback fixture, also CC0-1.0. Recreate its content with `node scripts/generate_video_fixture.mjs` after installing the frontend dependencies and a Playwright browser (or set `STILLROOM_BROWSER_CHANNEL=chrome` for installed Chrome). MediaRecorder container timestamps can vary; its visual content is reproducible. This script records a generated canvas, never a camera or microphone.

`stillroom-photo-demo.zip` contains the later browser-driven native reconstruction: 54 original synthetic photographs, thumbnails, a validated 30,557-splat PLY, recovered-camera provenance and one saved memory. It was exported after acceptance and imported into a separate temporary store with all asset hashes and source-space anchors preserved. See `docs/evidence/photo-browser-verification.json` and `docs/evidence/photo-archive-verification.json`. Its geometry is learned from the rendered photographs, not copied from the fixture GLB. The source images and resulting synthetic reconstruction share the CC0 dedication.

## Encoded room capture video

`capture-room-video.mp4` is a CC0 synthetic H.264 video encoded from the same 54 rendered camera views, holding each for six frames at 12 fps (27 seconds, 960×720). Recreate it with `.venv/bin/python scripts/generate_capture_video.py`. It is an encoded camera sequence, not a physical room recording or evidence about handheld blur/rolling shutter. No generator geometry or camera truth is supplied to frame extraction or reconstruction. A target of 80 yields 54 distinct frames with the current decoder, after skipping repeated views.

`stillroom-video-demo.zip` contains the source clip, 54 extracted timestamped captures and the accepted native 31,087-splat reconstruction. All 110 assets were independently imported and checksum-verified. The 25,904,004-byte archive preserves video/frame/scan provenance and the recovered initial view. It contains no fabricated success output or supplied camera poses.
