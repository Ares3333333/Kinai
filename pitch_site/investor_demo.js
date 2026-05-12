const phases = [
  {
    id: "baseline",
    label: "Baseline",
    body: "baseline",
    duration: 6500,
    tilt: [24, 32],
    readiness: [82, 78],
    tag: "01 / CALIBRATION",
    title: "Система калибрует игрока",
    copy: "Первые секунды показывают нейтральную посадку: лицо спокойно, плечи ровные, сигнал чистый.",
    command: "Система смотрит baseline.",
    meta: "Это scripted demo. Для реальной камеры используйте /play.",
    proof: "Proof появится после recovery",
    details: "Собираем baseline: posture, face tension, shoulders.",
    signals: ["Лицо: нейтрально", "Плечи: свободно", "Сигнал: чистый"],
  },
  {
    id: "rising",
    label: "Rising tilt",
    body: "rising",
    duration: 7000,
    tilt: [32, 62],
    readiness: [78, 56],
    tag: "02 / EARLY WARNING",
    title: "Тильт растет раньше, чем игрок это замечает",
    copy: "Челюсть фиксируется, плечи поднимаются, осанка становится жестче. Система видит body-state shift до срыва раунда.",
    command: "Тильт растет: челюсть и плечи зажимаются.",
    meta: "Порог реагирует на несколько сигналов сразу, а не на один случайный жест.",
    proof: "Peak формируется",
    details: "Dominant lock: jaw + shoulders.",
    signals: ["Jaw lock: растет", "Shoulders: tense", "Readiness: падает"],
  },
  {
    id: "alert",
    label: "Coach alert",
    body: "alert",
    duration: 6500,
    tilt: [62, 74],
    readiness: [56, 43],
    tag: "03 / COACH INTERVENTION",
    title: "Одна короткая команда вместо лишнего шума",
    copy: "Коуч не отвлекает от матча. Он дает короткий телесный reset, когда риск уже высокий.",
    command: "Смягчи челюсть. Опусти плечи. Длинный выдох.",
    meta: "Critical alert работает локально даже без LLM. LLM только улучшает формулировку команды.",
    proof: "Coach command issued",
    details: "Peak tilt: 74. Command delivered before escalation.",
    signals: ["Risk: высокий", "Command: sent", "LLM: optional"],
  },
  {
    id: "recovery",
    label: "Recovery proof",
    body: "recovery",
    duration: 8000,
    tilt: [74, 31],
    readiness: [43, 79],
    tag: "04 / PROOF",
    title: "Recovery видно в цифрах",
    copy: "После команды риск падает, readiness возвращается. Инвестор видит loop: signal -> alert -> recovery -> proof.",
    command: "Сброс пошел. Держи плечи свободными.",
    meta: "После команды risk падает, readiness возвращается.",
    proof: "Tilt 74 -> 31",
    details: "Recovery: 18 sec. Dominant lock: shoulders. Raw video not stored.",
    signals: ["Recovery: 18 сек", "Dominant lock: shoulders", "Raw video: не сохраняется"],
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
      return {
        phase,
        index,
        localProgress: clamp((elapsedMs - cursor) / phase.duration, 0, 1),
      };
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
    startButton.textContent = safeElapsed >= totalMs ? "Повторить demo" : isPlaying ? "Demo идет..." : "Показать demo";
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
