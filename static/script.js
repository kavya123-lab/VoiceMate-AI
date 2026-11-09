"use strict";

/* =====================================================================
   VoiceMate AI - browser code
   1. Settings and state   2. Small helpers      3. Server calls
   4. Chat messages        5. Audio player       6. Recording and WAV
   7. Waveform             8. Ask a question     9. History, stats and voice sessions
   10. Page controls and start-up
   ===================================================================== */

/* ---------- 1. Settings and state ---------- */
const TARGET_RATE = 16000;          // Prisma accepts 8-44.1 kHz; 16 kHz is plenty for speech
const MIN_SECONDS = 0.5;            // shorter recordings are rejected before upload
const SILENCE_PEAK = 0.01;          // quieter than this = nothing was heard
const REQUEST_TIMEOUT_MS = 90000;

const state = {
  lang: "en",
  voiceOutput: true,
  conversationId: null,             // null = a new conversation is created on the first question
  busy: false,
  recording: false,
  maxSeconds: 30,
  langNames: { en: "English", hi: "Hindi", gu: "Gujarati" },
};

let pendingRecordingId = null;      // the saved recording that belongs to the question being asked

const $ = (id) => document.getElementById(id);
const chatEl = $("chat-messages");
const welcomeEl = $("welcome");
const textInput = $("text-input");
const sendBtn = $("send-btn");
const micBtn = $("mic-btn");
const micIcon = $("mic-icon");
const micHint = $("mic-hint");
const recTimer = $("rec-timer");
const player = $("player");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- 2. Small helpers ---------- */
function loadPref(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch { return fallback; }
}
function savePref(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage may be blocked: ignore */ }
}

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;   // textContent (never innerHTML) keeps user text safe
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
function makeIcon(name, size = 16, fill = false) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", fill ? "icon icon-fill" : "icon");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}
function setIcon(svg, name) { svg.querySelector("use").setAttribute("href", `#i-${name}`); }

function formatTime(seconds) {
  if (!Number.isFinite(seconds)) seconds = 0;
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/* Status pill (top right of the voice card) */
let statusResetTimer = null;
function setStatus(kind, text) {
  clearTimeout(statusResetTimer);
  $("status-pill").dataset.state = kind;
  $("status-text").textContent = text;
}
function isPlaying() { return Boolean(player.src) && !player.paused && !player.ended; }
function restStatus() {
  if (state.recording || state.busy) return;
  if (isPlaying()) setStatus("speaking", "Speaking");
  else setStatus("ready", "Ready");
}

/* Error and info messages */
let toastTimer = null;
function showToast(message, kind = "error") {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("is-info", kind === "info");
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 7000);
}
function showError(message) {
  showToast(message, "error");
  setStatus("error", "Needs attention");
  statusResetTimer = setTimeout(restStatus, 3500);
}

function setBusy(flag) {
  state.busy = flag;
  sendBtn.disabled = flag;
  micBtn.disabled = flag;
  micBtn.classList.toggle("is-busy", flag);
}

/* ---------- 3. Server calls ---------- */
class ApiProblem extends Error {
  constructor(message, code) { super(message); this.code = code; }
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(path, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new ApiProblem("That took too long. Please try again.", "timeout");
    }
    throw new ApiProblem("Can’t reach the server. Check your connection and try again.", "network");
  } finally {
    clearTimeout(timer);
  }

  let data = null;
  try { data = await response.json(); } catch { /* not JSON */ }

  if (!response.ok || !data || data.success === false) {
    const info = (data && data.error) || {};
    throw new ApiProblem(info.message || "Something went wrong. Please try again.", info.code || "error");
  }
  return data;
}

function base64ToBlob(base64, mime) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

/* ---------- 4. Chat messages ---------- */
function scrollChat() { chatEl.scrollTop = chatEl.scrollHeight; }

function makeListenButton(messageId, conversationId, text) {
  const btn = el("button", "listen-btn");
  btn.type = "button";
  btn.dataset.messageId = String(messageId);
  btn.append(makeIcon("speaker", 14), el("span", "", "Listen"));
  btn.addEventListener("click", () => onListen(btn, messageId, conversationId, text));
  return btn;
}

function addMessage({ role, text, id = null, conversationId = null, inputType = "text" }) {
  const row = el("div", `msg ${role === "user" ? "msg-user" : "msg-ai"}`);

  const avatar = el("span", "avatar");
  avatar.appendChild(makeIcon(role === "user" ? "user" : "wave", 20));

  const bubble = el("div", "bubble");
  bubble.appendChild(el("p", "", text));

  if (role === "user" && inputType === "voice") {
    const meta = el("div", "bubble-meta");
    const tag = el("span", "bubble-tag");
    tag.append(makeIcon("mic", 12), "Heard by Prisma");
    meta.appendChild(tag);
    bubble.appendChild(meta);
  }
  if (role === "assistant" && id !== null) {
    const meta = el("div", "bubble-meta");
    meta.appendChild(makeListenButton(id, conversationId, text));
    bubble.appendChild(meta);
  }

  row.append(avatar, bubble);
  chatEl.appendChild(row);
  scrollChat();
  return row;
}

function addTyping() {
  const row = el("div", "msg msg-ai");
  const avatar = el("span", "avatar");
  avatar.appendChild(makeIcon("wave", 20));
  const bubble = el("div", "bubble");
  const dots = el("span", "typing");
  dots.setAttribute("aria-label", "VoiceMate AI is thinking");
  dots.append(el("span"), el("span"), el("span"));
  bubble.appendChild(dots);
  row.append(avatar, bubble);
  chatEl.appendChild(row);
  scrollChat();
  return row;
}

function addNote(messageRow, text) {
  const bubble = messageRow.querySelector(".bubble");
  if (bubble) bubble.appendChild(el("p", "bubble-note", text));
}

function showNewConversationView() {
  chatEl.replaceChildren(welcomeEl);
  scrollChat();
}

/* ---------- 5. Audio player ---------- */
// Keys: an answer's message id (a number), or "rec-<id>" for a saved voice recording
const audioCache = new Map();       // key -> audio, so nothing is downloaded or generated twice
let currentUrl = null;
let currentMessageId = null;

function refreshListenButtons() {
  // "Listen" buttons under each answer
  document.querySelectorAll(".listen-btn").forEach((btn) => {
    if (btn.classList.contains("is-loading")) return;
    const playing = isPlaying() && String(currentMessageId) === btn.dataset.messageId;
    btn.classList.toggle("is-playing", playing);
    btn.lastChild.textContent = playing ? "Pause" : "Listen";
    setIcon(btn.firstChild, playing ? "pause" : "speaker");
  });

  // Play buttons in "Recent voice sessions"
  document.querySelectorAll(".session-row").forEach((row) => {
    const playing = isPlaying() && currentMessageId === `rec-${row.dataset.id}`;
    row.classList.toggle("is-playing", playing);
    setIcon(row.querySelector(".round-btn svg"), playing ? "pause" : "play");
  });
}

async function playBlob(blob, messageId, label) {
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = URL.createObjectURL(blob);
  currentMessageId = messageId;
  player.src = currentUrl;
  $("player-title").textContent = label;
  $("player-toggle").disabled = false;
  $("player-seek").disabled = false;
  try {
    await player.play();
  } catch (error) {
    if (error.name === "NotAllowedError") {
      showToast("Your browser blocked automatic playback. Tap the play button to listen.", "info");
    } else if (error.name !== "AbortError") {
      showToast("The audio couldn’t be played.");
    }
  }
  refreshListenButtons();
}

async function playAudioBlock(audio, messageId, label) {
  const blob = base64ToBlob(audio.base64, audio.mime);
  audioCache.set(messageId, blob);
  await playBlob(blob, messageId, label);
}

async function onListen(btn, messageId, conversationId, text) {
  if (currentMessageId === messageId && player.src) {       // same answer: play or pause
    if (player.paused) player.play().catch(() => {}); else player.pause();
    return;
  }
  const label = `Answer: ${text.length > 60 ? text.slice(0, 60) + "…" : text}`;
  if (audioCache.has(messageId)) {
    await playBlob(audioCache.get(messageId), messageId, label);
    return;
  }
  btn.classList.add("is-loading");
  btn.lastChild.textContent = "Loading…";
  try {
    const data = await api(`/api/conversations/${conversationId}/messages/${messageId}/speech`, { method: "POST" });
    await playAudioBlock(data.audio, messageId, label);
  } catch (error) {
    showToast(error.message);
  } finally {
    btn.classList.remove("is-loading");
    refreshListenButtons();
  }
}

function updateSeek() {
  const duration = player.duration;
  $("player-seek").value = Number.isFinite(duration) && duration > 0 ? (player.currentTime / duration) * 100 : 0;
  $("player-time").textContent = `${formatTime(player.currentTime)} / ${formatTime(duration)}`;
}

player.addEventListener("play", () => { setIcon($("player-icon"), "pause"); $("player-toggle").setAttribute("aria-label", "Pause"); refreshListenButtons(); restStatus(); });
player.addEventListener("pause", () => { setIcon($("player-icon"), "play"); $("player-toggle").setAttribute("aria-label", "Play"); refreshListenButtons(); restStatus(); });
player.addEventListener("ended", () => { setIcon($("player-icon"), "play"); refreshListenButtons(); restStatus(); setWaveMode("idle"); });
player.addEventListener("timeupdate", updateSeek);
player.addEventListener("loadedmetadata", updateSeek);
player.addEventListener("play", () => { if (!state.recording) setWaveMode("pulse"); });
player.addEventListener("pause", () => { if (!state.recording) setWaveMode("idle"); });

$("player-toggle").addEventListener("click", () => {
  if (!player.src) return;
  if (player.paused) player.play().catch(() => {}); else player.pause();
});
$("player-seek").addEventListener("input", (event) => {
  if (Number.isFinite(player.duration)) player.currentTime = (event.target.value / 100) * player.duration;
});

/* ---------- 6. Recording and WAV conversion ---------- */
let mediaStream = null;
let recorder = null;
let recordedChunks = [];
let recordCtx = null;
let analyser = null;
let freqData = null;
let timerId = null;
let recordStart = 0;
let recordLang = "en";

function micErrorMessage(error) {
  switch (error && error.name) {
    case "NotAllowedError":
    case "SecurityError":
    case "PermissionDeniedError":
      return "Microphone access is blocked. Click the lock icon in the address bar, allow the microphone, and try again.";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "No microphone was found. Connect one and try again.";
    case "NotReadableError":
      return "Your microphone is being used by another app. Close it and try again.";
    default:
      return "The microphone couldn’t be started. You can still type your question.";
  }
}

async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
    showError("Voice recording isn’t available here. It needs a modern browser and a secure (https) page. You can still type your question.");
    return;
  }
  if (!player.paused) player.pause();   // never record the assistant’s own voice

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (error) {
    showError(micErrorMessage(error));
    return;
  }

  recordLang = state.lang;
  recordedChunks = [];
  recorder = new MediaRecorder(mediaStream);
  recorder.addEventListener("dataavailable", (event) => { if (event.data.size > 0) recordedChunks.push(event.data); });
  recorder.start();

  // The analyser only feeds the waveform. It is not connected to the speakers (no echo).
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  recordCtx = new AudioCtx();
  recordCtx.resume().catch(() => {});
  analyser = recordCtx.createAnalyser();
  analyser.fftSize = 256;
  freqData = new Uint8Array(analyser.frequencyBinCount);
  recordCtx.createMediaStreamSource(mediaStream).connect(analyser);

  state.recording = true;
  micBtn.classList.add("is-recording");
  micBtn.setAttribute("aria-pressed", "true");
  micBtn.setAttribute("aria-label", "Stop recording");
  setIcon(micIcon, "stop");
  micHint.textContent = "Listening… tap again when you’re done.";
  setStatus("recording", "Recording");
  setWaveMode("live");

  recordStart = performance.now();
  recTimer.hidden = false;
  recTimer.textContent = `0:00 of ${formatTime(state.maxSeconds)}`;
  timerId = setInterval(() => {
    const elapsed = (performance.now() - recordStart) / 1000;
    recTimer.textContent = `${formatTime(elapsed)} of ${formatTime(state.maxSeconds)}`;
    if (elapsed >= state.maxSeconds) stopRecording();   // Gnani allows 30 seconds at most
  }, 200);
}

function releaseMicrophone() {
  clearInterval(timerId);
  if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
  if (recordCtx) recordCtx.close().catch(() => {});
  mediaStream = null; recordCtx = null; analyser = null;
}

function stopRecording() {
  if (!state.recording || !recorder) return;
  state.recording = false;

  micBtn.classList.remove("is-recording");
  micBtn.setAttribute("aria-pressed", "false");
  micBtn.setAttribute("aria-label", "Start recording");
  setIcon(micIcon, "mic");
  recTimer.hidden = true;
  setWaveMode("pulse");

  const mimeType = recorder.mimeType;
  recorder.addEventListener("stop", () => {
    releaseMicrophone();
    processRecording(new Blob(recordedChunks, { type: mimeType }), recordLang);
  }, { once: true });
  recorder.stop();
}

/* Turn any browser recording (WebM, MP4...) into a mono 16 kHz WAV that Prisma accepts */
async function toWav16k(blob) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const OfflineCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const arrayBuffer = await blob.arrayBuffer();

  const ctx = new AudioCtx();
  let decoded;
  try {
    decoded = await new Promise((resolve, reject) => {
      const maybePromise = ctx.decodeAudioData(arrayBuffer, resolve, reject);
      if (maybePromise && maybePromise.catch) maybePromise.catch(() => {});
    });
  } finally {
    ctx.close().catch(() => {});
  }

  const frames = Math.max(1, Math.ceil(decoded.duration * TARGET_RATE));
  const offline = new OfflineCtx(1, frames, TARGET_RATE);       // 1 channel = mono
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const rendered = await offline.startRendering();

  let samples = rendered.getChannelData(0);
  samples = samples.slice(0, Math.floor(state.maxSeconds * TARGET_RATE));   // never longer than the limit

  let peak = 0;
  for (let i = 0; i < samples.length; i++) {
    const value = Math.abs(samples[i]);
    if (value > peak) peak = value;
  }
  return { blob: encodeWav(samples, TARGET_RATE), duration: samples.length / TARGET_RATE, peak };
}

function encodeWav(samples, sampleRate) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const writeText = (offset, text) => { for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i)); };

  writeText(0, "RIFF");  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");  writeText(12, "fmt ");
  view.setUint32(16, 16, true);              // size of the format block
  view.setUint16(20, 1, true);               // 1 = plain PCM
  view.setUint16(22, 1, true);               // 1 channel (mono)
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);  // bytes per second
  view.setUint16(32, 2, true);               // bytes per sample
  view.setUint16(34, 16, true);              // 16 bits
  writeText(36, "data"); view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

async function processRecording(rawBlob, lang) {
  setBusy(true);
  setStatus("processing", "Transcribing");
  micHint.textContent = "Working on it…";

  try {
    if (rawBlob.size === 0) throw new ApiProblem("Nothing was recorded. Please try again.", "empty_audio");

    let wav;
    try {
      wav = await toWav16k(rawBlob);
    } catch {
      throw new ApiProblem("This browser couldn’t read the recording. Try Chrome or Edge, or type your question.", "decode");
    }
    if (wav.duration < MIN_SECONDS) {
      throw new ApiProblem("That was too short. Tap the microphone, speak for a little longer, then tap again.", "too_short");
    }
    if (wav.peak < SILENCE_PEAK) {   // checked here so silent audio never costs credits
      throw new ApiProblem("I couldn’t hear anything. Check that the right microphone is selected and try again.", "silent");
    }

    const form = new FormData();
    form.append("audio", wav.blob, "recording.wav");
    form.append("language", lang);
    const data = await api("/api/transcribe", { method: "POST", body: form });

    pendingRecordingId = data.recording_id || null;   // the server saved this recording for "Recent voice sessions"
    setBusy(false);
    await askQuestion(data.text, "voice");            // the recognised text appears as your message first
  } catch (error) {
    setBusy(false);
    showError(error.message);
  } finally {
    micHint.textContent = "Tap the microphone and ask your question.";
    if (!state.recording) setWaveMode(isPlaying() ? "pulse" : "idle");
  }
}

/* ---------- 7. Waveform (canvas) ---------- */
const canvas = $("waveform");
const g = canvas.getContext("2d");
let waveMode = "idle";
let rafId = null;

function sizeCanvas() {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * ratio);
  canvas.height = Math.round(canvas.clientHeight * ratio);
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function drawWave(time) {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  g.clearRect(0, 0, w, h);

  const barW = 4, gap = 6, step = barW + gap;
  const count = Math.max(3, Math.floor(w / step));
  const mid = (count - 1) / 2;
  const startX = (w - count * step + gap) / 2;

  const gradient = g.createLinearGradient(0, 0, w, 0);
  gradient.addColorStop(0, "#a79cf5");
  gradient.addColorStop(0.5, "#6c63ec");
  gradient.addColorStop(1, "#4f8cf0");
  g.fillStyle = gradient;

  const live = waveMode === "live" && analyser;
  if (live) analyser.getByteFrequencyData(freqData);

  for (let i = 0; i < count; i++) {
    const d = Math.abs(i - mid) / mid;              // 0 at the centre, 1 at the edges
    let level;
    if (live) {
      level = Math.min(1, Math.pow(freqData[1 + Math.floor(d * 35)] / 255, 0.8) * 1.5);
    } else if (waveMode === "pulse") {
      level = reduceMotion ? 0.25 : 0.22 + 0.2 * Math.sin(time / 260 - d * 7);
    } else {
      level = 0.05 + 0.025 * Math.sin(i * 0.9);     // calm, nearly flat line when idle
    }
    const envelope = 1 - 0.55 * d;                  // taller near the microphone, like the design
    const barH = Math.max(4, level * envelope * h * 0.9);
    const x = startX + i * step;
    const y = (h - barH) / 2;
    g.beginPath();
    if (g.roundRect) g.roundRect(x, y, barW, barH, 2); else g.rect(x, y, barW, barH);
    g.fill();
  }
}

function waveLoop(time) {
  drawWave(time);
  rafId = waveMode === "idle" ? null : requestAnimationFrame(waveLoop);
}

function setWaveMode(mode) {
  waveMode = mode;
  if (mode === "idle" || (mode === "pulse" && reduceMotion)) {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    drawWave(0);
  } else if (!rafId) {
    rafId = requestAnimationFrame(waveLoop);
  }
}

window.addEventListener("resize", () => { sizeCanvas(); drawWave(performance.now()); });

/* ---------- 8. Ask a question (typed or spoken) ---------- */
async function askQuestion(text, inputType) {
  if (state.busy) return;
  setBusy(true);
  setStatus("processing", "Thinking");
  setWaveMode("pulse");

  const userRow = addMessage({ role: "user", text, inputType });
  const typingRow = addTyping();

  try {
    const data = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        language: state.lang,
        input_type: inputType,
        voice_output: state.voiceOutput,
        conversation_id: state.conversationId,
        recording_id: inputType === "voice" ? pendingRecordingId : null,   // links the saved recording to this question
      }),
    });

    typingRow.remove();
    state.conversationId = data.conversation.id;

    const answer = data.assistant_message;
    const answerRow = addMessage({
      role: "assistant",
      text: answer.content,
      id: answer.id,
      conversationId: data.conversation.id,
    });

    if (data.audio) {
      playAudioBlock(data.audio, answer.id, `Answer: ${answer.content.slice(0, 60)}…`);
    } else if (data.audio_error) {
      addNote(answerRow, `Voice unavailable: ${data.audio_error}`);   // the written answer still works
    }
    refreshSidebar();
   } catch (error) {
    typingRow.remove();
    userRow.remove();                              // nothing was saved, so nothing stays on screen
    if (!textInput.value) textInput.value = text;  // let the user retry with one click
    if (error.code === "not_found") {              // the server restarted, so it forgot this conversation
      state.conversationId = null;
      refreshSidebar();
      showError("This conversation was reset because the server restarted. Press send to ask again.");
    } else {
      showError(error.message);
    }
  } finally {
    pendingRecordingId = null;
    setBusy(false);
    restStatus();
    if (!isPlaying()) setWaveMode("idle");
  }
}

/* ---------- 9. History, stats and voice sessions ---------- */
async function loadStats() {
  try {
    const { stats } = await api("/api/stats");
    $("stat-conversations").textContent = stats.conversations;
    $("stat-questions").textContent = stats.questions;
    $("stat-voice").textContent = stats.voice_questions;
  } catch { /* the numbers simply stay as they are */ }
}

function renderHistory(conversations) {
  const list = $("history-list");
  list.replaceChildren();

  conversations.forEach((conv) => {
    const row = el("li", "history-row" + (conv.id === state.conversationId ? " is-active" : ""));

    const open = el("button", "history-item");
    open.type = "button";
    const count = conv.message_count;
    open.append(
      el("span", "history-title", conv.title),
      el("span", "history-meta", `${state.langNames[conv.language] || conv.language}, ${count} message${count === 1 ? "" : "s"}`)
    );
    open.addEventListener("click", () => openConversation(conv.id));

    const del = el("button", "history-delete");
    del.type = "button";
    del.setAttribute("aria-label", `Delete conversation: ${conv.title}`);
    del.appendChild(makeIcon("trash", 16));
    del.addEventListener("click", () => deleteConversation(conv.id));

    row.append(open, del);
    list.appendChild(row);
  });
}

async function loadHistory() {
  try {
    const { conversations } = await api("/api/conversations");
    renderHistory(conversations);
  } catch { /* leave the list as it is */ }
}

/* Recent voice sessions: your saved voice questions, newest first */
function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function renderSessions(recordings) {
  const list = $("sessions-list");
  list.replaceChildren();

  recordings.forEach((rec) => {
    const row = el("li", "session-row");
    row.dataset.id = rec.id;

    const play = el("button", "round-btn");
    play.type = "button";
    play.setAttribute("aria-label", "Play this recording");
    play.appendChild(makeIcon("play", 18, true));
    play.addEventListener("click", () => playSession(rec.id, rec.text));

    const open = el("button", "session-text");
    open.type = "button";
    open.append(
      el("span", "session-title", rec.text),
      el("span", "session-meta", `${state.langNames[rec.language] || rec.language}, ${formatDate(rec.created_at)}`)
    );
    open.addEventListener("click", () => openConversation(rec.conversation_id));
    
    const del = el("button", "session-delete");
    del.type = "button";
    del.setAttribute("aria-label", `Delete recording: ${rec.text}`);
    del.appendChild(makeIcon("trash", 16));
    del.addEventListener("click", () => deleteSession(rec.id));

    // Decorative bars (shaped from the id), like the reference design. They are not the real waveform.
    const bars = el("span", "session-bars");
    bars.setAttribute("aria-hidden", "true");
    for (let k = 0; k < 22; k++) {
      const bar = document.createElement("i");
      bar.style.height = `${6 + Math.abs(Math.sin(rec.id * 13 + k * 1.7)) * 20}px`;
      bars.appendChild(bar);
    }

    //row.append(play, open, bars, el("span", "session-duration", formatTime(rec.duration)));
    row.append(play, open, bars, el("span", "session-duration", formatTime(rec.duration)), del);
    list.appendChild(row);
  });
  refreshListenButtons();   // keep the play/pause icons correct after the list is rebuilt
}

async function loadSessions() {
  try {
    const { recordings } = await api("/api/recordings");
    renderSessions(recordings);
  } catch { /* leave the list as it is */ }
}

async function playSession(recId, text) {
  const key = `rec-${recId}`;
  if (currentMessageId === key && player.src) {            // same recording: play or pause
    if (player.paused) player.play().catch(() => {}); else player.pause();
    return;
  }
  try {
    if (!audioCache.has(key)) {
      const response = await fetch(`/api/recordings/${recId}/audio`);
      if (!response.ok) throw new Error("That recording is no longer available.");
      audioCache.set(key, await response.blob());
    }
    await playBlob(audioCache.get(key), key, `You said: ${text.length > 60 ? text.slice(0, 60) + "…" : text}`);
  } catch (error) {
    showToast(error.message);
  }
}
async function deleteSession(recId) {
  if (!window.confirm("Delete this voice recording? The question and answer stay in your conversation.")) return;
  try {
    await api(`/api/recordings/${recId}`, { method: "DELETE" });
    const key = `rec-${recId}`;
    if (currentMessageId === key) player.pause();   // stop it if it is playing right now
    audioCache.delete(key);
    loadSessions();
  } catch (error) {
    showError(error.message);
  }
}

function refreshSidebar() { loadHistory(); loadStats(); loadSessions(); }

async function openConversation(id) {
  if (state.busy || state.recording) return;
  try {
    const data = await api(`/api/conversations/${id}`);
    state.conversationId = id;
    audioCache.clear();
    chatEl.replaceChildren();
    data.messages.forEach((m) => addMessage({
      role: m.role, text: m.content, id: m.id, conversationId: id, inputType: m.input_type,
    }));
    setLanguage(data.conversation.language);
    closeSidebar();
    loadHistory();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteConversation(id) {
  if (!window.confirm("Delete this conversation? Its voice recordings will be deleted too. This can’t be undone.")) return;
  try {
    await api(`/api/conversations/${id}`, { method: "DELETE" });
    if (id === state.conversationId) startNewConversation();
    refreshSidebar();
  } catch (error) {
    showError(error.message);
  }
}

function startNewConversation() {
  if (state.recording) return;
  player.pause();
  audioCache.clear();
  state.conversationId = null;
  showNewConversationView();
  closeSidebar();
  loadHistory();
  textInput.focus();
}

/* ---------- 10. Page controls and start-up ---------- */
function setLanguage(key) {
  if (!document.querySelector(`.lang-card[data-lang="${key}"]`)) return;
  state.lang = key;
  savePref("voicemate_lang", key);
  document.querySelectorAll(".lang-card").forEach((card) => {
    const active = card.dataset.lang === key;
    card.classList.toggle("is-active", active);
    card.setAttribute("aria-checked", String(active));
  });
}

function openSidebar() {
  $("sidebar").classList.add("is-open");
  $("sidebar-overlay").hidden = false;
  $("menu-btn").setAttribute("aria-expanded", "true");
}
function closeSidebar() {
  $("sidebar").classList.remove("is-open");
  $("sidebar-overlay").hidden = true;
  $("menu-btn").setAttribute("aria-expanded", "false");
}

micBtn.addEventListener("click", () => {
  if (state.busy) return;
  if (state.recording) stopRecording(); else startRecording();
});

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text) return;
  if (state.recording) { showToast("Finish your recording first, then send.", "info"); return; }
  textInput.value = "";
  askQuestion(text, "text");
});

document.querySelectorAll(".lang-card").forEach((card) => {
  card.addEventListener("click", () => setLanguage(card.dataset.lang));
});

$("voice-toggle").addEventListener("change", (event) => {
  state.voiceOutput = event.target.checked;
  savePref("voicemate_voice", state.voiceOutput ? "on" : "off");
  if (!state.voiceOutput && isPlaying()) player.pause();
});

$("new-chat-btn").addEventListener("click", startNewConversation);
$("menu-btn").addEventListener("click", openSidebar);
$("sidebar-overlay").addEventListener("click", closeSidebar);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSidebar(); });

async function init() {
  setLanguage(loadPref("voicemate_lang", "en"));
  state.voiceOutput = loadPref("voicemate_voice", "on") === "on";
  $("voice-toggle").checked = state.voiceOutput;

  sizeCanvas();
  setWaveMode("idle");

  try {
    const config = await api("/api/config");
    state.maxSeconds = config.limits.max_audio_seconds;
    config.languages.forEach((language) => { state.langNames[language.key] = language.label; });
  } catch (error) {
    showError(error.message);
  }
  refreshSidebar();
}

init();