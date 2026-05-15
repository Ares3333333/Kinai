import { CV_CONFIG, TASKS_SOURCES } from "/cv_config.js";

const POSE_MODEL_URL = CV_CONFIG.poseModelUrl;
const FACE_MODEL_URL = CV_CONFIG.faceModelUrl;
const TILT_SENSITIVITY_SCALE = CV_CONFIG.tiltSensitivityScaleDefault;

function clamp(value, low = 0, high = 100) {
  return Math.max(low, Math.min(high, Number(value) || 0));
}

function desensitize(value) {
  return clamp((Number(value) || 0) * TILT_SENSITIVITY_SCALE);
}

function applyHysteresisBand(value, prevBand, cfg) {
  const v = Number(value) || 0;
  if (prevBand === "high") return v <= cfg.down ? (v >= cfg.midUp ? "medium" : "low") : "high";
  if (prevBand === "medium") {
    if (v >= cfg.up) return "high";
    if (v < cfg.midDown) return "low";
    return "medium";
  }
  return v >= cfg.up ? "high" : v >= cfg.midUp ? "medium" : "low";
}

function visibility(point) {
  return point?.visibility ?? point?.presence ?? 1;
}

function blendshapeMap(results) {
  const categories = results?.faceBlendshapes?.[0]?.categories || [];
  const map = {};
  for (const category of categories) {
    map[category.categoryName] = category.score;
  }
  return map;
}

function lowResMotion(video, canvas, previousFrameRef) {
  if (!video || !canvas || video.readyState < 2) {
    return { brightness: 0, motion: 0 };
  }
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return { brightness: 0, motion: 0 };
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let brightness = 0;
  let motion = 0;
  for (let i = 0; i < data.length; i += 4) {
    const lum = (data[i] + data[i + 1] + data[i + 2]) / 3;
    brightness += lum;
    if (previousFrameRef.current) {
      motion += Math.abs(lum - previousFrameRef.current[i / 4]);
    }
  }
  const pixels = data.length / 4;
  brightness /= pixels;
  motion = previousFrameRef.current ? motion / pixels : 0;
  previousFrameRef.current = new Float32Array(pixels);
  for (let i = 0; i < data.length; i += 4) {
    previousFrameRef.current[i / 4] = (data[i] + data[i + 1] + data[i + 2]) / 3;
  }
  return { brightness, motion };
}

function landmarkPoint(landmark, width, height) {
  return [landmark.x * width, landmark.y * height];
}

function drawDenseSignalField(ctx, width, height, landmarks, color, count = 1536) {
  const visible = (landmarks || []).filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y));
  if (!visible.length) return 0;
  const xs = visible.map((point) => point.x * width);
  const ys = visible.map((point) => point.y * height);
  const minX = Math.max(0, Math.min(...xs) - width * 0.08);
  const maxX = Math.min(width, Math.max(...xs) + width * 0.08);
  const minY = Math.max(0, Math.min(...ys) - height * 0.1);
  const maxY = Math.min(height, Math.max(...ys) + height * 0.12);
  const areaWidth = Math.max(1, maxX - minX);
  const areaHeight = Math.max(1, maxY - minY);
  ctx.save();
  ctx.shadowBlur = 0;
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.18;
  for (let i = 0; i < count; i += 1) {
    const a = (i * 1103515245 + 12345) % 2147483647;
    const b = (i * 1664525 + 1013904223) % 2147483647;
    const x = minX + (a / 2147483647) * areaWidth;
    const y = minY + (b / 2147483647) * areaHeight;
    const nearest = visible[i % visible.length];
    const nx = nearest.x * width;
    const ny = nearest.y * height;
    const distance = Math.hypot(x - nx, y - ny);
    if (distance > Math.max(areaWidth, areaHeight) * 0.38) continue;
    ctx.beginPath();
    ctx.arc(x, y, i % 7 === 0 ? 1.35 : 0.8, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
  return count;
}

function drawMotionFallbackOverlay(canvas, video, motion, brightness) {
  if (!canvas || !video) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, width, height);
  const color = motion > 42 ? "#ffb84d" : "#35f29a";
  ctx.save();
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.2;
  const columns = 48;
  const rows = 28;
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < columns; x += 1) {
      const wave = Math.sin((x + y) * 0.65 + performance.now() * 0.004);
      if (wave < -0.15) continue;
      ctx.beginPath();
      ctx.arc((x + 0.5) * (width / columns), (y + 0.5) * (height / rows), 0.8 + wave * 0.45, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.globalAlpha = 0.85;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.strokeRect(width * 0.18, height * 0.12, width * 0.64, height * 0.76);
  ctx.restore();
}

function drawBrowserOverlay(canvas, video, poseLandmarks, faceLandmarks, stressColor) {
  if (!canvas || !video) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, width, height);
  const allLandmarks = [...(poseLandmarks || []), ...(faceLandmarks || [])];
  const derivedCount = drawDenseSignalField(ctx, width, height, allLandmarks, stressColor, 2048);
  ctx.lineWidth = 3;
  ctx.strokeStyle = stressColor;
  ctx.fillStyle = stressColor;
  ctx.shadowColor = stressColor;
  ctx.shadowBlur = 12;

  const point = (landmark) => landmarkPoint(landmark, width, height);
  if (poseLandmarks?.length) {
    const pairs = [[11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24], [23, 25], [25, 27], [24, 26], [26, 28], [0, 11], [0, 12]];
    for (const [a, b] of pairs) {
      if (!poseLandmarks[a] || !poseLandmarks[b]) continue;
      const [x1, y1] = point(poseLandmarks[a]);
      const [x2, y2] = point(poseLandmarks[b]);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }
    for (const landmark of poseLandmarks) {
      if (!landmark) continue;
      const [x, y] = point(landmark);
      ctx.beginPath();
      ctx.arc(x, y, 3.2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  if (faceLandmarks?.length) {
    ctx.shadowBlur = 5;
    ctx.globalAlpha = 0.72;
    for (const landmark of faceLandmarks) {
      if (!landmark) continue;
      const [x, y] = point(landmark);
      ctx.beginPath();
      ctx.arc(x, y, 1.35, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  return {
    rawLandmarks: (poseLandmarks?.length || 0) + (faceLandmarks?.length || 0),
    derivedPoints: derivedCount,
    signalLayers: faceLandmarks?.length ? 7 : 4,
  };
}

async function createTask(factory, vision, options) {
  try {
    return await factory(vision, {
      ...options,
      baseOptions: { ...options.baseOptions, delegate: "GPU" },
    });
  } catch {
    return await factory(vision, {
      ...options,
      baseOptions: { ...options.baseOptions, delegate: "CPU" },
    });
  }
}

async function loadTasksVisionRuntime() {
  let lastError = null;
  for (const source of TASKS_SOURCES) {
    try {
      const module = await import(source.bundle);
      const vision = await module.FilesetResolver.forVisionTasks(source.wasm);
      return { ...module, vision, source: source.name };
    } catch (error) {
      lastError = error;
      console.warn(`MediaPipe Tasks Vision source failed: ${source.name}`, error);
    }
  }
  throw lastError || new Error("MediaPipe Tasks Vision runtime unavailable");
}

export async function createBrowserCvEngine({ video, overlayCanvas, signalCanvas, baselineProvider }) {
  const { PoseLandmarker, FaceLandmarker, vision, source } = await loadTasksVisionRuntime();
  const poseLandmarker = await createTask(PoseLandmarker.createFromOptions, vision, {
    baseOptions: { modelAssetPath: POSE_MODEL_URL },
    runningMode: "VIDEO",
    numPoses: 1,
    minPoseDetectionConfidence: 0.55,
    minPosePresenceConfidence: 0.55,
    minTrackingConfidence: 0.55,
  });
  const faceLandmarker = await createTask(FaceLandmarker.createFromOptions, vision, {
    baseOptions: { modelAssetPath: FACE_MODEL_URL },
    runningMode: "VIDEO",
    numFaces: 1,
    minFaceDetectionConfidence: 0.5,
    minFacePresenceConfidence: 0.5,
    minTrackingConfidence: 0.5,
    outputFaceBlendshapes: true,
    outputFacialTransformationMatrixes: false,
  });
  const previousFrameRef = { current: null };
  const runtime = {
    lastTime: null,
    lastVideoTime: null,
    frozenTicks: 0,
    fps: 0,
    smoothedTilt: null,
    lastTiltOutput: null,
    smoothedSignalConfidence: null,
    // Dynamic neutral posture baseline learned during calm windows.
    // This prevents false "forward head" alarms for users whose natural
    // camera geometry differs from hardcoded defaults.
    neutralShoulderDistance: null,
    neutralShoulderWidth: null,
    lastBands: {
      jaw: "low",
      shoulders: "low",
      posture: "low",
    },
  };

  return {
    mode: "mediapipe",
    detect() {
      const detectStarted = performance.now();
      const now = detectStarted;
      const poseResults = poseLandmarker.detectForVideo(video, now);
      const faceResults = faceLandmarker.detectForVideo(video, now);
      const pose = poseResults.landmarks?.[0] || [];
      const face = faceResults.faceLandmarks?.[0] || [];
      const shapes = blendshapeMap(faceResults);
      const motionSignals = lowResMotion(video, signalCanvas, previousFrameRef);
      const baseline = baselineProvider?.() || null;

      const nose = pose[0];
      const leftShoulder = pose[11];
      const rightShoulder = pose[12];
      const leftHip = pose[23];
      const rightHip = pose[24];
      const shouldersVisible = Boolean(
        leftShoulder && rightShoulder && visibility(leftShoulder) > 0.35 && visibility(rightShoulder) > 0.35
      );
      const faceDetected = face.length > 120;

      const videoTime = Number(video?.currentTime || 0);
      if (runtime.lastVideoTime !== null && Math.abs(videoTime - runtime.lastVideoTime) < 0.0005) {
        runtime.frozenTicks += 1;
      } else {
        runtime.frozenTicks = 0;
      }
      runtime.lastVideoTime = videoTime;
      const isVideoFrozen = runtime.frozenTicks >= (CV_CONFIG.hardening?.videoFreezeTicks || 8);

      let shoulderElevation = 0;
      let shoulderAsymmetry = 0;
      let headOffset = 0;
      let shoulderDistance = null;
      let shoulderWidth = null;
      let forwardHead = 0;       // chin jut: nose Y close to shoulder Y
      let shoulderProtraction = 0; // shoulders rolled forward (width shrinks)
      let headForwardZ = 0;       // depth lean toward camera
      let torsoLean = 0;          // hip vs shoulder midline drift
      let postureStress = 0;
      let dominantPosture = "neutral";
      let dominantPostureValue = 0;
      if (shouldersVisible && nose) {
        const shoulderY = (leftShoulder.y + rightShoulder.y) / 2;
        shoulderWidth = Math.max(Math.abs(leftShoulder.x - rightShoulder.x), 0.001);
        shoulderDistance = Math.max(shoulderY - nose.y, 0.001);
        const baselineDistance =
          baseline?.neutral?.shoulderDistance
          || runtime.neutralShoulderDistance
          || 0.235;
        const baselineShoulderWidth =
          baseline?.neutral?.shoulderWidth
          || runtime.neutralShoulderWidth
          || 0.30;
        shoulderElevation = clamp(((baselineDistance - shoulderDistance) / Math.max(baselineDistance * 0.28, 0.04)) * 100);
        shoulderAsymmetry = clamp((Math.abs(leftShoulder.y - rightShoulder.y) / shoulderWidth) * 100);
        headOffset = clamp(Math.abs(nose.x - 0.5) * 180);
        // Forward head posture relative to personal neutral distance.
        // Dead-zone (+8%) avoids false positives when sitting still.
        const neutralDistance = baselineDistance;
        const forwardHeadDelta = (neutralDistance * 0.92) - shoulderDistance;
        forwardHead = clamp(
          (forwardHeadDelta - neutralDistance * 0.08)
          / Math.max(neutralDistance * 0.22, 0.02)
          * 100
        );
        // Shoulder protraction: when the user rolls forward, the visible
        // distance between shoulders compresses relative to baseline.
        // Rolled shoulders relative to personal neutral width.
        // Dead-zone (6%) cancels jitter from camera crop changes.
        const protractionDelta = baselineShoulderWidth - shoulderWidth;
        shoulderProtraction = clamp(
          (protractionDelta - baselineShoulderWidth * 0.06)
          / Math.max(baselineShoulderWidth * 0.22, 0.035)
          * 100
        );
        // Camera-relative depth: MediaPipe Pose returns Z normalized so
        // negative = closer to camera. Big negative = leaning into the
        // monitor. Falls back to 0 if Z not provided.
        const noseZ = Number.isFinite(nose.z) ? nose.z : 0;
        headForwardZ = clamp(-noseZ * 220);
        // Torso lean: midline of shoulders vs midline of hips. When the
        // user rotates / leans sideways the two diverge.
        if (leftHip && rightHip && visibility(leftHip) > 0.35 && visibility(rightHip) > 0.35) {
          const shoulderMidX = (leftShoulder.x + rightShoulder.x) / 2;
          const hipMidX = (leftHip.x + rightHip.x) / 2;
          torsoLean = clamp(Math.abs(shoulderMidX - hipMidX) * 220);
        }
        // MAX-based posture composite mirrors the facial design so a
        // single strong cue (forward head OR rolled shoulders OR raised
        // shoulders) is enough to register, while multi-channel posture
        // collapse adds a small bonus.
        const postureChannels = [
          { key: "shoulders_up", value: shoulderElevation, weight: 0.95 },
          { key: "forward_head", value: forwardHead, weight: 0.92 },
          { key: "rolled_shoulders", value: shoulderProtraction, weight: 0.90 },
          { key: "leaning_in", value: headForwardZ, weight: 0.78 },
          { key: "asymmetric", value: shoulderAsymmetry, weight: 0.70 },
          { key: "torso_lean", value: torsoLean, weight: 0.68 },
          { key: "off_center", value: headOffset, weight: 0.55 },
        ];
        let pSum = 0;
        let pDominantWeighted = 0;
        for (const ch of postureChannels) {
          const weighted = ch.value * ch.weight;
          if (weighted > pDominantWeighted) {
            pDominantWeighted = weighted;
            dominantPosture = ch.key;
            dominantPostureValue = ch.value;
          }
          pSum += ch.value;
        }
        const supportingBonus = clamp((pSum - dominantPostureValue) * 0.06);
        postureStress = clamp(pDominantWeighted + supportingBonus);
        if (dominantPostureValue < 14) dominantPosture = "neutral";

        // Learn dynamic neutral baseline while posture looks calm.
        // Helps users who naturally sit closer/farther from camera.
        const calmPosture =
          dominantPostureValue < 22
          && Math.abs(leftShoulder.y - rightShoulder.y) < 0.045
          && motionSignals.motion < 10;
        if (calmPosture) {
          const alpha = 0.05;
          runtime.neutralShoulderDistance =
            runtime.neutralShoulderDistance == null
              ? shoulderDistance
              : (runtime.neutralShoulderDistance * (1 - alpha)) + shoulderDistance * alpha;
          runtime.neutralShoulderWidth =
            runtime.neutralShoulderWidth == null
              ? shoulderWidth
              : (runtime.neutralShoulderWidth * (1 - alpha)) + shoulderWidth * alpha;
        }
      }

      let jawClench = 0;
      let browTension = 0;
      let eyeTension = 0;
      let eyeWiden = 0;
      let mouthPressure = 0;
      let lipCompression = 0;
      let sneer = 0;
      let facialFrustration = 0;
      let facialArousal = 0;
      let facialTension = 0;
      let dominantFacial = "neutral";
      let dominantFacialValue = 0;
      if (faceDetected) {
        const lipGap = Math.abs((face[14]?.y || 0) - (face[13]?.y || 0));
        const faceHeight = Math.max(Math.abs((face[152]?.y || 0) - (face[10]?.y || 0)), 0.001);
        const jawOpenRatio = lipGap / faceHeight;
        const neutralJaw = baseline?.neutral?.jawOpenRatio || 0.035;
        const clenchedJaw = baseline?.jaw?.jawOpenRatio || 0.012;
        const landmarkJaw = clamp(((neutralJaw - jawOpenRatio) / Math.max(neutralJaw - clenchedJaw, 0.008)) * 100);
        // Raw ARKit-style blendshapes from MediaPipe FaceLandmarker.
        // Real-life maxima land around 0.6-0.85 even when the user
        // exaggerates, so we multiply aggressively (x140); otherwise a
        // hard frown reads as ~50 and disappears in downstream weighting.
        const browDown = ((shapes.browDownLeft || 0) + (shapes.browDownRight || 0)) * 0.5;
        const browInnerUp = (shapes.browInnerUp || 0);
        const eyeSquint = ((shapes.eyeSquintLeft || 0) + (shapes.eyeSquintRight || 0)) * 0.5;
        const eyeWide = ((shapes.eyeWideLeft || 0) + (shapes.eyeWideRight || 0)) * 0.5;
        const eyeBlink = ((shapes.eyeBlinkLeft || 0) + (shapes.eyeBlinkRight || 0)) * 0.5;
        const mouthPressBlend = ((shapes.mouthPressLeft || 0) + (shapes.mouthPressRight || 0)) * 0.5;
        const mouthClose = (shapes.mouthClose || 0);
        const mouthFrown = ((shapes.mouthFrownLeft || 0) + (shapes.mouthFrownRight || 0)) * 0.5;
        const mouthFunnel = (shapes.mouthFunnel || 0);
        const mouthPucker = (shapes.mouthPucker || 0);
        const cheekSquint = ((shapes.cheekSquintLeft || 0) + (shapes.cheekSquintRight || 0)) * 0.5;
        const noseSneer = ((shapes.noseSneerLeft || 0) + (shapes.noseSneerRight || 0)) * 0.5;

        // Per-channel tension scores (0-100). Each channel can hit 60+
        // alone with a single strong micro-expression; that is what makes
        // the system actually feel responsive when the user frowns / pops
        // their eyes / clenches their lips. A neutral face yields <8.
        const browSignal = clamp(browDown * 140 + browInnerUp * 60 + noseSneer * 35);
        const eyeWideSignal = clamp(eyeWide * 160);
        const eyeSquintSignal = clamp(eyeSquint * 130 + cheekSquint * 60);
        const lipSignal = clamp(
          mouthPressBlend * 140 + mouthClose * 80 + mouthFrown * 100 + mouthFunnel * 70 + mouthPucker * 55
        );
        const sneerSignal = clamp(noseSneer * 150 + browDown * 30);
        const pressureBlend = clamp(mouthPressBlend * 130 + mouthClose * 65 + mouthFunnel * 45);
        jawClench = clamp(Math.max(landmarkJaw, pressureBlend));

        browTension = browSignal;
        eyeTension = eyeSquintSignal;
        eyeWiden = eyeWideSignal;
        mouthPressure = pressureBlend;
        lipCompression = lipSignal;
        sneer = sneerSignal;

        // MAX-based composite: dominant cue mostly drives the signal,
        // with a small additive bonus when several cues fire together.
        // This avoids the previous cascade-multiplication problem where
        // a single channel was diluted to invisible levels.
        const channels = [
          { key: "brow", value: browSignal, weight: 0.95 },
          { key: "eye_wide", value: eyeWideSignal, weight: 0.92 },
          { key: "eye_squint", value: eyeSquintSignal, weight: 0.78 },
          { key: "lip", value: lipSignal, weight: 0.95 },
          { key: "sneer", value: sneerSignal, weight: 0.85 },
          { key: "jaw", value: jawClench, weight: 0.80 },
        ];
        let dominantWeighted = 0;
        let supportSum = 0;
        for (const ch of channels) {
          const weighted = ch.value * ch.weight;
          if (weighted > dominantWeighted) {
            dominantWeighted = weighted;
            dominantFacial = ch.key;
            dominantFacialValue = ch.value;
          }
          supportSum += ch.value;
        }
        const supportingBonus = clamp((supportSum - dominantFacialValue) * 0.10);
        facialTension = clamp(dominantWeighted + supportingBonus);
        facialFrustration = clamp(
          browSignal * 0.55 + lipSignal * 0.40 + sneerSignal * 0.20 + cheekSquint * 0.15 * 100
        );
        facialArousal = clamp(facialTension * 0.85 + Math.max(eyeWideSignal, eyeSquintSignal) * 0.20);
        if (dominantFacialValue < 14) dominantFacial = "neutral";
      }

      const lightScore = clamp((motionSignals.brightness - 20) * 1.25);
      const motionIntensity = clamp(motionSignals.motion * 4);
      const signalQuality = clamp((faceDetected ? 38 : 0) + (shouldersVisible ? 42 : 0) + lightScore * 0.2);
      const confidenceAlpha = CV_CONFIG.hardening?.confidenceEmaAlpha ?? 0.35;
      let smoothedSignalConfidence = runtime.smoothedSignalConfidence == null
        ? signalQuality / 100
        : runtime.smoothedSignalConfidence * (1 - confidenceAlpha) + (signalQuality / 100) * confidenceAlpha;
      if (isVideoFrozen) smoothedSignalConfidence = Math.min(smoothedSignalConfidence, 0.25);
      if (motionSignals.brightness < 18) smoothedSignalConfidence *= 0.75;
      runtime.smoothedSignalConfidence = clamp(smoothedSignalConfidence * 100) / 100;
      // Tilt budget: facialTension and postureStress are the two
      // first-class composites; jaw and motion ride alongside as
      // supporting evidence. A hard frown alone lifts tilt by ~35; rolled
      // shoulders alone lift it by ~30; both together push past 70.
      let tiltRiskRaw = clamp(
        jawClench * 0.18
        + postureStress * 0.42
        + motionIntensity * 0.10
        + facialTension * 0.50
      );

      // Single-channel face floors: when the face alone is already
      // screaming, the bar must reflect it even if the body is calm.
      if (facialTension >= 90) tiltRiskRaw = Math.max(tiltRiskRaw, 80);
      else if (facialTension >= 75) tiltRiskRaw = Math.max(tiltRiskRaw, 65);
      else if (facialTension >= 55) tiltRiskRaw = Math.max(tiltRiskRaw, 50);
      else if (facialTension >= 35) tiltRiskRaw = Math.max(tiltRiskRaw, 30);

      // Single-channel posture floors: same rule for the body. Forward
      // head + raised shoulders alone is a clear tilt sign even with a
      // poker face.
      if (postureStress >= 85) tiltRiskRaw = Math.max(tiltRiskRaw, 72);
      else if (postureStress >= 68) tiltRiskRaw = Math.max(tiltRiskRaw, 55);
      else if (postureStress >= 50) tiltRiskRaw = Math.max(tiltRiskRaw, 38);
      else if (postureStress >= 34) tiltRiskRaw = Math.max(tiltRiskRaw, 22);

      // Compound stress: count how many independent channels are firing.
      // Posture now counts as a single channel rather than three
      // overlapping ones, which used to inflate the score artificially.
      const compoundChannels = [
        facialTension >= 50,
        postureStress >= 52,
        jawClench >= 45,
        motionIntensity >= 35,
      ].filter(Boolean).length;

      if (compoundChannels >= 4) tiltRiskRaw = Math.max(tiltRiskRaw, 95);
      else if (compoundChannels >= 3) tiltRiskRaw = Math.max(tiltRiskRaw, 85);
      else if (compoundChannels >= 2) tiltRiskRaw = Math.max(tiltRiskRaw, 72);

      // Saturated extreme: face AND posture both at peak means genuinely
      // collapsed, so push to ~97 so the bar truly fills.
      if (facialTension >= 80 && postureStress >= 74) {
        tiltRiskRaw = Math.max(tiltRiskRaw, 97);
      }

      tiltRiskRaw = desensitize(tiltRiskRaw);

      // Light EMA: fast enough to feel instant (alpha 0.55) but quiet
      // enough that blendshape jitter does not flicker the bar.
      const prevTilt = runtime.smoothedTilt ?? tiltRiskRaw;
      const tiltRisk = clamp(prevTilt * 0.52 + tiltRiskRaw * 0.48);
      runtime.smoothedTilt = tiltRisk;
      // Hardening: prevent single-tick spikes from exploding the meter.
      const prevOut = runtime.lastTiltOutput ?? tiltRisk;
      const maxStep = CV_CONFIG.hardening?.maxTiltStepPerTick ?? 16;
      const tiltRiskStable = clamp(Math.max(prevOut - maxStep, Math.min(prevOut + maxStep, tiltRisk)));
      runtime.lastTiltOutput = tiltRiskStable;
      const recovery = clamp(100 - tiltRiskStable * 0.7 + signalQuality * 0.16);
      const readiness = clamp(100 - tiltRiskStable * 0.72 + recovery * 0.12);
      const stressColor = tiltRisk > 72 ? "#ff4f58" : tiltRisk > 45 ? "#ffb84d" : "#35f29a";
      const overlayStats = drawBrowserOverlay(overlayCanvas, video, pose, face, stressColor) || {};
      const latencyMs = performance.now() - detectStarted;
      runtime.fps = runtime.lastTime ? 1000 / Math.max(1, detectStarted - runtime.lastTime) : runtime.fps;
      runtime.lastTime = detectStarted;

      const jawBand = applyHysteresisBand(jawClench, runtime.lastBands.jaw, CV_CONFIG.hysteresis.jaw);
      const shoulderBand = applyHysteresisBand(shoulderElevation, runtime.lastBands.shoulders, CV_CONFIG.hysteresis.shoulders);
      const postureBand = applyHysteresisBand(postureStress, runtime.lastBands.posture, CV_CONFIG.hysteresis.posture);
      runtime.lastBands.jaw = jawBand;
      runtime.lastBands.shoulders = shoulderBand;
      runtime.lastBands.posture = postureBand;

      const runtimeSource = isVideoFrozen ? `${source}_camera_frozen_guard` : source;
      return {
        backend: "mediapipe_tasks_vision_web",
        runtime_source: runtimeSource,
        is_fallback: false,
        faceDetected,
        shouldersVisible,
        signal_confidence: runtime.smoothedSignalConfidence,
        face_confidence: faceDetected ? clamp(50 + face.length / 12) / 100 : 0,
        pose_confidence: shouldersVisible ? clamp(62 + Math.min(30, signalQuality * 0.25)) / 100 : 0,
        jaw_confidence: faceDetected ? clamp(55 + (shapes.mouthClose || 0) * 35 + signalQuality * 0.1) / 100 : 0,
        shoulder_confidence: shouldersVisible ? clamp(60 + signalQuality * 0.25) / 100 : 0,
        raw_landmarks: overlayStats.rawLandmarks || pose.length + face.length,
        derived_points: overlayStats.derivedPoints || 0,
        signal_layers: overlayStats.signalLayers || 0,
        fps: runtime.fps,
        latency_ms: latencyMs,
        tilt_risk: tiltRiskStable,
        readiness,
        recovery,
        jaw_tension: jawBand,
        shoulder_tension: shoulderBand,
        posture_tension: postureBand,
        jaw_score: jawClench,
        brow_tension: browTension,
        eye_tension: eyeTension,
        eye_widen: eyeWiden,
        mouth_pressure: mouthPressure,
        lip_compression: lipCompression,
        sneer: sneer,
        facial_frustration: facialFrustration,
        facial_arousal: facialArousal,
        facial_tension: facialTension,
        dominant_facial: dominantFacial,
        dominant_facial_value: dominantFacialValue,
        compound_channels: compoundChannels,
        head_drift: headOffset,
        shoulder_score: shoulderElevation,
        forward_head: forwardHead,
        shoulder_protraction: shoulderProtraction,
        head_forward_z: headForwardZ,
        torso_lean: torsoLean,
        posture_stress: postureStress,
        dominant_posture: dominantPosture,
        dominant_posture_value: dominantPostureValue,
        shoulderDistance,
        shoulderWidth,
        jawOpenRatio: faceDetected ? Math.abs((face[14]?.y || 0) - (face[13]?.y || 0)) / Math.max(Math.abs((face[152]?.y || 0) - (face[10]?.y || 0)), 0.001) : null,
        motion: motionIntensity,
        brightness: motionSignals.brightness,
        recommendation: isVideoFrozen
          ? "Video stream is frozen. Check the camera and restart the session."
          : tiltRiskStable > 70
          ? "MediaPipe: soften jaw, shoulders down, 20-second reset."
          : "MediaPipe: state is readable, keep a soft posture.",
      };
    },
  };
}

export function createMotionFallbackEngine({ video, overlayCanvas, signalCanvas, baselineProvider }) {
  const previousFrameRef = { current: null };
  const runtime = { lastTime: null, fps: 0, lastVideoTime: null, frozenTicks: 0, smoothedSignalConfidence: null };
  return {
    mode: "motion_fallback",
    detect() {
      const detectStarted = performance.now();
      const signals = lowResMotion(video, signalCanvas, previousFrameRef);
      const videoTime = Number(video?.currentTime || 0);
      if (runtime.lastVideoTime !== null && Math.abs(videoTime - runtime.lastVideoTime) < 0.0005) {
        runtime.frozenTicks += 1;
      } else {
        runtime.frozenTicks = 0;
      }
      runtime.lastVideoTime = videoTime;
      const isVideoFrozen = runtime.frozenTicks >= (CV_CONFIG.hardening?.videoFreezeTicks || 8);
      const baseline = baselineProvider?.() || null;
      const baselineMotion = baseline?.neutral?.motion || 12;
      const lightScore = clamp((signals.brightness - 20) * 1.25);
      const motionIntensity = clamp(signals.motion * 4);
      const motionStress = Math.max(0, motionIntensity - baselineMotion);
      const lowLightPenalty = Math.max(0, 70 - lightScore) * 0.45;
      const tiltRisk = clamp(motionStress * 1.2 + lowLightPenalty + 24);
      const readiness = clamp(100 - tiltRisk * 0.7 + lightScore * 0.16);
      const recovery = clamp(100 - motionStress * 0.8 - lowLightPenalty);
      drawMotionFallbackOverlay(overlayCanvas, video, motionIntensity, signals.brightness);
      const latencyMs = performance.now() - detectStarted;
      runtime.fps = runtime.lastTime ? 1000 / Math.max(1, detectStarted - runtime.lastTime) : runtime.fps;
      runtime.lastTime = detectStarted;
      const confidenceAlpha = CV_CONFIG.hardening?.confidenceEmaAlpha ?? 0.35;
      let smoothedSignalConfidence = runtime.smoothedSignalConfidence == null
        ? lightScore / 100
        : runtime.smoothedSignalConfidence * (1 - confidenceAlpha) + (lightScore / 100) * confidenceAlpha;
      if (isVideoFrozen) smoothedSignalConfidence = Math.min(smoothedSignalConfidence, 0.2);
      runtime.smoothedSignalConfidence = clamp(smoothedSignalConfidence * 100) / 100;
      return {
        backend: "motion_fallback",
        is_fallback: true,
        faceDetected: false,
        shouldersVisible: false,
        signal_confidence: runtime.smoothedSignalConfidence,
        face_confidence: 0,
        pose_confidence: 0,
        jaw_confidence: 0,
        shoulder_confidence: 0,
        raw_landmarks: 0,
        derived_points: 2048,
        signal_layers: 2,
        fps: runtime.fps,
        latency_ms: latencyMs,
        tilt_risk: tiltRisk,
        readiness,
        recovery,
        jaw_tension: tiltRisk > 65 ? "high" : tiltRisk > 42 ? "medium" : "low",
        shoulder_tension: motionIntensity > 32 ? "medium" : "low",
        jaw_score: 0,
        brow_tension: 0,
        eye_tension: 0,
        mouth_pressure: 0,
        head_drift: 0,
        shoulder_score: 0,
        motion: motionIntensity,
        brightness: signals.brightness,
        recommendation: isVideoFrozen
          ? "Fallback: video stream is frozen, restart the camera."
          : tiltRisk > 65
          ? "Fallback: pause for 20 seconds, soften your jaw."
          : "Fallback: camera is stable, but MediaPipe is not loaded.",
      };
    },
  };
}
