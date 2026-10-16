(() => {
"use strict";

// Core game logic is intentionally language-neutral. UI text comes from i18n.js.
const $ = id => document.getElementById(id);
const W = 960;
const H = 720;
const TAU = Math.PI * 2;
const SUPPORTED_LANGS = ["ru", "en", "zh", "kk", "ko"];

const canvas = $("game");
const ctx = canvas.getContext("2d");
const stage = $("stage-shell");
const menu = $("menu");
const overlay = $("overlay");
const overlayTitle = $("overlay-title");
const overlaySub = $("overlay-sub");
const overlayButton = $("overlay-button");
const video = $("camera-video");
const fallbackImg = $("camera-img");
const translations = window.SMILEGUN_I18N || {};

let lang = "en";
let CFG = {
  level_duration: 30,
  level_difficulty: 1,
  smile_threshold: 1.55,
  mouth_open_threshold: 0.3,
  hand_gain: 1.15,
  super_blink_gain: 0.2,
  round_time_cap: 50,
  smile_time_min: 10,
  smile_time_max: 25,
  smile_armor_seconds: 45,
  max_lives: 5,
  bullet_speed: 11,
  best_score: 0,
};

let volume = 0.55;
let selectedDifficulty = 1;
let cameraOn = false;
let browserMode = false;
let browserStream = null;
let pumpToken = 0;
let stateTimer = null;
let lastState = {};
let lastIds = {};
let running = false;
let starting = false;
let paused = false;
let pauseStarted = 0;
let roundEnd = 0;
let roundIndex = 0;
let score = 0;
let bestScore = 0;
let lives = 3;
let armorUntil = 0;
let ghost = false;
let lastPeace = false;
let hitInvulnUntil = 0;
let flashUntil = 0;
let toastTimer = 0;
let demoMode = false;
let demoTime = 37;

let player = {x: W / 2, y: H * 0.78, size: 30};
let bullets = [];
let enemyBullets = [];
let targets = [];
let enemies = [];
let hazards = [];
let particles = [];
let spawnClock = 0;
let enemyShotClock = 0;
let stars = [];
let scrollX = 0;
let lastFrame = performance.now();

const MODES = [
  {key: "targets", nameKey: "mode_targets", descKey: "mode_targets_desc"},
  {key: "galaxy", nameKey: "mode_galaxy", descKey: "mode_galaxy_desc"},
  {key: "dodge", nameKey: "mode_dodge", descKey: "mode_dodge_desc"},
  {key: "side", nameKey: "mode_side", descKey: "mode_side_desc"},
];

const soundFiles = {
  shot: "blaster.mp3",
  spread: "spread.mp3",
  bonus: "bonus.mp3",
  charge: "charge.mp3",
  super: "super.mp3",
  hit: "hit.mp3",
  hurt: "hurt.mp3",
  pause: "pause.mp3",
  ghost: "ghost.mp3",
  start: "start.mp3",
  level: "level_complete.mp3",
  gameover: "gameover.mp3",
  victory: "victory.mp3",
  select: "select.mp3",
  camera: "camera_on.mp3",
  stop: "stop.mp3",
};

function t(key, vars = {}) {
  const pack = translations[lang] || translations.en || {};
  const fallback = translations.en || {};
  let value = pack[key] ?? fallback[key] ?? key;
  for (const [name, replacement] of Object.entries(vars)) {
    value = value.replaceAll(`{${name}}`, String(replacement));
  }
  return value;
}

function chooseInitialLanguage() {
  const query = new URLSearchParams(location.search).get("lang") || window.__SMILEGUN_LANG;
  let saved = null;
  try { saved = localStorage.getItem("smilegun_lang"); } catch (_) {}
  const browser = (navigator.language || "").toLowerCase();
  const browserMatch = SUPPORTED_LANGS.find(code => browser === code || browser.startsWith(`${code}-`));
  return SUPPORTED_LANGS.includes(query) ? query : (SUPPORTED_LANGS.includes(saved) ? saved : (browserMatch || "ru"));
}

function setLanguage(code, persist = true) {
  if (!SUPPORTED_LANGS.includes(code)) code = "en";
  lang = code;
  document.documentElement.lang = code === "zh" ? "zh-CN" : code;
  $("language-select").value = code;
  if (persist) { try { localStorage.setItem("smilegun_lang", code); } catch (_) {} }
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  updateDifficultyText();
  setCameraUI();
  updateSuper(lastState);
  refreshHudLanguage();
}

function mode() {
  return MODES[roundIndex] || MODES[0];
}

function modeName(item = mode()) {
  return t(item.nameKey);
}

function modeDesc(item = mode()) {
  return t(item.descKey);
}

function refreshHudLanguage() {
  const arm = Math.max(0, (armorUntil - performance.now()) / 1000);
  $("hud-armor").textContent = arm > 0 ? `${t("armor")} ${Math.ceil(arm)}s` : `${t("armor")} —`;
  $("hud-ghost").textContent = ghost ? `${t("ghost")} ON` : `${t("ghost")} —`;
  if (demoMode) $("hud-round").textContent = `${roundIndex + 1}/4 ${modeName()}`;
}

function sound(name, rate = 1) {
  const file = soundFiles[name];
  if (!file || volume <= 0 || demoMode) return;
  const audio = new Audio(`/static/${file}`);
  audio.volume = volume;
  audio.playbackRate = Math.max(0.65, Math.min(1.8, rate));
  audio.play().catch(() => {});
}

function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function rand(a, b) { return a + Math.random() * (b - a); }
function randi(a, b) { return Math.floor(rand(a, b + 1)); }

function fitCanvas() {
  const r = stage.getBoundingClientRect();
  const s = Math.max(0.1, Math.min((r.width - 6) / W, (r.height - 6) / H));
  canvas.style.width = `${Math.floor(W * s)}px`;
  canvas.style.height = `${Math.floor(H * s)}px`;
}
window.addEventListener("resize", fitCanvas);

function fmt(key, value) {
  if (key === "super_blink_gain") return `${Math.round(value * 100)}%`;
  if (key === "level_duration") return `${Math.round(value)} s`;
  if (key === "volume") return `${Math.round(value * 100)}%`;
  return Number(value).toFixed(2);
}

function bindSlider(key, local = false) {
  const input = $(key);
  const out = $(`v-${key}`);
  if (!input) return;
  const apply = () => {
    const value = Number(input.value);
    if (local) volume = value;
    else {
      CFG[key] = value;
      pushConfig({[key]: value});
    }
    out.textContent = fmt(key, value);
    if (key === "level_duration") updateDifficultyText();
  };
  input.value = local ? volume : CFG[key];
  out.textContent = fmt(key, Number(input.value));
  input.addEventListener("input", apply);
}

let configTimer = 0;
let configPatch = {};
function pushConfig(patch) {
  Object.assign(configPatch, patch);
  clearTimeout(configTimer);
  configTimer = setTimeout(async () => {
    const body = configPatch;
    configPatch = {};
    try {
      const response = await fetch("/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      if (response.ok) Object.assign(CFG, await response.json());
    } catch (_) {}
  }, 120);
}

async function loadConfig() {
  try {
    const response = await fetch("/config");
    if (response.ok) Object.assign(CFG, await response.json());
  } catch (_) {}
  bestScore = Number(CFG.best_score) || 0;
  $("menu-best").textContent = bestScore;
  selectedDifficulty = clamp(Number(CFG.level_difficulty) || 1, 1, 5);
  document.querySelectorAll("[data-diff]").forEach(button => {
    button.classList.toggle("active", Number(button.dataset.diff) === selectedDifficulty);
  });
  ["smile_threshold", "mouth_open_threshold", "hand_gain", "super_blink_gain", "level_duration"].forEach(key => bindSlider(key));
  bindSlider("volume", true);
  updateDifficultyText();
}

function updateDifficultyText() {
  const density = t(`density_${selectedDifficulty}`);
  const el = $("difficulty-info");
  if (el) el.textContent = t("difficulty_info", {density, seconds: CFG.level_duration});
}

document.querySelectorAll("[data-diff]").forEach(button => button.addEventListener("click", () => {
  selectedDifficulty = Number(button.dataset.diff);
  CFG.level_difficulty = selectedDifficulty;
  document.querySelectorAll("[data-diff]").forEach(other => other.classList.toggle("active", other === button));
  pushConfig({level_difficulty: selectedDifficulty});
  updateDifficultyText();
  sound("select");
}));

$("language-select").addEventListener("change", event => setLanguage(event.target.value));

function cameraError(message = "") {
  $("camera-error").textContent = message;
  $("menu-status").textContent = message;
}

function setCameraUI() {
  $("camera-start").disabled = cameraOn;
  $("camera-stop").disabled = !cameraOn;
  $("camera-state").textContent = cameraOn ? (browserMode ? t("browser_camera") : t("opencv_fallback")) : t("camera_disabled");
  $("camera-source").textContent = cameraOn ? (browserMode ? t("camera_source_browser") : t("camera_source_opencv")) : t("camera_off");
  $("camera-source").classList.toggle("on", cameraOn);
}

function stopBrowserLocal() {
  pumpToken++;
  if (browserStream) browserStream.getTracks().forEach(track => track.stop());
  browserStream = null;
  video.srcObject = null;
  browserMode = false;
}

function canvasBlob(c, quality = 0.65) {
  return new Promise(resolve => c.toBlob(resolve, "image/jpeg", quality));
}

async function pumpFrames(token) {
  const pumpCanvas = document.createElement("canvas");
  const pumpCtx = pumpCanvas.getContext("2d", {alpha: false});
  while (cameraOn && browserMode && token === pumpToken) {
    if (video.readyState >= 2 && video.videoWidth) {
      const sw = video.videoWidth;
      const sh = video.videoHeight;
      const dw = Math.min(480, sw);
      const dh = Math.max(1, Math.round(sh * dw / sw));
      if (pumpCanvas.width !== dw || pumpCanvas.height !== dh) {
        pumpCanvas.width = dw;
        pumpCanvas.height = dh;
      }
      pumpCtx.drawImage(video, 0, 0, dw, dh);
      const blob = await canvasBlob(pumpCanvas);
      if (blob) {
        try {
          const response = await fetch("/camera/browser/frame", {
            method: "POST",
            headers: {"Content-Type": "image/jpeg"},
            body: blob,
          });
          if (!response.ok) throw new Error((await response.json()).error || "frame rejected");
        } catch (error) {
          if (token === pumpToken) cameraError(t("frame_error", {error: error.message}));
        }
      }
    }
    await new Promise(resolve => setTimeout(resolve, 75));
  }
}

async function startCamera() {
  if (cameraOn) return true;
  cameraError("");
  fallbackImg.style.display = "none";
  video.style.display = "block";
  const localHost = ["localhost", "127.0.0.1", "::1"].includes(location.hostname);
  if (!window.isSecureContext && !localHost) {
    cameraError(t("phone_https"));
    return false;
  }
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error(t("gum_unavailable"));
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: {ideal: "user"},
        width: {ideal: 640},
        height: {ideal: 480},
        frameRate: {ideal: 20, max: 24},
      },
    });
    browserStream = stream;
    video.srcObject = stream;
    await video.play().catch(() => {});
    const response = await fetch("/camera/browser/start", {method: "POST"});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || t("backend_camera_rejected"));
    browserMode = true;
    cameraOn = true;
    const token = ++pumpToken;
    setCameraUI();
    sound("camera");
    pumpFrames(token);
    startStatePolling();
    return true;
  } catch (error) {
    stopBrowserLocal();
    if (!localHost) {
      cameraError(t("phone_camera_failed", {error: error.message}));
      return false;
    }
    cameraError(t("browser_camera_failed", {error: error.message}));
    try {
      const response = await fetch("/camera/start", {method: "POST"});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || t("opencv_error"));
      browserMode = false;
      cameraOn = true;
      video.style.display = "none";
      fallbackImg.style.display = "block";
      fallbackImg.src = `/video_feed?ts=${Date.now()}`;
      setCameraUI();
      startStatePolling();
      return true;
    } catch (fallbackError) {
      cameraOn = false;
      setCameraUI();
      cameraError(t("camera_unavailable", {error: fallbackError.message}));
      return false;
    }
  }
}

async function stopCamera() {
  if (!cameraOn) return;
  stopBrowserLocal();
  cameraOn = false;
  try { await fetch("/camera/stop", {method: "POST"}); } catch (_) {}
  fallbackImg.src = "";
  fallbackImg.style.display = "none";
  video.style.display = "block";
  setCameraUI();
  sound("stop");
}

$("camera-start").addEventListener("click", startCamera);
$("camera-stop").addEventListener("click", stopCamera);

function chip(id, on, text) {
  const el = $(id);
  el.textContent = text;
  el.classList.toggle("on", Boolean(on));
}

function startStatePolling() {
  if (stateTimer) return;
  stateTimer = setInterval(pollState, 90);
  pollState();
}

function seedIds(state) {
  lastIds = {
    fist: Number(state.fist_shot_id) || 0,
    mouth: Number(state.mouth_shot_id) || 0,
    smile: Number(state.smile_id ?? state.smile_shot_id) || 0,
    blink: Number(state.blink_id) || 0,
    super: Number(state.super_id) || 0,
    palms: Number(state.two_palms_id) || 0,
  };
}

async function pollState() {
  if (!cameraOn) return;
  try {
    const response = await fetch("/state", {cache: "no-store"});
    if (!response.ok) return;
    const state = await response.json();
    lastState = state;
    chip("chip-face", state.face, state.face ? t("face_ok") : t("face_none"));
    chip("chip-hand", state.hand_present, state.hand_present ? t("hand_count", {count: state.hand_count || 1}) : t("hand_none"));
    chip("chip-mouth", state.mouth_open, state.mouth_open ? t("mouth_open") : t("mouth_none"));
    chip("chip-smile", state.smile, state.smile ? t("smile_yes") : t("smile_none"));
    chip("chip-peace", state.peace, state.peace ? t("ghost_on") : t("ghost_none"));
    $("chip-fps").textContent = `FPS ${(Number(state.fps) || 0).toFixed(0)}`;
    if (state.error) cameraError(state.error);
    if (state.hand_present) {
      player.x = clamp((Number(state.aim_x) || 0.5) * W, 18, W - 18);
      player.y = clamp((Number(state.aim_y) || 0.5) * H, 45, H - 18);
    }
    ghost = Boolean(state.peace);
    if (ghost && !lastPeace) sound("ghost");
    lastPeace = ghost;
    updateSuper(state);
    if (lastIds.fist === undefined) {
      seedIds(state);
      return;
    }
    consumeEvents(state);
  } catch (_) {}
}

function consumeEvents(state) {
  const id = (key, fallback) => Number(state[key] ?? fallback) || 0;
  let count = id("fist_shot_id", 0) - lastIds.fist;
  if (count > 0 && running && !paused) for (let i = 0; i < Math.min(count, 3); i++) fireSingle();
  lastIds.fist = id("fist_shot_id", 0);

  count = id("mouth_shot_id", 0) - lastIds.mouth;
  if (count > 0 && running && !paused) for (let i = 0; i < Math.min(count, 2); i++) fireSpread();
  lastIds.mouth = id("mouth_shot_id", 0);

  const smileId = id("smile_id", state.smile_shot_id) - lastIds.smile;
  if (smileId > 0 && running && !paused) for (let i = 0; i < Math.min(smileId, 2); i++) applySmileBonus();
  lastIds.smile = id("smile_id", state.smile_shot_id);

  const blinkId = id("blink_id", 0) - lastIds.blink;
  if (blinkId > 0 && running) for (let i = 0; i < Math.min(blinkId, 3); i++) sound("charge", 0.85 + (Number(state.super_charge) || 0) * 0.55);
  lastIds.blink = id("blink_id", 0);

  const superId = id("super_id", 0) - lastIds.super;
  if (superId > 0 && running && !paused) triggerSuper();
  lastIds.super = id("super_id", 0);

  const palmsId = id("two_palms_id", 0) - lastIds.palms;
  if (palmsId > 0 && running) togglePause();
  lastIds.palms = id("two_palms_id", 0);
}

function updateSuper(state = {}) {
  const charge = clamp(Number(state.super_charge) || 0, 0, 1);
  const pct = Math.round(charge * 100);
  $("super-fill").style.width = `${pct}%`;
  $("super-label").textContent = state.super_ready ? t("super_ready") : `SUPER ${pct}%`;
  $("super-panel").classList.toggle("super-ready", Boolean(state.super_ready));
  $("hud-effect").textContent = state.super_ready ? t("smile_for_super") : (state.charging ? t("charge_plus") : t("blink_to_charge"));
}

function clearWorld() {
  bullets = [];
  enemyBullets = [];
  targets = [];
  enemies = [];
  hazards = [];
  particles = [];
  spawnClock = 0;
  enemyShotClock = 0;
}

function startCampaign() {
  demoMode = false;
  score = 0;
  lives = 3;
  armorUntil = 0;
  ghost = false;
  roundIndex = 0;
  $("hud-score").textContent = score;
  beginRound(0);
}

function beginRound(index) {
  roundIndex = index;
  clearWorld();
  running = false;
  paused = false;
  document.body.classList.add("playing");
  menu.classList.add("hidden");
  overlay.classList.remove("hidden");
  overlayButton.classList.add("hidden");
  overlayTitle.textContent = `${index + 1} · ${modeName()}`;
  overlaySub.textContent = modeDesc();
  $("hud-round").textContent = `${index + 1}/4 ${modeName()}`;
  let count = 3;
  const tick = () => {
    if (!menu.classList.contains("hidden")) return;
    if (count > 0) {
      overlayTitle.textContent = String(count--);
      sound("select");
      setTimeout(tick, 520);
    } else {
      overlay.classList.add("hidden");
      overlayButton.classList.remove("hidden");
      running = true;
      const base = clamp(Number(CFG.level_duration) || 30, 10, Math.min(50, Number(CFG.round_time_cap) || 50));
      roundEnd = performance.now() + base * 1000;
      lastFrame = performance.now();
      sound("start");
    }
  };
  setTimeout(tick, 550);
}

function finishRound() {
  if (!running) return;
  running = false;
  clearWorld();
  sound("level");
  if (roundIndex < MODES.length - 1) {
    overlay.classList.remove("hidden");
    overlayTitle.textContent = t("round_passed");
    overlaySub.textContent = `${modeName()} · ${t("score")} ${score}`;
    overlayButton.textContent = t("next");
    overlayButton.onclick = () => beginRound(roundIndex + 1);
  } else {
    saveBest();
    sound("victory");
    overlay.classList.remove("hidden");
    overlayTitle.textContent = t("victory");
    overlaySub.textContent = t("all_rounds", {score});
    overlayButton.textContent = t("to_menu");
    overlayButton.onclick = showMenu;
  }
}

function gameOver() {
  running = false;
  paused = false;
  clearWorld();
  saveBest();
  sound("gameover");
  overlay.classList.remove("hidden");
  overlayTitle.textContent = t("game_over");
  overlaySub.textContent = t("score_best", {score, best: Math.max(score, bestScore)});
  overlayButton.textContent = t("to_menu");
  overlayButton.onclick = showMenu;
}

function showMenu() {
  demoMode = false;
  running = false;
  paused = false;
  clearWorld();
  document.body.classList.remove("playing");
  menu.classList.remove("hidden");
  overlay.classList.remove("hidden");
  overlayTitle.textContent = "SMILE·GUN";
  overlaySub.textContent = t("ready");
  overlayButton.textContent = t("continue");
  saveBest();
}

function saveBest() {
  if (score <= bestScore || demoMode) return;
  bestScore = score;
  $("menu-best").textContent = bestScore;
  fetch("/score", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({score}),
  }).catch(() => {});
}

async function play() {
  if (starting || menu.classList.contains("hidden")) return;
  starting = true;
  $("play").disabled = true;
  $("menu-status").textContent = "";
  const ok = await startCamera();
  starting = false;
  $("play").disabled = false;
  if (!ok) return;
  lastIds = {};
  startCampaign();
}

$("play").addEventListener("click", play);
$("round-stop").addEventListener("click", showMenu);
$("exit-game").addEventListener("click", showMenu);

function togglePause() {
  if (!running) return;
  if (!paused) {
    paused = true;
    pauseStarted = performance.now();
    overlay.classList.remove("hidden");
    overlayTitle.textContent = t("pause");
    overlaySub.textContent = t("pause_help");
    overlayButton.textContent = t("continue");
    overlayButton.onclick = togglePause;
    sound("pause");
  } else {
    const now = performance.now();
    roundEnd += now - pauseStarted;
    paused = false;
    overlay.classList.add("hidden");
    sound("pause");
  }
}

function showToast(text) {
  const toast = $("bonus-toast");
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 1800);
}

function applySmileBonus() {
  const now = performance.now();
  const bonus = randi(Number(CFG.smile_time_min) || 10, Number(CFG.smile_time_max) || 25);
  const cap = Math.min(50, Number(CFG.round_time_cap) || 50);
  const remain = Math.max(0, (roundEnd - now) / 1000);
  const newRemain = Math.min(cap, remain + bonus);
  const added = Math.max(0, Math.round(newRemain - remain));
  roundEnd = now + newRemain * 1000;
  lives = Math.min(Number(CFG.max_lives) || 5, lives + 1);
  armorUntil = now + (Number(CFG.smile_armor_seconds) || 45) * 1000;
  showToast(t("smile_bonus", {seconds: added, armor: CFG.smile_armor_seconds || 45}));
  sound("bonus");
  spawnBurst(player.x, player.y, "#55ff8a", 24);
}

function fireSingle() {
  const side = mode().key === "side";
  bullets.push({x: player.x, y: player.y, vx: side ? 620 : 0, vy: side ? 0 : -620, r: 4, life: 2});
  flashUntil = performance.now() + 100;
  sound("shot");
}

function fireSpread() {
  const side = mode().key === "side";
  const charge = clamp(Number(lastState.super_charge) || 0, 0, 1);
  const count = 5 + Math.round(charge * 6);
  const base = side ? 0 : -Math.PI / 2;
  const spread = 0.68 + charge * 0.45;
  for (let i = 0; i < count; i++) {
    const u = count === 1 ? 0 : (i / (count - 1) - 0.5);
    const angle = base + u * spread;
    bullets.push({x: player.x, y: player.y, vx: Math.cos(angle) * 650, vy: Math.sin(angle) * 650, r: 4.5, life: 1.7, spread: true});
  }
  flashUntil = performance.now() + 160;
  sound("spread", 0.9 + charge * 0.25);
}

function triggerSuper() {
  const destroyed = targets.length + enemies.length + hazards.length;
  score += destroyed * 15 + 50;
  targets = [];
  enemies = [];
  hazards = [];
  enemyBullets = [];
  spawnBurst(W / 2, H / 2, "#fff37a", 90);
  flashUntil = performance.now() + 420;
  showToast(t("super_destroyed", {count: destroyed}));
  sound("super");
}

function spawnBurst(x, y, color, count = 12) {
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * TAU;
    const speed = rand(60, 260);
    particles.push({x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, life: rand(0.25, 0.75), color});
  }
}

function damagePlayer() {
  const now = performance.now();
  if (ghost || now < armorUntil || now < hitInvulnUntil) return;
  hitInvulnUntil = now + 1100;
  lives--;
  spawnBurst(player.x, player.y, "#ff5964", 25);
  sound("hurt");
  if (lives <= 0) gameOver();
}

function addScore(points) {
  score += points;
  $("hud-score").textContent = score;
}

function spawnTarget() {
  const r = rand(23, 38) - selectedDifficulty * 1.7;
  targets.push({x: rand(r + 20, W - r - 20), y: rand(95, H * 0.62), r, vx: rand(-80, 80) * (selectedDifficulty > 1 ? 1 : 0), vy: rand(-25, 25), hp: 1});
}

function spawnEnemy() {
  enemies.push({x: rand(45, W - 45), y: -35, r: 20, vx: rand(-55, 55), vy: rand(45, 75) + selectedDifficulty * 8, phase: Math.random() * TAU, hp: selectedDifficulty >= 5 ? 2 : 1});
}

function spawnHazard(side = false) {
  if (side) {
    const gap = rand(120, H - 180);
    const width = rand(38, 70);
    const speed = -rand(170, 230) - selectedDifficulty * 18;
    hazards.push({x: W + 70, y: 0, w: width, h: gap - 70, vx: speed, kind: "wall"});
    hazards.push({x: W + 70, y: gap + 70, w: width, h: H - gap - 70, vx: speed, kind: "wall"});
  } else {
    const r = rand(22, 48);
    hazards.push({x: rand(r, W - r), y: -r, r, vx: rand(-35, 35), vy: rand(130, 210) + selectedDifficulty * 22, kind: "rock"});
  }
}

function enemyShoot(enemy) {
  if (mode().key === "side") {
    enemyBullets.push({x: enemy.x, y: enemy.y, vx: -260 - selectedDifficulty * 20, vy: rand(-35, 35), r: 6, life: 4});
  } else {
    const dx = player.x - enemy.x;
    const dy = player.y - enemy.y;
    const length = Math.hypot(dx, dy) || 1;
    const speed = 200 + selectedDifficulty * 22;
    enemyBullets.push({x: enemy.x, y: enemy.y, vx: dx / length * speed, vy: dy / length * speed, r: 6, life: 4});
  }
}

function circleHit(a, b, extra = 0) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  const r = (a.r || a.size * 0.5) + (b.r || b.size * 0.5) + extra;
  return dx * dx + dy * dy <= r * r;
}

function pointRectCircle(ball, object) {
  const cx = clamp(ball.x, object.x, object.x + object.w);
  const cy = clamp(ball.y, object.y, object.y + object.h);
  const dx = ball.x - cx;
  const dy = ball.y - cy;
  return dx * dx + dy * dy <= (ball.r || 4) * (ball.r || 4);
}

function playerHitsRect(object) {
  const half = player.size / 2;
  return player.x + half > object.x && player.x - half < object.x + object.w && player.y + half > object.y && player.y - half < object.y + object.h;
}

function updateWorld(dt, now) {
  const currentMode = mode().key;
  const difficulty = selectedDifficulty;
  spawnClock -= dt;
  enemyShotClock -= dt;

  if (spawnClock <= 0) {
    if (currentMode === "targets") {
      if (targets.length < 3 + difficulty) spawnTarget();
      spawnClock = Math.max(0.35, 1.15 - difficulty * 0.11);
    } else if (currentMode === "galaxy") {
      spawnEnemy();
      spawnClock = Math.max(0.38, 1.25 - difficulty * 0.12);
    } else if (currentMode === "dodge") {
      spawnHazard(false);
      spawnClock = Math.max(0.25, 0.9 - difficulty * 0.1);
    } else {
      spawnHazard(true);
      if (Math.random() < 0.6) enemies.push({x: W + 30, y: rand(90, H - 90), r: 18, vx: -rand(110, 150), vy: 0, phase: 0, hp: 1});
      spawnClock = Math.max(0.65, 1.45 - difficulty * 0.12);
    }
  }

  if ((currentMode === "galaxy" || currentMode === "side") && enemyShotClock <= 0 && enemies.length) {
    enemyShoot(enemies[Math.floor(Math.random() * enemies.length)]);
    enemyShotClock = Math.max(0.32, 1.25 - difficulty * 0.13);
  }

  for (const target of targets) {
    target.x += target.vx * dt;
    target.y += target.vy * dt;
    if (target.x < target.r || target.x > W - target.r) target.vx *= -1;
    if (target.y < 85 || target.y > H * 0.68) target.vy *= -1;
  }

  for (const enemy of enemies) {
    enemy.phase += dt * 2;
    enemy.x += enemy.vx * dt + (currentMode === "galaxy" ? Math.sin(enemy.phase) * 18 * dt : 0);
    enemy.y += enemy.vy * dt;
    if (currentMode === "side" && enemy.x < -40) {
      enemy.dead = true;
      damagePlayer();
    } else if (currentMode !== "side" && enemy.y > H + 40) {
      enemy.dead = true;
      damagePlayer();
    } else if (circleHit({x: player.x, y: player.y, r: player.size * 0.48}, enemy)) {
      enemy.dead = true;
      damagePlayer();
    }
  }
  enemies = enemies.filter(enemy => !enemy.dead);

  for (const hazard of hazards) {
    hazard.x += (hazard.vx || 0) * dt;
    hazard.y += (hazard.vy || 0) * dt;
    if (hazard.kind === "rock") {
      if (hazard.y > H + hazard.r) hazard.dead = true;
      else if (circleHit({x: player.x, y: player.y, r: player.size * 0.48}, hazard)) {
        hazard.dead = true;
        damagePlayer();
      }
    } else {
      if (hazard.x + hazard.w < 0) hazard.dead = true;
      else if (playerHitsRect(hazard)) damagePlayer();
    }
  }
  hazards = hazards.filter(hazard => !hazard.dead);

  for (const bullet of enemyBullets) {
    bullet.x += bullet.vx * dt;
    bullet.y += bullet.vy * dt;
    bullet.life -= dt;
    if (circleHit({x: player.x, y: player.y, r: player.size * 0.42}, bullet) && !ghost) {
      bullet.life = 0;
      damagePlayer();
    }
  }
  enemyBullets = enemyBullets.filter(bullet => bullet.life > 0 && bullet.x > -30 && bullet.x < W + 30 && bullet.y > -30 && bullet.y < H + 30);

  for (const bullet of bullets) {
    bullet.x += bullet.vx * dt;
    bullet.y += bullet.vy * dt;
    bullet.life -= dt;
    let hit = false;
    for (const target of targets) {
      if (circleHit(bullet, target, 2)) {
        target.dead = true;
        hit = true;
        addScore(10);
        spawnBurst(target.x, target.y, "#ffd95b", 10);
        break;
      }
    }
    if (!hit) for (const enemy of enemies) {
      if (circleHit(bullet, enemy, 2)) {
        enemy.hp--;
        hit = true;
        if (enemy.hp <= 0) {
          enemy.dead = true;
          addScore(15);
          spawnBurst(enemy.x, enemy.y, "#57e6ff", 12);
        }
        break;
      }
    }
    if (!hit) for (const hazard of hazards) {
      if (hazard.kind === "rock" && circleHit(bullet, hazard, 2)) {
        hazard.dead = true;
        hit = true;
        addScore(8);
        spawnBurst(hazard.x, hazard.y, "#ff9b55", 10);
        break;
      } else if (hazard.kind === "wall" && pointRectCircle(bullet, hazard)) {
        hit = true;
        break;
      }
    }
    if (hit) bullet.life = 0;
  }
  targets = targets.filter(target => !target.dead);
  enemies = enemies.filter(enemy => !enemy.dead);
  hazards = hazards.filter(hazard => !hazard.dead);
  bullets = bullets.filter(bullet => bullet.life > 0 && bullet.x > -40 && bullet.x < W + 40 && bullet.y > -40 && bullet.y < H + 40);

  for (const particle of particles) {
    particle.x += particle.vx * dt;
    particle.y += particle.vy * dt;
    particle.vx *= 0.98;
    particle.vy *= 0.98;
    particle.life -= dt;
  }
  particles = particles.filter(particle => particle.life > 0);
  scrollX = (scrollX + (currentMode === "side" ? 220 : 30) * dt) % 80;
}

function drawBackground(now) {
  const currentMode = mode().key;
  ctx.fillStyle = "#020509";
  ctx.fillRect(0, 0, W, H);
  if (currentMode === "galaxy") {
    ctx.fillStyle = "#071126";
    ctx.fillRect(0, 0, W, H);
    for (const star of stars) {
      const y = (star.y + now * 0.02 * star.z) % H;
      ctx.globalAlpha = 0.3 + star.z * 0.2;
      ctx.fillStyle = "#dff7ff";
      ctx.fillRect(star.x, y, star.z, star.z);
    }
    ctx.globalAlpha = 1;
  } else if (currentMode === "side") {
    const gradient = ctx.createLinearGradient(0, 0, 0, H);
    gradient.addColorStop(0, "#091526");
    gradient.addColorStop(0.55, "#101b25");
    gradient.addColorStop(1, "#10110d");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "rgba(87,230,255,.13)";
    ctx.lineWidth = 1;
    for (let x = -scrollX; x < W; x += 80) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    for (let y = 80; y < H; y += 80) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
  } else {
    ctx.strokeStyle = "rgba(87,230,255,.07)";
    ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 60) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    for (let y = 0; y < H; y += 60) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
  }
}

function drawWorld(now) {
  drawBackground(now);
  for (const target of targets) {
    ctx.save();
    ctx.translate(target.x, target.y);
    ctx.fillStyle = "#f4f6f8"; ctx.beginPath(); ctx.arc(0, 0, target.r, 0, TAU); ctx.fill();
    ctx.fillStyle = "#ff5964"; ctx.beginPath(); ctx.arc(0, 0, target.r * 0.72, 0, TAU); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(0, 0, target.r * 0.42, 0, TAU); ctx.fill();
    ctx.fillStyle = "#ffd95b"; ctx.beginPath(); ctx.arc(0, 0, target.r * 0.2, 0, TAU); ctx.fill();
    ctx.restore();
  }
  for (const enemy of enemies) {
    ctx.save();
    ctx.translate(enemy.x, enemy.y);
    ctx.fillStyle = "#57e6ff";
    ctx.beginPath(); ctx.moveTo(0, 22); ctx.lineTo(-22, -15); ctx.lineTo(0, -7); ctx.lineTo(22, -15); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#ff63d8"; ctx.fillRect(-5, -5, 10, 10);
    ctx.restore();
  }
  for (const hazard of hazards) {
    if (hazard.kind === "rock") {
      ctx.fillStyle = "#7d6757"; ctx.beginPath(); ctx.arc(hazard.x, hazard.y, hazard.r, 0, TAU); ctx.fill();
      ctx.strokeStyle = "#b8987a"; ctx.stroke();
    } else {
      ctx.fillStyle = "#5b2730"; ctx.fillRect(hazard.x, hazard.y, hazard.w, hazard.h);
      ctx.strokeStyle = "#ff5964"; ctx.strokeRect(hazard.x, hazard.y, hazard.w, hazard.h);
    }
  }
  for (const bullet of bullets) {
    ctx.fillStyle = bullet.spread ? "#ff8fe7" : "#ffe66c";
    ctx.shadowBlur = 10; ctx.shadowColor = ctx.fillStyle;
    ctx.beginPath(); ctx.arc(bullet.x, bullet.y, bullet.r, 0, TAU); ctx.fill();
    ctx.shadowBlur = 0;
  }
  for (const bullet of enemyBullets) {
    ctx.fillStyle = "#ff5964"; ctx.beginPath(); ctx.arc(bullet.x, bullet.y, bullet.r, 0, TAU); ctx.fill();
  }
  for (const particle of particles) {
    ctx.globalAlpha = clamp(particle.life * 2, 0, 1);
    ctx.fillStyle = particle.color;
    ctx.fillRect(particle.x, particle.y, 3, 3);
  }
  ctx.globalAlpha = 1;
  drawPlayer(now);
}

function drawPlayer(now) {
  const charge = clamp(Number(lastState.super_charge) || 0, 0, 1);
  const pulse = 1 + Math.sin(now * 0.02) * (charge * 0.13);
  const jitter = charge > 0 ? Math.sin(now * 0.07) * charge * 4 : 0;
  ctx.save();
  ctx.translate(player.x + jitter, player.y - jitter * 0.4);
  ctx.scale(pulse, pulse);
  const armor = now < armorUntil;
  const invulnerable = now < hitInvulnUntil;
  ctx.globalAlpha = ghost ? 0.28 : (invulnerable && Math.floor(now / 90) % 2 === 0 ? 0.35 : 1);
  ctx.fillStyle = now < flashUntil ? "#fff" : (charge >= 1 ? "#ffd95b" : "#55ff8a");
  ctx.shadowBlur = 12 + charge * 28;
  ctx.shadowColor = ctx.fillStyle;
  ctx.fillRect(-player.size / 2, -player.size / 2, player.size, player.size);
  ctx.shadowBlur = 0;
  if (armor) {
    ctx.strokeStyle = "#57e6ff";
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(0, 0, player.size * 0.78, 0, TAU); ctx.stroke();
  }
  if (ghost) {
    ctx.strokeStyle = "#c6f7ff";
    ctx.setLineDash([4, 5]);
    ctx.strokeRect(-player.size * 0.7, -player.size * 0.7, player.size * 1.4, player.size * 1.4);
  }
  ctx.restore();
}

function updateHud(now) {
  if (demoMode) {
    $("hud-time").textContent = demoTime;
    $("hud-lives").textContent = "♥♥♥♥♡";
    $("hud-armor").textContent = `${t("armor")} 24s`;
    $("hud-ghost").textContent = `${t("ghost")} —`;
    return;
  }
  const clockNow = paused ? pauseStarted : now;
  const remain = running ? Math.max(0, (roundEnd - clockNow) / 1000) : 0;
  $("hud-time").textContent = Math.ceil(remain);
  $("hud-lives").textContent = "♥".repeat(Math.max(0, lives)) + "♡".repeat(Math.max(0, 3 - lives));
  const armor = Math.max(0, (armorUntil - now) / 1000);
  $("hud-armor").textContent = armor > 0 ? `${t("armor")} ${Math.ceil(armor)}s` : `${t("armor")} —`;
  $("hud-ghost").textContent = ghost ? `${t("ghost")} ON` : `${t("ghost")} —`;
  if (running && !paused && remain <= 0) finishRound();
}

function loop(now) {
  requestAnimationFrame(loop);
  const dt = Math.min(0.035, Math.max(0.001, (now - lastFrame) / 1000));
  lastFrame = now;
  if (running && !paused) updateWorld(dt, now);
  drawWorld(now);
  updateHud(now);
}

async function closeApplication() {
  showMenu();
  $("menu-status").textContent = t("closing");
  await stopCamera();
  try {
    await fetch("/shutdown", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: true}),
    });
  } catch (_) {}
  for (let i = 0; i < 18; i++) {
    await new Promise(resolve => setTimeout(resolve, 250));
    try { await fetch("/state", {cache: "no-store"}); }
    catch (_) { location.reload(); return; }
  }
  $("menu-status").textContent = t("close_sent");
  location.reload();
}

$("close-app").addEventListener("click", closeApplication);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") showMenu();
  if (event.code === "Space" && running) togglePause();
});

// Demo URLs are used only to create reproducible repository screenshots.
function applyDemoMode() {
  const params = new URLSearchParams(location.search);
  const demo = params.get("demo") || window.__SMILEGUN_DEMO;
  if (!demo) return;
  demoMode = true;
  if (demo === "menu") {
    bestScore = Math.max(bestScore, 780);
    $("menu-best").textContent = bestScore;
    menu.classList.remove("hidden");
    return;
  }
  const index = MODES.findIndex(item => item.key === demo);
  if (index < 0) return;
  roundIndex = index;
  selectedDifficulty = 4;
  score = 1260 + index * 340;
  lives = 4;
  armorUntil = performance.now() + 24000;
  player = {x: demo === "side" ? 260 : W / 2, y: H * 0.76, size: 32};
  lastState = {super_charge: 0.74, super_ready: false, charging: true};
  clearWorld();
  menu.classList.add("hidden");
  overlay.classList.add("hidden");
  document.body.classList.add("playing");
  $("hud-score").textContent = score;
  $("hud-round").textContent = `${index + 1}/4 ${modeName()}`;
  updateSuper(lastState);
  if (demo === "targets") {
    targets = [
      {x: 180, y: 170, r: 38, vx: 0, vy: 0}, {x: 430, y: 270, r: 31, vx: 0, vy: 0},
      {x: 730, y: 160, r: 27, vx: 0, vy: 0}, {x: 780, y: 390, r: 34, vx: 0, vy: 0}, {x: 285, y: 420, r: 25, vx: 0, vy: 0},
    ];
    bullets = [{x: player.x, y: player.y - 95, vx: 0, vy: 0, r: 5, life: 9}, {x: player.x - 35, y: player.y - 155, vx: 0, vy: 0, r: 5, life: 9, spread: true}];
  } else if (demo === "galaxy") {
    enemies = [
      {x: 180, y: 170, r: 20, vx: 0, vy: 0, phase: 0, hp: 1}, {x: 470, y: 240, r: 20, vx: 0, vy: 0, phase: 1, hp: 1},
      {x: 760, y: 150, r: 20, vx: 0, vy: 0, phase: 2, hp: 1}, {x: 680, y: 390, r: 20, vx: 0, vy: 0, phase: 3, hp: 1},
    ];
    enemyBullets = [{x: 420, y: 430, vx: 0, vy: 0, r: 7, life: 9}, {x: 610, y: 485, vx: 0, vy: 0, r: 7, life: 9}, {x: 330, y: 350, vx: 0, vy: 0, r: 7, life: 9}];
    bullets = [{x: player.x, y: player.y - 95, vx: 0, vy: 0, r: 5, life: 9}];
  } else if (demo === "dodge") {
    hazards = [
      {x: 150, y: 160, r: 46, vx: 0, vy: 0, kind: "rock"}, {x: 390, y: 290, r: 32, vx: 0, vy: 0, kind: "rock"},
      {x: 680, y: 190, r: 40, vx: 0, vy: 0, kind: "rock"}, {x: 810, y: 410, r: 28, vx: 0, vy: 0, kind: "rock"}, {x: 250, y: 450, r: 26, vx: 0, vy: 0, kind: "rock"},
    ];
    bullets = [{x: player.x, y: player.y - 85, vx: 0, vy: 0, r: 5, life: 9, spread: true}];
  } else if (demo === "side") {
    hazards = [
      {x: 470, y: 0, w: 66, h: 210, vx: 0, kind: "wall"}, {x: 470, y: 405, w: 66, h: 315, vx: 0, kind: "wall"},
      {x: 760, y: 0, w: 52, h: 315, vx: 0, kind: "wall"}, {x: 760, y: 520, w: 52, h: 200, vx: 0, kind: "wall"},
    ];
    enemies = [{x: 650, y: 360, r: 18, vx: 0, vy: 0, phase: 0, hp: 1}, {x: 870, y: 470, r: 18, vx: 0, vy: 0, phase: 0, hp: 1}];
    enemyBullets = [{x: 560, y: 360, vx: 0, vy: 0, r: 7, life: 9}];
    bullets = [{x: 360, y: player.y, vx: 0, vy: 0, r: 5, life: 9}, {x: 415, y: player.y - 24, vx: 0, vy: 0, r: 5, life: 9, spread: true}];
  }
}

async function boot() {
  for (let i = 0; i < 90; i++) stars.push({x: Math.random() * W, y: Math.random() * H, z: rand(0.5, 2.2)});
  setLanguage(chooseInitialLanguage(), false);
  await loadConfig();
  setCameraUI();
  fitCanvas();
  applyDemoMode();
  requestAnimationFrame(loop);
  overlayButton.onclick = () => overlay.classList.add("hidden");
}

boot();
})();
