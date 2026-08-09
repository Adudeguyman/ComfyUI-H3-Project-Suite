// H3 Project Suite - project panel on the H3 Project Hub node.
//
// The panel is a view over /h3_suite/ routes; the manifest stays the only
// source of truth. Review actions (approve / reject / reopen) POST to the
// server and the node side re-resolves on the next queue press because
// IS_CHANGED hashes the manifest. Nothing here writes graph state except
// keeping the project_name widget in sync with the selector.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const C = {
  bg: "#1d1d1d", card: "#262626", line: "#3a3a3a", text: "#d6d6d6",
  dim: "#8f8f8f", ok: "#7ac37a", warn: "#e0b45c", bad: "#d97a7a",
  accent: "#6f9fd8",
};

function el(tag, style, text) {
  const e = document.createElement(tag);
  if (style) Object.assign(e.style, style);
  if (text !== undefined) e.textContent = text;
  return e;
}

function btn(label, onclick, color) {
  const b = el("button", {
    background: "transparent", color: color || C.text,
    border: `1px solid ${color || C.line}`, borderRadius: "4px",
    padding: "4px 10px", cursor: "pointer", fontSize: "12px",
  }, label);
  b.onclick = onclick;
  return b;
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

class Panel {
  constructor(node) {
    this.node = node;
    this.root = el("div", {
      background: C.bg, color: C.text, border: `1px solid ${C.line}`,
      borderRadius: "6px", padding: "8px", fontSize: "12px",
      fontFamily: "sans-serif", display: "flex", flexDirection: "column",
      gap: "8px", minHeight: "300px", overflow: "hidden",
    });

    // header: project select + new + refresh
    const head = el("div", { display: "flex", gap: "6px",
                             alignItems: "center" });
    this.select = el("select", {
      flex: "1", background: C.card, color: C.text,
      border: `1px solid ${C.line}`, borderRadius: "4px", padding: "3px",
    });
    this.select.onchange = () => {
      this.setWidgetName(this.select.value);
      this.refresh();
    };
    head.append(this.select,
                btn("New", () => this.newProject()),
                btn("\u21bb", () => this.refresh()));
    this.root.append(head);

    // inline name entry (shown by New; replaces window.prompt)
    this.nameRow = el("div", { display: "none", gap: "6px" });
    this.nameInput = el("input", {
      flex: "1", background: C.card, color: C.text,
      border: `1px solid ${C.line}`, borderRadius: "4px", padding: "3px 6px",
    });
    this.nameInput.placeholder = "new project name";
    this.nameInput.onkeydown = (e) => {
      if (e.key === "Enter") this.createProject();
      if (e.key === "Escape") this.nameRow.style.display = "none";
      e.stopPropagation(); // keep LiteGraph hotkeys out of the field
    };
    this.nameRow.append(this.nameInput,
                        btn("Create", () => this.createProject(), C.ok),
                        btn("Cancel", () => {
                          this.nameRow.style.display = "none";
                        }));
    this.root.append(this.nameRow);

    // inline confirm bar (replaces window.confirm; one pending action)
    this.confirmBar = el("div", {
      display: "none", flexDirection: "column", gap: "6px",
      background: C.card, border: `1px solid ${C.warn}`,
      borderRadius: "6px", padding: "8px",
    });
    this.confirmText = el("div", { whiteSpace: "pre-wrap" });
    const confirmBtns = el("div", { display: "flex", gap: "6px" });
    this.confirmYes = btn("Confirm", () => {}, C.warn);
    confirmBtns.append(this.confirmYes,
                       btn("Cancel", () => this.hideConfirm()));
    this.confirmBar.append(this.confirmText, confirmBtns);
    this.root.append(this.confirmBar);

    // pending review card
    this.review = el("div", {
      background: C.card, border: `1px solid ${C.line}`,
      borderRadius: "6px", padding: "8px", display: "none",
      flexDirection: "column", gap: "6px",
    });
    this.reviewTitle = el("div", { color: C.warn, fontWeight: "bold" });
    this.video = el("video", {
      width: "100%", maxHeight: "220px", background: "#000",
      borderRadius: "4px",
    });
    this.video.controls = true;
    this.video.loop = true;
    const actions = el("div", { display: "flex", gap: "6px" });
    actions.append(
      btn("Approve \u2014 chain from this", () => this.act("approve"), C.ok),
      btn("Re-roll (queue again)", () => app.queuePrompt(0, 1), C.warn),
      btn("Reject", () => this.act("reject"), C.bad));
    this.review.append(this.reviewTitle, this.video, actions);
    this.root.append(this.review);

    // chain strip
    this.strip = el("div", {
      display: "flex", gap: "4px", alignItems: "stretch",
      flexWrap: "wrap",
    });
    this.root.append(this.strip);

    // status + footer
    this.statusLine = el("div", { color: C.dim, whiteSpace: "pre-wrap" });
    const foot = el("div", { display: "flex", gap: "6px", marginTop: "auto" });
    foot.append(
      btn("Export master", () => this.exportMaster()),
      btn("Purge trash", () => this.act("purge_trash")));
    this.root.append(this.statusLine, foot);

    api.addEventListener("executed", () => this.refresh());
    api.addEventListener("execution_error", () => this.refresh());
    this.loadProjects().then(() => this.refresh());
  }

  widget() {
    return this.node.widgets?.find((w) => w.name === "project_name");
  }

  name() {
    return this.widget()?.value || "";
  }

  setWidgetName(v) {
    const w = this.widget();
    if (w && w.value !== v) {
      w.value = v;
      this.node.setDirtyCanvas(true, true);
    }
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
        const o = document.createElement("option");
        o.value = o.textContent = n;
        this.select.append(o);
      }
      if (current) this.select.value = current;
    } catch (e) { /* server without routes: leave empty */ }
  }

  showConfirm(message, onYes) {
    this.confirmText.textContent = message;
    this.confirmYes.onclick = () => { this.hideConfirm(); onYes(); };
    this.confirmBar.style.display = "flex";
  }

  hideConfirm() {
    this.confirmBar.style.display = "none";
  }

  newProject() {
    this.nameInput.value = "";
    this.nameRow.style.display = "flex";
    this.nameInput.focus();
  }

  async createProject() {
    const name = this.nameInput.value.trim();
    if (!name) return;
    try {
      await post("/h3_suite/project/create", { name });
      this.nameRow.style.display = "none";
      this.setWidgetName(name);
      await this.loadProjects();
      this.select.value = name;
      this.refresh();
    } catch (e) { this.error(e); }
  }

  async act(action) {
    try {
      await post(`/h3_suite/project/${action}`, { name: this.name() });
      this.refresh();
    } catch (e) { this.error(e); }
  }

  async reopen(index) {
    try {
      const probe = await post("/h3_suite/project/reopen",
                               { name: this.name(), index });
      const drop = probe.would_drop || [];
      const msg = drop.length
        ? `Reopen clip ${index}? Everything after it was conditioned on ` +
          `it and will be dropped to .trash/:\n\n${drop.join("\n")}`
        : `Reopen clip ${index} for a re-roll?`;
      this.showConfirm(msg, async () => {
        try {
          await post("/h3_suite/project/reopen",
                     { name: this.name(), index, confirm: true });
          this.refresh();
        } catch (e) { this.error(e); }
      });
    } catch (e) { this.error(e); }
  }

  async exportMaster() {
    try {
      const out = await post("/h3_suite/project/export",
                             { name: this.name() });
      this.statusLine.textContent = `master written: ${out.master}`;
      this.statusLine.style.color = C.ok;
    } catch (e) { this.error(e); }
  }

  error(e) {
    this.statusLine.textContent = String(e.message || e);
    this.statusLine.style.color = C.bad;
  }

  chip(clip, isTail) {
    const approved = clip.status === "approved";
    const box = el("div", {
      border: `1px solid ${approved ? C.line : C.warn}`,
      borderRadius: "4px", padding: "4px 8px", background: C.card,
      cursor: approved ? "pointer" : "default", minWidth: "56px",
    });
    const row = el("div", { display: "flex", gap: "5px",
                            alignItems: "center" });
    row.append(el("span", { fontWeight: "bold" }, String(clip.index)));
    row.append(el("span", {
      color: approved ? C.ok : C.warn, fontSize: "11px",
    }, approved ? "\u2713" : "\u25cf"));
    if (isTail) row.append(el("span", { color: C.accent,
                                        fontSize: "10px" }, "tail"));
    box.append(row);
    box.append(el("div", { color: C.dim, fontSize: "10px" },
                  `take ${clip.take}`));
    if (approved) {
      box.title = "click to reopen (drops later clips)";
      box.onclick = () => this.reopen(clip.index);
    }
    return box;
  }

  async refresh() {
    const name = this.name();
    if (!name) return;
    let s;
    try {
      const r = await api.fetchApi(
        `/h3_suite/project/state?name=${encodeURIComponent(name)}`);
      s = await r.json();
      if (s.error) throw new Error(s.error);
    } catch (e) {
      this.statusLine.textContent =
        `${e.message || e} \u2014 press New to create it, or queue once ` +
        `with create_if_missing on.`;
      this.statusLine.style.color = C.dim;
      this.review.style.display = "none";
      this.strip.innerHTML = "";
      return;
    }

    // pending review
    if (s.pending) {
      this.review.style.display = "flex";
      this.reviewTitle.textContent =
        `Pending review: clip ${s.pending.index} take ${s.pending.take}`;
      const src = `/h3_suite/project/video?name=${encodeURIComponent(name)}` +
        `&basename=${encodeURIComponent(s.pending.basename)}` +
        `&t=${Date.now()}`;
      if (!this.video.src.includes(s.pending.basename)) {
        this.video.src = api.apiURL ? api.apiURL(src) : src;
      }
    } else {
      this.review.style.display = "none";
      this.video.removeAttribute("src");
    }

    // strip
    this.strip.innerHTML = "";
    const approved = s.clips.filter((c) => c.status === "approved");
    const tailIdx = approved.length ? approved[approved.length - 1].index
                                    : -1;
    for (const c of s.clips) {
      this.strip.append(this.chip(c, c.index === tailIdx));
      this.strip.append(el("span", { color: C.dim, alignSelf: "center" },
                           "\u203a"));
    }
    const next = el("div", {
      border: `1px dashed ${C.dim}`, borderRadius: "4px",
      padding: "4px 8px", color: C.dim, minWidth: "56px",
    });
    next.append(el("div", {}, `+ ${s.next_save.basename}`));
    this.strip.append(next);

    const tail = approved.length
      ? `chains from clip ${tailIdx}` : "chain inactive (fresh clip 1)";
    this.statusLine.style.color = C.dim;
    this.statusLine.textContent =
      `${approved.length} approved \u00b7 ${tail} \u00b7 next queue press ` +
      `renders ${s.next_save.basename}`;
  }
}

app.registerExtension({
  name: "h3_suite.project_panel",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "H3ProjectHub") return;
    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      orig?.apply(this, arguments);
      const panel = new Panel(this);
      const w = this.addDOMWidget("h3_project_panel", "div", panel.root, {
        serialize: false,
        getMinHeight: () => 320,
      });
      this._h3Panel = panel;
      this.size = [Math.max(this.size[0], 340),
                   Math.max(this.size[1], 430)];
    };
  },
});
