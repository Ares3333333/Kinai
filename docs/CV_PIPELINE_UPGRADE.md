# Kinaesthetic AI CV Pipeline Upgrade

This is the practical pipeline we are using after reviewing the Habr/Sber emotional AI article and current browser-CV tooling.

## What We Adopt Now

1. **On-device first**
   Browser camera analysis stays local. Backend receives only derived scores, labels, session metadata and camera launch diagnostics.

2. **Small fast models before giant models**
   Keep MediaPipe Tasks as the production baseline because it runs in the browser and already gives face landmarks, blendshapes and pose landmarks.

3. **Late fusion**
   Compute each signal separately, then combine:

   ```text
   face/jaw signal
   shoulder/posture signal
   signal quality
   personal baseline
   tester labels
   -> tilt risk / readiness / recovery
   ```

4. **Sequential analysis**
   Do not process every frame aggressively. Use adaptive ticks, confidence gates and recovery proof windows. This follows the same idea as early/efficient video analysis: stop spending compute when signal quality is weak or stable.

5. **Personalization**
   Compare the player with their own neutral baseline. Do not claim universal emotion detection.

6. **Quality gate before live**
   `/camera-check` now verifies HTTPS, permission, available device and busy-camera failures before `/play`.

## Libraries / Engines

Current production baseline:

- MediaPipe Tasks Vision Face Landmarker
- MediaPipe Tasks Vision Pose Landmarker
- local fallback motion engine

Candidate upgrades after 100+ tester base:

- ONNX Runtime Web with WebGPU for optional small custom classifiers over derived landmarks.
- Transformers.js only for non-camera text/proof summarization or tiny browser-side classifiers. Do not upload frames.
- A simple logistic/gradient-boosted model trained on derived features after enough labels.

## Not Shipping Yet

- Raw emotion classification as a product claim.
- Audio stress detection.
- Cloud video inference.
- Heavy foundation model claims before validation data.

## Validation Target

Before adding a custom model, collect:

```text
100+ testers
150+ feedback labels
camera failure cases from /camera-check
per-player baseline samples
false alert rate
helped / not helped command feedback
```
