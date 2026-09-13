// H3 Project Suite - project hub UI.
//
// Design language follows ComfyUI-Fantastic-MiniMaxH3-PromptBuilder: the
// node itself carries only a compact summary card and an "Open project..."
// button; the real surface is a full-screen modal (overlay + card) with the
// review player, the chain rail, and the project actions. No window.prompt,
// no window.confirm, no browser notifications anywhere - naming and
// destructive confirmation are inline UI.
//
// The manifest stays the single source of truth: every action POSTs to
// /h3_suite/ and re-renders from a fresh state fetch.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* ------------------------------------------------------------------ */
/* CSS                                                                 */
/* ------------------------------------------------------------------ */

const CSS = `
.h3p-overlay{position:fixed;inset:0;z-index:10000;background:rgba(8,10,14,.62);
  display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;}
.h3p-modal{width:min(1240px,95vw);height:min(820px,92vh);display:flex;flex-direction:column;
  background:#191c22;color:#d7dbe2;border:1px solid #303642;border-radius:10px;
  box-shadow:0 24px 64px rgba(0,0,0,.55);overflow:hidden;font-size:calc(13px * var(--h3p-fs, 1));}
.h3p-head{display:flex;align-items:center;gap:12px;padding:10px 16px;
  border-bottom:1px solid #2a2f3a;background:#1e222a;}
.h3p-title{font-weight:600;font-size:calc(14px * var(--h3p-fs, 1));letter-spacing:.02em;white-space:nowrap;}
.h3p-title small{color:#8a93a3;font-weight:400;margin-left:8px;}
.h3p-select{background:#12151b;color:#d7dbe2;border:1px solid #2a2f3a;border-radius:7px;
  padding:5px 8px;font-size:calc(12px * var(--h3p-fs, 1));min-width:160px;}
.h3p-btn{background:#12151b;border:1px solid #2a2f3a;color:#9aa3b2;padding:5px 12px;
  border-radius:7px;cursor:pointer;font-size:calc(12px * var(--h3p-fs, 1));white-space:nowrap;}
.h3p-btn:hover{color:#fff;border-color:#59637a;}
.h3p-btn.ok{color:#7ec87e;border-color:#2e4a2e;} .h3p-btn.ok:hover{border-color:#7ec87e;}
.h3p-btn.warn{color:#e0a94c;border-color:#4a3d20;} .h3p-btn.warn:hover{border-color:#e0a94c;}
.h3p-btn.bad{color:#e05a5a;border-color:#4a2424;} .h3p-btn.bad:hover{border-color:#e05a5a;}
.h3p-x{background:none;border:0;color:#8a93a3;font-size:calc(18px * var(--h3p-fs, 1));cursor:pointer;
  padding:2px 8px;margin-left:4px;}
.h3p-x:hover{color:#fff;}
.h3p-namewrap{display:none;align-items:center;gap:6px;}
.h3p-namewrap.on{display:flex;}
.h3p-input{background:#12151b;color:#d7dbe2;border:1px solid #2a2f3a;border-radius:7px;
  padding:5px 8px;font-size:calc(12px * var(--h3p-fs, 1));width:170px;}
.h3p-input:focus{outline:none;border-color:#59637a;}
.h3p-body{flex:1;display:grid;grid-template-columns:minmax(0,1fr) 300px;min-height:0;}
@media (max-width:900px){.h3p-body{grid-template-columns:1fr;}}
.h3p-main{display:flex;flex-direction:column;min-width:0;min-height:0;padding:14px 16px;
  gap:10px;overflow:auto;}
.h3p-viewhead{display:flex;align-items:baseline;gap:10px;}
.h3p-viewtitle{font-weight:600;font-size:calc(13px * var(--h3p-fs, 1));}
.h3p-viewtitle.pending{color:#e0a94c;}
.h3p-viewtitle.approved{color:#7ec87e;}
.h3p-viewsub{color:#8a93a3;font-size:calc(11px * var(--h3p-fs, 1));}
.h3p-player{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;
  background:#0d1015;border:1px solid #23272f;border-radius:9px;overflow:hidden;}
.h3p-player video{max-width:100%;max-height:100%;display:block;background:#000;}
.h3p-playerempty{color:#5c6472;font-size:calc(12px * var(--h3p-fs, 1));text-align:center;line-height:1.7;
  padding:30px;white-space:pre-wrap;}
.h3p-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.h3p-exportrow{display:flex;align-items:center;min-height:30px;}
a.h3p-btn{text-decoration:none;display:inline-flex;align-items:center;}
a.h3p-btn:not([href]){opacity:.5;pointer-events:none;}
.h3p-inline{display:flex;gap:8px;align-items:center;flex:1;}
.h3p-modes{display:flex;gap:2px;background:#12151b;border:1px solid #2a2f3a;
  border-radius:7px;padding:2px;}
.h3p-modes button{background:none;border:0;color:#9aa3b2;padding:4px 12px;
  border-radius:5px;cursor:pointer;font-size:calc(12px * var(--h3p-fs, 1));}
.h3p-modes button.on{background:#2f3947;color:#fff;}
.h3p-transport{display:flex;flex-direction:column;gap:5px;}
.h3p-tbar{display:flex;align-items:center;gap:10px;}
.h3p-play{background:#12151b;border:1px solid #2a2f3a;color:#d7dbe2;width:30px;
  height:30px;border-radius:15px;cursor:pointer;font-size:calc(12px * var(--h3p-fs, 1));line-height:1;
  display:flex;align-items:center;justify-content:center;flex:0 0 auto;}
.h3p-play:hover{border-color:#59637a;}
.h3p-time{font-family:ui-monospace,monospace;font-size:calc(11px * var(--h3p-fs, 1));color:#8a93a3;
  white-space:nowrap;flex:0 0 auto;}
.h3p-time b{color:#d7dbe2;font-weight:400;}
.h3p-where{font-family:ui-monospace,monospace;font-size:calc(11px * var(--h3p-fs, 1));color:#8a93a3;
  padding:2px 0 0 46px;min-height:14px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;}
.h3p-scrub{position:relative;flex:1;height:26px;cursor:pointer;
  display:flex;align-items:center;}
.h3p-track{position:absolute;inset:6px 0;display:flex;gap:2px;}
.h3p-seg{position:relative;background:#232833;border-radius:3px;overflow:hidden;
  transition:background .12s;}
.h3p-seg.approved{background:#2a3a2c;}
.h3p-seg.pending{background:#3a3120;}
.h3p-seg.cur{outline:1px solid #59637a;outline-offset:0;}
.h3p-seg span{position:absolute;left:5px;top:1px;font-size:calc(9px * var(--h3p-fs, 1));color:#7d8698;
  font-family:ui-monospace,monospace;pointer-events:none;}
.h3p-fill{position:absolute;inset:6px auto 6px 0;background:rgba(111,134,184,.28);
  border-right:2px solid #8fa8d8;pointer-events:none;border-radius:3px 0 0 3px;}
.h3p-measuring{font-size:calc(10px * var(--h3p-fs, 1));color:#5c6472;}
.h3p-hint{font-size:calc(11px * var(--h3p-fs, 1));color:#6b7484;line-height:1.4;}
.h3p-takes{display:none;align-items:center;gap:6px;}
.h3p-takelabel{font-size:calc(10px * var(--h3p-fs, 1));text-transform:uppercase;letter-spacing:.08em;color:#8a93a3;}

.h3p-spacer{flex:1;}
.h3p-confirm{display:none;flex-direction:column;gap:8px;background:#241f14;
  border:1px solid #4a3d20;border-radius:9px;padding:10px 12px;}
.h3p-confirm.on{display:flex;}
.h3p-confirm .msg{white-space:pre-wrap;color:#e0c890;font-size:calc(12px * var(--h3p-fs, 1));line-height:1.5;}
.h3p-pathfield{display:block;width:100%;box-sizing:border-box;margin-top:6px;
  background:#141821;border:1px solid #4a4128;color:#f0e6cc;border-radius:5px;
  padding:6px 8px;font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:calc(12px * var(--h3p-fs, 1));}
.h3p-rail{border-left:1px solid #2a2f3a;background:#15181e;display:flex;
  flex-direction:column;min-height:0;}
.h3p-railhead{font-size:calc(10px * var(--h3p-fs, 1));text-transform:uppercase;letter-spacing:.08em;color:#8a93a3;
  padding:12px 12px 8px;}
.h3p-railbody{flex:1;overflow:hidden auto;display:flex;flex-direction:column;gap:8px;
  padding:0 12px 12px;}
.h3p-clip{border:1px solid #2e3440;border-radius:8px;overflow:hidden;background:#12151b;
  cursor:pointer;flex:0 0 auto;}
.h3p-clip:hover{border-color:#59637a;}
.h3p-clip.sel{border-color:#6f86b8;}
.h3p-clip.pending{border-color:#4a3d20;}
.h3p-clip.pending:hover,.h3p-clip.pending.sel{border-color:#e0a94c;}
.h3p-clip video{width:100%;height:84px;object-fit:cover;display:block;background:#0d1015;
  pointer-events:none;}
.h3p-clipbar{display:flex;align-items:center;gap:6px;padding:4px 8px;font-size:calc(11px * var(--h3p-fs, 1));}
.h3p-clipname{font-family:ui-monospace,monospace;font-size:calc(10px * var(--h3p-fs, 1));color:#9aa3b2;}
.h3p-clipstat{margin-left:auto;font-size:calc(10px * var(--h3p-fs, 1));}
.h3p-clipstat.ok{color:#7ec87e;} .h3p-clipstat.pend{color:#e0a94c;}
.h3p-tail{font-size:calc(9px * var(--h3p-fs, 1));color:#6f86b8;border:1px solid #2b3a52;border-radius:7px;
  padding:0 5px;}
.h3p-next{border:1px dashed #2e3440;border-radius:8px;padding:10px 8px;text-align:center;
  font-size:calc(10px * var(--h3p-fs, 1));color:#5c6472;line-height:1.5;flex:0 0 auto;}
.h3p-next b{color:#8a93a3;font-family:ui-monospace,monospace;font-weight:400;}
.h3p-driftwrap{position:absolute;inset:0;background:rgba(10,12,16,.72);
  display:none;align-items:center;justify-content:center;z-index:40;}
.h3p-driftwrap.on{display:flex;}
.h3p-driftwrap .card{background:#171b23;border:1px solid #2b3140;
  border-radius:10px;width:min(680px,92%);max-height:86%;overflow:auto;}
.h3p-driftwrap .head{display:flex;align-items:center;gap:8px;
  padding:10px 14px;border-bottom:1px solid #232833;color:#d7dbe2;
  font-size:calc(13px * var(--h3p-fs, 1));}
.h3p-driftwrap .body{padding:14px;}
table.h3p-drift{width:100%;border-collapse:collapse;font-size:calc(12px * var(--h3p-fs, 1));
  color:#c3c9d4;}
table.h3p-drift th{text-align:left;font-weight:400;color:#7d8697;
  padding:4px 8px;border-bottom:1px solid #262c38;font-size:calc(11px * var(--h3p-fs, 1));}
table.h3p-drift td{padding:5px 8px;border-bottom:1px solid #1e232d;}
table.h3p-drift td.n{font-family:ui-monospace,monospace;text-align:right;}
.h3p-spark{margin-top:14px;}
.h3p-spark .lbl{font-size:calc(11px * var(--h3p-fs, 1));color:#7d8697;margin-bottom:5px;}
.h3p-spark .bars{display:flex;align-items:flex-end;gap:3px;height:40px;}
.h3p-spark .bars i{flex:1;background:#3b4657;border-radius:2px 2px 0 0;}
.h3p-note{font-size:calc(11px * var(--h3p-fs, 1));color:#7d8697;line-height:1.5;margin:14px 0 0;}
.h3p-drop{position:absolute;inset:0;z-index:30;display:none;
  align-items:center;justify-content:center;border-radius:12px;
  background:rgba(20,26,38,.86);border:2px dashed #5b8cff;
  color:#dfe4ee;font-size:calc(14px * var(--h3p-fs, 1));font-weight:600;}
.h3p-drop.on{display:flex;}
.h3p-empty{display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:10px;min-height:220px;color:#8a93a3;
  border:1px dashed #333c4c;border-radius:10px;margin:4px 0;
  font-size:calc(12px * var(--h3p-fs, 1));}
.h3p-impbar{display:flex;align-items:center;gap:8px;padding:8px 16px;
  border-bottom:1px solid #262b36;}
.h3p-impbar select{flex:1;min-width:0;background:#141821;
  border:1px solid #333c4c;border-radius:7px;color:#dfe4ee;padding:5px 8px;
  font-size:calc(12px * var(--h3p-fs, 1));}
.h3p-impwrap{display:flex;flex-direction:column;gap:8px;padding:10px 16px;}
.h3p-impvid{width:100%;max-height:44vh;background:#000;border-radius:8px;
  display:block;}
/* overflow:hidden is what turns the box's huge spread shadow into a
   dimming mask over everything the crop drops */
.h3p-cropwrap{position:relative;line-height:0;overflow:hidden;
  border-radius:8px;}
.h3p-cropbox{position:absolute;box-sizing:border-box;
  border:2px solid #5b8cff;box-shadow:0 0 0 9999px rgba(0,0,0,0.6);
  pointer-events:auto;}
.h3p-cropbox::after{content:"";position:absolute;inset:0;
  border:1px solid rgba(255,255,255,0.35);}
.h3p-strip{position:relative;height:64px;background:#11151d;
  border:1px solid #2a3140;border-radius:8px;overflow:hidden;
  cursor:grab;user-select:none;}
.h3p-strip.drag{cursor:grabbing;}
.h3p-stripimgs{position:absolute;inset:0;display:flex;}
.h3p-stripimgs canvas{height:100%;flex:1 1 0;min-width:0;object-fit:cover;}
.h3p-cut{position:absolute;top:0;bottom:0;background:rgba(10,12,18,.72);}
.h3p-keep{position:absolute;top:0;bottom:0;border:2px solid #5b8cff;
  border-radius:4px;box-shadow:0 0 0 9999px rgba(0,0,0,0) inset;}
.h3p-keep::after{content:"";position:absolute;inset:0;
  background:rgba(91,140,255,.10);}
.h3p-playhead{position:absolute;top:0;bottom:0;width:2px;
  background:#ffd479;pointer-events:none;}
.h3p-impinfo{display:flex;gap:14px;align-items:center;flex-wrap:wrap;
  color:#8a93a3;font-size:calc(11px * var(--h3p-fs, 1));}
.h3p-impinfo b{color:#dfe4ee;font-weight:600;}
.h3p-impinfo .drop{color:#ff9d5c;}
.h3p-scalewrap{position:relative;}
.h3p-scalemenu{--h3p-fs:1;position:absolute;right:0;top:100%;margin-top:6px;
  z-index:20;display:none;width:300px;background:#1e222a;
  border:1px solid #3a4252;border-radius:9px;padding:10px;
  box-shadow:0 14px 34px rgba(0,0,0,.5);}
.h3p-scalemenu.on{display:block;}
.h3p-scalerow{display:flex;align-items:center;gap:8px;margin-bottom:8px;}
.h3p-scalerow label{width:58px;color:#8a93a3;
  font-size:calc(11px * var(--h3p-fs, 1));}
.h3p-scalerow input[type=range]{flex:1;accent-color:#5b8cff;min-width:0;}
.h3p-scalerow input[type=number]{width:56px;background:#141821;
  border:1px solid #333c4c;border-radius:6px;color:#dfe4ee;padding:3px 6px;
  font-size:calc(11px * var(--h3p-fs, 1));}
.h3p-scalefoot{display:flex;gap:6px;align-items:center;}
.h3p-scalefoot .sp{flex:1;}
.h3p-scalenote{color:#6f7787;font-size:calc(10px * var(--h3p-fs, 1));
  margin:2px 0 8px;line-height:1.35;}
.h3p-autowarn{color:#ff5c5c;font-weight:600;line-height:1.35;
  margin-bottom:4px;}
.h3p-autobar{display:flex;align-items:center;gap:8px;padding:7px 12px;
  background:rgba(255,60,60,.10);border:1px solid rgba(255,92,92,.45);
  border-radius:7px;color:#ff5c5c;font-size:calc(12px * var(--h3p-fs, 1));font-weight:600;
  margin:0 16px 10px;}
.h3p-autobar .sp{flex:1;}
.h3p-auto{display:flex;align-items:center;gap:6px;cursor:pointer;
  user-select:none;font-size:calc(12px * var(--h3p-fs, 1));color:#8a93a3;}
.h3p-auto.on{color:#ff5c5c;font-weight:600;}
.h3p-auto input{accent-color:#ff5c5c;cursor:pointer;margin:0;}
.h3p-foot{display:flex;align-items:center;gap:8px;padding:9px 16px;
  border-top:1px solid #2a2f3a;background:#171a20;font-size:calc(11px * var(--h3p-fs, 1));color:#7d8698;}
.h3p-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:10001;
  background:#1e222a;border:1px solid #3a4252;color:#d7dbe2;border-radius:9px;
  padding:9px 16px;font-size:calc(12px * var(--h3p-fs, 1));box-shadow:0 12px 32px rgba(0,0,0,.5);
  font-family:system-ui,sans-serif;}
.h3p-toast.bad{border-color:#7a3a3a;color:#e8b0b0;}
.h3p-summary{background:#12151b;border:1px solid #2e3440;border-radius:7px;
  padding:7px 10px;font-size:calc(11px * var(--h3p-fs, 1));line-height:1.55;color:#9aa3b2;
  font-family:system-ui,sans-serif;overflow:hidden;}
.h3p-summary b{color:#d7dbe2;font-weight:600;}
.h3p-summary .pend{color:#e0a94c;} .h3p-summary .ok{color:#7ec87e;}
.h3p-summary .nx{font-family:ui-monospace,monospace;font-size:calc(10px * var(--h3p-fs, 1));color:#6f86b8;}
`;

function injectCSS() {
  if (document.getElementById("h3p-css")) return;
  const s = document.createElement("style");
  s.id = "h3p-css";
  s.textContent = CSS;
  document.head.append(s);
}

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e[k] = v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v);
  }
  e.append(...children.filter(Boolean));
  return e;
}

function mb(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024 * 1024) return (n / 1073741824).toFixed(2) + " GB";
  if (n >= 1024 * 1024) return (n / 1048576).toFixed(1) + " MB";
  return Math.max(1, Math.round(n / 1024)) + " KB";
}

const SCALE_KEY = "h3suite.panelPrefs";
const SCALE_MIN = 1.0;
const SCALE_MAX = 3.0;          // window: oversized is merely awkward
const TEXT_SCALE_MAX = 2.0;     // type: oversized genuinely breaks layouts
const SCALE_DEFAULTS = { windowScale: 1.0, textScale: 1.0,
                         exportFromLatents: false,
                         masterQuality: "16:medium" };

function clampScale(v, max = SCALE_MAX) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 1;
  return Math.min(max, Math.max(SCALE_MIN, Math.round(n * 100) / 100));
}

function loadScalePrefs() {
  try {
    const v = { ...SCALE_DEFAULTS,
      ...JSON.parse(localStorage.getItem(SCALE_KEY) || "{}") };
    // clamp on the way IN too: a hand-edited or stale entry must not be
    // able to inject NaN or 900% into a running panel
    v.windowScale = clampScale(v.windowScale);
    v.textScale = clampScale(v.textScale, TEXT_SCALE_MAX);
    return v;
  } catch (e) {
    return { ...SCALE_DEFAULTS };
  }
}

function saveScalePrefs(prefs) {
  try { localStorage.setItem(SCALE_KEY, JSON.stringify(prefs)); }
  catch (e) { /* private mode: the session's choice still applies */ }
}

// Text scale rides one custom property on documentElement, because
// overlays and popovers attach to <body> and are not descendants of the
// modal. Window scale is per-box, so each modal applies its own.
function applyTextScale(prefs) {
  document.documentElement.style.setProperty(
    "--h3p-fs", String(clampScale(prefs.textScale, TEXT_SCALE_MAX)));
}

function sizeScaledBox(box, baseW, baseH, prefs) {
  if (!box?.style) return;
  const w = clampScale(prefs.windowScale);
  // the viewport clamps are load-bearing: without them a 300% window on a
  // laptop pushes its own header - including this control - off-screen
  box.style.width = `min(${Math.round(baseW * w)}px, 95vw)`;
  if (baseH) box.style.height = `min(${Math.round(baseH * w)}px, 92vh)`;
}

// Every window listens for Escape on document, so without this check one
// press closes the window you are in AND the project panel underneath
// it. Only the most recently opened overlay answers.
function isTopOverlay(overlay) {
  if (!overlay || !overlay.isConnected) return false;
  const all = document.querySelectorAll(".h3p-overlay");
  return all[all.length - 1] === overlay;
}

function toast(msg, bad = false) {
  const t = el("div", { class: "h3p-toast" + (bad ? " bad" : ""), text: msg });
  document.body.append(t);
  setTimeout(() => t.remove(), bad ? 5200 : 2600);
}

// Every state-changing route wants the server's session token in a
// header. The token comes from a same-origin GET, which a page on
// another origin can send but never read - that is the whole defence.
// One shared helper attaches it and retries once when the server says
// the token is stale (a restart with the editor still open).
const TOKEN_HEADER = "X-H3Suite-Token";
let tokenPromise = null;
function sessionToken(fresh = false) {
  if (fresh || !tokenPromise) {
    tokenPromise = api.fetchApi("/h3_suite/token")
      .then((r) => (r.ok ? r.json()
                         : Promise.reject(new Error(`token ${r.status}`))))
      .then((j) => j.token || Promise.reject(new Error("no token in response")))
      .catch((err) => { tokenPromise = null; throw err; });
  }
  return tokenPromise;
}

async function postApi(path, init = {}) {
  const send = async (token) => api.fetchApi(path, {
    ...init, method: "POST",
    headers: { ...(init.headers || {}), [TOKEN_HEADER]: token },
  });
  let resp = await send(await sessionToken());
  if (resp.status === 403) {
    const data = await resp.clone().json().catch(() => ({}));
    if (data.token_required) resp = await send(await sessionToken(true));
  }
  return resp;
}

async function post(path, body) {
  const r = await postApi(path, {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok || data.error) throw new Error(data.error || r.statusText);
  return data;
}

async function getState(name) {
  const r = await api.fetchApi(
    `/h3_suite/project/state?name=${encodeURIComponent(name)}`);
  const data = await r.json();
  if (data.error) throw new Error(data.error);
  return data;
}

function videoURL(name, basename) {
  const p = `/h3_suite/project/video?name=${encodeURIComponent(name)}` +
    `&basename=${encodeURIComponent(basename)}`;
  return api.apiURL ? api.apiURL(p) : p;
}

/* ------------------------------------------------------------------ */
/* ChainTimeline: a scrubbable player over an ordered list of clips     */
/* ------------------------------------------------------------------ */
// Both the review modal and the branch modal need the same thing: play a
// sequence of separate mp4s as one shot, with a segmented scrub bar. The
// only difference is which clips are in the list, so the machinery lives
// here and each modal supplies its own segments.

class ChainTimeline {
  initPlayer() {
    this.curSeg = null;
    this.standbySeg = null;
    this.durations = this.durations || {};
    this.timeline = [];
    this.total = 0;

    // two elements: while one plays, the next clip is already decoded
    // into the other, so a boundary is a swap rather than a load
    const mkVideo = () => {
      const v = el("video", {});
      v.preload = "auto";
      v.addEventListener("ended", () => {
        if (v === this.video) this.onClipEnded();
      });
      v.addEventListener("play", () => this.paintPlayhead());
      v.addEventListener("pause", () => this.paintPlayhead());
      return v;
    };
    this.vA = mkVideo();
    this.vB = mkVideo();
    this.vB.style.display = "none";
    this.video = this.vA;
    this.standby = this.vB;
    this.standbySeg = null;

    // transport: play/pause, running time, segmented scrub bar
    this.playBtn = el("button", { class: "h3p-play", text: "\u25b6",
      onclick: () => {
        if (this.video.paused) this.video.play().catch(() => {});
        else this.video.pause();
      } });
    this.timeEl = el("div", { class: "h3p-time" });
    this.track = el("div", { class: "h3p-track" });
    this.fill = el("div", { class: "h3p-fill" });
    this.scrub = el("div", { class: "h3p-scrub" }, this.track, this.fill);
    const scrubTo = (ev) => {
      const r = this.scrub.getBoundingClientRect();
      const frac = Math.max(0, Math.min((ev.clientX - r.left) / r.width, 1));
      this.seekGlobal(frac * this.total, !this.video.paused);
    };
    this.scrub.onmousedown = (ev) => {
      scrubTo(ev);
      const move = (e2) => scrubTo(e2);
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    };
    this.whereEl = el("div", { class: "h3p-where" });
    this.transport = el("div", { class: "h3p-transport" },
      el("div", { class: "h3p-tbar" }, this.playBtn, this.timeEl,
         this.scrub),
      this.whereEl);
    this.confirmMsg = el("div", { class: "msg" });
    this.confirmBtns = el("div", { style: "display:flex;gap:8px;" +
                                          "flex-wrap:wrap" });
    this.driftBody = el("div", { class: "body" });
    this.drift = el("div", { class: "h3p-driftwrap" },
      el("div", { class: "card" },
        el("div", { class: "head" },
          el("span", { text: "Drift across the chain" }),
          el("div", { class: "h3p-spacer" }),
          el("button", { class: "h3p-x", text: "\u2715",
                         onclick: () => this.drift.classList.remove("on") })),
        this.driftBody));
    this.confirm = el("div", { class: "h3p-confirm" }, this.confirmMsg,
                      this.confirmBtns);

    this.playerEmpty = el("div", { class: "h3p-playerempty" });
    this.player = el("div", { class: "h3p-player" }, this.playerEmpty);
  }

  clipURL(basename) {
    throw new Error("clipURL must be implemented");
  }

  showPlayer() {
    this.player.replaceChildren(this.vA, this.vB);
    for (const v of [this.vA, this.vB]) { v.controls = false;
                                          v.loop = false; }
  }

  stopPlayer() {
    if (this._tick) { cancelAnimationFrame(this._tick); this._tick = null; }
    this.vA.pause();
    this.vB.pause();
  }

  /* ---- continuous timeline ------------------------------------- */
  // The chain plays as one shot by sequencing the individual clip mp4s
  // through a single <video>: a virtual timeline maps global seconds to
  // (clip, local offset). Durations come from each file's metadata, so
  // the bar is proportional once they have all reported in. Switching
  // files at a boundary costs a frame or two - this is a review tool,
  // not a finishing monitor. Export master is the seamless artifact.

  measure(clips) {
    // cache duration per basename; resolve as metadata arrives
    this.durations = this.durations || {};
    // clips saved with metadata carry their duration in the manifest, so
    // the timeline builds instantly; only older clips need probing
    for (const c of clips) {
      if (!(c.basename in this.durations) && c.meta?.duration > 0) {
        this.durations[c.basename] = c.meta.duration;
      }
    }
    const need = clips.filter((c) => !(c.basename in this.durations));
    if (!need.length) return Promise.resolve();
    return Promise.all(need.map((c) => new Promise((res) => {
      const v = document.createElement("video");
      v.preload = "metadata";
      v.muted = true;
      const done = (d) => {
        this.durations[c.basename] = d;
        v.removeAttribute("src");
        res();
      };
      v.onloadedmetadata = () => done(
        isFinite(v.duration) && v.duration > 0 ? v.duration : 0);
      v.onerror = () => done(0);
      v.src = this.clipURL(c.basename);
    })));
  }

  buildTimeline(clips) {
    let t = 0;
    this.timeline = clips.map((c) => {
      const dur = this.durations?.[c.basename] || 0;
      const seg = { clip: c, start: t, dur };
      t += dur;
      return seg;
    });
    this.total = t;
    return this.timeline;
  }

  segAt(globalT) {
    const tl = this.timeline || [];
    for (let i = tl.length - 1; i >= 0; i--) {
      if (globalT >= tl[i].start - 1e-6) return i;
    }
    return tl.length ? 0 : -1;
  }

  globalNow() {
    const i = this.curSeg;
    if (i == null || !this.timeline?.[i]) return 0;
    return this.timeline[i].start + (this.video.currentTime || 0);
  }

  swapBuffers() {
    const old = this.video;
    this.video = this.standby;
    this.standby = old;
    this.standby.pause();
    this.standby.style.display = "none";
    this.video.style.display = "";
    this.video.muted = false;   // may still be muted from the warm pass
    this.standbySeg = null;
  }

  preload(j) {
    const seg = this.timeline?.[j];
    if (!seg || this.standbySeg === j) return;
    const v = this.standby;
    this.standbySeg = j;
    v.muted = true;
    v.src = this.clipURL(seg.clip.basename);
    // preload="auto" buffers bytes but engines defer decoder init until
    // play; warm it: one muted frame, pause, rewind. The swap then starts
    // on a live pipeline instead of paying spin-up at the join.
    v.onloadeddata = () => {
      v.onloadeddata = null;
      if (this.standbySeg !== j) return;
      v.play().then(() => requestAnimationFrame(() => {
        if (this.standbySeg !== j) return;
        v.pause();
        try { v.currentTime = 0; } catch (e) { /* fine */ }
        v.muted = false;
      })).catch(() => { v.muted = false; });
    };
  }

  async seekGlobal(globalT, play) {
    const tl = this.timeline || [];
    if (!tl.length) return;
    const t = Math.max(0, Math.min(globalT, this.total - 0.02));
    const i = this.segAt(t);
    const local = t - tl[i].start;
    if (i !== this.curSeg) {
      if (this.standbySeg === i) {
        this.swapBuffers();            // already decoded: instant
      } else {
        this.video.src = this.clipURL(tl[i].clip.basename);
        await new Promise((res) => {
          const go = () => { this.video.onloadedmetadata = null; res(); };
          this.video.onloadedmetadata = go;
          setTimeout(go, 4000);        // never hang the UI on a bad file
        });
      }
      this.curSeg = i;
      this.paintSegments();
      this.syncBranchButton?.();
      this.preload(i + 1);
    }
    try { this.video.currentTime = local; } catch (e) { /* seeking */ }
    if (play) this.video.play().catch(() => {});
    this.paintPlayhead();
  }

  onClipEnded() {
    if (this.mode && this.mode !== "timeline") return;
    const next = (this.curSeg ?? 0) + 1;
    if (this.timeline && next < this.timeline.length) {
      this.seekGlobal(this.timeline[next].start, true);
    } else {
      this.paintPlayhead();
    }
  }

  paintSegments() {
    if (!this.track) return;
    this.track.innerHTML = "";
    const tl = this.timeline || [];
    for (let i = 0; i < tl.length; i++) {
      const seg = tl[i];
      const share = this.total > 0 ? seg.dur / this.total : 1 / tl.length;
      const d = el("div", {
        class: "h3p-seg " + seg.clip.status +
               (i === this.curSeg ? " cur" : ""),
        title: `clip ${seg.clip.index} take ${seg.clip.take}` +
               (seg.dur ? ` \u00b7 ${seg.dur.toFixed(2)}s` : ""),
      }, el("span", { text: `${seg.clip.index}` }));
      d.style.flex = `${Math.max(share, 0.02)} 1 0`;
      this.track.append(d);
    }
  }

  paintPlayhead() {
    if (!this.fill || !this.timeEl) return;
    const now = this.globalNow();
    const pct = this.total > 0 ? (now / this.total) * 100 : 0;
    this.fill.style.width = `${Math.max(0, Math.min(pct, 100))}%`;
    const fmt = (x) => {
      const m = Math.floor(x / 60);
      const sec = (x - m * 60);
      return `${m}:${sec < 10 ? "0" : ""}${sec.toFixed(1)}`;
    };
    const seg = this.timeline?.[this.curSeg];
    // which take is actually in the chain here, not just which clip:
    // after a few re-rolls "clip 7" is ambiguous, and the take number is
    // what ties what you are watching back to a file on disk
    let where = "";
    if (seg) {
      const c = seg.clip;
      const n = (c.takes || []).length;
      where = ` \u00b7 clip ${c.index}`;
      if (c.take != null) {
        where += ` \u00b7 take ${c.take}`;
        if (n > 1) where += `/${n}`;
      }
      if (c.status && c.status !== "approved") {
        where += ` \u00b7 ${c.status}`;
      }
      if (c.level_match) where += " \u00b7 levelled";
    }
    this.timeEl.innerHTML = `<b>${fmt(now)}</b> / ${fmt(this.total || 0)}`;
    if (this.whereEl) {
      this.whereEl.textContent = where ? where.replace(/^ \u00b7 /, "") : "";
      this.whereEl.title = seg
        ? `${seg.clip.basename}` +
          (seg.clip.from ? `\ncontinues ${seg.clip.from}` : "") +
          `\nstarts at ${fmt(seg.start)} of ${fmt(this.total || 0)}`
        : "";
    }
    this.playBtn.textContent = this.video.paused ? "\u25b6" : "\u2758\u2758";
    this.syncBranchButton?.();
  }

  startTicker() {
    if (this._tick) return;
    const step = () => {
      if (!this.overlay.isConnected) { this._tick = null; return; }
      if (!this.mode || this.mode === "timeline") {
        this.paintPlayhead();
      }
      this._tick = requestAnimationFrame(step);
    };
    this._tick = requestAnimationFrame(step);
  }

}


/* ------------------------------------------------------------------ */
/* branch modal                                                        */
/* ------------------------------------------------------------------ */
// Its own surface with its own timeline: the chain AS IT WOULD BE, ending
// on whichever take you are considering. Switching takes rebuilds only the
// last segment, so you can watch the new join before committing. The
// review modal's timeline is left alone.

class BranchModal extends ChainTimeline {

  constructor(project, index, onDone) {
    super();
    injectCSS();
    this.project = project;
    this.index = index;
    this.onDone = onDone;
    this.initPlayer();

    this.takeSel = el("select", { class: "h3p-select",
      onchange: () => this.pickTake(Number(this.takeSel.value)) });
    this.nameInput = el("input", { class: "h3p-input" });
    this.nameInput.onkeydown = (e) => {
      if (e.key === "Enter") this.create();
      if (e.key === "Escape") this.close();
      e.stopPropagation();
    };
    this.note = el("div", { class: "h3p-hint" });
    this.viewTitle = el("div", { class: "h3p-viewtitle" });
    this.viewSub = el("div", { class: "h3p-viewsub" });
    this.createBtn = el("button", { class: "h3p-btn ok",
      text: "Create branch", onclick: () => this.create() });

    this.overlay = el("div", {
      class: "h3p-overlay",
      onmousedown: (e) => { if (e.target === this.overlay) this.close(); },
    },
      el("div", { class: "h3p-modal", style: "height:min(720px,88vh)" },
        el("div", { class: "h3p-head" },
          el("div", { class: "h3p-title", text: `Branch from clip ${index}` },
            el("small", { text: project })),
          el("div", { class: "h3p-spacer" }),
          el("span", { class: "h3p-takelabel", text: "from take" }),
          this.takeSel,
          el("button", { class: "h3p-x", text: "\u2715",
                         onclick: () => this.close() })),
        el("div", { class: "h3p-main", style: "padding:14px 16px" },
          el("div", { class: "h3p-viewhead" }, this.viewTitle, this.viewSub),
          this.player, this.transport, this.note,
          el("div", { class: "h3p-actions" },
            el("span", { class: "h3p-takelabel", text: "branch as" }),
            this.nameInput, this.createBtn,
            el("button", { class: "h3p-btn", text: "Cancel",
                           onclick: () => this.close() }),
            el("button", { class: "h3p-btn",
                           text: "\u23ee Jump to the join",
                           onclick: () => this.jumpToJoin() })))));

    this._esc = (e) => {
      if (e.key !== "Escape") return;
      if (!isTopOverlay(this.overlay)) return;   // a window above owns it
      if (this.drift.classList.contains("on")) {
        this.drift.classList.remove("on");
        return;
      }
      this.close();
    };
  }

  clipURL(basename) {
    return videoURL(this.project, basename);
  }

  async open() {
    document.body.append(this.overlay);
    // a nested overlay inherits text scale via documentElement, but its
    // own box needs sizing from the same stored preference
    try {
      sizeScaledBox(this.overlay.querySelector(".h3p-modal"),
                    1240, 720, loadScalePrefs());
    } catch (e) { /* a corrupt entry degrades to 100%, not a broken modal */ }
    document.addEventListener("keydown", this._esc);
    this.transport.style.display = "flex";
    this.showPlayer();
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/branch_name?name=` +
        `${encodeURIComponent(this.project)}&index=${this.index}`);
      const data = await r.json();
      if (data.error) throw new Error(data.error);
      this.nameInput.value = data.suggested;
      this.takes = data.takes || [];
      this.approvedTake = data.current_take;
      this.takeSel.innerHTML = "";
      for (const t of this.takes) {
        const secs = t.meta?.duration ? ` \u00b7 ${t.meta.duration}s` : "";
        this.takeSel.append(el("option", {
          value: String(t.take),
          text: `take ${t.take}${secs}` +
                (t.take === data.current_take ? " (approved)" : "") +
                (t.location === "trash" ? " \u00b7 recovered" : ""),
        }));
      }
      this.takeSel.value = String(data.current_take);
      const sr = await api.fetchApi(
        `/h3_suite/project/state?name=` +
        `${encodeURIComponent(this.project)}`);
      this.state = await sr.json();
      this.pickTake(data.current_take);
    } catch (e) {
      toast(e.message, true);
      this.close();
    }
  }

  close() {
    this.stopPlayer();
    this.overlay.remove();
    document.removeEventListener("keydown", this._esc);
  }

  async pickTake(take) {
    const chosen = (this.takes || []).find((t) => t.take === take);
    if (!chosen || !this.state) return;
    this.chosen = chosen;
    // the branch's chain: clips 1..index-1 as selected, then this take
    const clips = this.state.clips.slice(0, this.index - 1).map((c) => ({
      index: c.index, take: c.take, basename: c.basename,
      status: "approved", meta: c.meta,
    }));
    clips.push({ index: this.index, take: chosen.take,
                 basename: chosen.basename, status: "approved",
                 meta: chosen.meta });
    this.curSeg = null;
    this.standbySeg = null;
    this.viewTitle.textContent =
      `Branch chain \u2014 ${clips.length} clips, ending on take ` +
      `${chosen.take}`;
    this.viewTitle.className = "h3p-viewtitle approved";
    this.viewSub.textContent = chosen.location === "trash"
      ? "this take is in .trash/ and would be recovered into the branch"
      : "";
    const total = this.state.clips.length;
    const swapped = chosen.take !== this.approvedTake;
    this.note.textContent =
      `copies clips 1\u2013${this.index} into a new project` +
      (swapped ? `, using take ${chosen.take} of clip ${this.index} as the ` +
                 `tail instead of the approved take ${this.approvedTake}` : "") +
      `. ` + (total > this.index
        ? `Clips ${this.index + 1}\u2013${total} stay in ${this.project}, ` +
          `untouched.`
        : `${this.project} is untouched.`);
    this.timeEl.innerHTML =
      "<span class='h3p-measuring'>measuring clips\u2026</span>";
    await this.measure(clips);
    this.buildTimeline(clips);
    this.paintSegments();
    this.jumpToJoin();
    this.startTicker();
  }

  jumpToJoin() {
    const last = this.timeline?.[this.timeline.length - 1];
    if (!last) return;
    this.seekGlobal(Math.max(0, last.start - 2), true);
  }

  async create() {
    const newName = this.nameInput.value.trim();
    if (!newName || !this.chosen) return;
    this.createBtn.disabled = true;
    toast("copying clips\u2026");
    try {
      await post("/h3_suite/project/branch", {
        name: this.project, index: this.index, new_name: newName,
        take: this.chosen.take,
      });
      toast(`branched into "${newName}" at clip ${this.index}`);
      this.close();
      this.onDone?.(newName);
    } catch (e) {
      this.createBtn.disabled = false;
      toast(e.message, true);
    }
  }
}

/* ------------------------------------------------------------------ */
/* review modal                                                        */
/* ------------------------------------------------------------------ */


/** Filenames of the VAE loaders wired into a node's inputs.
 *
 * Reading the graph, not running it: follow the link on each named
 * input back to whatever feeds it and take that node's first widget
 * value, which for every VAE loader is the filename. Nothing has to
 * execute, so Import works on an empty project in a fresh session.
 */
// a model file, as a loader's widget names one
const MODEL_FILE = /\.(safetensors|sft|ckpt|pt|pth|bin|gguf)$/i;

function nodeWidgetStrings(node) {
  // live widget values first (widgets_values is the serialised snapshot
  // and can trail an edit until the graph is saved)
  const live = Array.isArray(node?.widgets)
    ? node.widgets.map((w) => w?.value) : [];
  const saved = Array.isArray(node?.widgets_values) ? node.widgets_values : [];
  return [...live, ...saved].filter((v) => typeof v === "string" && v.trim());
}

function upstreamNode(graph, node, slotIndex) {
  const slot = node?.inputs?.[slotIndex];
  if (!slot || slot.link == null) return null;
  const link = graph.links?.[slot.link];
  return link ? graph.getNodeById?.(link.origin_id) : null;
}

/** The model filename a loader somewhere upstream of `node` names.
 *
 * The Hub's vae inputs are often not wired straight from a loader but
 * through a reroute, a switch, or an rgthree Get node whose only widget
 * is a variable name - "video_vae" is not a file and must not be sent
 * as one. So: take a filename-looking widget if this node has one,
 * otherwise jump a Get node to its Set node and follow the first
 * connected input, a few hops at most.
 */
function loaderFileUpstream(graph, node, depth = 0) {
  if (!node || depth > 8) return null;
  const file = nodeWidgetStrings(node).find((v) => MODEL_FILE.test(v));
  if (file) return file;
  const type = String(node.type || "");
  if (/^GetNode$/i.test(type) || /\bGet\b/.test(type)) {
    const varName = nodeWidgetStrings(node)[0];
    const setter = (graph._nodes || []).find((n) =>
      n !== node && /Set/.test(String(n.type || "")) &&
      nodeWidgetStrings(n)[0] === varName);
    if (setter) return loaderFileUpstream(graph, upstreamNode(graph, setter, 0),
                                          depth + 1);
  }
  for (let i = 0; i < (node.inputs || []).length; i++) {
    const up = upstreamNode(graph, node, i);
    if (up) {
      const found = loaderFileUpstream(graph, up, depth + 1);
      if (found) return found;
    }
  }
  return null;
}

// Core's Resolution Selector, mirrored so the Hub can say what a given
// setting will produce BEFORE the queue refuses it. Kept identical to
// comfy_extras/nodes_resolution.py: megapixels are 1024-based, and each
// side is rounded (not floored) to the multiple.
const ASPECT_RATIOS = {
  "1:1 (Square)": [1, 1],
  "2:3 (Portrait Photo)": [2, 3],
  "3:2 (Photo)": [3, 2],
  "3:4 (Portrait Standard)": [3, 4],
  "4:3 (Standard)": [4, 3],
  "9:16 (Portrait Widescreen)": [9, 16],
  "16:9 (Widescreen)": [16, 9],
  "21:9 (Ultrawide)": [21, 9],
};
const H3_MULTIPLE = 32;

function resolutionFrom(ratio, megapixels, multiple) {
  const r = ASPECT_RATIOS[ratio];
  if (!r || !(megapixels > 0) || !(multiple > 0)) return null;
  const scale = Math.sqrt((megapixels * 1024 * 1024) / (r[0] * r[1]));
  return { width: Math.round((r[0] * scale) / multiple) * multiple,
           height: Math.round((r[1] * scale) / multiple) * multiple };
}

function widgetValue(node, name, positional) {
  const live = node?.widgets?.find((w) => w?.name === name);
  if (live && live.value != null) return live.value;
  const vals = node?.widgets_values;
  return Array.isArray(vals) ? vals[positional] : undefined;
}

/** The Resolution Selector feeding this node's width input, if there is
 *  one, with what it currently produces. */
function upstreamSelector(node) {
  try {
    const graph = node?.graph || app?.graph;
    if (!graph || !node?.inputs) return null;
    const idx = node.inputs.findIndex((i) => i && i.name === "width");
    if (idx < 0) return null;
    let src = upstreamNode(graph, node, idx);
    for (let hop = 0; src && hop < 8; hop++) {
      const ratio = widgetValue(src, "aspect_ratio", 0);
      if (ratio && ASPECT_RATIOS[ratio]) {
        const mp = Number(widgetValue(src, "megapixels", 1));
        const multiple = Number(widgetValue(src, "multiple", 2)) || 8;
        return { ratio, mp, multiple,
                 size: resolutionFrom(ratio, mp, multiple) };
      }
      src = upstreamNode(graph, src, 0);   // through a reroute
    }
  } catch (e) { /* an unreadable graph just means no advice */ }
  return null;
}

/** A Resolution Selector setting that lands exactly on w x h, preferring
 *  the ratio already chosen. */
function selectorSettingFor(w, h, preferRatio) {
  const names = Object.keys(ASPECT_RATIOS);
  if (preferRatio && names.includes(preferRatio)) {
    names.splice(names.indexOf(preferRatio), 1);
    names.unshift(preferRatio);
  }
  for (const ratio of names) {
    for (let mp = 1; mp <= 160; mp++) {          // 0.1 .. 16.0 MP
      const got = resolutionFrom(ratio, mp / 10, H3_MULTIPLE);
      if (got && got.width === w && got.height === h) {
        return `${ratio.split(" ")[0]} at ${(mp / 10).toFixed(1)} MP`;
      }
    }
  }
  return null;
}

/** The Hub summary's size line: red only when the next queue would be
 *  refused, so red always means "this is about to fail". */
function hubSizeLine(node, state) {
  const res = state?.resolution || null;
  const sel = upstreamSelector(node);
  const px = (s) => `${s.width} × ${s.height}`;
  const red = (t) => `<div class="h3p-autowarn">⚠ ${t}</div>`;

  if (sel && sel.multiple !== H3_MULTIPLE) {
    return red(`Resolution Selector's <b>multiple</b> is ${sel.multiple} ` +
      `— set it to ${H3_MULTIPLE}. H3 needs both sides a multiple ` +
      `of ${H3_MULTIPLE}, and the next queue will be refused.`);
  }
  if (sel && res && sel.size &&
      (sel.size.width !== res.width || sel.size.height !== res.height)) {
    const fix = selectorSettingFor(res.width, res.height, sel.ratio);
    return red(`Resolution Selector gives ${px(sel.size)} but this ` +
      `project is ${px(res)}. ` +
      (fix ? `Set it to <b>${fix}</b>, ` : "Match it, ") +
      `or unwire it — the next queue will be refused.`);
  }
  if (res) {
    return `<br><span class="nx">renders at ${px(res)}</span>`;
  }
  if (sel && sel.size) {
    return `<br><span class="nx">first clip will set this project to ` +
      `${px(sel.size)}</span>`;
  }
  return `<br><span class="nx">size not set — the first clip ` +
    `decides it</span>`;
}

function wiredVaeNames(node, inputNames = ["vae", "audio_vae"]) {
  const out = [];
  try {
    const graph = node?.graph || app?.graph;
    if (!graph || !node?.inputs) return out;
    for (const want of inputNames) {
      const idx = node.inputs.findIndex((i) => i && i.name === want);
      if (idx < 0) continue;
      const name = loaderFileUpstream(graph, upstreamNode(graph, node, idx));
      if (name && out.indexOf(name) === -1) out.push(name);
    }
  } catch (e) { /* an unreadable graph just means no names */ }
  return out;
}

/** Pick a window out of a source video, seeing exactly what is cut.
 *
 * H3 can only render certain lengths (5, 22, 39, 56 ... frames), so
 * importing footage always means dropping some. The filmstrip shows the
 * whole source with the kept span lit and the dropped ends dimmed; the
 * span snaps to valid lengths as it is resized, so an invalid window
 * cannot be chosen. Scrubbing plays the real file underneath.
 */
class ImportModal {
  constructor(panel) {
    this.panel = panel;
    this.file = null;
    this.info = null;
    this.start = 0;
    this.len = 0;

    this.picker = el("select", {
      onchange: (e) => this.choose(e.target.value),
    });
    this.fileInput = el("input", {
      type: "file", accept: "video/*", style: "display:none",
      onchange: (e) => {
        const f = e.target.files && e.target.files[0];
        if (f) this.upload(f);
        e.target.value = "";
      },
    });
    this.dropZone = el("div", { class: "h3p-drop",
                                text: "Drop a video to import" });
    this.empty = el("div", { class: "h3p-empty" },
      el("div", { text: "No videos yet." }),
      el("div", { text: "Drop one anywhere in this window, or:" }),
      el("button", { class: "h3p-btn primary", text: "Choose a video\u2026",
                     onclick: () => this.fileInput.click() }));
    this.video = el("video", { class: "h3p-impvid", controls: false,
                               preload: "metadata" });
    this.video.addEventListener("timeupdate", () => this.drawHead());
    // the crop box sits over the picture, not over the element: a video
    // letterboxes itself inside its box, so every rectangle here is
    // measured against the real picture area (see paintCrop)
    this.cropBox = el("div", { class: "h3p-cropbox" });
    this.cropBox.addEventListener("mousedown", (e) => this.grabCrop(e));
    this.cropWrap = el("div", { class: "h3p-cropwrap" },
                       this.video, this.cropBox);
    this.cropOffset = 0.5;          // 0 = top/left, 1 = bottom/right
    this.fitMode = "fill";          // "fill" crops, "fit" adds bars
    this.fillBtn = el("button", { class: "h3p-btn on", text: "Fill",
      title: "crop to the project's shape, losing the edges",
      onclick: () => this.setFit("fill") });
    this.fitBtn = el("button", { class: "h3p-btn", text: "Fit",
      title: "keep the whole frame and add bars",
      onclick: () => this.setFit("fit") });
    this.cropHint = el("span", { class: "h3p-hint" });
    this.cropRow = el("div", { class: "h3p-inline" },
      el("span", { class: "h3p-takelabel", text: "framing" }),
      this.fillBtn, this.fitBtn, this.cropHint);
    for (const ev of ["loadedmetadata", "resize"]) {
      this.video.addEventListener(ev, () => this.paintCrop());
    }
    window.addEventListener("resize", () => this.paintCrop());
    this.stripImgs = el("div", { class: "h3p-stripimgs" });
    this.cutL = el("div", { class: "h3p-cut" });
    this.cutR = el("div", { class: "h3p-cut" });
    this.keepBox = el("div", { class: "h3p-keep" });
    this.head = el("div", { class: "h3p-playhead" });
    this.strip = el("div", { class: "h3p-strip" },
                    this.stripImgs, this.cutL, this.cutR, this.keepBox,
                    this.head);
    this.strip.addEventListener("mousedown", (e) => this.grab(e));
    this.info2 = el("div", { class: "h3p-impinfo" });
    this.lenSel = el("select", {
      onchange: (e) => { this.setLen(Number(e.target.value)); },
    });
    this.importBtn = el("button", { class: "h3p-btn primary",
                                    text: "Import as clip",
                                    onclick: () => this.doImport() });
    this.body = el("div", { class: "h3p-impwrap" },
      this.empty, this.fileInput,
      this.cropWrap, this.strip, this.info2, this.cropRow,
      el("div", { class: "h3p-inline" },
        el("span", { class: "h3p-takelabel", text: "length" }), this.lenSel,
        el("div", { class: "h3p-spacer" }),
        el("button", { class: "h3p-btn", text: "Play window",
                       onclick: () => this.playWindow() }),
        this.importBtn));

    this.overlay = el("div", {
      class: "h3p-overlay",
      onmousedown: (e) => { if (e.target === this.overlay) this.close(); },
    },
      el("div", { class: "h3p-modal" },
        el("div", { class: "h3p-head" },
          el("div", { class: "h3p-title", text: "Import footage" },
             el("small", { text: "pick what to keep" })),
          el("div", { class: "h3p-spacer" }),
          el("button", { class: "h3p-x", text: "\u2715",
                         onclick: () => this.close() })),
        el("div", { class: "h3p-impbar" },
          el("span", { class: "h3p-takelabel", text: "file" }),
          this.picker,
          el("button", { class: "h3p-btn", text: "Choose\u2026",
                         title: "pick a video from your computer",
                         onclick: () => this.fileInput.click() }),
          el("button", { class: "h3p-btn", text: "Refresh",
                         onclick: () => this.loadFiles() })),
        this.body,
        this.dropZone));
    this._esc = (e) => {
      // only the topmost window answers; see isTopOverlay
      if (e.key === "Escape" && isTopOverlay(this.overlay)) this.close();
    };

    // drag events fire per child element, so a plain enter/leave pair
    // flickers; count them instead
    this._dragDepth = 0;
    const modal = this.overlay.querySelector(".h3p-modal");
    modal.addEventListener("dragenter", (e) => {
      if (!this._hasFiles(e)) return;
      e.preventDefault();
      this._dragDepth += 1;
      this.dropZone.classList.add("on");
    });
    modal.addEventListener("dragover", (e) => {
      if (this._hasFiles(e)) e.preventDefault();
    });
    modal.addEventListener("dragleave", () => {
      this._dragDepth = Math.max(0, this._dragDepth - 1);
      if (!this._dragDepth) this.dropZone.classList.remove("on");
    });
    modal.addEventListener("drop", (e) => {
      if (!this._hasFiles(e)) return;
      e.preventDefault();
      this._dragDepth = 0;
      this.dropZone.classList.remove("on");
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) this.upload(f);
    });
  }

  _hasFiles(e) {
    const t = e.dataTransfer && e.dataTransfer.types;
    return !!t && Array.prototype.indexOf.call(t, "Files") !== -1;
  }

  async upload(file) {
    const mb = (file.size / 1048576).toFixed(0);
    toast(`uploading ${file.name} (${mb} MB)\u2026`);
    const fd = new FormData();
    fd.append("file", file, file.name);
    try {
      const r = await postApi("/h3_suite/source/upload", { body: fd });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      await this.loadFiles(d.rel);
      toast(`${d.rel} ready`);
    } catch (e) {
      toast(e.message, true);
    }
  }

  async open() {
    document.body.append(this.overlay);
    try {
      sizeScaledBox(this.overlay.querySelector(".h3p-modal"), 1240, 820,
                    loadScalePrefs());
    } catch (e) { /* a corrupt pref must not block the import */ }
    document.addEventListener("keydown", this._esc);
    // the project's picture size, if it has one: an import must match
    // it, and the info line says so before anything is encoded
    this.projectRes = this.panel.state?.resolution || null;
    if (!this.projectRes) {
      try {
        this.projectRes = (await getState(this.panel.name())).resolution
          || null;
      } catch (e) { this.projectRes = null; }
    }
    await this.loadFiles();
  }

  setFit(mode) {
    this.fitMode = mode;
    this.fillBtn.className = "h3p-btn" + (mode === "fill" ? " on" : "");
    this.fitBtn.className = "h3p-btn" + (mode === "fit" ? " on" : "");
    this.paintCrop();
    if (this.info) this.render();
  }

  /** Where the picture actually is inside the video element, and what
   *  the kept rectangle is within it. Null when there is nothing to
   *  choose: no metadata yet, or the aspects already agree. */
  cropGeometry() {
    const v = this.video;
    const sw = v.videoWidth, sh = v.videoHeight;
    if (!sw || !sh || !this.info) return null;
    const t = this.targetSize();
    const cw = v.clientWidth, ch = v.clientHeight;
    if (!cw || !ch) return null;
    // a video letterboxes itself inside its element
    const shown = Math.min(cw / sw, ch / sh);
    const pw = sw * shown, ph = sh * shown;
    const px = (cw - pw) / 2, py = (ch - ph) / 2;

    const ta = t.width / t.height, sa = sw / sh;
    let keepW = sw, keepH = sh, axis = null;
    if (Math.abs(sa - ta) > 0.001) {
      if (sa > ta) { keepW = sh * ta; axis = "x"; }
      else { keepH = sw / ta; axis = "y"; }
    }
    const slackX = sw - keepW, slackY = sh - keepH;
    const x0 = slackX * (axis === "x" ? this.cropOffset : 0.5);
    const y0 = slackY * (axis === "y" ? this.cropOffset : 0.5);
    return { axis, shown, target: t,
             left: px + x0 * shown, top: py + y0 * shown,
             width: keepW * shown, height: keepH * shown,
             pictureLeft: px, pictureTop: py,
             pictureWidth: pw, pictureHeight: ph,
             slack: axis === "x" ? slackX : slackY,
             lost: axis === "x" ? slackX / sw : slackY / sh };
  }

  paintCrop() {
    const g = this.cropGeometry();
    const box = this.cropBox;
    if (!g || !g.axis || this.fitMode !== "fill") {
      box.style.display = "none";
    } else {
      box.style.display = "";
      box.style.left = `${g.left}px`;
      box.style.top = `${g.top}px`;
      box.style.width = `${g.width}px`;
      box.style.height = `${g.height}px`;
      box.style.cursor = g.axis === "x" ? "ew-resize" : "ns-resize";
    }
    if (this.cropRow) {
      const off = !g || !g.axis;
      this.cropRow.style.display = off ? "none" : "flex";
      if (!off) {
        this.cropHint.textContent = this.fitMode === "fill"
          ? `drag the box — ${Math.round(g.lost * 100)}% of the ` +
            `${g.axis === "x" ? "width" : "height"} is cut`
          : "whole frame kept, bars added";
      }
    }
  }

  grabCrop(ev) {
    const g = this.cropGeometry();
    if (!g || !g.axis || !g.slack) return;
    ev.preventDefault();
    const startPos = g.axis === "x" ? ev.clientX : ev.clientY;
    const startOff = this.cropOffset;
    const span = g.slack * g.shown;      // travel, in screen pixels
    const move = (e) => {
      const d = (g.axis === "x" ? e.clientX : e.clientY) - startPos;
      this.cropOffset = Math.max(0, Math.min(startOff + d / span, 1));
      this.paintCrop();
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      this.render();
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  // what the server will encode this footage at (see target_size)
  targetSize() {
    if (this.projectRes) return { ...this.projectRes, why: "project" };
    const snap = (n) => Math.max(32, Math.floor(n / 32) * 32);
    return { width: snap(this.info.width), height: snap(this.info.height),
             why: "source" };
  }

  close() {
    document.removeEventListener("keydown", this._esc);
    try { this.video.pause(); } catch (e) { /* already gone */ }
    this.overlay.remove();
    this.panel.refresh(true);
  }

  async loadFiles(prefer) {
    this.picker.innerHTML = "";
    let files = [];
    try {
      const r = await api.fetchApi("/h3_suite/source/list");
      files = (await r.json()).files || [];
    } catch (e) { toast(e.message, true); }
    const has = files.length > 0;
    this.empty.style.display = has ? "none" : "flex";
    for (const nm of ["video", "strip", "info2"]) {
      this[nm].style.display = has ? "" : "none";
    }
    this.picker.disabled = !has;
    if (!has) {
      this.picker.append(el("option", { value: "", text: "no videos yet" }));
      return;
    }
    for (const f of files) {
      this.picker.append(el("option", { value: f.rel, text: f.rel }));
    }
    const pick = prefer && files.some((f) => f.rel === prefer)
      ? prefer : files[0].rel;
    this.picker.value = pick;
    await this.choose(pick);
    this.checkVaes();
  }

  checkVaes() {
    const names = wiredVaeNames(this.panel.node);
    const ok = names.length > 0;
    this.importBtn.disabled = !ok;
    this.importBtn.title = ok
      ? `encoding with ${names.join(" and ")}`
      : "wire the VAEs into the Project Hub node first";
    if (!ok) {
      this.info2.innerHTML =
        `<span class="drop">Wire the H3 video and audio VAEs into the ` +
        `Project Hub node's <b>vae</b> and <b>audio_vae</b> inputs, then ` +
        `reopen this window. Importing has to encode the footage, and ` +
        `those inputs are how it knows which models to use.</span>`;
    }
  }

  async choose(rel) {
    if (!rel) return;
    this.file = rel;
    this.info = null;
    this.info2.textContent = "reading\u2026";
    try {
      const r = await api.fetchApi(
        `/h3_suite/source/probe?rel=${encodeURIComponent(rel)}`);
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      this.info = d;
    } catch (e) {
      this.info2.textContent = e.message;
      return;
    }
    this.video.src =
      `/h3_suite/source/file?rel=${encodeURIComponent(rel)}`;
    const valid = this.info.valid_lengths || [];
    this.lenSel.innerHTML = "";
    for (const n of valid.slice().reverse()) {
      this.lenSel.append(el("option", { value: String(n),
        text: `${n} frames \u00b7 ${(n / 24).toFixed(2)}s` }));
    }
    // default: the longest window that fits, anchored at the END, since
    // imported footage usually runs INTO the chain
    this.len = valid.length ? valid[valid.length - 1] : 0;
    this.start = Math.max(0, (this.info.resampled || 0) - this.len);
    this.lenSel.value = String(this.len);
    this.buildStrip();
    this.render();
  }

  async buildStrip() {
    this.stripImgs.innerHTML = "";
    const n = 12;
    const dur = this.info?.duration || 0;
    if (!dur) return;
    // thumbnails are grabbed from the same <video> the panel scrubs, so
    // no extra decode path and no server-side thumbnailer
    const v = document.createElement("video");
    v.src = this.video.src;
    v.muted = true;
    const canvases = [];
    for (let i = 0; i < n; i++) {
      const c = document.createElement("canvas");
      c.width = 160; c.height = 90;
      this.stripImgs.append(c);
      canvases.push(c);
    }
    await new Promise((res) => {
      v.addEventListener("loadeddata", res, { once: true });
      setTimeout(res, 4000);
    });
    for (let i = 0; i < n; i++) {
      const t = (dur * (i + 0.5)) / n;
      try {
        await new Promise((res) => {
          v.addEventListener("seeked", res, { once: true });
          v.currentTime = t;
          setTimeout(res, 1200);
        });
        const cx = canvases[i].getContext("2d");
        cx.drawImage(v, 0, 0, canvases[i].width, canvases[i].height);
      } catch (e) { /* a thumbnail that will not draw is not fatal */ }
    }
  }

  setLen(n) {
    const total = this.info?.resampled || 0;
    this.len = Math.min(n, total);
    this.start = Math.min(this.start, Math.max(0, total - this.len));
    this.render();
  }

  grab(e) {
    const total = this.info?.resampled || 0;
    if (!total) return;
    const rect = this.strip.getBoundingClientRect();
    const at = (ev) => Math.round(
      ((ev.clientX - rect.left) / rect.width) * total);
    const startAt = at(e);
    const from = this.start;
    const inside = startAt >= this.start && startAt < this.start + this.len;
    if (!inside) {
      this.start = Math.max(0, Math.min(total - this.len,
                                        startAt - Math.floor(this.len / 2)));
      this.render();
      this.seekTo(this.start);
    }
    this.strip.classList.add("drag");
    const move = (ev) => {
      const d = at(ev) - startAt;
      this.start = Math.max(0, Math.min(total - this.len,
                                        (inside ? from : this.start) + d));
      this.render();
    };
    const up = () => {
      this.strip.classList.remove("drag");
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      this.seekTo(this.start);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  seekTo(frame) {
    try { this.video.currentTime = frame / 24; } catch (e) { /* seeking */ }
  }

  playWindow() {
    if (!this.info) return;
    this.seekTo(this.start);
    this.video.play();
    const stop = () => {
      if (this.video.currentTime >= (this.start + this.len) / 24) {
        this.video.pause();
        this.video.removeEventListener("timeupdate", stop);
      }
    };
    this.video.addEventListener("timeupdate", stop);
  }

  drawHead() {
    const total = this.info?.resampled || 0;
    if (!total) return;
    const f = this.video.currentTime * 24;
    this.head.style.left = `${(f / total) * 100}%`;
  }

  render() {
    const total = this.info?.resampled || 0;
    if (!total) return;
    const pct = (n) => `${(n / total) * 100}%`;
    this.cutL.style.left = "0";
    this.cutL.style.width = pct(this.start);
    this.cutR.style.left = pct(this.start + this.len);
    this.cutR.style.width = pct(total - this.start - this.len);
    this.keepBox.style.left = pct(this.start);
    this.keepBox.style.width = pct(this.len);
    const drop = total - this.len;
    const i = this.info;
    this.info2.innerHTML =
      `<span>source <b>${i.frames}</b> frames at <b>${
        (+i.fps).toFixed(2)}</b> fps` +
      (Math.abs(i.fps - 24) > 0.01
        ? ` \u2192 <b>${total}</b> at 24` : "") +
      `</span><span>keeping <b>${this.start}\u2013${
        this.start + this.len - 1}</b> (<b>${this.len}</b> frames, ` +
      `${(this.len / 24).toFixed(2)}s)</span>` +
      (drop ? `<span class="drop">dropping ${drop} frame${
        drop === 1 ? "" : "s"} (${(drop / 24).toFixed(2)}s)</span>` : "") +
      (i.has_audio ? "" : "<span>no audio track</span>") +
      this.sizeNote();
    this.paintCrop();
    const names = wiredVaeNames(this.panel.node);
    if (!names.length) this.checkVaes();
  }

  sizeNote() {
    const i = this.info;
    const t = this.targetSize();
    const same = t.width === i.width && t.height === i.height;
    const aspectOff =
      Math.abs(i.width / i.height - t.width / t.height) > 0.01;
    const how = !aspectOff ? "resized"
      : (this.fitMode === "fit" ? "fitted with bars" : "cropped and resized");
    if (t.why === "project") {
      if (same) return `<span>${i.width}×${i.height}, as the project</span>`;
      return `<span class="drop">${how} ${i.width}×${i.height} → ` +
        `<b>${t.width}×${t.height}</b> to match the project</span>`;
    }
    return `<span>${same ? "" : `${i.width}×${i.height} → `}` +
      `<b>${t.width}×${t.height}</b> sets the project's size</span>`;
  }

  async doImport() {
    if (!this.info || !this.len) return;
    this.importBtn.disabled = true;
    toast("decoding and encoding the window\u2026");
    try {
      const t = this.targetSize();
      const out = await post("/h3_suite/source/import", {
        name: this.panel.name(), rel: this.file,
        start: this.start, frames: this.len,
        width: t.width, height: t.height,
        fit: this.fitMode, crop_offset: this.cropOffset,
        with_audio: !!this.info.has_audio,
        vae_names: wiredVaeNames(this.panel.node),
      });
      const im = out.imported || {};
      toast(`imported as ${im.basename} \u2014 review it, then approve`);
      this.close();
    } catch (e) {
      toast(e.message, true);
    } finally {
      this.importBtn.disabled = false;
    }
  }
}

class ProjectModal extends ChainTimeline {
  constructor(node) {
    super();
    injectCSS();
    this.initPlayer();
    this.node = node;
    this.state = null;
    this.viewing = null; // basename explicitly selected in the rail

    this.select = el("select", { class: "h3p-select",
                                 onchange: () => this.switchProject() });
    this.nameInput = el("input", { class: "h3p-input",
                                   placeholder: "new project name" });
    this.nameInput.onkeydown = (e) => {
      if (e.key === "Enter") this.createProject();
      if (e.key === "Escape") this.nameWrap.classList.remove("on");
      e.stopPropagation();
    };
    // the panel follows the queue on its own, but 'executed' can be missed
    // if the browser was asleep, the tab was closed mid-render, or a clip
    // arrived from another window. This forces a re-read from disk.
    this.refreshBtn = el("button", {
      class: "h3p-btn", text: "\u21bb",
      title: "check the project folder for new clips",
      onclick: () => this.manualRefresh() });

    this.nameWrap = el("div", { class: "h3p-namewrap" },
      this.nameInput,
      el("button", { class: "h3p-btn ok", text: "Create",
                     onclick: () => this.createProject() }),
      el("button", { class: "h3p-btn", text: "Cancel",
                     onclick: () => this.nameWrap.classList.remove("on") }));

    this.mode = "timeline";
    this.curSeg = null;
    this.durations = {};
    this.timeline = [];
    this.total = 0;

    this.viewTitle = el("div", { class: "h3p-viewtitle" });
    this.viewSub = el("div", { class: "h3p-viewsub" });

    this.btnTimeline = el("button", { text: "Timeline",
                                      onclick: () => this.setMode("timeline") });
    this.btnClip = el("button", { text: "Single clip",
                                  onclick: () => this.setMode("clip") });
    this.modes = el("div", { class: "h3p-modes" },
                    this.btnTimeline, this.btnClip);

    this.takeSel = el("select", { class: "h3p-select",
      onchange: () => this.selectTake(this.takeSel.value) });
    this.takeWrap = el("div", { class: "h3p-takes" },
      el("span", { class: "h3p-takelabel", text: "take" }), this.takeSel);

    this.exportInput = el("input", { class: "h3p-input" });
    this.exportInput.onkeydown = (e) => {
      if (e.key === "Enter") this.doExport();
      if (e.key === "Escape") this.closeExportNaming();
      e.stopPropagation();
    };
    // every master this project has written, newest first, and a way to
    // get one out over HTTP - the only way out when ComfyUI runs
    // somewhere the output folder cannot be browsed. Hidden until the
    // project has an export.
    this.exports = [];
    this.exportsSel = el("select", { class: "h3p-select",
      title: "masters and previews this project has written",
      onchange: () => this.paintExports() });
    this.downloadLink = el("a", { class: "h3p-btn", text: "Download",
      title: "save the selected export through the browser" });
    this.exportsWrap = el("span", { class: "h3p-inline",
                                    style: "display:none" },
      el("span", { class: "h3p-takelabel", text: "exports" }),
      this.exportsSel, this.downloadLink);
    // one fixed row: buttons by default, the name field in their place
    // while naming. Same height either way, so nothing below it moves.
    this.latentExportBox = el("input", {
      type: "checkbox",
      onchange: (e) => {
        this.scalePrefs.exportFromLatents = !!e.target.checked;
        saveScalePrefs(this.scalePrefs);
      },
    });
    this.latentExportWrap = el("label", { class: "h3p-auto",
      title: "assemble the master by decoding the saved latents instead " +
             "of joining the clip videos. Every frame is encoded exactly " +
             "once, and level matching happens before that encode. " +
             "Slower, and needs a clip queued this session so the " +
             "graph's VAEs are known." },
      this.latentExportBox, el("span", { text: "from latents" }));
    this.exportBtns = el("div", { class: "h3p-inline" },
      el("button", { class: "h3p-btn", text: "Export master",
                     onclick: () => this.exportMaster(false) }),
      el("button", { class: "h3p-btn", text: "Export + pending",
                     title: "seamless concat including the clip awaiting " +
                            "review \u2014 judge the join without the " +
                            "player's boundary stutter",
                     onclick: () => this.exportMaster(true) }),
      this.latentExportWrap,
      this.exportsWrap);
    this.qualitySel = el("select", {
      title: "how the master itself is encoded. The review clips are " +
             "unaffected.",
      onchange: (e) => {
        this.scalePrefs.masterQuality = e.target.value;
        saveScalePrefs(this.scalePrefs);
      },
    });
    for (const [v, label] of [
      ["16:medium", "High \u00b7 CRF 16 (default)"],
      ["14:slow", "Archive \u00b7 CRF 14, slow"],
      ["18:fast", "Quick \u00b7 CRF 18, fast"],
    ]) {
      this.qualitySel.append(el("option", { value: v, text: label }));
    }
    this.qualityWrap = el("span", { class: "h3p-inline",
                                    style: "display:none" },
      el("span", { class: "h3p-takelabel", text: "quality" }),
      this.qualitySel);
    this.exportNaming = el("div", { class: "h3p-inline",
                                    style: "display:none" },
      el("span", { class: "h3p-takelabel", text: "save as" }),
      this.exportInput,
      this.qualityWrap,
      el("button", { class: "h3p-btn ok", text: "Write",
                     onclick: () => this.doExport() }),
      el("button", { class: "h3p-btn", text: "Cancel",
                     onclick: () => this.closeExportNaming() }));
    this.exportRow = el("div", { class: "h3p-exportrow" },
      this.exportBtns, this.exportNaming);

    this.actions = el("div", { class: "h3p-actions" });
    this.railBody = el("div", { class: "h3p-railbody" });
    this.footText = el("div", {});

    this.scalePrefs = loadScalePrefs();
    this.scaleMenu = el("div", { class: "h3p-scalemenu" });
    this.scaleBtn = el("button", {
      class: "h3p-btn", text: "Scale",
      title: "resize this window, or just its text",
      onclick: (e) => {
        e.stopPropagation();
        this.scaleMenu.classList.toggle("on");
      },
    });
    this.scaleWrap = el("div", { class: "h3p-scalewrap" },
                        this.scaleBtn, this.scaleMenu);
    this.buildScaleMenu();
    this.autoBox = el("input", {
      type: "checkbox",
      onchange: (e) => this.toggleAuto(e.target.checked),
    });
    this.autoWrap = el("label", { class: "h3p-auto",
                                  title: "approve every render as it " +
                                         "finishes, without stopping for " +
                                         "review" },
                       this.autoBox, el("span", { text: "Auto-approve" }));
    this.autoBar = el("div", { class: "h3p-autobar",
                               style: "display:none;" },
      el("span", { text: "\u26a0 Auto-approval is on \u2014 the chain is " +
                         "progressing without manual review" }),
      el("div", { class: "sp" }),
      el("button", { class: "h3p-btn", text: "Turn off",
                     onclick: () => this.toggleAuto(false) }));
    this.overlay = el("div", {
      class: "h3p-overlay",
      onmousedown: (e) => {
        if (!this.scaleWrap?.contains(e.target)) {
          this.scaleMenu?.classList.remove("on");
        }
        if (e.target === this.overlay) this.close();
      },
    },
      el("div", { class: "h3p-modal" },
        el("div", { class: "h3p-head" },
          el("div", { class: "h3p-title", text: "H3 Project Hub" },
            el("small", { text: "sequential clip chain" })),
          this.select,
          el("button", { class: "h3p-btn", text: "New",
                         onclick: () => this.showNameEntry() }),
          this.refreshBtn,
          this.nameWrap,
          el("div", { class: "h3p-spacer" }),
          this.autoWrap,
          this.scaleWrap,
          el("button", { class: "h3p-btn", text: "Import\u2026",
                         title: "bring in outside footage as a clip, " +
                                "choosing what to keep",
                         onclick: () => new ImportModal(this).open() }),
          el("button", { class: "h3p-btn", text: "Copy folder path",
                         title: "where this project lives, on the machine " +
                                "running ComfyUI",
                         onclick: () => this.openFolder() }),
          el("button", { class: "h3p-btn", text: "Measure drift",
                         title: "how brightness, contrast, sharpness and " +
                                "colour move across the chain",
                         onclick: () => this.measureDrift() }),
          el("button", { class: "h3p-btn", text: "Clean up takes",
                         title: "move every alternate take to .trash/; " +
                                "the chain is untouched",
                         onclick: () => this.cleanupTakes() }),
          el("button", { class: "h3p-btn", text: "Purge trash",
                         onclick: () => this.purge() }),
          el("button", { class: "h3p-x", text: "\u2715",
                         onclick: () => this.close() })),
        this.autoBar,
        el("div", { class: "h3p-body" },
          el("div", { class: "h3p-main" },
            el("div", { class: "h3p-viewhead" }, this.viewTitle,
               this.viewSub,
               el("div", { class: "h3p-spacer" }), this.modes),
            this.player, this.transport, this.confirm,
            this.exportRow, this.actions),
          el("div", { class: "h3p-rail" },
            el("div", { class: "h3p-railhead", text: "Chain" }),
            this.railBody)),
        this.drift,
        el("div", { class: "h3p-foot" }, this.footText)));

    this._esc = (e) => {
      // only the topmost window answers; see isTopOverlay
      if (e.key === "Escape" && isTopOverlay(this.overlay)) this.close();
    };
    this._onExec = () => this.refresh(true);
    // coming back to the tab is the moment a missed 'executed' shows up,
    // so re-read then too. Cheap: one small JSON fetch.
    this._onFocus = () => {
      if (document.visibilityState === "visible") this.refresh(true);
    };
  }

  open() {
    this._sig = null;
    this.durations = {};
    document.body.append(this.overlay);
    this.applyScale();
    document.addEventListener("keydown", this._esc);
    api.addEventListener("executed", this._onExec);
    document.addEventListener("visibilitychange", this._onFocus);
    window.addEventListener("focus", this._onFocus);
    this.loadProjects().then(() => this.refresh(true));
  }

  close() {
    this.stopPlayer();
    this.overlay.remove();
    document.removeEventListener("keydown", this._esc);
    api.removeEventListener("executed", this._onExec);
    document.removeEventListener("visibilitychange", this._onFocus);
    window.removeEventListener("focus", this._onFocus);
    // the "a new take just landed, play into it" marker is only meaningful
    // while the panel is open and watching. This object outlives the
    // window, so without this a take that arrived while it was closed
    // would count as new on the next open and start playing unasked.
    this._timelinePending = null;
    this.node._h3RefreshSummary?.();
  }

  name() {
    return this.node.widgets?.find((w) => w.name === "project_name")
      ?.value || "";
  }

  clipURL(basename) {
    return videoURL(this.name(), basename);
  }

  setName(v) {
    const w = this.node.widgets?.find((w2) => w2.name === "project_name");
    if (w && w.value !== v) {
      w.value = v;
      this.node.setDirtyCanvas(true, true);
    }
  }

  showNameEntry() {
    this.nameInput.value = "";
    this.nameWrap.classList.add("on");
    this.nameInput.focus();
  }

  showConfirmMsg(msg) {
    this.confirmMsg.textContent = msg;
    this.confirmBtns.innerHTML = "";
    this.confirm.classList.add("on");
  }

  showConfirm(msg, onYes) {
    this.showChoices(msg, [{ label: "Confirm", cls: "warn", pick: onYes }]);
  }

  showChoices(msg, choices) {
    this.confirmMsg.textContent = msg;
    this.confirmBtns.innerHTML = "";
    for (const c of choices) {
      this.confirmBtns.append(el("button", {
        class: "h3p-btn " + (c.cls || ""),
        text: c.label,
        title: c.title || "",
        onclick: () => { this.hideConfirm(); c.pick(); },
      }));
    }
    this.confirmBtns.append(el("button", {
      class: "h3p-btn", text: "Cancel",
      onclick: () => this.hideConfirm() }));
    this.confirm.classList.add("on");
  }

  hideConfirm() {
    this.confirm.classList.remove("on");
  }

  async loadProjects() {
    try {
      const r = await api.fetchApi("/h3_suite/projects");
      const { projects } = await r.json();
      const current = this.name();
      this.select.innerHTML = "";
      const names = projects.includes(current) || !current
        ? projects : [current, ...projects];
      for (const n of names) {
        this.select.append(el("option", { value: n, text: n }));
      }
      if (current) this.select.value = current;
    } catch (e) { /* routes absent */ }
  }

  switchProject() {
    this.setName(this.select.value);
    this.viewing = null;
    this.hideConfirm();
    this.closeExportNaming();
    this.refresh(true);
  }

  async createProject() {
    const name = this.nameInput.value.trim();
    if (!name) return;
    try {
      await post("/h3_suite/project/create", { name });
      this.nameWrap.classList.remove("on");
      this.setName(name);
      await this.loadProjects();
      this.select.value = name;
      this.viewing = null;
      toast(`project "${name}" created`);
      this.refresh(true);
    } catch (e) { toast(e.message, true); }
  }

  async act(action, okMsg) {
    try {
      await post(`/h3_suite/project/${action}`, { name: this.name() });
      if (okMsg) toast(okMsg);
      this.viewing = null;
      this.refresh(true);
    } catch (e) { toast(e.message, true); }
  }

  reopen(index) {
    post("/h3_suite/project/reopen", { name: this.name(), index })
      .then((probe) => {
        const drop = probe.would_drop || [];
        const run = async (snapshot) => {
          try {
            const out = await post("/h3_suite/project/reopen", {
              name: this.name(), index, confirm: true,
              snapshot: !!snapshot,
              snapshot_name: probe.snapshot_name,
            });
            toast(out.snapshot
              ? `clip ${index} reopened \u2014 backup saved as ` +
                `"${out.snapshot}"`
              : `clip ${index} reopened`);
            this.viewing = null;
            this._sig = null;
            this.durations = {};
            await this.loadProjects();
            this.refresh(true);
          } catch (e) { toast(e.message, true); }
        };

        if (!drop.length) {
          // nothing downstream to lose; this is just an un-approve
          this.showConfirm(
            `Reopen clip ${index}? It goes back to pending, and the next ` +
            `queue press re-renders it as a new take.`,
            () => run(false));
          return;
        }

        const n = drop.length;
        const pend = this.state?.pending;
        const pendNote = pend && pend.index > index
          ? `\n\n(That includes clip ${pend.index}, still awaiting ` +
            `review — it continues from clip ${index}, so it goes too.)`
          : "";
        const msg =
          `Reopen clip ${index}?\n\n` +
          `${n} later clip${n === 1 ? "" : "s"} ` +
          `${n === 1 ? "was" : "were"} built on it and can no longer ` +
          `follow on:\n\n${drop.map((d) => "  " + d).join("\n")}` +
          pendNote + `\n\nPick what happens to ` +
          `${n === 1 ? "it" : "them"}:`;
        const choices = [
          { label: "Back them up first", cls: "ok",
            title: "copy the whole chain into a separate project, then " +
                   "reopen here",
            pick: () => run(true) },
          { label: "Discard them", cls: "bad",
            title: "move them to .trash/ - recoverable until you purge",
            pick: () => run(false) },
        ];
        if (index > 1) {
          choices.push({
            label: `Branch from clip ${index - 1} instead`,
            title: "leave this project untouched and redo clip " +
                   index + " in a new one",
            pick: () => this.branchFrom(index - 1),
          });
        }
        this.showChoices(msg, choices);
      })
      .catch((e) => toast(e.message, true));
  }

  syncBranchButton() {
    const b = this.branchBtn;
    if (!b) return;
    const seg = this.timeline?.[this.curSeg];
    const clip = seg?.clip;
    const key = clip ? `${clip.index}:${clip.status}` : "none";
    if (key === this._branchBtnIndex) return;   // nothing changed
    this._branchBtnIndex = key;
    const r = this.reopenBtn;
    if (!clip) {
      b.style.display = "none";
      if (r) r.style.display = "none";
      return;
    }
    b.style.display = "";
    // an approved clip stays actionable even while a later one is
    // pending - that pending clip continues from it, so reopening
    // simply takes it along
    if (r) {
      const approved = clip.status === "approved";
      r.style.display = approved ? "" : "none";
      if (approved) {
        r.textContent = `Reopen clip ${clip.index}\u2026`;
        r.title = "send clip " + clip.index + " back for a re-render; " +
                  "you will be shown what that costs first";
        r.onclick = () => this.reopen(clip.index);
      }
    }
    // the join belongs to the clip AFTER it, so clip 1 has none
    const lb = this.levelBtn;
    if (lb) {
      const has = clip.index > 1;
      lb.style.display = has ? "" : "none";
      if (has) {
        const on = !!clip.level_match;
        lb.textContent = on
          ? `\u2713 Level-matching join ${clip.index - 1}\u2192` +
            `${clip.index}`
          : `Level-match join ${clip.index - 1}\u2192${clip.index}`;
        lb.className = "h3p-btn" + (on ? " ok" : "");
        lb.title = on
          ? "on export, clip " + clip.index + "'s opening is corrected to " +
            "meet clip " + (clip.index - 1) + "'s level"
          : "measure this join and correct the brightness step on export";
        lb.onclick = () => this.toggleLevelMatch(clip);
      }
    }
    if (clip.status === "approved") {
      b.disabled = false;
      b.textContent = `Branch from clip ${clip.index}\u2026`;
      b.title = "copy clips 1.." + clip.index + " into a new project and " +
                "iterate there, leaving this one intact";
      b.onclick = () => this.branchFrom(clip.index);
    } else {
      // branching needs a settled tail; a pending clip has none yet
      b.disabled = true;
      b.textContent = `Branch from clip ${clip.index}\u2026`;
      b.title = `Clip ${clip.index} is still pending. Approve or reject ` +
                `it first, or scrub back to an approved clip to branch ` +
                `from there.`;
      b.onclick = null;
    }
  }

  async toggleLevelMatch(clip) {
    const on = !!clip.level_match;
    if (on) {
      try {
        await post("/h3_suite/project/level_match",
                   { name: this.name(), index: clip.index, enabled: false });
        toast(`join ${clip.index - 1}\u2192${clip.index} left as rendered`);
        this._branchBtnIndex = null;
        this.refresh(true);
      } catch (e) { toast(e.message, true); }
      return;
    }
    // measure first: turning this on without saying what it found would
    // be asking the user to trust an invisible change to their pixels
    let info = null;
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/level_match_preview?name=` +
        `${encodeURIComponent(this.name())}&index=${clip.index}`);
      info = await r.json();
      if (info.error) throw new Error(info.error);
    } catch (e) { toast(e.message, true); return; }
    if (!info.needed) { toast(info.message || "join is already level"); return; }
    const secs = (info.span / 24).toFixed(1);
    this.showConfirm(
      `Join ${clip.index - 1}\u2192${clip.index} steps by ` +
      `${info.step > 0 ? "+" : ""}${info.step} in brightness` +
      (info.tau ? `, settling with a time constant of ${info.tau} frames`
                : "") +
      `.\n\nOn export, clip ${clip.index}'s opening will be scaled by ` +
      `${info.gain} decaying to 1.0 over about ${secs}s, so it meets clip ` +
      `${clip.index - 1}'s level.\n\nYour clips are not modified \u2014 ` +
      `this only affects the exported master, and any export with a ` +
      `corrected join is re-encoded rather than stream-copied.` +
      (info.chained_note ? `\n\nNote: ${info.chained_note}.` : "") +
      (info.reaches_tail ? `\n\nNote: the correction is still fading at ` +
        `this clip's end, so a level-matched join after it will follow ` +
        `from the corrected level.` : ""),
      async () => {
        try {
          await post("/h3_suite/project/level_match",
                     { name: this.name(), index: clip.index, enabled: true });
          toast(`join ${clip.index - 1}\u2192${clip.index} will be matched`);
          this._branchBtnIndex = null;
          this.refresh(true);
        } catch (e) { toast(e.message, true); }
      });
  }

  branchFrom(index) {
    const modal = new BranchModal(this.name(), index, (newName) => {
      // land in the branch: it is the thing you now want to work on
      this.setName(newName);
      this.viewing = null;
      this._sig = null;
      this.durations = {};
      this.loadProjects().then(() => {
        this.select.value = newName;
        this.refresh(true);
      });
    });
    modal.open();
  }

  async selectTake(take) {
    try {
      const pend = this.state?.pending;
      if (!pend) return;
      await post("/h3_suite/project/select_take",
                 { name: this.name(), index: pend.index, take: Number(take) });
      this.viewing = null;
      this._sig = null;              // timeline must rebuild on new content
      toast(`take ${take} selected`);
      this.refresh(true);
    } catch (e) { toast(e.message, true); }
  }

  discardTakes(index) {
    this.showConfirm(
      `Trash every take of clip ${index} except the selected one? They ` +
      `move to .trash/ and can be recovered until you purge.`,
      async () => {
        try {
          const out = await post("/h3_suite/project/discard_takes",
                                 { name: this.name(), index });
          toast(`${(out.dropped || []).length} take(s) trashed`);
          this.refresh(true);
        } catch (e) { toast(e.message, true); }
      });
  }

  async exportMaster(includePending) {
    this._exportPending = !!includePending;
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/export_name?name=` +
        `${encodeURIComponent(this.name())}` +
        `&preview=${includePending ? 1 : 0}`);
      const { suggested, error } = await r.json();
      if (error) throw new Error(error);
      this.exportInput.value = suggested;
    } catch (e) {
      this.exportInput.value = includePending ? "preview.mp4" : "master.mp4";
    }
    this.exportBtns.style.display = "none";
    this.exportNaming.style.display = "flex";
    const latent = !!this.scalePrefs.exportFromLatents;
    this.qualityWrap.style.display = latent ? "inline-flex" : "none";
    if (latent) {
      this.qualitySel.value =
        this.scalePrefs.masterQuality || "16:medium";
    }
    this.exportInput.focus();
    this.exportInput.select();
  }

  closeExportNaming() {
    this.exportNaming.style.display = "none";
    this.exportBtns.style.display = "flex";
  }

  async loadExports() {
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/exports?name=${encodeURIComponent(this.name())}`);
      const d = await r.json();
      this.exports = (r.ok && !d.error && Array.isArray(d.exports))
        ? d.exports : [];
    } catch (e) { this.exports = []; }
  }

  /** Fill the exports dropdown and point Download at the chosen file.
   *  `prefer` (a filename) wins, else the current choice if it still
   *  exists, else the newest. */
  paintExports(prefer) {
    const list = this.exports || [];
    if (!list.length) {
      this.exportsWrap.style.display = "none";
      this.downloadLink.removeAttribute("href");
      return;
    }
    const current = this.exportsSel.value;
    const want = prefer || (list.some((e) => e.file === current) ? current
                                                                 : list[0].file);
    this.exportsSel.innerHTML = "";
    for (const e of list) {
      const mb = (e.size / 1048576).toFixed(1);
      this.exportsSel.append(el("option", { value: e.file,
        text: `${e.file} · ${mb} MB${e.preview ? " · preview" : ""}` }));
    }
    this.exportsSel.value = want;
    const p = `/h3_suite/project/master?name=${encodeURIComponent(this.name())}` +
      `&file=${encodeURIComponent(this.exportsSel.value)}`;
    this.downloadLink.href = api.apiURL ? api.apiURL(p) : p;
    this.downloadLink.setAttribute("download", this.exportsSel.value);
    this.exportsWrap.style.display = "inline-flex";
  }

  async doExport() {
    const filename = this.exportInput.value.trim();
    if (!filename) return;
    this.closeExportNaming();
    const fromLatents = !!this.scalePrefs.exportFromLatents;
    const q = (this.scalePrefs.masterQuality || "16:medium").split(":");
    toast(fromLatents ? "decoding latents\u2026 (slower than a concat)"
                      : "concatenating\u2026");
    try {
      const out = await post("/h3_suite/project/export", {
        name: this.name(), include_pending: this._exportPending,
        filename, use_latents: fromLatents,
        vae_names: fromLatents ? wiredVaeNames(this.node) : undefined,
        crf: fromLatents ? Number(q[0]) : undefined,
        preset: fromLatents ? q[1] : undefined,
      });
      const lm = (out.level_matched || []).length;
      const what = out.preview ? "preview" : "master";
      const name = out.master || out.exported;
      const n = out.clip_count || out.clips || "";
      toast(`${what} written${out.from_latents ? " from latents" : ""} ` +
            `(${n} clips${lm ? `, ${lm} join` +
            `${lm === 1 ? "" : "s"} level-matched` : ""}): ${name}`);
      // the new file goes straight into the dropdown, selected, so
      // Download is one click away without a refresh
      await this.loadExports();
      this.paintExports(out.exported);
    } catch (e) { toast(e.message, true); }
  }

  async openFolder() {
    // the server reports where the project lives and nothing more: it
    // does not launch a file manager (that would run on whichever
    // machine hosts ComfyUI, and a web request should not start
    // programs there). The path stays on screen until closed, so it can
    // still be copied by hand when the browser refuses to copy it.
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/folder?name=${encodeURIComponent(this.name())}`);
      const out = await r.json();
      if (!r.ok || out.error) throw new Error(out.error || r.statusText);
      this.showFolderPath(out.path);
    } catch (e) { toast(e.message, true); }
  }

  showFolderPath(path) {
    const field = el("input", { class: "h3p-pathfield" });
    field.readOnly = true;
    field.spellcheck = false;
    field.value = path;
    field.addEventListener("focus", () => field.select());
    const copyBtn = el("button", { class: "h3p-btn primary", text: "Copy" });
    copyBtn.onclick = async () => {
      let ok = false;
      try {
        // the clipboard API needs a secure page: localhost or https
        await navigator.clipboard.writeText(path);
        ok = true;
      } catch (e) {
        // plain http over a LAN has no clipboard API, but copying the
        // current selection still works there
        try {
          field.focus();
          field.select();
          ok = document.execCommand("copy");
        } catch (e2) { ok = false; }
      }
      copyBtn.textContent = ok ? "Copied" : "Select it and copy";
    };
    this.confirmMsg.replaceChildren(
      el("div", { text: "Project folder, on the machine running ComfyUI:" }),
      field);
    this.confirmBtns.replaceChildren(
      copyBtn,
      el("button", { class: "h3p-btn", text: "Close",
                     onclick: () => this.hideConfirm() }));
    this.confirm.classList.add("on");
    field.focus();
    field.select();
  }

  async measureDrift() {
    this.showConfirmMsg("measuring every clip \u2014 this decodes a few " +
                        "frames from each, so give it a moment\u2026");
    let d;
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/drift?name=${encodeURIComponent(this.name())}`);
      d = await r.json();
      if (d.error) throw new Error(d.error);
    } catch (e) { this.hideConfirm(); toast(e.message, true); return; }

    const t = d.trend || {};
    const fmtRow = (key, label, unit) => {
      const x = t[key];
      if (!x) return "";
      const per = x.per_clip;
      const dir = Math.abs(x.pct_total) < 1.5 ? "steady"
        : (x.total > 0 ? "rising" : "falling");
      return `<tr><td>${label}</td>` +
        `<td class="n">${x.first.toFixed(1)}${unit}</td>` +
        `<td class="n">${x.last.toFixed(1)}${unit}</td>` +
        `<td class="n">${x.total > 0 ? "+" : ""}${x.total.toFixed(1)}` +
        ` (${x.pct_total > 0 ? "+" : ""}${x.pct_total.toFixed(1)}%)</td>` +
        `<td class="n">${per > 0 ? "+" : ""}${per.toFixed(2)}/clip</td>` +
        `<td>${dir}</td></tr>`;
    };
    // a bar per clip for the measure that moved most, so the shape of
    // the drift is visible - steady slide or one bad clip
    let worst = null, worstPct = 0;
    for (const k of ["luma", "contrast", "sharpness", "colour"]) {
      const x = t[k];
      if (x && Math.abs(x.pct_total || 0) > worstPct) {
        worstPct = Math.abs(x.pct_total); worst = k;
      }
    }
    let spark = "";
    if (worst) {
      const vals = d.clips.map((c) => c[worst]);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      const span = hi - lo || 1;
      spark = `<div class="h3p-spark"><div class="lbl">${worst} per clip` +
        ` \u2014 ${lo.toFixed(1)} to ${hi.toFixed(1)}</div><div class="bars">` +
        d.clips.map((c) => {
          const h = 4 + 34 * ((c[worst] - lo) / span);
          return `<i style="height:${h}px" title="${c.label}: ` +
                 `${c[worst].toFixed(2)}"></i>`;
        }).join("") + `</div></div>`;
    }

    this.driftBody.innerHTML =
      `<table class="h3p-drift"><thead><tr><th></th><th>clip 1</th>` +
      `<th>clip ${d.clips.length}</th><th>change</th><th>rate</th>` +
      `<th></th></tr></thead><tbody>` +
      fmtRow("luma", "brightness", "") +
      fmtRow("contrast", "contrast", "") +
      fmtRow("sharpness", "sharpness", "") +
      fmtRow("colour", "colour", "%") +
      `</tbody></table>` + spark +
      `<p class="h3p-note">These also move when the content changes \u2014 ` +
      `a darker scene lowers brightness honestly. Read the trend across ` +
      `many clips of one continuous scene, not any single number. A cut ` +
      `to a new angle resets most of this.</p>`;
    this.hideConfirm();
    this.drift.classList.add("on");
  }

  async cleanupTakes() {
    let plan;
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/storage?name=` +
        `${encodeURIComponent(this.name())}`);
      plan = await r.json();
      if (plan.error) throw new Error(plan.error);
    } catch (e) { toast(e.message, true); return; }
    const n = plan.cleanup.planned.length;
    if (!n) { toast("no alternate takes to clean up"); return; }
    const list = plan.cleanup.planned
      .map((t) => `  clip ${t.index} take ${t.take} (${mb(t.bytes)})`)
      .join("\n");
    this.showConfirm(
      `Move ${n} alternate take${n === 1 ? "" : "s"} to .trash/, ` +
      `freeing ${mb(plan.cleanup.bytes)} from clips/?\n\n${list}\n\n` +
      `The chain itself is untouched. These stay branchable from .trash/ ` +
      `until you purge.`,
      async () => {
        try {
          const out = await post("/h3_suite/project/cleanup_takes",
                                 { name: this.name() });
          toast(`${out.cleaned} take(s) moved to trash, ${mb(out.bytes)}`);
          this.refresh(true);
        } catch (e) { toast(e.message, true); }
      });
  }

  async purge() {
    let s = null;
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/storage?name=` +
        `${encodeURIComponent(this.name())}`);
      s = await r.json();
    } catch (e) { /* fall back to the generic wording */ }
    const detail = s && !s.error
      ? `${s.trash_takes} take${s.trash_takes === 1 ? "" : "s"} ` +
        `(${mb(s.trash_bytes)})`
      : "everything in .trash/";
    this.showConfirm(
      `Permanently delete ${detail}?\n\nThese are rejected and ` +
      `discarded takes. After this they can no longer be branched from.`,
      () => this.act("purge_trash", "trash purged"));
  }

  view(basename) {
    this.viewing = basename;
    this.hideConfirm();
    this.render();
  }

  setMode(mode) {
    this.mode = mode;
    this.viewing = null;
    this.closeExportNaming();
    this.curSeg = null;
    this.standbySeg = null;
    for (const v of [this.vA, this.vB]) {
      v.pause();
      v.removeAttribute("src");
    }
    this.render();
  }

  async manualRefresh() {
    const before = this.state?.clips?.length || 0;
    const beforePending = this.state?.pending?.basename || null;
    this.refreshBtn.disabled = true;
    this.refreshBtn.textContent = "\u2026";
    // durations and the timeline signature are cleared so a clip that was
    // overwritten (a re-roll landing on the same name) is re-measured
    // rather than served from cache
    this.durations = {};
    this._sig = null;
    try {
      await this.loadProjects();
      await this.refresh(true);
      const after = this.state?.clips?.length || 0;
      const pending = this.state?.pending;
      if (this.state?.missing) {
        toast(this.state.missing, true);
      } else if (after > before) {
        toast(`found ${after - before} new clip` +
              `${after - before === 1 ? "" : "s"}`);
      } else if (pending && pending.basename !== beforePending) {
        toast(`clip ${pending.index} take ${pending.take} is ready`);
      } else {
        toast("up to date");
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      this.refreshBtn.disabled = false;
      this.refreshBtn.textContent = "\u21bb";
    }
  }

  async refresh(refetch = false) {
    if (refetch) {
      try {
        this.state = await getState(this.name());
        try {
          const r = await api.fetchApi(
            `/h3_suite/project/storage?name=` +
            `${encodeURIComponent(this.name())}`);
          const sd = await r.json();
          this.storage = sd.error ? null : sd;
        } catch (e) { this.storage = null; }
        await this.loadExports();
      } catch (e) {
        this.state = { missing: String(e.message || e) };
        this.storage = null;
        this.exports = [];
      }
    }
    this.render();
    this.node._h3RefreshSummary?.();
  }

  buildScaleMenu() {
    // Set-then-Apply, not live. This control sits INSIDE the thing it
    // resizes: applying on input means the first pixel of slider travel
    // moves the popover out from under the pointer and the drag dies.
    const pending = { ...this.scalePrefs };
    const inputs = {};
    const outs = {};

    const dirty = () => this.scaleApply.classList.toggle("primary",
      pending.windowScale !== this.scalePrefs.windowScale ||
      pending.textScale !== this.scalePrefs.textScale);

    const row = (key, label, max) => {
      const pct = () => Math.round(pending[key] * 100);
      const range = el("input", {
        type: "range", min: String(SCALE_MIN * 100), max: String(max * 100),
        step: "5", value: String(pct()),
        oninput: (e) => {
          pending[key] = clampScale(Number(e.target.value) / 100, max);
          outs[key].value = String(Math.round(pending[key] * 100));
          dirty();
        },
      });
      const num = el("input", {
        type: "number", min: String(SCALE_MIN * 100),
        max: String(max * 100), step: "5", value: String(pct()),
        oninput: (e) => {
          pending[key] = clampScale(Number(e.target.value) / 100, max);
          inputs[key].value = String(Math.round(pending[key] * 100));
          dirty();
        },
        // without this the modal's own Enter/Escape handling sees the
        // keystroke and can close the dialog mid-type
        onkeydown: (e) => {
          if (e.key === "Enter") { e.stopPropagation(); e.target.blur(); }
        },
      });
      inputs[key] = range;
      outs[key] = num;
      return el("div", { class: "h3p-scalerow" },
                el("label", { text: label }), range, num,
                el("span", { class: "h3p-scalenote", text: "%" }));
    };

    this.scaleApply = el("button", { class: "h3p-btn", text: "Apply",
      onclick: () => this.setScale(pending.windowScale, pending.textScale,
                                   inputs, outs) });

    this.scaleMenu.append(
      el("div", { class: "h3p-scalenote",
                  text: "Window resizes the box. Text changes type size " +
                        "only, so the layout rewraps instead of showing " +
                        "less. Remembered in this browser." }),
      row("windowScale", "Window", SCALE_MAX),
      row("textScale", "Text", TEXT_SCALE_MAX),
      el("div", { class: "h3p-scalefoot" },
        el("button", { class: "h3p-btn", text: "Reset",
                       onclick: () => this.setScale(1, 1, inputs, outs) }),
        el("div", { class: "sp" }),
        this.scaleApply,
        el("button", { class: "h3p-btn", text: "Close",
                       onclick: () => this.scaleMenu.classList
                         .remove("on") })));
  }

  setScale(w, t, inputs, outs) {
    this.scalePrefs.windowScale = clampScale(w);
    this.scalePrefs.textScale = clampScale(t, TEXT_SCALE_MAX);
    // push committed values back into every control, or Reset leaves the
    // sliders showing the old position
    for (const [k, max] of [["windowScale", SCALE_MAX],
                            ["textScale", TEXT_SCALE_MAX]]) {
      const pct = String(Math.round(clampScale(this.scalePrefs[k], max) * 100));
      if (inputs?.[k]) inputs[k].value = pct;
      if (outs?.[k]) outs[k].value = pct;
    }
    saveScalePrefs(this.scalePrefs);
    this.applyScale();
    this.scaleApply?.classList.remove("primary");
  }

  applyScale() {
    sizeScaledBox(this.overlay?.querySelector(".h3p-modal"),
                  1240, 820, this.scalePrefs);
    applyTextScale(this.scalePrefs);
  }

  async toggleAuto(on) {
    if (on) {
      // easy to hit by accident, hard to notice afterwards: make the
      // consequence explicit before it takes effect
      this.autoBox.checked = false;
      this.showConfirm(
        "Turn OFF review for this project? Every render will be approved " +
        "as it finishes and the chain will keep extending on its own. " +
        "Takes are still kept, so clips can be reopened afterwards.",
        () => this.setAuto(true));
      return;
    }
    this.setAuto(false);
  }

  async setAuto(on) {
    try {
      await post("/h3_suite/project/auto_approve",
                 { name: this.name(), on: !!on });
    } catch (e) { toast(e.message, true); }
    this.refresh(true);
  }

  render() {
    const s = this.state;
    const auto = !!(s && s.auto_approve);
    this.latentExportBox.checked = !!this.scalePrefs.exportFromLatents;
    this.autoBox.checked = auto;
    this.autoWrap.classList.toggle("on", auto);
    this.autoBar.style.display = auto ? "flex" : "none";
    this.actions.innerHTML = "";
    this.railBody.innerHTML = "";
    if (!s || s.missing) {
      this.viewTitle.textContent = "";
      this.viewSub.textContent = "";
      this.playerEmpty.textContent = s
        ? `${s.missing}\npress New to create a project, or queue once ` +
          `with create_if_missing on`
        : "";
      this.player.replaceChildren(this.playerEmpty);
      this.transport.style.display = "none";
      this.footText.textContent = "";
      return;
    }

    const name = this.name();
    const approved = s.clips.filter((c) => c.status === "approved");
    const tail = approved.length ? approved[approved.length - 1] : null;
    this.paintExports();

    this.btnTimeline.className = this.mode === "timeline" ? "on" : "";
    this.btnClip.className = this.mode === "clip" ? "on" : "";

    // ---- timeline mode: the whole chain as one shot ----
    if (this.mode === "timeline" && s.clips.length) {
      this.transport.style.display = "flex";
      this.showPlayer();

      const pend = s.pending;
      const shownSeg = this.timeline?.[this.curSeg] || null;
      this.viewTitle.textContent = pend
        ? `Chain \u2014 clip ${pend.index} take ${pend.take} pending at ` +
          `the end`
        : `Chain \u2014 ${approved.length} approved`;
      this.viewTitle.className = "h3p-viewtitle " +
        (pend ? "pending" : "approved");
      this.viewSub.textContent = pend
        ? "watch the join, then approve or reject"
        : "";

      const sig = s.clips.map((c) => c.basename).join("|");
      if (sig !== this._sig) {
        this._sig = sig;
        this.curSeg = null;
        // the standby buffer is remembered by POSITION, and the clip at a
        // position just changed: an imported take 3 sits where take 2 was.
        // Trusting it swaps the stale file in at the join and plays the
        // old take under the new take's label, so forget it.
        this.standbySeg = null;
        this.timeEl.innerHTML =
          "<span class='h3p-measuring'>measuring clips\u2026</span>";
        this.measure(s.clips).then(() => {
          if (this._sig !== sig) return;   // state moved on
          this.buildTimeline(s.clips);
          this.paintSegments();
          // land the playhead just before the pending clip so the join
          // is the first thing you see
          const jump = pend
            ? Math.max(0, (this.timeline.find(
                (x) => x.clip.basename === pend.basename)?.start || 0) - 2)
            : 0;
          // and PLAY it when the pending take just changed under an open
          // panel - an import landing, a re-roll finishing, a take picked.
          // Parking paused there leaves the previous clip's last frame on
          // screen, which reads as "the new take never arrived". First
          // open and switching project stay paused, as before.
          const prev = this._timelinePending;
          const now = { project: name,
                        basename: pend ? pend.basename : null };
          this._timelinePending = now;
          const arrived = !!(prev && prev.project === now.project &&
                             now.basename && prev.basename !== now.basename);
          this.seekGlobal(jump, arrived);
          this.startTicker();
        });
      } else {
        this.buildTimeline(s.clips);
        this.paintSegments();
        this.paintPlayhead();
        this.startTicker();
      }

      if (pend) {
        this.paintTakes(pend);
        this.actions.append(
          this.takeWrap,
          el("button", { class: "h3p-btn",
                         text: "\u23ee Jump to the join",
                         onclick: () => {
                           const seg = this.timeline.find(
                             (x) => x.clip.basename === pend.basename);
                           this.seekGlobal(
                             Math.max(0, (seg?.start || 0) - 2), true);
                         } }),
          el("button", { class: "h3p-btn ok",
                         text: "Approve \u2014 chain from this",
                         onclick: () => this.act("approve", "approved") }),
          el("button", { class: "h3p-btn bad", text: "Reject",
                         onclick: () => this.showConfirm(
                           `Reject clip ${pend.index}? Its files go to ` +
                           `.trash/ and the chain steps back.`,
                           () => this.act("reject", "rejected")) }),
          el("span", { class: "h3p-hint",
                       text: "to re-roll: change the prompt or seed, then " +
                             `Queue \u2014 it lands as clip ${pend.index} ` +
                             `take ${pend.take + 1}` }));
      }
      // the branch target follows the PLAYHEAD, not whatever segment
      // happened to be current when this row was built. paintPlayhead
      // keeps its label and handler in sync as you scrub.
      this.reopenBtn = el("button", { class: "h3p-btn warn",
        text: "Reopen\u2026" });
      this.levelBtn = el("button", { class: "h3p-btn",
        text: "Level-match join" });
      this.branchBtn = el("button", { class: "h3p-btn", text: "Branch\u2026",
        title: "copy clips 1..N into a new project and iterate there, " +
               "leaving this one intact" });
      this.actions.append(this.reopenBtn, this.levelBtn, this.branchBtn);
      this._branchBtnIndex = null;
      this.syncBranchButton();
      this.paintRail(s, name, approved, tail, null);
      this.paintFoot(approved, tail, name);
      return;
    }

    this.transport.style.display = "none";
    if (this.video !== this.vA) this.swapBuffers();
    this.vB.style.display = "none";
    this.video.controls = true;
    this.video.loop = true;

    // player shows: explicit rail selection, else the pending clip,
    // else the chain tail, else the empty message
    let shown = null;
    if (this.viewing) {
      shown = s.clips.find((c) => c.basename === this.viewing) || null;
    }
    if (!shown) shown = s.pending || tail;

    if (shown) {
      const pend = shown.status === "pending";
      this.viewTitle.textContent = pend
        ? `Pending review \u2014 clip ${shown.index} take ${shown.take}`
        : `Clip ${shown.index} take ${shown.take}`;
      this.viewTitle.className = "h3p-viewtitle " + shown.status;
      const m = shown.meta;
      const facts = m
        ? ` \u00b7 ${m.width}\u00d7${m.height} \u00b7 ${m.frames}f ` +
          `\u00b7 ${m.duration}s`
        : "";
      this.viewSub.textContent = (shown.from
        ? `continues ${shown.from}` : "clip 1 \u2014 fresh start") + facts;
      const url = videoURL(name, shown.basename);
      if (!this.video.src.includes(encodeURIComponent(shown.basename))) {
        this.video.src = url;
      }
      this.player.replaceChildren(this.video);

      if (pend) {
        this.paintTakes(shown);
        this.actions.append(
          this.takeWrap,
          el("button", { class: "h3p-btn ok",
                         text: "Approve \u2014 chain from this",
                         onclick: () => this.act("approve", "approved") }),
          el("button", { class: "h3p-btn bad", text: "Reject",
                         onclick: () => this.showConfirm(
                           `Reject clip ${shown.index}? Its files go to ` +
                           `.trash/ and the chain steps back.`,
                           () => this.act("reject", "rejected")) }),
          el("span", { class: "h3p-hint",
                       text: "to re-roll: change the prompt or seed, then " +
                             `Queue \u2014 it lands as take ` +
                             `${shown.take + 1}` }));
      } else {
        this.actions.append(
          el("button", { class: "h3p-btn warn",
                         text: `Reopen clip ${shown.index}\u2026`,
                         onclick: () => this.reopen(shown.index) }),
          el("button", { class: "h3p-btn",
                         text: `Branch from clip ${shown.index}\u2026`,
                         title: "copy clips 1..N into a new project and " +
                                "iterate there, leaving this one intact",
                         onclick: () => this.branchFrom(shown.index) }));
        if (this.viewing) {
          this.actions.append(
            el("button", { class: "h3p-btn", text: "Back to latest",
                           onclick: () => this.view(null) }));
        }
      }
    } else {
      this.viewTitle.textContent = "Empty project";
      this.viewTitle.className = "h3p-viewtitle";
      this.viewSub.textContent = "";
      this.playerEmpty.textContent =
        "no clips yet \u2014 queue the workflow and clip 1 lands here " +
        "for review";
      this.player.replaceChildren(this.playerEmpty);
      this.transport.style.display = "none";
    }

    this.paintRail(s, name, approved, tail, shown);
    this.paintFoot(approved, tail, name);
  }

  paintTakes(clip) {
    // trashed takes stay in the manifest for branching, but they are not
    // selectable as the live take
    const takes = (clip.takes || []).filter((t) => !t.trashed);
    this.takeSel.innerHTML = "";
    for (const t of takes) {
      const secs = t.meta?.duration ? ` \u00b7 ${t.meta.duration}s` : "";
      this.takeSel.append(el("option", {
        value: String(t.take), text: `take ${t.take}${secs}` }));
    }
    this.takeSel.value = String(clip.take);
    // one take needs no chooser; several get a chooser and a way to prune
    this.takeWrap.style.display = takes.length > 1 ? "flex" : "none";
    if (takes.length > 1) {
      this.takeWrap.querySelectorAll(".h3p-discard").forEach(
        (n) => n.remove());
      const b = el("button", { class: "h3p-btn h3p-discard",
        text: "Keep only this",
        title: "trash the other takes of this clip",
        onclick: () => this.discardTakes(clip.index) });
      this.takeWrap.append(b);
    }
  }

  paintRail(s, name, approved, tail, shown) {
    this.railBody.innerHTML = "";
    for (const c of s.clips) {
      const isTail = tail && c.basename === tail.basename;
      const card = el("div", {
        class: "h3p-clip " + c.status +
          (shown && shown.basename === c.basename ? " sel" : ""),
        onclick: () => {
          if (this.mode === "timeline") {
            const seg = this.timeline?.find(
              (x) => x.clip.basename === c.basename);
            if (seg) this.seekGlobal(seg.start, false);
          } else {
            this.view(c.basename);
          }
        },
      });
      const v = el("video", { muted: "", preload: "metadata" });
      v.src = videoURL(name, c.basename) + "#t=0.1";
      card.append(v,
        el("div", { class: "h3p-clipbar" },
          el("span", { text: `clip ${c.index}` }),
          el("span", { class: "h3p-clipname", text: `take ${c.take}` }),
          isTail ? el("span", { class: "h3p-tail", text: "tail" }) : null,
          el("span", {
            class: "h3p-clipstat " +
              (c.status === "approved" ? "ok" : "pend"),
            text: c.status === "approved" ? "\u2713" : "\u25cf pending",
          })));
      this.railBody.append(card);
    }
    this.railBody.append(el("div", { class: "h3p-next" },
      el("div", { text: "next queue press renders" }),
      el("b", { text: s.next_save.basename })));
  }

  paintFoot(approved, tail, name) {
    const dur = this.total
      ? ` \u00b7 ${this.total.toFixed(1)}s total` : "";
    let disk = "";
    const s = this.storage;
    if (s) {
      disk = ` \u00b7 ${mb(s.chain_bytes)} chain`;
      if (s.alternate_takes) {
        disk += `, ${mb(s.alternate_bytes)} in ${s.alternate_takes} ` +
                `alternate take${s.alternate_takes === 1 ? "" : "s"}`;
      }
      if (s.trash_takes) disk += `, ${mb(s.trash_bytes)} trashed`;
    }
    this.footText.textContent =
      `${approved.length} approved${dur} \u00b7 ` +
      (tail ? `chain tails clip ${tail.index}` :
              "chain inactive (fresh clip 1)") +
      disk + ` \u00b7 output/h3_projects/${name}`;
  }
}

/* ------------------------------------------------------------------ */
/* node registration: summary card + open button                       */
/* ------------------------------------------------------------------ */

app.registerExtension({
  name: "h3_suite.project_panel",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "H3ProjectHub") return;
    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      orig?.apply(this, arguments);
      injectCSS();
      const modal = new ProjectModal(this);
      this.addWidget("button", "Open project\u2026", null,
                     () => modal.open());

      const summary = el("div", { class: "h3p-summary",
                                  text: "\u2026" });
      this.addDOMWidget("h3p_summary", "div", summary,
                        { serialize: false, getMinHeight: () => 64 });

      // paint from the cached state plus a live read of the graph, so
      // turning the Resolution Selector's dial updates the warning
      // without another round trip
      this._h3PaintSummary = () => {
        const name = this.widgets?.find(
          (w) => w.name === "project_name")?.value;
        const s = this._h3State;
        if (!name) return;
        if (!s || s._name !== name) {
          summary.textContent =
            `${name}: not created yet \u2014 open the project panel or ` +
            `queue once`;
          return;
        }
        const approved = s.clips.filter(
          (c) => c.status === "approved").length;
        const pend = s.pending;
        const warn = s.auto_approve
          ? `<div class="h3p-autowarn">\u26a0 Auto-approval is on \u2014 ` +
            `the chain is progressing without manual review</div>`
          : "";
        const html = warn +
          `<b>${name}</b> \u00b7 ${approved} approved` +
          (pend ? ` \u00b7 <span class="pend">clip ${pend.index} ` +
                  `take ${pend.take} pending review</span>` : "") +
          `<br><span class="nx">next: ${s.next_save.basename}` +
          `</span> \u00b7 ` +
          (s.chain_active ? `<span class="ok">chain active</span>`
                          : `chain inactive`) +
          hubSizeLine(this, s);
        if (html !== this._h3Html) {       // don't churn the DOM
          this._h3Html = html;
          summary.innerHTML = html;
        }
      };

      this._h3RefreshSummary = async () => {
        const name = this.widgets?.find(
          (w) => w.name === "project_name")?.value;
        if (!name) return;
        try {
          const s = await getState(name);
          s._name = name;
          this._h3State = s;
        } catch (e) {
          this._h3State = null;
        }
        this._h3PaintSummary();
      };
      this._h3RefreshSummary();
      api.addEventListener("executed", this._h3RefreshSummary);
      // the selector's widgets can change at any time and nothing
      // announces it; repainting is a graph read and a string compare
      this._h3Timer = setInterval(() => this._h3PaintSummary(), 1000);
      const origRemoved = this.onRemoved;
      this.onRemoved = function () {
        api.removeEventListener("executed", this._h3RefreshSummary);
        clearInterval(this._h3Timer);
        origRemoved?.apply(this, arguments);
      };
      this.size = [Math.max(this.size[0], 300),
                   Math.max(this.size[1], 170)];
    };
  },
});
