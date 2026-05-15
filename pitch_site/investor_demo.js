const phases = [
  {
    id: "baseline",
    label: "Baseline",
    body: "baseline",
    duration: 6500,
    tilt: [24, 32],
    readiness: [82, 78],
    tag: "01 / CALIBRATION",
    title: "The system calibrates the player",
    copy: "The first seconds show a neutral baseline: calm face, even shoulders, clean signal.",
    command: "The system is watching baseline.",
    meta: "This is a scripted demo. Use /play for real camera analysis.",
    proof: "Proof appears after recovery",
    details: "Collecting baseline: posture, face tension, shoulders.",
    signals: ["Face: neutral", "Shoulders: relaxed", "Signal: clean"],
  },
  {
    id: "rising",
    label: "Rising tilt",
    body: "rising",
    duration: 7000,
    tilt: [32, 62],
    readiness: [78, 56],
    tag: "02 / EARLY WARNING",
    title: "Tilt rises before the player notices it",
    copy: "Jaw tension, lifted shoulders, and rigid posture combine into an early body-state shift before the round breaks.",
    command: "Tilt is rising: jaw and shoulders are locking.",
    meta: "The threshold reacts to several signals at once, not one random gesture.",
    proof: "Peak is forming",
    details: "Dominant lock: jaw + shoulders.",
    signals: ["Jaw lock: rising", "Shoulders: tense", "Readiness: falling"],
  },
  {
    id: "alert",
    label: "Coach alert",
    body: "alert",
    duration: 6500,
    tilt: [62, 74],
    readiness: [56, 43],
    tag: "03 / COACH INTERVENTION",
    title: "One short command instead of noise",
    copy: "The coach does not distract from the match. It gives a short physical reset when risk is already high.",
    command: "Soften jaw. Drop shoulders. Long exhale.",
    meta: "Critical alerts work locally without an LLM. Cloud coach only improves wording.",
    proof: "Coach command issued",
    details: "Peak tilt: 74. Command delivered before escalation.",
    signals: ["Risk: high", "Command: sent", "LLM: optional"],
  },
  {
    id: "recovery",
    label: "Recovery proof",
    body: "recovery",
    duration: 8000,
    tilt: [74, 31],
    readiness: [43, 79],
    tag: "04 / PROOF",
    title: "Recovery is visible in numbers",
    copy: "After the command, risk drops and readiness returns. Investors see the loop: signal -> alert -> recovery -> proof.",
    command: "Reset is working. Keep shoulders loose.",
    meta: "After the command, risk drops and readiness returns.",
    proof: "Tilt 74 -> 31",
    details: "Recovery: 18 sec. Dominant lock: shoulders. Raw video not stored.",
    signals: ["Recovery: 18 sec", "Dominant lock: shoulders", "Raw video: not stored"],
  },
];

const q = (id) => document.getElementById(id);
const totalMs = phases.reduce((sum, phase) => sum + phase.duration, 0);
let animationFrame = null;
let startedAt = 0;
let isPlaying = false;

function ease(value) {
  return value < 0.5 ? 2 * value * value : 1 - Math.pow(-2 * value + 2, 2) / 2;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function formatClock(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function phaseAt(elapsedMs) {
  let cursor = 0;
  for (let index = 0; index < phases.length; index += 1) {
    const phase = phases[index];
    const next = cursor + phase.duration;
    if (elapsedMs <= next || index === phases.length - 1) {
      return { phase, index, localProgress: clamp((elapsedMs - cursor) / phase.duration, 0, 1) };
    }
    cursor = next;
  }
  return { phase: phases[0], index: 0, localProgress: 0 };
}

function interpolate([from, to], progress) {
  return Math.round(from + (to - from) * ease(progress));
}

function setText(id, text) {
  const node = q(id);
  if (node) node.textContent = text;
}

function render(elapsedMs) {
  const safeElapsed = clamp(elapsedMs, 0, totalMs);
  const { phase, index, localProgress } = phaseAt(safeElapsed);
  const tilt = interpolate(phase.tilt, localProgress);
  const readiness = interpolate(phase.readiness, localProgress);
  const totalProgress = safeElapsed / totalMs;

  document.body.dataset.demoPhase = phase.body;
  setText("demoPhaseLabel", phase.label);
  setText("demoClock", `${formatClock(safeElapsed)} / ${formatClock(totalMs)}`);
  setText("demoSceneTag", phase.tag);
  setText("demoSceneTitle", phase.title);
  setText("demoSceneCopy", phase.copy);
  setText("demoTilt", String(tilt));
  setText("demoReadiness", String(readiness));
  setText("demoCommand", phase.command);
  setText("demoCommandMeta", phase.meta);
  setText("demoProof", phase.proof);
  setText("demoProofDetails", phase.details);
  setText("demoSignalOne", phase.signals[0]);
  setText("demoSignalTwo", phase.signals[1]);
  setText("demoSignalThree", phase.signals[2]);

  const tiltBar = q("demoTiltBar");
  if (tiltBar) tiltBar.style.width = `${tilt}%`;
  const progressBar = q("demoProgressBar");
  if (progressBar) progressBar.style.width = `${Math.round(totalProgress * 100)}%`;

  document.querySelectorAll(".demo-timeline li").forEach((node) => {
    const itemIndex = phases.findIndex((item) => item.id === node.dataset.step);
    node.classList.toggle("is-active", itemIndex === index);
    node.classList.toggle("is-done", itemIndex < index);
  });

  const startButton = q("startInvestorDemo");
  if (startButton) {
    startButton.textContent = safeElapsed >= totalMs ? "Replay demo" : isPlaying ? "Demo running..." : "Show demo";
  }

  const nextActions = q("demoNextActions");
  if (nextActions) nextActions.hidden = safeElapsed < totalMs;
}

function tick(now) {
  const elapsed = now - startedAt;
  render(elapsed);
  if (elapsed >= totalMs) {
    isPlaying = false;
    animationFrame = null;
    render(totalMs);
    return;
  }
  animationFrame = requestAnimationFrame(tick);
}

function start() {
  if (animationFrame) cancelAnimationFrame(animationFrame);
  isPlaying = true;
  startedAt = performance.now();
  animationFrame = requestAnimationFrame(tick);
}

function reset() {
  if (animationFrame) cancelAnimationFrame(animationFrame);
  animationFrame = null;
  isPlaying = false;
  const nextActions = q("demoNextActions");
  if (nextActions) nextActions.hidden = true;
  render(0);
}

q("startInvestorDemo")?.addEventListener("click", start);
q("resetInvestorDemo")?.addEventListener("click", reset);
window.__startInvestorDemo = start;
window.__resetInvestorDemo = reset;

render(0);
