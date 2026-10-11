# Coordinate and anchor contract

The authoritative fields are in `frontend/src/types.ts`, validated by `backend/models.py`. Coordinate operations live in `frontend/src/coordinates.ts` and `frontend/src/viewer/viewpoint.ts`.

## Frames and transform order

Stillroom uses a right-handed **display frame with Y up**. Photo reconstruction produces a source frame with unknown metric scale and physical upright direction. The initial display rotation estimates upright from recovered camera orientations; review it in Room setup before treating the axes as physical directions. A camera looks along its local **−Z** direction. Source positions belong to the imported GLB scene-root frame, after each node's own glTF transforms, or to the original Gaussian splat frame. Imported or reconstructed source vertices are never rewritten to accommodate display setup. The locally generated Gaussian PLY is the reconstructed source representation; the app does not derive a floor plan or infer missing walls from it.

The room display transform is:

```text
worldPoint = translation + rotationXYZ × (uniformScale × sourcePoint)
sourcePoint = inverse(roomMatrix) × worldPoint
```

`room.transform.position` is translation, `rotation` is Euler XYZ in radians, and `scale` is a positive scalar. The UI displays rotation in degrees. Source coordinates remain unchanged when these fields change. `floorY` is the exception: it is a **world/display-space** floor height. Display-transform changes clear the floor and proxy-alignment acknowledgement; manual scale changes also clear calibration and the metre-unit declaration.

A collision proxy has its own transform inside the room source frame:

```text
proxyWorldPoint = roomMatrix × collisionTransform × proxyLocalPoint
```

Its translation therefore uses source units, before room scale. There is no automatic registration or alignment-confidence score.

## Stable memories and views

A memory has a UUID. Its anchor stores a source-space position, immutable asset ID and `scanVersion` equal to that asset's SHA-256. Optional normal, node path and triangle index describe the original pick; they are not the persistent identity. Screen markers project these source positions through the current room transform.

A GLB raycast pick is labelled `surface`. A Gaussian pick uses Spark's raycast and is labelled `approximate`: a Gaussian is an extended, partly transparent volume, not a measured solid surface. Numeric coordinate edits and translation-gizmo edits remain approximate. A fresh mesh pick can restore surface placement. This label describes placement against the imported representation, not agreement with the physical room. Splat markers show through surfaces; they are not reliable evidence of occlusion or depth ordering.

Saved camera position and orbit target are in source coordinates. Camera orientation is a normalized quaternion in **XYZW** order, relative to the room root. Restoring a view applies the current room transform to both its position and orientation.

Accepting a new reconstruction or replacing an imported scan changes the asset association, marks previous anchors `needsReview`, clears memory viewpoints and room saved views, and resets calibration, independent checks, the old entrance, floor and collision association. For a reconstruction, the current room remains intact during processing and isolated preview. Explicit acceptance checks the expected project revision, sets the recovered source-space camera as a new entrance, applies only the estimated display rotation, and preserves previous scan files. Scale becomes unknown and the transform scale returns to one. Old anchor positions/scan identities remain available for review; they are not silently transferred to changed geometry. Repick them, or explicitly confirm a manually adjusted approximate location. Relinking a missing asset is different: only identical bytes with the original SHA-256 are accepted, retaining its identity.

The recovered camera is chosen near the median registered camera position. COLMAP cameras use right/down/forward axes; their inverse camera pose is multiplied by `diag(1,-1,-1)` to form a Three.js camera with right/up/back axes. Its position and quaternion stay in source coordinates. A separate Euler XYZ display rotation aligns the average recovered camera-up direction with display +Y; it does not establish gravity or metric scale. Both values are validated and stored in reconstruction provenance so preview, acceptance and portable import agree.

## Calibration and independent checks

For picked source points A and B and a measured physical distance D in metres:

```text
scale = D / length(B − A)
measuredDistance = length(B − A) × scale
absoluteError = abs(measuredDistance − referenceDistance)
relativeError = absoluteError / referenceDistance
```

The stored calibration retains A, B, D, a UUID and timestamp. It changes uniform scale only; it cannot remove lens distortion, scan drift, locally stretched geometry or bad point selection. Declaring original units as metres records a user assertion and is displayed separately from two-point calibration.

The calibration pair is not independent evidence. Add checks on distinct measured features; the UI rejects the identical calibration pair in either order. Record multiple directions and locations to reveal local drift. Independent checks retain source points and recompute discrepancies at the current scale. Splat calibration points inherit the uncertainty of approximate picking.

The fixture is modelled at 6×5×3 metres. Its known 6 m width and 5 m depth, listed in `fixtures/measurements.json`, test numerical handling only. They do not establish the accuracy of a real capture. No real-room measured scan is included. The separate 54-photo reconstruction trial uses synthetic rendered images with generating camera poses and geometry withheld from the engines. Its source dimensions cannot serve as a physical-room accuracy measurement or be assumed to survive reconstruction at the same scale.

## Collision boundary

Walk is enabled only with declared/calibrated scale, a display-space floor, a supporting collision surface and a clear spawn. The controller uses a 0.28 m radius capsule and 1.65 m eye height, triangle collision, bounded substeps and a reset after falling more than 2 m below the configured floor. Imported meshes or visually acknowledged proxies above 500,000 triangles are rejected for collision setup.

A photo reconstruction starts without a floor, metric scale or collision proxy, so Orbit/Fly are available and Walk stays gated. A separately imported, aligned and visually acknowledged GLB proxy may enable Walk after the other checks pass. A proxy controls navigation; it does not make splat anchors exact or fill absent visual geometry. Transparent splats may reveal missing surfaces. Open boundaries and holes remain part of the imported representation. The software cannot certify that a virtual route is physically safe or that a scan has complete coverage.

## Numeric guardrails

Stored positions, reference points and the floor are finite and bounded to ±1e9 source/display units. Rotation components are bounded to ±1e6 radians; uniform scale is constrained to 1e-6…1e6. Gaussian centres have a tighter ±65,504 source-unit bound because the pinned default Spark path stores centres as packed half floats. These bounds prevent overflow and singular transforms, not floating-point precision loss at extreme allowed coordinates. Use room-scale geometry close to the source origin.
