// DataCreator front end: plain ES module, no build step.

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") {
      for (const [sk, sv] of Object.entries(v)) {
        if (sk.startsWith("--")) el.style.setProperty(sk, sv); else el.style[sk] = sv;
      }
    }
    else if (k === "value") el.value = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.blob();
}

function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 3500);
}

function modal(title, body, buttons = []) {
  const root = $("#modal-root");
  const close = () => { root.innerHTML = ""; document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  const backdrop = h("div", { class: "modal-backdrop", onmousedown: (e) => { if (e.target === backdrop) close(); } },
    h("div", { class: "modal" },
      h("div", { class: "modal-head" }, h("h2", { style: { margin: 0 } }, title), h("button", { class: "ghost icon", onclick: close, title: "Close", style: { fontSize: "20px" } }, "×")),
      h("div", { class: "modal-body" }, body),
      buttons.length ? h("div", { class: "modal-foot" }, buttons) : null));
  root.innerHTML = "";
  root.append(backdrop);
  return close;
}

function confirmDialog(message, okLabel = "Delete") {
  return new Promise((resolve) => {
    let close;
    const done = (v) => { close(); resolve(v); };
    close = modal("Are you sure?", h("p", {}, message), [
      h("button", { onclick: () => done(false) }, "Cancel"),
      h("button", { class: "primary", onclick: () => done(true) }, okLabel),
    ]);
  });
}

const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
const clone = (o) => JSON.parse(JSON.stringify(o));
const uid = () => Math.random().toString(16).slice(2, 12);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

// ------------------------------------------------------------------ app state
const state = { status: null, types: [], typeMap: {}, templates: [] };
let currentCleanup = null;
let dirtyGuard = null; // function returning true if navigation should be blocked

async function loadTypes() {
  state.types = await api("GET", "/api/types");
  state.typeMap = Object.fromEntries(state.types.map((t) => [t.key, t]));
}
async function loadTemplates() { state.templates = await api("GET", "/api/templates"); }

const KIND_INFO = {
  picklist: ["Pick-list", "Choose randomly from a list of values (optionally weighted). Can be generated with AI."],
  pattern: ["Pattern", "Codes and reference numbers, e.g. MRN-####-??"],
  number_range: ["Number range", "A random number between a minimum and maximum."],
  date_range: ["Date range", "A random date: between two dates, relative to today, or relative to another field."],
  composite: ["Combined", "Build a value from other fields/types, e.g. {title} {first_name} {last_name}."],
  boolean: ["Yes / No", "True a set percentage of the time. Good for tick boxes."],
  ai: ["AI-generated text", "Free text written by AI to fit each record (e.g. clinical notes), or a pre-generated pool."],
};
const RENDER_INFO = {
  text: "Single line", multiline: "Multi-line", comb: "Character boxes", checkbox: "Tick box", signature: "Signature",
};
const PERSONS = {
  1: "Patient (person 1)", 2: "Person 2: relative (next of kin / carer)", 3: "Person 3: e.g. prescriber",
  4: "Person 4", 5: "Person 5", 6: "Person 6", 7: "Person 7", 8: "Person 8",
};

function typeLabel(key) {
  const t = state.typeMap[key];
  return t ? t.label : key ? `${key} (missing)` : "No type";
}

function typeSelect(value, onChange) {
  const groups = {};
  for (const t of state.types) (groups[t.category] ||= []).push(t);
  const order = ["Person", "Contact", "Address", "GP", "Clinical", "Dates", "Other"];
  const cats = Object.keys(groups).sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const sel = h("select", { onchange: (e) => onChange(e.target.value) },
    h("option", { value: "" }, "— choose a data type —"),
    cats.map((c) => h("optgroup", { label: c },
      groups[c].map((t) => h("option", { value: t.key, selected: t.key === value }, `${t.label}  ·  ${t.key}`)))));
  sel.value = value || "";
  return sel;
}

// ------------------------------------------------------------------ router
const routes = [
  [/^#\/templates\/(\d+)$/, (m) => viewEditor(+m[1])],
  [/^#\/templates$/, () => viewTemplates()],
  [/^#\/types$/, () => viewTypes()],
  [/^#\/generate$/, () => viewGenerate()],
  [/^#\/runs$/, () => viewRuns()],
];
let lastHash = location.hash;

async function route() {
  if (dirtyGuard && dirtyGuard()) {
    if (!window.confirm("You have unsaved changes. Leave without saving?")) {
      history.replaceState(null, "", lastHash);
      return;
    }
  }
  dirtyGuard = null;
  if (currentCleanup) { currentCleanup(); currentCleanup = null; }
  const hash = location.hash || "#/templates";
  lastHash = hash;
  const tab = hash.split("/")[1];
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  const view = $("#view");
  view.className = "";
  view.innerHTML = "";
  for (const [re, fn] of routes) {
    const m = hash.match(re);
    if (m) {
      try { await fn(m); } catch (e) { view.append(h("div", { class: "notice err" }, e.message)); }
      return;
    }
  }
  location.hash = "#/templates";
}
window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", (e) => { if (dirtyGuard && dirtyGuard()) { e.preventDefault(); e.returnValue = ""; } });

// ================================================================== TEMPLATES LIST
async function viewTemplates() {
  await loadTemplates();
  const view = $("#view");
  const fileInput = h("input", { type: "file", accept: "application/pdf,.pdf", style: { display: "none" } });
  const nameInput = h("input", { type: "text", placeholder: "Template name (defaults to file name)" });
  const dz = h("div", { class: "dropzone" },
    h("p", {}, h("strong", {}, "Drop a blank PDF here"), " or ",
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); fileInput.click(); } }, "choose a file")),
    h("p", { class: "muted small" }, "Upload blank templates only. Multi-page PDFs are fine."));

  async function upload(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", nameInput.value);
    try {
      dz.classList.add("over");
      const t = await api("POST", "/api/templates", fd);
      toast(`Uploaded "${t.name}"`, "ok");
      location.hash = `#/templates/${t.id}`;
    } catch (e) { toast(e.message, "err"); } finally { dz.classList.remove("over"); }
  }
  fileInput.addEventListener("change", () => upload(fileInput.files[0]));
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); upload(e.dataTransfer.files[0]); });

  const cards = state.templates.map((t) => {
    const bad = t.fields.filter((f) => !state.typeMap[f.type]).length;
    return h("div", { class: "tpl-card" },
      h("div", { class: "tpl-thumb", onclick: () => (location.hash = `#/templates/${t.id}`) },
        h("img", { src: `/api/templates/${t.id}/pages/0.png?dpi=40`, alt: "" })),
      h("div", { class: "tpl-body" },
        h("div", { class: "name" }, t.name),
        h("div", { class: "muted small" },
          `${t.page_sizes.length} page${t.page_sizes.length > 1 ? "s" : ""} · ${t.fields.length} field${t.fields.length === 1 ? "" : "s"}`),
        bad ? h("div", { class: "pill err", style: { marginTop: "6px" } }, `${bad} field(s) need a type`) : null,
        h("div", { class: "muted small" }, `Updated ${fmtDate(t.updated_at)}`)),
      h("div", { class: "tpl-actions" },
        h("a", { class: "btn sm primary", href: `#/templates/${t.id}` }, "Edit"),
        h("button", {
          class: "sm", onclick: async () => {
            try { await api("POST", `/api/templates/${t.id}/duplicate`); toast("Duplicated", "ok"); viewTemplatesReload(); } catch (e) { toast(e.message, "err"); }
          },
        }, "Duplicate"),
        h("button", {
          class: "sm danger", onclick: async () => {
            if (!(await confirmDialog(`Delete template "${t.name}"? This can't be undone.`))) return;
            try { await api("DELETE", `/api/templates/${t.id}`); viewTemplatesReload(); } catch (e) { toast(e.message, "err"); }
          },
        }, "Delete")));
  });

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Templates"),
        h("p", { class: "muted" }, "Upload a blank PDF, then draw boxes where data should go and choose what goes in each one."))),
    h("div", { class: "card" }, h("div", { class: "stack" }, nameInput, dz, fileInput)),
    h("div", { style: { height: "20px" } }),
    state.templates.length ? h("div", { class: "tpl-grid" }, cards)
      : h("div", { class: "card empty" }, "No templates yet. Upload a blank PDF to get started."));
}
function viewTemplatesReload() { $("#view").innerHTML = ""; viewTemplates(); }

// ================================================================== TEMPLATE EDITOR
async function viewEditor(id) {
  await loadTypes();
  const tpl = await api("GET", `/api/templates/${id}`);
  const others = await api("GET", "/api/templates");
  const otherNames = new Map(); // field name -> [template names] in other templates (for linking hints)
  for (const t of others) if (t.id !== id) for (const f of t.fields) {
    if (!otherNames.has(f.name)) otherNames.set(f.name, { templates: new Set(), type: f.type, person: f.person });
    otherNames.get(f.name).templates.add(t.name);
  }

  const view = $("#view");
  view.className = "full";
  let fields = clone(tpl.fields);
  let name = tpl.name;
  let saved = JSON.stringify({ name, fields });
  let page = 0;
  let zoom = store.get("dc.zoom", 1.3);
  let selId = null;
  let sideTab = "field";
  let drafts = [];   // AI-suggested new data types awaiting review
  let missed = [];   // questions the AI saw with no box to fill
  let detectNote = "";
  const isDirty = () => JSON.stringify({ name, fields }) !== saved;
  dirtyGuard = isDirty;

  const pageWrap = h("div", { class: "page-wrap" });
  let detectBtn;
  const canvasScroll = h("div", { class: "canvas-scroll" }, pageWrap);
  const sideBody = h("div", { class: "side-body" });
  const pageLabel = h("span", { class: "muted" });
  const saveBtn = h("button", { class: "primary", onclick: () => save() }, "Save");
  const sideTabs = h("div", { class: "side-tabs" });

  const nameInput = h("input", { type: "text", class: "name-input", value: name, oninput: (e) => { name = e.target.value; refreshDirty(); } });

  const toolbar = h("div", { class: "editor-toolbar" },
    h("a", { class: "btn sm ghost", href: "#/templates", title: "Back to templates" }, "←"),
    nameInput,
    h("span", { class: "sep" }),
    h("button", { class: "sm icon", title: "Previous page", onclick: () => setPage(page - 1) }, "‹"),
    pageLabel,
    h("button", { class: "sm icon", title: "Next page", onclick: () => setPage(page + 1) }, "›"),
    h("span", { class: "sep" }),
    h("button", { class: "sm icon", title: "Zoom out", onclick: () => setZoom(zoom / 1.15) }, "−"),
    h("button", { class: "sm icon", title: "Zoom in", onclick: () => setZoom(zoom * 1.15) }, "+"),
    h("button", { class: "sm", title: "Fit width", onclick: () => fitWidth() }, "Fit"),
    h("span", { class: "grow" }),
    (detectBtn = h("button", { class: "sm", onclick: () => autoDetect(), title: "Find input boxes and suggest a field for each" }, "Auto-detect fields")),
    h("span", { class: "muted small" }, "Preview"),
    h("div", { class: "seg" },
      h("button", { class: "sm", onclick: () => preview(0), title: "Open a typed sample in a new tab" }, "Typed"),
      h("button", { class: "sm", onclick: () => preview(100), title: "Open a handwritten sample in a new tab" }, "Handwritten")),
    saveBtn);

  view.append(h("div", { class: "editor" },
    h("div", { class: "editor-main" }, toolbar, canvasScroll),
    h("div", { class: "sidebar" }, sideTabs, sideBody)));

  function refreshDirty() {
    saveBtn.textContent = isDirty() ? "Save •" : "Saved";
    saveBtn.disabled = !isDirty();
  }

  function setZoom(z) { zoom = Math.max(0.4, Math.min(4, z)); store.set("dc.zoom", zoom); renderPage(); }
  function fitWidth() { setZoom((canvasScroll.clientWidth - 48) / tpl.page_sizes[page][0]); }
  function setPage(p) {
    if (p < 0 || p >= tpl.page_sizes.length) return;
    page = p;
    renderPage();
    canvasScroll.scrollTop = 0;
  }

  // --- page + boxes
  function renderPage() {
    const [w, hgt] = tpl.page_sizes[page];
    pageWrap.style.width = `${w * zoom}px`;
    pageWrap.style.height = `${hgt * zoom}px`;
    pageWrap.innerHTML = "";
    pageWrap.append(h("img", { src: `/api/templates/${id}/pages/${page}.png?dpi=${zoom > 2 ? 216 : 144}`, alt: "", draggable: false }));
    for (const f of fields.filter((f) => f.page === page)) pageWrap.append(boxEl(f));
    pageLabel.textContent = `Page ${page + 1} / ${tpl.page_sizes.length}`;
  }

  function boxEl(f) {
    const [x0, y0, x1, y1] = f.rect;
    const bad = !state.typeMap[f.type] || !f.name;
    const el = h("div", {
      class: `fbox${f.id === selId ? " sel" : ""}${bad ? " bad" : ""}${f.proposed ? " proposed" : ""}`, "data-render": f.render, "data-id": f.id,
      style: { left: `${x0 * zoom}px`, top: `${y0 * zoom}px`, width: `${(x1 - x0) * zoom}px`, height: `${(y1 - y0) * zoom}px` },
      title: `${f.name || "(unnamed)"} — ${typeLabel(f.type)}`,
    },
      h("div", { class: "tag" }, `${f.proposed ? "suggested · " : ""}${f.name || "unnamed"}${f.person > 1 ? ` · P${f.person}` : ""}`),
      h("div", { class: "handle" }));
    return el;
  }

  const toPt = (e) => {
    const r = pageWrap.getBoundingClientRect();
    return [(e.clientX - r.left) / zoom, (e.clientY - r.top) / zoom];
  };
  const clampRect = (r) => {
    const [w, hgt] = tpl.page_sizes[page];
    let [x0, y0, x1, y1] = r;
    const bw = x1 - x0, bh = y1 - y0;
    x0 = Math.max(0, Math.min(w - bw, x0)); y0 = Math.max(0, Math.min(hgt - bh, y0));
    return [x0, y0, x0 + bw, y0 + bh].map((v) => Math.round(v * 100) / 100);
  };

  pageWrap.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const box = e.target.closest(".fbox");
    const [sx, sy] = toPt(e);
    if (box) {
      const f = fields.find((x) => x.id === box.dataset.id);
      select(f.id);
      const orig = [...f.rect];
      const resizing = e.target.classList.contains("handle");
      const move = (ev) => {
        const [cx, cy] = toPt(ev);
        const dx = cx - sx, dy = cy - sy;
        if (resizing) {
          f.rect = [orig[0], orig[1], Math.max(orig[0] + 4, orig[2] + dx), Math.max(orig[1] + 4, orig[3] + dy)].map((v) => Math.round(v * 100) / 100);
        } else {
          f.rect = clampRect([orig[0] + dx, orig[1] + dy, orig[2] + dx, orig[3] + dy]);
        }
        const el = pageWrap.querySelector(`[data-id="${f.id}"]`);
        Object.assign(el.style, { left: `${f.rect[0] * zoom}px`, top: `${f.rect[1] * zoom}px`, width: `${(f.rect[2] - f.rect[0]) * zoom}px`, height: `${(f.rect[3] - f.rect[1]) * zoom}px` });
      };
      const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); refreshDirty(); };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
      e.preventDefault();
      return;
    }
    // draw a new box
    const rubber = h("div", { class: "rubber" });
    pageWrap.append(rubber);
    let rect = [sx, sy, sx, sy];
    const move = (ev) => {
      const [cx, cy] = toPt(ev);
      rect = [Math.min(sx, cx), Math.min(sy, cy), Math.max(sx, cx), Math.max(sy, cy)];
      Object.assign(rubber.style, { left: `${rect[0] * zoom}px`, top: `${rect[1] * zoom}px`, width: `${(rect[2] - rect[0]) * zoom}px`, height: `${(rect[3] - rect[1]) * zoom}px` });
    };
    const up = async () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      rubber.remove();
      if (rect[2] - rect[0] < 4 || rect[3] - rect[1] < 4) { select(null); return; }
      rect = rect.map((v) => Math.round(v * 100) / 100);
      const f = { id: uid(), name: "", type: "", page, rect, render: "text", person: 1, options: {} };
      fields.push(f);
      selId = f.id;
      renderPage();
      renderSide();
      refreshDirty();
      try {
        const s = await api("POST", `/api/templates/${id}/suggest`, { page, rect });
        applySuggestion(f, s);
      } catch (err) { toast(`Suggestion failed: ${err.message}`, "err"); }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    e.preventDefault();
  });

  function uniqueName(base, f, s) {
    if (!base) return "";
    const clash = (n) => fields.some((x) => x !== f && x.name === n);
    // a deliberately shared name (e.g. Yes/No boxes for one question) keeps the same name
    const same = fields.find((x) => x !== f && x.name === base);
    if (!same || (same.type === s.type && same.render === "checkbox" && s.render === "checkbox")) return base;
    let i = 2;
    while (clash(`${base}_${i}`)) i++;
    return `${base}_${i}`;
  }

  function applySuggestion(f, s) {
    if (!fields.includes(f)) return;
    f.label = s.label || "";
    if (!f.name) f.name = uniqueName(s.name, f, s);
    if (!f.type && s.type) f.type = s.type;
    if (s.person) f.person = s.person;
    if (s.render) f.render = s.render;
    if (s.options) f.options = { ...f.options, ...s.options };
    if (f.render === "comb" && !f.options.cells && f.type === "nhs_number") f.options.cells = 10;
    renderPage();
    renderSide();
    refreshDirty();
  }

  function select(fid) {
    selId = fid;
    if (fid) sideTab = "field";
    pageWrap.querySelectorAll(".fbox").forEach((el) => el.classList.toggle("sel", el.dataset.id === fid));
    renderSide();
  }

  function removeField(fid) {
    fields = fields.filter((x) => x.id !== fid);
    if (selId === fid) selId = null;
    renderPage(); renderSide(); refreshDirty();
  }

  function duplicateField(f) {
    const hgt = f.rect[3] - f.rect[1];
    const [, ph] = tpl.page_sizes[f.page];
    const dy = f.rect[3] + hgt + 4 <= ph ? hgt + 4 : 0;
    const n = { ...clone(f), id: uid(), name: uniqueName(f.name.replace(/_\d+$/, ""), null, {}), rect: [f.rect[0], f.rect[1] + dy, f.rect[2], f.rect[3] + dy] };
    fields.push(n);
    renderPage(); select(n.id); refreshDirty();
  }

  // --- keyboard
  const onKey = (e) => {
    if (e.target.matches("input, textarea, select")) return;
    const f = fields.find((x) => x.id === selId);
    if ((e.key === "Delete" || e.key === "Backspace") && f) { e.preventDefault(); removeField(f.id); }
    else if (e.key === "Escape") select(null);
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d" && f) { e.preventDefault(); duplicateField(f); }
    else if (f && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      f.rect = clampRect([f.rect[0] + d[0], f.rect[1] + d[1], f.rect[2] + d[0], f.rect[3] + d[1]]);
      renderPage(); refreshDirty();
    } else if (e.key === "PageDown") setPage(page + 1);
    else if (e.key === "PageUp") setPage(page - 1);
  };
  document.addEventListener("keydown", onKey);
  currentCleanup = () => document.removeEventListener("keydown", onKey);

  // --- sidebar
  function renderSide() {
    sideTabs.innerHTML = "";
    sideTabs.append(
      h("button", { class: sideTab === "field" ? "active" : "", onclick: () => { sideTab = "field"; renderSide(); } }, "Field"),
      h("button", { class: sideTab === "all" ? "active" : "", onclick: () => { sideTab = "all"; renderSide(); } }, `All fields (${fields.length})`));
    sideBody.innerHTML = "";
    const panel = suggestionsPanel();
    if (panel) sideBody.append(panel);
    if (sideTab === "all") return renderFieldList();
    const f = fields.find((x) => x.id === selId);
    if (!f) {
      sideBody.append(h("div", { class: "stack" },
        h("h3", {}, "Mark up the template"),
        h("p", { class: "muted" }, "Drag on the page to draw a box where data should go. Printed labels next to the box are read automatically to suggest the field name and data type."),
        h("div", { class: "notice" }, "Fields with the ", h("strong", {}, "same name"), " share one value. That applies within this template and across templates in a linked run."),
        h("div", { class: "small muted" },
          h("div", {}, h("span", { class: "kbd" }, "Del"), " delete  ·  ", h("span", { class: "kbd" }, "Ctrl"), "+", h("span", { class: "kbd" }, "D"), " duplicate"),
          h("div", {}, h("span", { class: "kbd" }, "←↑→↓"), " nudge (Shift ×10)  ·  ", h("span", { class: "kbd" }, "Ctrl"), "+", h("span", { class: "kbd" }, "S"), " save")),
        legend()));
      return;
    }
    sideBody.append(fieldForm(f));
  }

  function legend() {
    return h("div", { class: "small" }, h("div", { class: "label" }, "Box colours"),
      Object.entries(RENDER_INFO).map(([k, v]) => h("div", { class: "row", style: { gap: "6px" } },
        h("span", { class: "dot", style: { width: "10px", height: "10px", borderRadius: "2px", display: "inline-block", background: `var(--f-${k})` } }), v)));
  }

  function renderFieldList() {
    if (!fields.length) { sideBody.append(h("p", { class: "muted" }, "No fields yet.")); return; }
    for (let p = 0; p < tpl.page_sizes.length; p++) {
      const pf = fields.filter((f) => f.page === p).sort((a, b) => a.rect[1] - b.rect[1] || a.rect[0] - b.rect[0]);
      if (!pf.length) continue;
      sideBody.append(h("div", { class: "label", style: { marginTop: p ? "14px" : 0 } }, `Page ${p + 1}`));
      for (const f of pf) {
        const bad = !state.typeMap[f.type] || !f.name;
        sideBody.append(h("div", {
          class: `flist-item${f.id === selId ? " sel" : ""}`, style: { "--c": `var(--f-${f.render})` },
          onclick: () => { if (page !== f.page) setPage(f.page); select(f.id); },
        },
          h("span", { class: "dot" }),
          h("span", { class: "nm" }, f.name || "(unnamed)"),
          f.proposed ? h("span", { class: "pill accent" }, "suggested") : null,
          bad ? h("span", { class: "pill err" }, "needs type") : h("span", { class: "muted small" }, typeLabel(f.type))));
      }
    }
  }

  function fieldForm(f) {
    const t = state.typeMap[f.type];
    const opts = f.options || (f.options = {});
    const set = (k, v, rerender = false) => {
      if (v === "" || v === null || v === undefined) delete opts[k]; else opts[k] = v;
      refreshDirty();
      if (rerender) renderSide();
    };
    const linked = otherNames.get(f.name);
    const dl = h("datalist", { id: "dl-names" },
      [...new Set([...otherNames.keys(), ...fields.map((x) => x.name)])].filter(Boolean).sort().map((n) => h("option", { value: n })));
    const samples = h("div", { class: "samples" }, h("div", { class: "muted" }, "…"));
    if (t) {
      api("POST", "/api/types/sample", { key: t.key, n: 4 })
        .then((r) => { samples.innerHTML = ""; r.values.forEach((v) => samples.append(h("div", {}, v || "(blank)"))); })
        .catch((e) => { samples.innerHTML = ""; samples.append(h("div", { class: "muted" }, e.message)); });
    }
    const vk = t ? t.value_kind : "text";
    const dateFormats = state.status.date_formats;

    const formatOpts = [];
    if (f.render !== "checkbox" && f.render !== "signature") {
      if (vk === "date") {
        const custom = opts.date_format && !dateFormats.includes(opts.date_format);
        const pyFmt = (fmt) => fmt.replace("%d", "07").replace("%m", "03").replace("%Y", "1984").replace("%y", "84").replace("%b", "Mar").replace("%B", "March");
        formatOpts.push(h("label", { class: "field" }, h("span", {}, "Date format"),
          h("select", { onchange: (e) => set("date_format", e.target.value === "__custom" ? "%d/%m/%Y" : e.target.value, true) },
            dateFormats.map((d) => h("option", { value: d, selected: (opts.date_format || "%d/%m/%Y") === d }, `${pyFmt(d)}   (${d})`)),
            h("option", { value: "__custom", selected: custom }, "Custom…")),
          custom ? h("input", { type: "text", value: opts.date_format, style: { marginTop: "6px" }, oninput: (e) => set("date_format", e.target.value) }) : null,
          h("small", {}, "Python strftime codes, e.g. %d/%m/%Y")));
      }
      if (vk === "bool") {
        formatOpts.push(h("div", { class: "grid2" },
          h("label", { class: "field" }, h("span", {}, "Text when yes"), h("input", { type: "text", value: opts.true_text || "", placeholder: "Yes", oninput: (e) => set("true_text", e.target.value) })),
          h("label", { class: "field" }, h("span", {}, "Text when no"), h("input", { type: "text", value: opts.false_text || "", placeholder: "No", oninput: (e) => set("false_text", e.target.value) }))));
      }
      formatOpts.push(h("label", { class: "field" }, h("span", {}, "Text transform"),
        h("select", { onchange: (e) => set("transform", e.target.value) },
          [["", "As generated"], ["upper", "UPPER CASE"], ["lower", "lower case"], ["title", "Title Case"], ["no_spaces", "Remove spaces"], ["single_line", "Join lines with commas"]]
            .map(([v, l]) => h("option", { value: v, selected: (opts.transform || "") === v }, l)))));
      formatOpts.push(h("div", { class: "grid2" },
        h("label", { class: "field" }, h("span", {}, "Prefix"), h("input", { type: "text", value: opts.prefix || "", oninput: (e) => set("prefix", e.target.value) })),
        h("label", { class: "field" }, h("span", {}, "Suffix"), h("input", { type: "text", value: opts.suffix || "", oninput: (e) => set("suffix", e.target.value) }))));
    }
    const renderOpts = [];
    if (f.render === "comb") {
      renderOpts.push(h("div", { class: "grid2" },
        h("label", { class: "field" }, h("span", {}, "Number of boxes"),
          h("input", { type: "number", min: 1, max: 60, value: opts.cells || "", placeholder: "auto", oninput: (e) => set("cells", e.target.value ? +e.target.value : "") })),
        h("label", { class: "check", style: { marginTop: "22px" } },
          h("input", { type: "checkbox", checked: opts.comb_strip !== false, onchange: (e) => set("comb_strip", e.target.checked ? "" : false) }), "Skip spaces")));
    }
    if (f.render === "checkbox") {
      renderOpts.push(h("label", { class: "field" }, h("span", {}, "Tick when the value is"),
        h("input", { type: "text", value: opts.check_value || "", placeholder: "Yes / true (default)", oninput: (e) => set("check_value", e.target.value) }),
        h("small", {}, "E.g. type “Sex” with “Female” ticks this box only for female records. Give Yes/No boxes for one question the same field name.")));
    }
    if (f.render === "text") {
      renderOpts.push(h("label", { class: "field" }, h("span", {}, "Alignment"),
        h("select", { onchange: (e) => set("align", e.target.value) },
          [["", "Left"], ["center", "Centre"], ["right", "Right"]].map(([v, l]) => h("option", { value: v, selected: (opts.align || "") === v }, l)))));
    }
    if (f.render !== "checkbox") {
      renderOpts.push(h("label", { class: "check" },
        h("input", { type: "checkbox", checked: !!opts.erase, onchange: (e) => set("erase", e.target.checked || "") }),
        "Cover printed text in this box"),
        h("div", { class: "hint", style: { marginTop: "-8px" } }, "For Word placeholders like “Choose an item.”: hidden when a value is written, kept when the field is left blank."));
      renderOpts.push(h("label", { class: "field" }, h("span", {}, "Font size (pt)"),
        h("input", { type: "number", min: 4, max: 40, step: 0.5, value: opts.font_size || "", placeholder: "auto-fit", oninput: (e) => set("font_size", e.target.value ? +e.target.value : "") })));
    }

    const [x0, y0, x1, y1] = f.rect;
    return h("div", { class: "stack" },
      f.proposed ? h("div", { class: "notice row", style: { justifyContent: "space-between" } },
        h("span", {}, "Suggested field"),
        h("span", { class: "row", style: { gap: "6px" } },
          h("button", { class: "sm primary", onclick: () => acceptProposals([f]) }, "Accept"),
          h("button", { class: "sm", onclick: () => rejectProposals([f]) }, "Reject"))) : null,
      f.draft_type && !state.typeMap[f.type] ? h("div", { class: "notice warn" }, "Uses the suggested new type ", h("code", {}, f.draft_type),
        ". Review and create it in the suggestions panel above, or pick an existing type.") : null,
      f.label ? h("div", { class: "muted small" }, `Detected label: “${f.label}”`) : null,
      h("label", { class: "field" }, h("span", {}, "Field name"),
        h("input", {
          type: "text", value: f.name, list: "dl-names", placeholder: "e.g. patient_surname",
          oninput: (e) => { f.name = e.target.value.trim(); refreshDirty(); const el = pageWrap.querySelector(`[data-id="${f.id}"] .tag`); if (el) el.textContent = f.name || "unnamed"; },
          onchange: () => renderSide(),
        }), dl,
        linked ? h("small", {}, h("span", { class: "pill accent" }, "linked"), ` Also in: ${[...linked.templates].join(", ")}`) : h("small", {}, "Letters, digits and underscores.")),
      linked && (linked.type !== f.type || (linked.person || 1) !== f.person)
        ? h("div", { class: "notice warn" }, `In other templates “${f.name}” is ${typeLabel(linked.type)} (person ${linked.person || 1}). Linked runs use one definition, so match them.`) : null,
      h("label", { class: "field" }, h("span", {}, "Data type"),
        typeSelect(f.type, (v) => {
          f.type = v;
          const nt = state.typeMap[v];
          if (nt && ["signature", "checkbox"].includes(nt.render) && f.render === "text") f.render = nt.render;
          refreshDirty(); renderPage(); renderSide();
        }),
        h("small", {}, h("a", { href: "#/types" }, "Create a new data type"))),
      t ? samples : null,
      h("label", { class: "field" }, h("span", {}, "Whose data"),
        h("select", { onchange: (e) => { f.person = +e.target.value; refreshDirty(); renderPage(); } },
          Object.entries(PERSONS).map(([v, l]) => h("option", { value: v, selected: +v === (f.person || 1) }, l))),
        h("small", {}, "Each record has a patient, a relative (person 2) and unrelated people 3–8 such as clinicians. Person-based types (name, phone, email, address…) read from the chosen person.")),
      h("label", { class: "field" }, h("span", {}, "Draw as"),
        h("select", { onchange: (e) => { f.render = e.target.value; refreshDirty(); renderPage(); renderSide(); } },
          Object.entries(RENDER_INFO).map(([v, l]) => h("option", { value: v, selected: v === f.render }, l)))),
      ...renderOpts,
      ...formatOpts,
      h("div", { class: "grid2" },
        h("label", { class: "check", style: { marginTop: "22px" } },
          h("input", { type: "checkbox", checked: !!opts.required, onchange: (e) => set("required", e.target.checked || "") }), "Never leave blank"),
        h("label", { class: "field" }, h("span", {}, "Missing % override"),
          h("input", { type: "number", min: 0, max: 100, value: opts.missing_pct ?? "", placeholder: "run default", disabled: !!opts.required, oninput: (e) => set("missing_pct", e.target.value === "" ? "" : +e.target.value) }))),
      h("div", { class: "muted small mono" }, `page ${f.page + 1} · x ${x0.toFixed(0)} y ${y0.toFixed(0)} · ${(x1 - x0).toFixed(0)}×${(y1 - y0).toFixed(0)} pt`),
      h("div", { class: "row" },
        h("button", { class: "sm", onclick: () => duplicateField(f) }, "Duplicate"),
        h("button", { class: "sm danger", onclick: () => removeField(f.id) }, "Delete field")));
  }

  // --- auto-detect
  async function autoDetect() {
    const had = fields.filter((f) => f.proposed).length;
    fields = fields.filter((f) => !f.proposed);
    detectBtn.disabled = true;
    detectBtn.textContent = "Detecting…";
    try {
      const r = await api("POST", `/api/templates/${id}/autodetect`, { existing: fields.map(({ label, ...f }) => f) });
      for (const p of r.proposals) fields.push({ ...p, id: uid(), proposed: true });
      drafts = r.new_types;
      missed = r.missed;
      detectNote = r.ai_used ? "" : "AI is off, so these come from printed labels only. Check the types carefully.";
      if (r.errors.length) toast(`Some pages fell back to label matching: ${r.errors.join("; ")}`, "err");
      if (!r.proposals.length) toast(r.candidates ? "No new fields found" : "No input boxes found on this template", "err");
      else toast(`${r.proposals.length} suggested field${r.proposals.length > 1 ? "s" : ""}${drafts.length ? ` and ${drafts.length} new type${drafts.length > 1 ? "s" : ""}` : ""}. Review them in the sidebar.`, "ok");
      selId = null;
      sideTab = "field";
      const first = fields.find((f) => f.proposed);
      if (first && first.page !== page) page = first.page;
    } catch (e) {
      toast(e.message, "err");
      if (had) toast("Previous suggestions were cleared", "err");
    } finally {
      detectBtn.disabled = false;
      detectBtn.textContent = "Auto-detect fields";
      renderPage(); renderSide(); refreshDirty();
    }
  }

  function acceptProposals(list) {
    for (const f of list) delete f.proposed;
    renderPage(); renderSide(); refreshDirty();
  }

  function rejectProposals(list) {
    const ids = new Set(list.map((f) => f.id));
    fields = fields.filter((f) => !ids.has(f.id));
    if (ids.has(selId)) selId = null;
    const stillUsed = new Set(fields.map((f) => f.draft_type).filter(Boolean));
    drafts = drafts.filter((d) => stillUsed.has(d.key));
    renderPage(); renderSide(); refreshDirty();
  }

  function dismissDraft(d) {
    drafts = drafts.filter((x) => x !== d);
    for (const f of fields) if (f.draft_type === d.key) { delete f.draft_type; if (f.type === d.key) f.type = ""; }
    renderPage(); renderSide(); refreshDirty();
  }

  function createDraft(d) {
    typeEditor(d, {
      isNew: true,
      onSaved: async (saved) => {
        await loadTypes();
        drafts = drafts.filter((x) => x !== d);
        for (const f of fields) if (f.draft_type === d.key) { f.type = saved.key; delete f.draft_type; }
        renderPage(); renderSide(); refreshDirty();
      },
    });
  }

  function suggestionsPanel() {
    const pending = fields.filter((f) => f.proposed);
    if (!pending.length && !drafts.length && !missed.length) return null;
    const usage = (d) => fields.filter((f) => f.draft_type === d.key).length;
    return h("div", { class: "ai-panel stack" },
      pending.length ? h("div", {},
        h("div", { class: "row", style: { justifyContent: "space-between" } },
          h("strong", {}, `${pending.length} suggested field${pending.length > 1 ? "s" : ""}`),
          h("span", { class: "row", style: { gap: "6px" } },
            h("button", { class: "sm primary", onclick: () => acceptProposals(pending) }, "Accept all"),
            h("button", { class: "sm", onclick: () => rejectProposals(pending) }, "Reject all"))),
        h("div", { class: "hint" }, "Dashed boxes are suggestions. Click one to check or edit it. Nothing is saved until you accept and save."),
        detectNote ? h("div", { class: "notice warn", style: { marginTop: "8px" } }, detectNote) : null) : null,
      drafts.length ? h("div", {},
        h("div", { class: "label" }, "Suggested new data types"),
        h("div", { class: "hint", style: { marginTop: 0, marginBottom: "6px" } }, "Nothing in your type list fitted these fields. Review each draft, then create it or dismiss it."),
        drafts.map((d) => h("div", { class: "draft-type" },
          h("div", {}, h("div", { style: { fontWeight: 600 } }, d.label), h("div", { class: "muted small" },
            `${KIND_INFO[d.kind]?.[0] || d.kind} · used by ${usage(d)} field${usage(d) === 1 ? "" : "s"}`)),
          h("span", { class: "row", style: { gap: "6px" } },
            h("button", { class: "sm primary", onclick: () => createDraft(d) }, "Review & create"),
            h("button", { class: "sm ghost", onclick: () => dismissDraft(d) }, "Dismiss"))))) : null,
      missed.length ? h("div", {},
        h("div", { class: "label" }, "Questions with no box found"),
        h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } },
          missed.map((m) => h("li", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); setPage(m.page); } }, `p${m.page + 1}`), ` “${m.label}”`, m.note ? h("span", { class: "muted" }, ` — ${m.note}`) : null))),
        h("div", { class: "row", style: { justifyContent: "space-between" } },
          h("span", { class: "hint", style: { margin: 0 } }, "Draw these yourself if needed."),
          h("button", { class: "sm ghost", onclick: () => { missed = []; renderSide(); } }, "Clear"))) : null);
  }

  // --- save / preview
  async function save() {
    const pending = fields.filter((f) => f.proposed);
    if (pending.length) {
      toast(`Accept or reject the ${pending.length} suggested field(s) first`, "err");
      sideTab = "field"; select(pending[0].id);
      if (pending[0].page !== page) setPage(pending[0].page);
      return false;
    }
    const problems = fields.filter((f) => !f.name || !state.typeMap[f.type]);
    if (problems.length) {
      toast(`${problems.length} field(s) need a name and a data type before saving`, "err");
      select(problems[0].id);
      if (problems[0].page !== page) setPage(problems[0].page);
      return false;
    }
    try {
      const body = { name, fields: fields.map(({ label, proposed, draft_type, source, ...f }) => f) };
      const t = await api("PUT", `/api/templates/${id}`, body);
      fields = fields.map((f) => ({ ...t.fields.find((x) => x.id === f.id), label: f.label }));
      saved = JSON.stringify({ name, fields });
      refreshDirty();
      toast("Template saved", "ok");
      return true;
    } catch (e) { toast(e.message, "err"); return false; }
  }

  async function preview(hwPct) {
    if (isDirty() && !(await save())) return;
    if (!fields.length) { toast("Add some fields first", "err"); return; }
    const win = window.open("", "_blank");
    try {
      const blob = await api("POST", `/api/templates/${id}/preview`, { handwritten_pct: hwPct, seed: Math.floor(Math.random() * 1e9) });
      const url = URL.createObjectURL(blob);
      if (win) win.location = url; else window.open(url, "_blank");
    } catch (e) { if (win) win.close(); toast(e.message, "err"); }
  }

  renderPage();
  renderSide();
  refreshDirty();
  requestAnimationFrame(() => { if (!store.get("dc.zoom", null)) fitWidth(); });
}

// ================================================================== DATA TYPES
async function viewTypes() {
  await loadTypes();
  const view = $("#view");
  const custom = state.types.filter((t) => !t.builtin);
  const builtin = state.types.filter((t) => t.builtin);
  const exampleCells = new Map();

  const customTable = custom.length
    ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "Type"), h("th", {}, "Kind"), h("th", {}, "Examples"), h("th", {}))),
      h("tbody", {}, custom.map((t) => {
        const ex = h("td", { class: "small" }, "…");
        exampleCells.set(t.key, ex);
        return h("tr", {},
          h("td", {}, h("div", { style: { fontWeight: 600 } }, t.label), h("code", {}, t.key),
            t.description ? h("div", { class: "muted small" }, t.description) : null),
          h("td", {}, h("span", { class: "pill" }, KIND_INFO[t.kind]?.[0] || t.kind),
            t.kind === "ai" ? h("div", { class: "muted small", style: { marginTop: "4px" } }, t.config.mode === "pool" ? `pool of ${t.config.pool_size || 0}` : "live per record") : null),
          ex,
          h("td", { class: "actions" },
            h("button", { class: "sm", onclick: () => typeEditor(t) }, "Edit"), " ",
            h("button", {
              class: "sm danger", onclick: async () => {
                if (!(await confirmDialog(`Delete data type "${t.label}"?`))) return;
                try { await api("DELETE", `/api/types/${t.key}`); toast("Deleted", "ok"); reload(); } catch (e) { toast(e.message, "err"); }
              },
            }, "Delete")));
      })))
    : h("div", { class: "empty" }, "No custom types yet. Built-in types cover most common fields. Create one when you need something they don't cover.");

  const cats = [...new Set(builtin.map((t) => t.category))];
  const builtinTable = h("table", { class: "list" },
    h("thead", {}, h("tr", {}, h("th", { style: { width: "34%" } }, "Type"), h("th", {}, "Examples"))),
    h("tbody", {}, cats.map((c) => [
      h("tr", {}, h("td", { colspan: 2, style: { background: "#fafbfc", fontWeight: 600, fontSize: "12px", color: "#475467" } }, c)),
      builtin.filter((t) => t.category === c).map((t) => {
        const ex = h("td", { class: "small" }, "…");
        exampleCells.set(t.key, ex);
        return h("tr", {}, h("td", {}, h("div", {}, t.label), h("code", {}, t.key)), ex);
      }),
    ])));

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Data types"), h("p", { class: "muted" }, "What can be generated for a field. Built-in types read from a consistent synthetic person per record.")),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => typeEditor(null) }, "+ New data type"))),
    h("div", { class: "card" }, h("h2", {}, "Your types"), customTable),
    h("div", { class: "card" }, h("h2", {}, "Built-in types"), builtinTable));

  api("GET", "/api/types/examples").then((ex) => {
    for (const [k, cell] of exampleCells) {
      cell.innerHTML = "";
      const vals = ex[k] || [];
      if (state.typeMap[k]?.kind === "ai" && state.typeMap[k].config.mode !== "pool") cell.append(h("span", { class: "muted" }, "Written by AI during runs"));
      else vals.forEach((v) => cell.append(h("div", { style: { whiteSpace: "pre-wrap" } }, v || h("span", { class: "muted" }, "(blank)"))));
    }
  }).catch(() => {});
}
function reload() { route(); }

function typeEditor(existing, opts = {}) {
  const isNew = !existing || !!opts.isNew;
  const t = existing ? clone(existing) : { key: "", label: "", kind: "picklist", category: "Custom", description: "", config: {} };
  const cfgBox = h("div", { class: "stack" });
  const samplesBox = h("div");
  const aiOk = state.status.ai_available;

  const keyInput = h("input", { type: "text", value: t.key, disabled: !isNew, placeholder: "e.g. drug_name", oninput: (e) => (t.key = e.target.value.trim()) });
  const labelInput = h("input", {
    type: "text", value: t.label, placeholder: "e.g. Drug name",
    oninput: (e) => {
      t.label = e.target.value;
      if (isNew && !keyInput.dataset.touched) { t.key = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "t_$1"); keyInput.value = t.key; }
    },
  });
  keyInput.addEventListener("input", () => (keyInput.dataset.touched = "1"));
  const cats = [...new Set(state.types.map((x) => x.category))];

  const kindSel = h("select", {
    onchange: (e) => { t.kind = e.target.value; t.config = {}; renderCfg(); },
  }, Object.entries(KIND_INFO).map(([k, [l]]) => h("option", { value: k, selected: k === t.kind }, l)));

  function aiListBox(onValues, defaultDesc = "") {
    if (!aiOk) return h("div", { class: "notice warn" }, "AI generation is unavailable: REQUESTY_API_KEY is not set in .env");
    const desc = h("input", { type: "text", value: defaultDesc, placeholder: "Describe the values, e.g. UK oral antibiotic with a typical adult dose" });
    const n = h("input", { type: "number", value: 100, min: 5, max: 500, style: { width: "90px" } });
    const btn = h("button", {
      onclick: async () => {
        if (!desc.value.trim()) { toast("Describe what to generate", "err"); return; }
        btn.disabled = true; btn.textContent = "Generating…";
        try { const r = await api("POST", "/api/ai/list", { description: desc.value, n: +n.value }); onValues(r.values); toast(`Generated ${r.values.length} values`, "ok"); }
        catch (e) { toast(e.message, "err"); }
        finally { btn.disabled = false; btn.textContent = "Generate with AI"; }
      },
    }, "Generate with AI");
    return h("div", { class: "notice" }, h("div", { class: "label" }, "Describe it and let AI write the list"),
      h("div", { class: "row" }, h("div", { class: "grow" }, desc), n, btn),
      h("div", { class: "hint" }, `Uses ${state.status.ai_model}. Only your description is sent. Review the list before saving.`));
  }

  function renderCfg() {
    const c = t.config;
    cfgBox.innerHTML = "";
    cfgBox.append(h("p", { class: "muted small" }, KIND_INFO[t.kind][1]));
    if (t.kind === "picklist") {
      const toText = () => (c.values || []).map((v, i) => (c.weights && c.weights[i] !== undefined && c.weights[i] !== 1 ? `${v} | ${c.weights[i]}` : v)).join("\n");
      const ta = h("textarea", { rows: 10, value: toText(), oninput: () => parse() });
      const count = h("span", { class: "muted small" });
      const parse = () => {
        const vals = [], weights = [];
        let weighted = false;
        for (const line of ta.value.split("\n")) {
          if (!line.trim()) continue;
          const m = line.match(/^(.*?)\s*\|\s*([\d.]+)\s*$/);
          if (m) { vals.push(m[1].trim()); weights.push(+m[2]); weighted = true; } else { vals.push(line.trim()); weights.push(1); }
        }
        c.values = vals;
        if (weighted) c.weights = weights; else delete c.weights;
        count.textContent = `${vals.length} value${vals.length === 1 ? "" : "s"}`;
      };
      parse();
      cfgBox.append(
        h("label", { class: "field" }, h("span", {}, "Values, one per line"), ta,
          h("small", {}, "Optional weight after a bar, e.g. ", h("code", {}, "Penicillin | 3"), " is 3× as likely as a weight-1 value. "), count),
        aiListBox((vals) => { ta.value = (ta.value.trim() ? ta.value.trim() + "\n" : "") + vals.join("\n"); parse(); }, t.description || t.label));
    } else if (t.kind === "pattern") {
      cfgBox.append(h("label", { class: "field" }, h("span", {}, "Pattern"),
        h("input", { type: "text", value: c.pattern || "", placeholder: "MRN-####-??", oninput: (e) => (c.pattern = e.target.value) }),
        h("small", {}, h("code", {}, "#"), " digit · ", h("code", {}, "?"), " letter A–Z · ", h("code", {}, "*"), " letter or digit · ",
          h("code", {}, "[ABC]"), " one of A, B, C · ", h("code", {}, "\\#"), " a literal #")));
    } else if (t.kind === "number_range") {
      cfgBox.append(h("div", { class: "grid3" },
        numIn("Minimum", "min", 0), numIn("Maximum", "max", 100), numIn("Decimal places", "decimals", 0),
        txtIn("Prefix", "prefix"), txtIn("Suffix", "suffix", "e.g. mg")));
    } else if (t.kind === "date_range") {
      c.mode ||= "relative_today";
      const modeSel = h("select", { onchange: (e) => { c.mode = e.target.value; renderCfg(); } },
        [["relative_today", "Relative to today"], ["absolute", "Between two dates"], ["relative_to", "Relative to another field / type"]]
          .map(([v, l]) => h("option", { value: v, selected: c.mode === v }, l)));
      cfgBox.append(h("label", { class: "field" }, h("span", {}, "Range"), modeSel));
      if (c.mode === "absolute") {
        cfgBox.append(h("div", { class: "grid2" },
          h("label", { class: "field" }, h("span", {}, "From"), h("input", { type: "date", value: c.start || "", oninput: (e) => (c.start = e.target.value) })),
          h("label", { class: "field" }, h("span", {}, "To"), h("input", { type: "date", value: c.end || "", oninput: (e) => (c.end = e.target.value) }))));
      } else {
        if (c.mode === "relative_to") {
          cfgBox.append(h("label", { class: "field" }, h("span", {}, "Base: a field name or type key"),
            h("input", { type: "text", value: c.base || "", list: "dl-types", placeholder: "e.g. date_of_birth or referral_date", oninput: (e) => (c.base = e.target.value.trim()) }),
            h("datalist", { id: "dl-types" }, state.types.filter((x) => x.value_kind === "date").map((x) => h("option", { value: x.key }, x.label))),
            h("small", {}, "If a field with this name exists in the run its value is used; otherwise the type is generated.")));
        }
        cfgBox.append(h("div", { class: "grid2" }, numIn("From (days)", "min_days", -30), numIn("To (days)", "max_days", 0)),
          h("p", { class: "hint" }, "Negative = before, positive = after. E.g. −90 to −1 is ‘in the last 3 months’."));
      }
    } else if (t.kind === "composite") {
      const inp = h("input", { type: "text", value: c.template || "", placeholder: "{title} {first_name} {last_name}", oninput: (e) => (c.template = e.target.value) });
      cfgBox.append(h("label", { class: "field" }, h("span", {}, "Template"), inp,
        h("small", {}, "Use ", h("code", {}, "{key}"), " for a type or field name. Dates accept a format: ", h("code", {}, "{date_of_birth:%d %b %Y}"))),
        h("div", { class: "small" }, h("div", { class: "label" }, "Insert"),
          h("div", { class: "row", style: { gap: "4px" } }, state.types.filter((x) => x.builtin).slice(0, 40).map((x) =>
            h("button", { class: "sm", type: "button", onclick: () => { inp.value += `{${x.key}}`; c.template = inp.value; inp.focus(); } }, x.key)))));
    } else if (t.kind === "boolean") {
      cfgBox.append(numIn("Percentage of records that are ‘yes’", "true_pct", 50));
    } else if (t.kind === "ai") {
      c.mode ||= "live";
      cfgBox.append(
        h("label", { class: "field" }, h("span", {}, "Instructions for the AI"),
          h("textarea", { rows: 4, value: c.prompt || "", placeholder: "e.g. A 1–2 sentence reason for referral to a homecare service, written by a clinician", oninput: (e) => (c.prompt = e.target.value) })),
        h("div", { class: "grid2" },
          h("label", { class: "field" }, h("span", {}, "Mode"),
            h("select", { onchange: (e) => { c.mode = e.target.value; renderCfg(); } },
              h("option", { value: "live", selected: c.mode === "live" }, "Live: written per record"),
              h("option", { value: "pool", selected: c.mode === "pool" }, "Pool: generate once, reuse"))),
          numIn("Max words (optional)", "max_words", "")),
        c.mode === "live"
          ? h("div", { class: "notice" }, "Each record gets fresh text that fits the person (age, sex, conditions, medication…). All of a record's AI fields are written in one call per set. Runs with the same seed will still differ.")
          : h("div", { class: "stack" },
            h("div", { class: "notice" }, "A pool is generated once and sampled during runs: faster, free, and repeatable with a seed, but not tailored to each record."),
            (() => {
              const ta = h("textarea", { rows: 8, value: (c.pool || []).join("\n"), oninput: (e) => (c.pool = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean)) });
              return h("label", { class: "field" }, h("span", {}, `Pool (${(c.pool || []).length} values, one per line)`), ta);
            })(),
            aiListBox((vals) => { c.pool = vals; renderCfg(); }, c.prompt || "")),
        !aiOk ? h("div", { class: "notice warn" }, "AI is not configured (REQUESTY_API_KEY missing), so live AI types can't be used in runs.") : null);
    }
  }
  const numIn = (label, k, ph) => h("label", { class: "field" }, h("span", {}, label),
    h("input", { type: "number", value: t.config[k] ?? "", placeholder: String(ph), oninput: (e) => { if (e.target.value === "") delete t.config[k]; else t.config[k] = +e.target.value; } }));
  const txtIn = (label, k, ph = "") => h("label", { class: "field" }, h("span", {}, label),
    h("input", { type: "text", value: t.config[k] ?? "", placeholder: ph, oninput: (e) => (t.config[k] = e.target.value) }));

  const testBtn = h("button", {
    onclick: async () => {
      try {
        const r = await api("POST", "/api/types/sample", { kind: t.kind, config: t.config, n: 6 });
        samplesBox.innerHTML = "";
        samplesBox.append(h("div", { class: "label" }, "Examples"), h("div", { class: "samples" }, r.values.map((v) => h("div", {}, v || "(blank)"))));
      } catch (e) { toast(e.message, "err"); }
    },
  }, "Test");

  renderCfg();
  const close = modal(opts.isNew && existing ? `New data type: “${t.label}”` : isNew ? "New data type" : `Edit “${t.label}”`,
    h("div", { class: "stack" },
      h("div", { class: "grid2" },
        h("label", { class: "field" }, h("span", {}, "Name"), labelInput),
        h("label", { class: "field" }, h("span", {}, "Key"), keyInput, h("small", {}, isNew ? "Used in templates. Can't be changed later." : "Keys can't be changed."))),
      h("div", { class: "grid2" },
        h("label", { class: "field" }, h("span", {}, "Kind"), kindSel),
        h("label", { class: "field" }, h("span", {}, "Category"),
          h("input", { type: "text", value: t.category, list: "dl-cats", oninput: (e) => (t.category = e.target.value) }),
          h("datalist", { id: "dl-cats" }, cats.map((c) => h("option", { value: c }))))),
      h("label", { class: "field" }, h("span", {}, "Description (optional)"),
        h("input", { type: "text", value: t.description, oninput: (e) => (t.description = e.target.value) })),
      h("hr", { style: { border: "none", borderTop: "1px solid var(--border)", margin: "16px 0" } }),
      cfgBox, samplesBox),
    [testBtn, h("span", { class: "grow" }), h("button", { onclick: () => close() }, "Cancel"),
      h("button", {
        class: "primary", onclick: async () => {
          try {
            const body = { key: t.key, label: t.label, kind: t.kind, category: t.category, description: t.description, config: t.config };
            const saved = isNew ? await api("POST", "/api/types", body) : await api("PUT", `/api/types/${t.key}`, body);
            toast(`Saved “${t.label || t.key}”`, "ok");
            close();
            if (opts.onSaved) await opts.onSaved(saved); else reload();
          } catch (e) { toast(e.message, "err"); }
        },
      }, "Save type")]);
}

// ================================================================== GENERATE
async function viewGenerate() {
  await Promise.all([loadTypes(), loadTemplates()]);
  const view = $("#view");
  const d = state.status.defaults;
  const s = { ...d, ...store.get("dc.generate", {}) };
  s.template_ids = (s.template_ids || []).filter((id) => state.templates.some((t) => t.id === id));
  s.fonts = (s.fonts || []).filter((f) => state.status.handwriting_fonts.includes(f));
  if (!s.fonts.length) s.fonts = [...state.status.handwriting_fonts];
  const persist = () => store.set("dc.generate", { ...s, seed: s.seed });

  if (!state.templates.length) {
    view.append(h("div", { class: "card empty" }, "No templates yet. ", h("a", { href: "#/templates" }, "Upload one first.")));
    return;
  }

  const body = h("div");
  const render = () => { body.innerHTML = ""; body.append(...sections()); persist(); };

  function section(num, title, ...content) {
    return h("div", { class: "card" }, h("div", { class: "section-title" }, h("span", { class: "num" }, num), h("h2", { style: { margin: 0 } }, title)), ...content);
  }

  function selectedTemplates() { return s.template_ids.map((id) => state.templates.find((t) => t.id === id)).filter(Boolean); }

  function linkInfo() {
    const tpls = selectedTemplates();
    if (tpls.length < 2) return null;
    const byName = new Map();
    for (const t of tpls) for (const f of t.fields) {
      if (!byName.has(f.name)) byName.set(f.name, []);
      byName.get(f.name).push({ t: t.name, type: f.type, person: f.person || 1 });
    }
    const shared = [...byName].filter(([, v]) => new Set(v.map((x) => x.t)).size > 1);
    const conflicts = shared.filter(([, v]) => new Set(v.map((x) => `${x.type}/${x.person}`)).size > 1);
    return h("div", { class: "stack", style: { marginTop: "12px" } },
      h("div", { class: "notice" }, h("strong", {}, "Linked run. "),
        `Each set produces ${tpls.length} documents (one per template) for the same synthetic record. `,
        shared.length ? `${shared.length} field name(s) appear in more than one template and will share values: ` : "No field names are shared between these templates yet. Give fields the same name to share a value. ",
        shared.map(([n]) => h("code", { style: { marginRight: "4px" } }, n))),
      h("p", { class: "muted small" }, "Person-based types (names, DOB, address, NHS number…) agree across all documents in a set even when the field names differ."),
      conflicts.length ? h("div", { class: "notice warn" }, "Same name, different type: ",
        conflicts.map(([n, v]) => `${n} (${v.map((x) => `${x.t}: ${x.type}`).join(" vs ")})`).join("; "), ". The first template's definition is used.") : null);
  }

  function aiInfo() {
    const tpls = selectedTemplates();
    const names = new Set();
    for (const t of tpls) for (const f of t.fields) {
      const ty = state.typeMap[f.type];
      if (ty && ty.kind === "ai" && ty.config.mode !== "pool") names.add(f.name);
    }
    if (!names.size) return null;
    if (!state.status.ai_available) return h("div", { class: "notice err" }, `These templates use live AI fields (${[...names].join(", ")}) but AI isn't configured.`);
    return h("div", { class: "notice" }, `Live AI fields: ${[...names].join(", ")}. This needs ${s.count} AI call${s.count == 1 ? "" : "s"} (one per set) to ${state.status.ai_model}.`);
  }

  const segs = (key, options, after) => h("div", { class: "seg" }, options.map(([v, l]) =>
    h("button", { type: "button", class: s[key] === v ? "on" : "", onclick: () => { s[key] = v; render(); after && after(); } }, l)));

  function sections() {
    const tpls = selectedTemplates();
    const docs = s.count * Math.max(1, tpls.length);
    return [
      section(1, "Templates",
        h("p", { class: "muted" }, "Pick one template, or several to produce linked sets that share the same synthetic record."),
        h("div", { class: "choice-list" }, state.templates.map((t) => {
          const on = s.template_ids.includes(t.id);
          const bad = !t.fields.length || t.fields.some((f) => !state.typeMap[f.type]);
          return h("label", { class: `choice${on ? " on" : ""}` },
            h("input", {
              type: "checkbox", checked: on, onchange: (e) => {
                s.template_ids = e.target.checked ? [...s.template_ids, t.id] : s.template_ids.filter((x) => x !== t.id);
                render();
              },
            }),
            h("div", {}, h("div", { style: { fontWeight: 600 } }, t.name),
              h("div", { class: "muted small" }, `${t.page_sizes.length} page(s) · ${t.fields.length} fields`),
              bad ? h("span", { class: "pill err" }, t.fields.length ? "has fields without a type" : "no fields") : null));
        })),
        linkInfo()),
      section(2, "How many",
        h("div", { class: "grid3" },
          h("label", { class: "field" }, h("span", {}, tpls.length > 1 ? "Number of sets" : "Number of documents"),
            h("input", { type: "number", min: 1, max: 10000, value: s.count, oninput: (e) => { s.count = Math.max(1, +e.target.value || 1); refreshCounts(); persist(); } })),
          h("div", {}, h("div", { class: "label" }, "Total"), (totalEl = h("div", { class: "big-number" }, `${docs.toLocaleString()} documents`))),
          h("label", { class: "field" }, h("span", {}, "Seed (optional)"),
            h("input", { type: "number", value: s.seed ?? "", placeholder: "random", oninput: (e) => { s.seed = e.target.value === "" ? null : +e.target.value; persist(); } }),
            h("small", {}, "Same seed + settings = same data (except live AI text)."))),
        (aiEl = h("div", {}, aiInfo()))),
      section(3, "Data",
        h("div", { class: "grid3" },
          rangeField("Missing fields", "missing_pct", "% of non-required fields left blank, per document"),
          h("label", { class: "field" }, h("span", {}, "Patient age from"),
            h("input", { type: "number", min: 0, max: 105, value: s.age_min, oninput: (e) => { s.age_min = +e.target.value; persist(); } })),
          h("label", { class: "field" }, h("span", {}, "to"),
            h("input", { type: "number", min: 0, max: 105, value: s.age_max, oninput: (e) => { s.age_max = +e.target.value; persist(); } })))),
      section(4, "Handwriting",
        h("div", { class: "grid3" },
          rangeField("Handwritten", "handwritten_pct", s.handwriting_mode === "document" ? "% of documents filled in by hand (the rest typed)" : "% of fields written by hand"),
          h("div", {}, h("div", { class: "label" }, "Applies to"), segs("handwriting_mode", [["document", "Whole documents"], ["field", "Individual fields"]])),
          h("div", {}, h("div", { class: "label" }, "Ink"), segs("ink", [["mixed", "Mixed"], ["blue", "Blue"], ["black", "Black"]]))),
        h("div", { style: { marginTop: "14px" } },
          h("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "6px" } },
            h("div", { class: "label", style: { margin: 0 } }, `Handwriting styles (${s.fonts.length} of ${state.status.handwriting_fonts.length})`),
            h("div", { class: "row", style: { gap: "4px" } },
              h("button", { class: "sm ghost", onclick: () => { s.fonts = [...state.status.handwriting_fonts]; render(); } }, "All"),
              h("button", { class: "sm ghost", onclick: () => { s.fonts = []; render(); } }, "None"))),
          h("div", { class: "font-grid" }, state.status.handwriting_fonts.map((f) => {
            const on = s.fonts.includes(f);
            return h("label", { class: `font-opt${on ? " on" : ""}` },
              h("img", { src: `/api/fonts/${f}.png`, alt: f, loading: "lazy" }),
              h("span", { class: "fname" }, h("input", { type: "checkbox", checked: on, onchange: (e) => { s.fonts = e.target.checked ? [...s.fonts, f] : s.fonts.filter((x) => x !== f); render(); } }), f));
          })),
          h("p", { class: "hint" }, "Each document gets one ‘writer’: a single handwriting style, pen colour and slant, with natural variation per letter. Signatures are always handwritten.")),
        h("label", { class: "field", style: { marginTop: "12px", maxWidth: "260px" } }, h("span", {}, "Typed font"),
          h("select", { onchange: (e) => { s.typed_font = e.target.value; persist(); } },
            Object.entries(state.status.typed_fonts).map(([k, v]) => h("option", { value: k, selected: s.typed_font === k }, v))))),
      section(5, "Output",
        h("div", { class: "grid2" },
          h("div", {}, h("div", { class: "label" }, "Scan effect"),
            segs("scan", [["none", "None"], ["light", "Light"], ["medium", "Medium"], ["heavy", "Heavy"], ["fax", "Fax"]]),
            h("p", { class: "hint" }, "Simulates scanning: skew, blur, noise, speckles. Scanned output is image-only, like a real scan.")),
          h("div", {}, h("div", { class: "label" }, "Colour"),
            segs("colour", [["colour", "Colour"], ["greyscale", "Greyscale"], ["bw", "Black & white"]]))),
        h("div", { class: "grid3", style: { marginTop: "14px" } },
          h("div", {}, h("div", { class: "label" }, "File formats"),
            h("div", { class: "row" }, [["pdf", "PDF"], ["png", "PNG (per page)"], ["tiff", "TIFF (multi-page)"]].map(([v, l]) =>
              h("label", { class: "check" }, h("input", { type: "checkbox", checked: s.formats.includes(v), onchange: (e) => { s.formats = e.target.checked ? [...new Set([...s.formats, v])] : s.formats.filter((x) => x !== v); persist(); } }), l)))),
          h("label", { class: "field" }, h("span", {}, "Image resolution (DPI)"),
            h("select", { onchange: (e) => { s.dpi = +e.target.value; persist(); } }, [100, 150, 200, 300].map((v) => h("option", { value: v, selected: s.dpi === v }, `${v} dpi`))),
            h("small", {}, "For PNG/TIFF and scanned PDFs.")),
          h("div", {}, h("div", { class: "label" }, "Layout"),
            segs("layout", [["folders", "Folder per set"], ["flat", "Flat"], ["merged", "Merged PDF per set"]])))),
      h("div", { class: "sticky-foot" },
        h("span", { class: "muted small" }, "Ground truth (JSON + CSV) and a records.csv are written with every run."),
        h("button", { onclick: previewFirst, disabled: !tpls.length }, "Preview one"),
        (genBtn = h("button", { class: "primary", onclick: start, disabled: !tpls.length }, `Generate ${docs.toLocaleString()} documents`))),
    ];
  }
  let totalEl, aiEl, genBtn;
  function refreshCounts() {
    const docs = (s.count * Math.max(1, selectedTemplates().length)).toLocaleString();
    totalEl.textContent = `${docs} documents`;
    genBtn.textContent = `Generate ${docs} documents`;
    aiEl.replaceChildren(...[aiInfo()].filter(Boolean));
  }

  function rangeField(label, key, hint) {
    const out = h("strong", {}, `${s[key]}%`);
    return h("label", { class: "field" }, h("span", {}, label, " ", out),
      h("input", { type: "range", min: 0, max: 100, step: 1, value: s[key], oninput: (e) => { s[key] = +e.target.value; out.textContent = `${s[key]}%`; persist(); } }),
      h("small", {}, hint));
  }

  async function previewFirst() {
    const t = selectedTemplates()[0];
    const win = window.open("", "_blank");
    try {
      const blob = await api("POST", `/api/templates/${t.id}/preview`, { ...s, seed: s.seed ?? Math.floor(Math.random() * 1e9) });
      const url = URL.createObjectURL(blob);
      if (win) win.location = url; else window.open(url, "_blank");
    } catch (e) { if (win) win.close(); toast(e.message, "err"); }
  }

  async function start() {
    try {
      await api("POST", "/api/runs", s);
      toast("Run started", "ok");
      location.hash = "#/runs";
    } catch (e) { toast(e.message, "err"); }
  }

  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Generate"), h("p", { class: "muted" }, "Produce filled-in synthetic documents from your templates."))), body);
  render();
}

// ================================================================== RUNS
async function viewRuns() {
  const view = $("#view");
  const list = h("div");
  const expanded = new Set(store.get("dc.expanded", []));
  let timer = null;

  async function refresh() {
    let runs;
    try { runs = await api("GET", "/api/runs"); } catch (e) { return; }
    list.innerHTML = "";
    if (!runs.length) {
      list.append(h("div", { class: "card empty" }, "No runs yet. ", h("a", { href: "#/generate" }, "Generate some documents.")));
    } else {
      list.append(h("div", { class: "card", style: { padding: 0 } }, h("table", { class: "list" },
        h("thead", {}, h("tr", {}, h("th", {}, "Run"), h("th", {}, "Templates"), h("th", {}, "Documents"), h("th", {}, "Status"), h("th", {}))),
        h("tbody", {}, runs.map(runRow)))));
    }
    const active = runs.some((r) => ["queued", "running", "cancelling"].includes(r.status));
    clearTimeout(timer);
    timer = setTimeout(refresh, active ? 1000 : 5000);
  }

  function runRow(r) {
    const s = r.settings, sum = r.summary || {};
    const names = sum.templates || s.template_ids.map((id) => state.templates.find((t) => t.id === id)?.name || `#${id}`);
    const pct = r.total ? Math.round((100 * r.progress) / r.total) : 0;
    const statusCls = { done: "ok", failed: "err", cancelled: "warn" }[r.status] || "accent";
    const isOpen = expanded.has(r.id);
    const toggle = () => { isOpen ? expanded.delete(r.id) : expanded.add(r.id); store.set("dc.expanded", [...expanded]); refresh(); };
    const issues = (sum.warnings?.length || 0) + (sum.ai_errors?.length || 0);
    return h("tr", {},
      h("td", {}, h("div", { style: { fontWeight: 600 } }, fmtDate(r.created_at)), h("div", { class: "muted small mono" }, r.id),
        h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); toggle(); } }, isOpen ? "Hide details" : "Details"),
        isOpen ? h("div", { class: "run-detail" },
          h("div", {}, `Seed ${s.seed} · missing ${s.missing_pct}% · handwritten ${s.handwritten_pct}% (${s.handwriting_mode}) · scan ${s.scan} · ${s.colour} · ${s.formats.join("+")} @ ${s.dpi}dpi · ${s.layout}`),
          sum.output_dir ? h("div", {}, "Folder: ", h("code", {}, sum.output_dir)) : null,
          sum.linked_fields?.length ? h("div", {}, "Linked fields: ", sum.linked_fields.join(", ")) : null,
          sum.seconds !== undefined ? h("div", {}, `Took ${sum.seconds}s${sum.ai_calls ? ` · ${sum.ai_calls} AI calls` : ""}`) : null,
          sum.warnings?.length ? h("div", {}, h("strong", {}, "Warnings"), h("ul", {}, sum.warnings.map((w) => h("li", {}, w)))) : null,
          sum.ai_errors?.length ? h("div", {}, h("strong", {}, "AI errors (fields left blank and marked in ground truth)"), h("ul", {}, sum.ai_errors.map((w) => h("li", {}, w)))) : null) : null),
      h("td", {}, names.join(" + ")),
      h("td", {}, `${(s.count * names.length).toLocaleString()}`, h("div", { class: "muted small" }, names.length > 1 ? `${s.count} sets × ${names.length}` : "")),
      h("td", {}, h("span", { class: `pill ${statusCls}` }, r.status), issues && r.status === "done" ? h("span", { class: "pill warn", style: { marginLeft: "4px" } }, `${issues} issue${issues > 1 ? "s" : ""}`) : null,
        ["queued", "running", "cancelling"].includes(r.status)
          ? h("div", {}, h("div", { class: "progress" }, h("div", { style: { width: `${pct}%` } })), h("div", { class: "muted small" }, r.message))
          : r.status !== "done" ? h("div", { class: "muted small" }, r.message) : null),
      h("td", { class: "actions" },
        r.status === "done" ? [h("a", { class: "btn sm primary", href: `/api/runs/${r.id}/download` }, "Download .zip"), " "] : null,
        ["queued", "running"].includes(r.status)
          ? h("button", { class: "sm", onclick: async () => { await api("POST", `/api/runs/${r.id}/cancel`); refresh(); } }, "Cancel")
          : h("button", {
            class: "sm danger", onclick: async () => {
              if (!(await confirmDialog("Delete this run and its files?"))) return;
              try { await api("DELETE", `/api/runs/${r.id}`); refresh(); } catch (e) { toast(e.message, "err"); }
            },
          }, "Delete")));
  }

  await loadTemplates();
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Runs"), h("p", { class: "muted" }, "Generated batches. Each download includes the documents, ground_truth.json/.csv and records.csv.")),
    h("a", { class: "btn primary", href: "#/generate" }, "New run")), list);
  await refresh();
  currentCleanup = () => clearTimeout(timer);
}

// ------------------------------------------------------------------ boot
async function boot() {
  try {
    state.status = await api("GET", "/api/status");
  } catch (e) {
    $("#view").append(h("div", { class: "notice err" }, `Can't reach the DataCreator server: ${e.message}`));
    return;
  }
  $("#ai-status").append(state.status.ai_available
    ? h("span", { class: "pill ok", title: state.status.ai_model }, "AI connected")
    : h("span", { class: "pill warn", title: "Set REQUESTY_API_KEY in .env" }, "AI off"));
  await loadTypes();
  route();
}
boot();
