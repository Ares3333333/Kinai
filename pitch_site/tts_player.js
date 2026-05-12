// Voice coach for /play.
// Raw audio is never recorded. This module only plays short local browser
// prompts through Web Speech API, with an earcon fallback.

const MUTE_KEY = "kinaesthetic_voice_muted_v1";
const VOLUME_KEY = "kinaesthetic_voice_volume_v1";
const LANG_KEY = "kinaesthetic_voice_lang_v1";

let audioContext = null;
let unlocked = false;
let lastSpoken = "";
let lastSpokenAt = 0;

export function isMuted() {
  return localStorage.getItem(MUTE_KEY) === "true";
}

export function setMuted(value) {
  localStorage.setItem(MUTE_KEY, value ? "true" : "false");
}

export function getVolume() {
  const value = Number(localStorage.getItem(VOLUME_KEY));
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0.8;
}

export function setVolume(value) {
  localStorage.setItem(VOLUME_KEY, String(Math.max(0, Math.min(1, Number(value) || 0))));
}

export function getVoiceLang() {
  return localStorage.getItem(LANG_KEY) || "ru";
}

export function setVoiceLang(value) {
  localStorage.setItem(LANG_KEY, String(value || "ru").startsWith("en") ? "en" : "ru");
}

export async function unlockAudio() {
  if (unlocked) return true;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx && !audioContext) audioContext = new AudioCtx();
    if (audioContext?.state === "suspended") await audioContext.resume();
    unlocked = true;
    return true;
  } catch (_err) {
    unlocked = true;
    return false;
  }
}

export function playEarcon(kind = "alert") {
  if (isMuted()) return;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!audioContext && AudioCtx) audioContext = new AudioCtx();
    if (!audioContext) return;
    const osc = audioContext.createOscillator();
    const gain = audioContext.createGain();
    const now = audioContext.currentTime;
    osc.type = "sine";
    osc.frequency.value = kind === "recovery" ? 660 : 440;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(Math.max(0.02, getVolume() * 0.12), now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.26);
    osc.connect(gain);
    gain.connect(audioContext.destination);
    osc.start(now);
    osc.stop(now + 0.28);
  } catch (_err) {
    // Text command still remains visible.
  }
}

export function speak(text, options = {}) {
  const phrase = String(text || "").trim();
  if (!phrase || isMuted()) return false;
  const cooldownMs = Number(options.cooldownMs || 10_000);
  const now = Date.now();
  if (phrase === lastSpoken && now - lastSpokenAt < cooldownMs) return false;
  lastSpoken = phrase;
  lastSpokenAt = now;

  const lang = String(options.lang || getVoiceLang()).startsWith("en") ? "en-US" : "ru-RU";
  if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
    playEarcon(options.kind || "alert");
    return false;
  }
  try {
    const utter = new SpeechSynthesisUtterance(phrase);
    utter.lang = lang;
    utter.rate = 1.05;
    utter.pitch = 1.0;
    utter.volume = getVolume();
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
    return true;
  } catch (_err) {
    playEarcon(options.kind || "alert");
    return false;
  }
}
