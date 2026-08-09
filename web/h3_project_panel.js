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
  box-shadow:0 24px 64px rgba(0,0,0,.55);overflow:hidden;font-size:13px;}
.h3p-head{display:flex;align-items:center;gap:12px;padding:10px 16px;
  border-bottom:1px solid #2a2f3a;background:#1e222a;}
.h3p-title{font-weight:600;font-size:14px;letter-spacing:.02em;white-space:nowrap;}
.h3p-title small{color:#8a93a3;font-weight:400;margin-left:8px;}
.h3p-select{background:#12151b;color:#d7dbe2;border:1px solid #2a2f3a;border-radius:7px;
  padding:5px 8px;font-size:12px;min-width:160px;}
.h3p-btn{background:#12151b;border:1px solid #2a2f3a;color:#9aa3b2;padding:5px 12px;
  border-radius:7px;cursor:pointer;font-size:12px;white-space:nowrap;}
.h3p-btn:hover{color:#fff;border-color:#59637a;}
.h3p-btn.ok{color:#7ec87e;border-color:#2e4a2e;} .h3p-btn.ok:hover{border-color:#7ec87e;}
.h3p-btn.warn{color:#e0a94c;border-color:#4a3d20;} .h3p-btn.warn:hover{border-color:#e0a94c;}
.h3p-btn.bad{color:#e05a5a;border-color:#4a2424;} .h3p-btn.bad:hover{border-color:#e05a5a;}
.h3p-x{background:none;border:0;color:#8a93a3;font-size:18px;cursor:pointer;
  padding:2px 8px;margin-left:4px;}
.h3p-x:hover{color:#fff;}
.h3p-namewrap{display:none;align-items:center;gap:6px;}
.h3p-namewrap.on{display:flex;}
.h3p-input{background:#12151b;color:#d7dbe2;border:1px solid #2a2f3a;border-radius:7px;
  padding:5px 8px;font-size:12px;width:170px;}
.h3p-input:focus{outline:none;border-color:#59637a;}
.h3p-body{flex:1;display:grid;grid-template-columns:minmax(0,1fr) 300px;min-height:0;}
@media (max-width:900px){.h3p-body{grid-template-columns:1fr;}}
.h3p-main{display:flex;flex-direction:column;min-width:0;min-height:0;padding:14px 16px;
  gap:10px;overflow:auto;}
.h3p-viewhead{display:flex;align-items:baseline;gap:10px;}
.h3p-viewtitle{font-weight:600;font-size:13px;}
.h3p-viewtitle.pending{color:#e0a94c;}
.h3p-viewtitle.approved{color:#7ec87e;}
.h3p-viewsub{color:#8a93a3;font-size:11px;}
.h3p-player{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;
  background:#0d1015;border:1px solid #23272f;border-radius:9px;overflow:hidden;}
.h3p-player video{max-width:100%;max-height:100%;display:block;background:#000;}
.h3p-playerempty{color:#5c6472;font-size:12px;text-align:center;line-height:1.7;
  padding:30px;white-space:pre-wrap;}
.h3p-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.h3p-spacer{flex:1;}
.h3p-confirm{display:none;flex-direction:column;gap:8px;background:#241f14;
  border:1px solid #4a3d20;border-radius:9px;padding:10px 12px;}
.h3p-confirm.on{display:flex;}
.h3p-confirm .msg{white-space:pre-wrap;color:#e0c890;font-size:12px;line-height:1.5;}
.h3p-rail{border-left:1px solid #2a2f3a;background:#15181e;display:flex;
  flex-direction:column;min-height:0;}
.h3p-railhead{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#8a93a3;
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
.h3p-clipbar{display:flex;align-items:center;gap:6px;padding:4px 8px;font-size:11px;}
.h3p-clipname{font-family:ui-monospace,monospace;font-size:10px;color:#9aa3b2;}
.h3p-clipstat{margin-left:auto;font-size:10px;}
.h3p-clipstat.ok{color:#7ec87e;} .h3p-clipstat.pend{color:#e0a94c;}
.h3p-tail{font-size:9px;color:#6f86b8;border:1px solid #2b3a52;border-radius:7px;
  padding:0 5px;}
.h3p-next{border:1px dashed #2e3440;border-radius:8px;padding:10px 8px;text-align:center;
  font-size:10px;color:#5c6472;line-height:1.5;flex:0 0 auto;}
.h3p-next b{color:#8a93a3;font-family:ui-monospace,monospace;font-weight:400;}
.h3p-foot{display:flex;align-items:center;gap:8px;padding:9px 16px;
  border-top:1px solid #2a2f3a;background:#171a20;font-size:11px;color:#7d8698;}
.h3p-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:10001;
  background:#1e222a;border:1px solid #3a4252;color:#d7dbe2;border-radius:9px;
  padding:9px 16px;font-size:12px;box-shadow:0 12px 32px rgba(0,0,0,.5);
  font-family:system-ui,sans-serif;}
.h3p-toast.bad{border-color:#7a3a3a;color:#e8b0b0;}
.h3p-summary{background:#12151b;border:1px solid #2e3440;border-radius:7px;
  padding:7px 10px;font-size:11px;line-height:1.55;color:#9aa3b2;
  font-family:system-ui,sans-serif;overflow:hidden;}
.h3p-summary b{color:#d7dbe2;font-weight:600;}
.h3p-summary .pend{color:#e0a94c;} .h3p-summary .ok{color:#7ec87e;}
.h3p-summary .nx{font-family:ui-monospace,monospace;font-size:10px;color:#6f86b8;}
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

function toast(msg, bad = false) {
  const t = el("div", { class: "h3p-toast" + (bad ? " bad" : ""), text: msg });
  document.body.append(t);
  setTimeout(() => t.remove(), bad ? 5200 : 2600);
}

async function post(path, body) {
  const r = await api.fetchApi(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
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
/* modal                                                               */
/* ------------------------------------------------------------------ */

class ProjectModal {
  constructor(node) {
    injectCSS();
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
    this.nameWrap = el("div", { class: "h3p-namewrap" },
      this.nameInput,
      el("button", { class: "h3p-btn ok", text: "Create",
                     onclick: () => this.createProject() }),
      el("button", { class: "h3p-btn", text: "Cancel",
                     onclick: () => this.nameWrap.classList.remove("on") }));

    this.viewTitle = el("div", { class: "h3p-viewtitle" });
    this.viewSub = el("div", { class: "h3p-viewsub" });
    this.video = el("video", { controls: "", loop: "" });
    this.playerEmpty = el("div", { class: "h3p-playerempty" });
    this.player = el("div", { class: "h3p-player" }, this.playerEmpty);

    this.confirmMsg = el("div", { class: "msg" });
    this.confirmYes = el("button", { class: "h3p-btn warn", text: "Confirm" });
    this.confirm = el("div", { class: "h3p-confirm" }, this.confirmMsg,
      el("div", { style: "display:flex;gap:8px" }, this.confirmYes,
        el("button", { class: "h3p-btn", text: "Cancel",
                       onclick: () => this.hideConfirm() })));

    this.actions = el("div", { class: "h3p-actions" });
    this.railBody = el("div", { class: "h3p-railbody" });
    this.footText = el("div", {});

    this.overlay = el("div", {
      class: "h3p-overlay",
      onmousedown: (e) => { if (e.target === this.overlay) this.close(); },
    },
      el("div", { class: "h3p-modal" },
        el("div", { class: "h3p-head" },
          el("div", { class: "h3p-title", text: "H3 Project Hub" },
            el("small", { text: "sequential clip chain" })),
          this.select,
          el("button", { class: "h3p-btn", text: "New",
                         onclick: () => this.showNameEntry() }),
          this.nameWrap,
          el("div", { class: "h3p-spacer" }),
          el("button", { class: "h3p-btn", text: "Export master",
                         onclick: () => this.exportMaster() }),
          el("button", { class: "h3p-btn", text: "Purge trash",
                         onclick: () => this.purge() }),
          el("button", { class: "h3p-x", text: "\u2715",
                         onclick: () => this.close() })),
        el("div", { class: "h3p-body" },
          el("div", { class: "h3p-main" },
            el("div", { class: "h3p-viewhead" }, this.viewTitle,
               this.viewSub),
            this.player, this.confirm, this.actions),
          el("div", { class: "h3p-rail" },
            el("div", { class: "h3p-railhead", text: "Chain" }),
            this.railBody)),
        el("div", { class: "h3p-foot" }, this.footText)));

    this._esc = (e) => { if (e.key === "Escape") this.close(); };
    this._onExec = () => this.refresh(true);
  }

  open() {
    document.body.append(this.overlay);
    document.addEventListener("keydown", this._esc);
    api.addEventListener("executed", this._onExec);
    this.loadProjects().then(() => this.refresh(true));
  }

  close() {
    this.video.pause();
    this.overlay.remove();
    document.removeEventListener("keydown", this._esc);
    api.removeEventListener("executed", this._onExec);
    this.node._h3RefreshSummary?.();
  }

  name() {
    return this.node.widgets?.find((w) => w.name === "project_name")
      ?.value || "";
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

  showConfirm(msg, onYes) {
    this.confirmMsg.textContent = msg;
    this.confirmYes.onclick = () => { this.hideConfirm(); onYes(); };
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
        const msg = drop.length
          ? `Reopen clip ${index}? Everything after it was conditioned ` +
            `on it and will be dropped to .trash/:\n\n${drop.join("\n")}`
          : `Reopen clip ${index}? The next queue press re-renders it ` +
            `as a new take.`;
        this.showConfirm(msg, async () => {
          try {
            await post("/h3_suite/project/reopen",
                       { name: this.name(), index, confirm: true });
            toast(`clip ${index} reopened`);
            this.viewing = null;
            this.refresh(true);
          } catch (e) { toast(e.message, true); }
        });
      })
      .catch((e) => toast(e.message, true));
  }

  async exportMaster() {
    try {
      const out = await post("/h3_suite/project/export",
                             { name: this.name() });
      toast(`master written: ${out.master}`);
    } catch (e) { toast(e.message, true); }
  }

  purge() {
    this.showConfirm(
      "Empty this project's .trash/? Rejected and superseded takes in it " +
      "are deleted permanently.",
      () => this.act("purge_trash", "trash purged"));
  }

  view(basename) {
    this.viewing = basename;
    this.hideConfirm();
    this.render();
  }

  async refresh(refetch = false) {
    if (refetch) {
      try {
        this.state = await getState(this.name());
      } catch (e) {
        this.state = { missing: String(e.message || e) };
      }
    }
    this.render();
    this.node._h3RefreshSummary?.();
  }

  render() {
    const s = this.state;
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
      this.footText.textContent = "";
      return;
    }

    const name = this.name();
    const approved = s.clips.filter((c) => c.status === "approved");
    const tail = approved.length ? approved[approved.length - 1] : null;

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
      this.viewSub.textContent = shown.from
        ? `continues ${shown.from}` : "clip 1 \u2014 fresh start";
      const url = videoURL(name, shown.basename);
      if (!this.video.src.includes(encodeURIComponent(shown.basename))) {
        this.video.src = url;
      }
      this.player.replaceChildren(this.video);

      if (pend) {
        this.actions.append(
          el("button", { class: "h3p-btn ok",
                         text: "Approve \u2014 chain from this",
                         onclick: () => this.act("approve", "approved") }),
          el("button", { class: "h3p-btn warn", text: "Re-roll",
                         onclick: () => {
                           app.queuePrompt(0, 1);
                           toast("queued \u2014 next render replaces " +
                                 "this take");
                         } }),
          el("button", { class: "h3p-btn bad", text: "Reject",
                         onclick: () => this.showConfirm(
                           `Reject clip ${shown.index}? Its files go to ` +
                           `.trash/ and the chain steps back.`,
                           () => this.act("reject", "rejected")) }));
      } else {
        this.actions.append(
          el("button", { class: "h3p-btn warn",
                         text: `Reopen clip ${shown.index}\u2026`,
                         onclick: () => this.reopen(shown.index) }));
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
    }

    // chain rail
    for (const c of s.clips) {
      const isTail = tail && c.basename === tail.basename;
      const card = el("div", {
        class: "h3p-clip " + c.status +
          (shown && shown.basename === c.basename ? " sel" : ""),
        onclick: () => this.view(c.basename),
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

    this.footText.textContent =
      `${approved.length} approved \u00b7 ` +
      (tail ? `chain tails clip ${tail.index}` :
              "chain inactive (fresh clip 1)") +
      ` \u00b7 project folder: output/h3_projects/${name}`;
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

      this._h3RefreshSummary = async () => {
        const name = this.widgets?.find(
          (w) => w.name === "project_name")?.value;
        if (!name) return;
        try {
          const s = await getState(name);
          const approved = s.clips.filter(
            (c) => c.status === "approved").length;
          const pend = s.pending;
          summary.innerHTML =
            `<b>${name}</b> \u00b7 ${approved} approved` +
            (pend ? ` \u00b7 <span class="pend">clip ${pend.index} ` +
                    `take ${pend.take} pending review</span>` : "") +
            `<br><span class="nx">next: ${s.next_save.basename}` +
            `</span> \u00b7 ` +
            (s.chain_active ? `<span class="ok">chain active</span>`
                            : `chain inactive`);
        } catch (e) {
          summary.textContent =
            `${name}: not created yet \u2014 open the project panel or ` +
            `queue once`;
        }
      };
      this._h3RefreshSummary();
      api.addEventListener("executed", this._h3RefreshSummary);
      const origRemoved = this.onRemoved;
      this.onRemoved = function () {
        api.removeEventListener("executed", this._h3RefreshSummary);
        origRemoved?.apply(this, arguments);
      };
      this.size = [Math.max(this.size[0], 300),
                   Math.max(this.size[1], 170)];
    };
  },
});
