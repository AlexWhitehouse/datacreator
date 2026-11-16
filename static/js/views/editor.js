// Template editor: draw / auto-detect fields on the page, review suggestions, configure each field.
import { api, busy, clone, h, icon, modalOpen, notice, plural, previewModal, store, toast, uid } from "../ui.js";
import { KIND_INFO, PERSONS, RENDER_INFO, generateFrom, loadTypes, nav, state, typeLabel, typeSelect } from "../state.js";
import { typeEditor } from "./types.js";

export async function viewEditor(view, id) {
  const [, tpl, others] = await Promise.all([loadTypes(), api("GET", `/api/templates/${id}`), api("GET", "/api/templates")]);
  const otherNames = new Map(); // field name -> {templates, type, person} in other templates (for linking hints)
  for (const t of others) if (t.id !== id) for (const f of t.fields) {
    if (!otherNames.has(f.name)) otherNames.set(f.name, { templates: new Set(), type: f.type, person: f.person });
    otherNames.get(f.name).templates.add(t.name);
  }

  const nPages = tpl.page_sizes.length;
  let fields = clone(tpl.fields);
  let name = tpl.name;
  let saved = JSON.stringify({ name, fields });
  let page = 0;
  let zoom = store.get("dc.zoom", 1.3);
  let selId = null;
  let sideTab = "field";
  let listFilter = "";
  let drafts = [];   // AI-suggested new data types awaiting review
  let missed = [];   // questions the AI saw with no box to fill
  let detectNote = "";
  let detecting = false;
  let reviewTotal = 0; // suggestions in the current review, for the progress meter
  const fresh = new Set(); // ids of boxes to animate in on next render
  const groupsOpen = store.get("dc.fgroups", { look: true, format: false, blanks: false });
  const isDirty = () => JSON.stringify({ name, fields }) !== saved;
  const isBad = (f) => !state.typeMap[f.type] || !f.name;
  nav.guard = { dirty: isDirty, save: () => save() };

  // ------------------------------------------------------------ layout
  const pageWrap = h("div", { class: "page-wrap" });
  const canvasScroll = h("div", { class: "canvas-scroll" }, pageWrap);
  const sideBody = h("div", { class: "side-body" });
  const sideTabs = h("div", { class: "side-tabs" });
  const pageLabel = h("span", { class: "lbl" });
  const zoomLabel = h("span", { class: "lbl", style: { minWidth: "48px" } });
  const saveState = h("span", { class: "save-state" }, h("span", { class: "dot" }), h("span", { class: "t" }, "Saved"));
  const saveBtn = h("button", { class: "sm primary quiet", onclick: (e) => busy(e.currentTarget, save), title: "Save (Ctrl+S)" }, icon("save", "sm"), "Save");
  const detectBtn = h("button", { class: "sm", onclick: () => autoDetect(), title: "Find input boxes and suggest a field for each" }, icon("sparkles", "sm"), "Auto-detect");
  const genBtn = h("button", { class: "sm", onclick: () => goGenerate(), title: "Generate documents from this template" }, icon("zap", "sm"), "Generate");
  const nameInput = h("input", { type: "text", class: "name-input", value: name, "aria-label": "Template name", oninput: (e) => { name = e.target.value; refreshDirty(); } });

  const toolbar = h("div", { class: "editor-toolbar" },
    h("a", { class: "btn sm ghost icon", href: "#/templates", title: "Back to templates" }, icon("arrowL")),
    nameInput,
    h("span", { class: "grow" }),
    nPages > 1 ? h("div", { class: "tb-group" },
      h("button", { class: "xs icon", title: "Previous page (PgUp)", onclick: () => setPage(page - 1) }, icon("chevL", "sm")),
      pageLabel,
      h("button", { class: "xs icon", title: "Next page (PgDn)", onclick: () => setPage(page + 1) }, icon("chevR", "sm"))) : null,
    h("div", { class: "tb-group" },
      h("button", { class: "xs icon", title: "Zoom out (Ctrl + scroll)", onclick: () => setZoom(zoom / 1.15) }, icon("zoomOut", "sm")),
      zoomLabel,
      h("button", { class: "xs icon", title: "Zoom in (Ctrl + scroll)", onclick: () => setZoom(zoom * 1.15) }, icon("zoomIn", "sm")),
      h("button", { class: "xs icon", title: "Fit to width", onclick: () => fitWidth() }, icon("fit", "sm"))),
    h("span", { class: "grow" }),
    detectBtn,
    h("button", { class: "sm", onclick: () => preview(), title: "See a filled-in sample" }, icon("eye", "sm"), "Preview"),
    genBtn,
    saveState,
    saveBtn);

  view.append(h("div", { class: "editor" },
    h("div", { class: "editor-main" }, toolbar, canvasScroll),
    h("div", { class: "sidebar" }, sideTabs, sideBody)));

  function refreshDirty() {
    const d = isDirty();
    saveState.classList.toggle("dirty", d);
    saveState.querySelector(".t").textContent = d ? "Unsaved changes" : "All changes saved";
    saveBtn.disabled = !d;
    const ready = fields.length && !fields.some((f) => isBad(f) || f.proposed);
    genBtn.disabled = !ready;
    genBtn.title = ready ? "Generate documents from this template" : "Every field needs a name and type first";
  }

  function setZoom(z) {
    zoom = Math.max(0.4, Math.min(4, z));
    store.set("dc.zoom", zoom);
    renderPage();
  }
  function fitWidth() { setZoom((canvasScroll.clientWidth - 56) / tpl.page_sizes[page][0]); }
  function setPage(p) {
    if (p < 0 || p >= nPages || p === page) return;
    page = p;
    renderPage();
    pageWrap.classList.remove("turning");
    void pageWrap.offsetWidth;
    pageWrap.classList.add("turning");
    canvasScroll.scrollTop = 0;
  }
  canvasScroll.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    setZoom(zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08));
  }, { passive: false });

  // ------------------------------------------------------------ page + boxes
  function renderPage() {
    const [w, hgt] = tpl.page_sizes[page];
    pageWrap.style.width = `${w * zoom}px`;
    pageWrap.style.height = `${hgt * zoom}px`;
    const overlay = pageWrap.querySelector(".scan-overlay");
    pageWrap.innerHTML = "";
    pageWrap.append(h("img", { src: `/api/templates/${id}/pages/${page}.png?dpi=${zoom > 2 ? 216 : 144}`, alt: "", draggable: false }));
    for (const f of fields.filter((f) => f.page === page)) pageWrap.append(boxEl(f));
    if (overlay) pageWrap.append(overlay);
    fresh.clear();
    pageLabel.textContent = `${page + 1} / ${nPages}`;
    zoomLabel.textContent = `${Math.round(zoom * 75)}%`; // 100% = printed size on a 96 dpi screen
  }

  function boxEl(f) {
    const [x0, y0, x1, y1] = f.rect;
    const cls = ["fbox", f.id === selId && "sel", isBad(f) && "bad", f.proposed && "proposed", fresh.has(f.id) && "new"].filter(Boolean).join(" ");
    return h("div", {
      class: cls, "data-render": f.render, "data-id": f.id,
      style: { left: `${x0 * zoom}px`, top: `${y0 * zoom}px`, width: `${(x1 - x0) * zoom}px`, height: `${(y1 - y0) * zoom}px` },
      title: `${f.name || "(unnamed)"} · ${typeLabel(f.type)}`,
    },
      h("div", { class: "tag" }, `${f.proposed ? "suggested · " : ""}${f.name || "unnamed"}${f.person > 1 ? ` · P${f.person}` : ""}`),
      h("div", { class: "handle" }));
  }
  const boxNode = (fid) => pageWrap.querySelector(`[data-id="${fid}"]`);

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
    if (e.button !== 0 || detecting) return;
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
        Object.assign(boxNode(f.id).style, { left: `${f.rect[0] * zoom}px`, top: `${f.rect[1] * zoom}px`, width: `${(f.rect[2] - f.rect[0]) * zoom}px`, height: `${(f.rect[3] - f.rect[1]) * zoom}px` });
      };
      const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); refreshDirty(); updateCoords(f); };
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
      fresh.add(f.id);
      selId = f.id;
      sideTab = "field";
      renderPage(); renderSide(); refreshDirty();
      try {
        const s = await api("POST", `/api/templates/${id}/suggest`, { page, rect });
        applySuggestion(f, s);
      } catch (err) { toast(`Couldn't read the label: ${err.message}`, "err"); }
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
    renderPage(); renderSide(); refreshDirty();
  }

  /** Select a field; reveal: switch page and scroll it into view. */
  function select(fid, { reveal = false } = {}) {
    selId = fid;
    if (fid) sideTab = "field";
    const f = fields.find((x) => x.id === fid);
    if (f && reveal && f.page !== page) setPage(f.page);
    pageWrap.querySelectorAll(".fbox").forEach((el) => el.classList.toggle("sel", el.dataset.id === fid));
    renderSide();
    if (f && reveal) {
      const el = boxNode(fid);
      if (el) {
        el.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
        el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
      }
    }
  }

  function removeField(fid) {
    const idx = fields.findIndex((x) => x.id === fid);
    if (idx < 0) return;
    const [gone] = fields.splice(idx, 1);
    if (selId === fid) selId = null;
    renderPage(); renderSide(); refreshDirty();
    toast(`Deleted “${gone.name || "unnamed"}”`, "", {
      action: { label: "Undo", fn: () => { fields.splice(idx, 0, gone); fresh.add(gone.id); renderPage(); select(gone.id, { reveal: true }); refreshDirty(); } },
    });
  }

  function duplicateField(f) {
    const hgt = f.rect[3] - f.rect[1];
    const [, ph] = tpl.page_sizes[f.page];
    const dy = f.rect[3] + hgt + 4 <= ph ? hgt + 4 : 0;
    const n = { ...clone(f), id: uid(), name: uniqueName(f.name.replace(/_\d+$/, ""), null, {}), rect: [f.rect[0], f.rect[1] + dy, f.rect[2], f.rect[3] + dy] };
    delete n.proposed;
    fields.push(n);
    fresh.add(n.id);
    renderPage(); select(n.id); refreshDirty();
  }

  // ------------------------------------------------------------ keyboard
  const onKey = (e) => {
    if (modalOpen() || e.target.matches("input, textarea, select")) return;
    const f = fields.find((x) => x.id === selId);
    const mod = e.ctrlKey || e.metaKey;
    const free = e.target === document.body || canvasScroll.contains(e.target); // Tab/Enter keep their usual meaning on buttons
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); busy(saveBtn, save); }
    else if ((e.key === "Delete" || e.key === "Backspace") && f) { e.preventDefault(); removeField(f.id); }
    else if (e.key === "Escape") select(null);
    else if (mod && e.key.toLowerCase() === "d" && f) { e.preventDefault(); duplicateField(f); }
    else if (f?.proposed && !mod && (e.key === "a" || (e.key === "Enter" && free))) { e.preventDefault(); acceptProposals([f]); }
    else if (f?.proposed && !mod && (e.key === "x" || e.key === "r")) { e.preventDefault(); rejectProposals([f]); }
    else if (e.key === "Tab" && free && !mod && fields.length) {
      e.preventDefault();
      const order = sortedFields();
      const i = order.findIndex((x) => x.id === selId);
      const next = order[(i + (e.shiftKey ? -1 : 1) + order.length) % order.length];
      select(next.id, { reveal: true });
    }
    else if (f && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      f.rect = clampRect([f.rect[0] + d[0], f.rect[1] + d[1], f.rect[2] + d[0], f.rect[3] + d[1]]);
      renderPage(); refreshDirty(); updateCoords(f);
    } else if (e.key === "PageDown") setPage(page + 1);
    else if (e.key === "PageUp") setPage(page - 1);
  };
  document.addEventListener("keydown", onKey);
  nav.cleanup = () => { document.removeEventListener("keydown", onKey); clearInterval(detectTimer); };

  const sortedFields = (list = fields) => [...list].sort((a, b) => a.page - b.page || a.rect[1] - b.rect[1] || a.rect[0] - b.rect[0]);

  // ------------------------------------------------------------ sidebar
  function renderSide() {
    const bad = fields.filter(isBad).length;
    sideTabs.replaceChildren(
      h("button", { class: sideTab === "field" ? "active" : "", onclick: () => { sideTab = "field"; renderSide(); } }, selId ? "Field" : "Overview"),
      h("button", { class: sideTab === "all" ? "active" : "", onclick: () => { sideTab = "all"; renderSide(); } },
        "All fields ", h("span", { class: "count" }, fields.length),
        bad ? h("span", { class: "pill err", style: { height: "18px", marginLeft: "4px" } }, bad) : null));
    sideBody.replaceChildren();
    const panel = suggestionsPanel();
    if (panel) sideBody.append(panel);
    if (sideTab === "all") return renderFieldList();
    const f = fields.find((x) => x.id === selId);
    sideBody.append(f ? fieldForm(f) : overview());
    if (f) updateCoords(f);
  }

  function overview() {
    const bad = fields.filter((f) => isBad(f) && !f.proposed);
    if (!fields.length) {
      return h("div", { class: "stack lg" },
        h("div", {},
          h("h3", { style: { fontSize: "15px" } }, "Mark up this template"),
          h("p", { class: "muted" }, "Tell DataCreator where each piece of data goes. Auto-detect finds the input boxes and suggests a name and data type for each one, so you only review them.")),
        h("button", { class: "primary block lg", onclick: () => autoDetect() }, icon("sparkles"), "Auto-detect fields"),
        h("div", { class: "muted small", style: { textAlign: "center" } }, state.status.ai_available ? "Takes 5–20 seconds. Nothing is saved until you accept." : "AI is off, so suggestions come from printed labels only."),
        h("div", { class: "howto" },
          h("div", { class: "label" }, "Or draw boxes yourself"),
          h("div", { class: "s" }, h("span", { class: "n" }, 1), h("div", { class: "small" }, "Drag on the page where a value should be written.")),
          h("div", { class: "s" }, h("span", { class: "n" }, 2), h("div", { class: "small" }, "The printed label next to it is read to suggest a field name and data type.")),
          h("div", { class: "s" }, h("span", { class: "n" }, 3), h("div", { class: "small" }, "Check the field on the right, then save."))),
        legend());
    }
    return h("div", { class: "stack lg" },
      h("div", { class: "grid2" },
        stat(fields.length, "fields", `${plural(nPages, "page")}`),
        stat(bad.length, "need attention", bad.length ? "missing a name or type" : "all set", bad.length ? "err" : "ok")),
      bad.length ? h("button", { class: "block", onclick: () => select(sortedFields(bad)[0].id, { reveal: true }) }, icon("alert", "sm"), "Go to the next field to fix") : null,
      notice("plain", "Click a box to edit it, or drag on the page to add one. Fields with the ", h("strong", {}, "same name"), " share one value, here and across templates in a linked run."),
      h("details", { class: "fgroup" },
        h("summary", {}, h("span", { class: "row tight" }, icon("keyboard", "sm"), "Shortcuts"), icon("chevD", "sm")),
        h("div", { class: "body" }, h("div", { class: "shortcuts" },
          keys("Tab"), "Next field (Shift: previous)",
          keys("A"), "Accept suggestion",
          keys("X"), "Reject suggestion",
          keys("Del"), "Delete field",
          keys("Ctrl", "D"), "Duplicate field",
          keys("←↑→↓"), "Nudge (Shift ×10)",
          keys("Ctrl", "S"), "Save",
          keys("Ctrl", "scroll"), "Zoom"))),
      legend());
  }
  const keys = (...k) => h("span", {}, k.map((x) => h("span", { class: "kbd" }, x)));
  const stat = (n, label, sub, kind = "") => h("div", { class: "samples", style: { padding: "12px 14px" } },
    h("div", { style: { fontSize: "22px", fontWeight: 700, color: kind === "err" ? "var(--err)" : "var(--text)" } }, n),
    h("div", { class: "small", style: { fontWeight: 600 } }, label), h("div", { class: "tiny muted" }, sub));

  function legend() {
    return h("div", {}, h("div", { class: "label" }, "Box colours"),
      h("div", { class: "legend" }, Object.entries(RENDER_INFO).map(([k, [v]]) =>
        h("span", {}, h("i", { style: { "--c": `var(--f-${k})` } }), v))));
  }

  function renderFieldList() {
    if (!fields.length) { sideBody.append(h("p", { class: "muted" }, "No fields yet. Drag on the page to add one.")); return; }
    const search = h("input", { type: "search", placeholder: "Filter by name or type", value: listFilter });
    const list = h("div");
    const draw = () => {
      list.replaceChildren();
      const q = listFilter.toLowerCase();
      const match = (f) => !q || (f.name || "").toLowerCase().includes(q) || typeLabel(f.type).toLowerCase().includes(q);
      let shown = 0;
      for (let p = 0; p < nPages; p++) {
        const pf = sortedFields(fields.filter((f) => f.page === p && match(f)));
        if (!pf.length) continue;
        shown += pf.length;
        if (nPages > 1) list.append(h("div", { class: "flist-page" }, `Page ${p + 1}`, h("span", {}, pf.length)));
        for (const f of pf) {
          list.append(h("div", {
            class: `flist-item${f.id === selId ? " sel" : ""}`, style: { "--c": `var(--f-${f.render})` },
            onclick: () => select(f.id, { reveal: true }),
          },
            h("span", { class: "dot" }),
            h("span", { class: "nm" }, f.name || h("span", { class: "faint" }, "unnamed")),
            f.proposed ? h("span", { class: "pill accent" }, "suggested") : null,
            isBad(f) ? h("span", { class: "pill err" }, "needs type") : h("span", { class: "ty" }, typeLabel(f.type))));
        }
      }
      if (!shown) list.append(h("p", { class: "muted small", style: { padding: "8px" } }, "No fields match."));
    };
    search.addEventListener("input", () => { listFilter = search.value; draw(); });
    sideBody.append(h("div", { class: "flist-search input-icon" }, icon("search", "sm"), search), list);
    draw();
  }

  function updateCoords(f) {
    const el = sideBody.querySelector(".coords");
    if (!el || selId !== f.id) return;
    const [x0, y0, x1, y1] = f.rect;
    el.textContent = `Page ${f.page + 1} · x ${x0.toFixed(0)}, y ${y0.toFixed(0)} · ${(x1 - x0).toFixed(0)} × ${(y1 - y0).toFixed(0)} pt`;
  }

  function group(key, title, summary, ...content) {
    const d = h("details", { class: "fgroup", open: !!groupsOpen[key], ontoggle: () => { groupsOpen[key] = d.open; store.set("dc.fgroups", groupsOpen); } },
      h("summary", {}, title, summary ? h("span", { class: "sumval" }, summary) : null, icon("chevD", "sm")),
      h("div", { class: "body" }, ...content));
    return d;
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
    const samples = h("div", { class: "samples" }, h("div", { class: "lbl" }, "Example values", h("span", { class: "spin", style: { width: "10px", height: "10px", borderWidth: "1.5px" } })));
    if (t) {
      api("POST", "/api/types/sample", { key: t.key, n: 3 })
        .then((r) => { samples.replaceChildren(h("div", { class: "lbl" }, "Example values"), ...r.values.map((v) => h("div", { class: "v" }, v || h("span", { class: "faint" }, "(blank)")))); })
        .catch((e) => { samples.replaceChildren(h("div", { class: "v muted" }, e.message)); });
    }
    const vk = t ? t.value_kind : "text";
    const dateFormats = state.status.date_formats;

    // --- format options
    const formatOpts = [];
    const fmtBits = [];
    if (f.render !== "checkbox" && f.render !== "signature") {
      if (vk === "date") {
        const custom = opts.date_format && !dateFormats.includes(opts.date_format);
        const pyFmt = (fmt) => fmt.replace("%d", "07").replace("%m", "03").replace("%Y", "1984").replace("%y", "84").replace("%b", "Mar").replace("%B", "March");
        fmtBits.push(pyFmt(opts.date_format || "%d/%m/%Y"));
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
      const transforms = [["", "As generated"], ["upper", "UPPER CASE"], ["lower", "lower case"], ["title", "Title Case"], ["no_spaces", "Remove spaces"], ["single_line", "Join lines with commas"]];
      if (opts.transform) fmtBits.push(transforms.find(([v]) => v === opts.transform)?.[1]);
      if (opts.prefix || opts.suffix) fmtBits.push("affix");
      formatOpts.push(h("label", { class: "field" }, h("span", {}, "Text transform"),
        h("select", { onchange: (e) => set("transform", e.target.value, true) },
          transforms.map(([v, l]) => h("option", { value: v, selected: (opts.transform || "") === v }, l)))));
      formatOpts.push(h("div", { class: "grid2" },
        h("label", { class: "field" }, h("span", {}, "Prefix"), h("input", { type: "text", value: opts.prefix || "", oninput: (e) => set("prefix", e.target.value) })),
        h("label", { class: "field" }, h("span", {}, "Suffix"), h("input", { type: "text", value: opts.suffix || "", oninput: (e) => set("suffix", e.target.value) }))));
    }

    // --- appearance options
    const renderOpts = [];
    if (f.render === "comb") {
      renderOpts.push(h("div", { class: "grid2" },
        h("label", { class: "field" }, h("span", {}, "Number of boxes"),
          h("input", { type: "number", min: 1, max: 60, value: opts.cells || "", placeholder: "auto", oninput: (e) => set("cells", e.target.value ? +e.target.value : "") })),
        h("label", { class: "check", style: { marginTop: "26px" } },
          h("input", { type: "checkbox", checked: opts.comb_strip !== false, onchange: (e) => set("comb_strip", e.target.checked ? "" : false) }), "Skip spaces")));
    }
    if (f.render === "checkbox") {
      renderOpts.push(h("label", { class: "field" }, h("span", {}, "Tick when the value is"),
        h("input", { type: "text", value: opts.check_value || "", placeholder: "Yes / true (default)", oninput: (e) => set("check_value", e.target.value) }),
        h("small", {}, "E.g. type “Sex” with “Female” ticks this box only for female records. Give the Yes/No boxes of one question the same field name.")));
    }
    if (f.render === "text") {
      renderOpts.push(h("div", {}, h("div", { class: "label" }, "Alignment"),
        h("div", { class: "seg sm block" }, [["", "Left"], ["center", "Centre"], ["right", "Right"]].map(([v, l]) =>
          h("button", { class: (opts.align || "") === v ? "on" : "", onclick: () => set("align", v, true) }, l)))));
    }
    if (f.render !== "checkbox") {
      renderOpts.push(h("label", { class: "field" }, h("span", {}, "Font size"),
        h("div", { class: "input-suffix" }, h("input", { type: "number", min: 4, max: 40, step: 0.5, value: opts.font_size || "", placeholder: "Auto-fit", oninput: (e) => set("font_size", e.target.value ? +e.target.value : "") }), h("span", {}, "pt"))));
      renderOpts.push(h("div", {}, h("label", { class: "check" },
        h("input", { type: "checkbox", checked: !!opts.erase, onchange: (e) => set("erase", e.target.checked || "") }),
        "Cover printed text in this box"),
        h("div", { class: "hint" }, "For Word placeholders like “Choose an item.”: hidden when a value is written, kept when the field is left blank.")));
    }

    const pending = sortedFields(fields.filter((x) => x.proposed));
    const pos = pending.findIndex((x) => x.id === f.id) + 1;
    const total = Math.max(reviewTotal, pending.length);
    const doneCount = total - pending.length;

    return h("div", { class: "stack lg" },
      f.proposed ? h("div", { class: "review-bar" },
        h("div", { class: "top" }, h("span", { class: "row tight" }, icon("sparkles", "sm"), `Suggestion ${doneCount + pos} of ${total}`),
          h("button", { class: "xs ghost", onclick: () => acceptProposals(pending) }, "Accept all")),
        h("div", { class: "meter" }, h("div", { style: { width: `${(doneCount / Math.max(1, total)) * 100}%` } })),
        h("div", { class: "btns" },
          h("button", { class: "sm", onclick: () => rejectProposals([f]) }, icon("x", "sm"), "Reject", h("span", { class: "kbd" }, "X")),
          h("button", { class: "sm primary", onclick: () => acceptProposals([f]) }, icon("check", "sm"), "Accept", h("span", { class: "kbd", style: { color: "var(--accent-dark)" } }, "A")))) : null,
      f.draft_type && !state.typeMap[f.type] ? notice("warn", "Uses the suggested new type ", h("code", {}, f.draft_type), ". Create it from the suggestions panel above, or pick an existing type.") : null,
      f.label ? h("div", { class: "muted small row tight" }, icon("type", "sm"), `Printed label: “${f.label}”`) : null,

      // basics
      h("label", { class: "field" }, h("span", {}, "Field name"),
        h("input", {
          type: "text", value: f.name, list: "dl-names", placeholder: "e.g. patient_surname",
          oninput: (e) => { f.name = e.target.value.trim(); refreshDirty(); const el = pageWrap.querySelector(`[data-id="${f.id}"] .tag`); if (el) el.textContent = f.name || "unnamed"; },
          onchange: () => { renderPage(); renderSide(); },
        }), dl,
        linked ? h("small", { class: "row tight" }, h("span", { class: "pill accent" }, icon("link", "sm"), "linked"), `Also in ${[...linked.templates].join(", ")}`) : h("small", {}, "Letters, digits and underscores. Same name = same value.")),
      linked && (linked.type !== f.type || (linked.person || 1) !== f.person)
        ? notice("warn", `In other templates “${f.name}” is ${typeLabel(linked.type)} (person ${linked.person || 1}). Linked runs use one definition, so match them.`) : null,
      h("div", {},
        h("label", { class: "field" }, h("span", {}, "Data type"),
          typeSelect(f.type, (v) => {
            f.type = v;
            const nt = state.typeMap[v];
            if (nt && ["signature", "checkbox"].includes(nt.render) && f.render === "text") f.render = nt.render;
            refreshDirty(); renderPage(); renderSide();
          })),
        h("div", { class: "row between", style: { marginTop: "6px" } },
          h("span", {}),
          h("button", { class: "xs ghost", onclick: () => typeEditor(null, { onSaved: async (st) => { await loadTypes(); f.type = st.key; refreshDirty(); renderPage(); renderSide(); } }) }, icon("plus", "sm"), "New data type"))),
      t ? samples : null,
      h("label", { class: "field" }, h("span", {}, "Whose data"),
        h("select", { onchange: (e) => { f.person = +e.target.value; refreshDirty(); renderPage(); } },
          Object.entries(PERSONS).map(([v, l]) => h("option", { value: v, selected: +v === (f.person || 1) }, l))),
        h("small", {}, "Each record has a patient, a relative (person 2) and unrelated people 3–8 such as clinicians.")),

      group("look", "Appearance", RENDER_INFO[f.render]?.[0],
        h("div", { class: "render-tiles" }, Object.entries(RENDER_INFO).map(([v, [full, short]]) =>
          h("button", { class: `render-tile${v === f.render ? " on" : ""}`, title: full, style: { "--c": `var(--f-${v})` }, onclick: () => { f.render = v; refreshDirty(); renderPage(); renderSide(); } },
            h("span", { class: "sw" }), short))),
        ...renderOpts),
      formatOpts.length ? group("format", "Formatting", fmtBits.filter(Boolean).join(" · ") || "Default", ...formatOpts) : null,
      group("blanks", "Blanks", opts.required ? "Always filled" : opts.missing_pct !== undefined ? `${opts.missing_pct}% blank` : "Run default",
        h("label", { class: "switch" },
          h("input", { type: "checkbox", checked: !!opts.required, onchange: (e) => set("required", e.target.checked || "", true) }),
          h("span", { class: "track" }), h("span", {}, "Never leave blank")),
        h("label", { class: "field" }, h("span", {}, "Missing % for this field"),
          h("div", { class: "input-suffix" }, h("input", { type: "number", min: 0, max: 100, value: opts.missing_pct ?? "", placeholder: "Use the run's setting", disabled: !!opts.required, oninput: (e) => set("missing_pct", e.target.value === "" ? "" : +e.target.value) }), h("span", {}, "%")))),

      h("div", { class: "fgroup" },
        h("div", { class: "coords muted tiny mono", style: { marginBottom: "10px" } }),
        h("div", { class: "row tight" },
          h("button", { class: "sm", onclick: () => duplicateField(f), title: "Ctrl+D" }, icon("copy", "sm"), "Duplicate"),
          h("button", { class: "sm ghost danger", onclick: () => removeField(f.id), title: "Delete" }, icon("trash", "sm"), "Delete"),
          h("span", { class: "grow" }),
          h("button", { class: "sm ghost", onclick: () => select(null) }, "Done"))));
  }

  // ------------------------------------------------------------ auto-detect
  // One POST returns every proposal at once, so the step list below is paced by typical timings
  // (and says so). The overlay is purely visual: pointer-events none, and nothing waits on it.
  let detectTimer = null;
  let detectUi = null; // the last run's overlay + status card, so a re-run can drop its outro at once
  const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const overlapRatio = (a, b) => {
    const ix = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
    const iy = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
    const area = (r) => Math.max(0, r[2] - r[0]) * Math.max(0, r[3] - r[1]);
    const inter = ix * iy;
    return inter ? inter / (area(a) + area(b) - inter) : 0;
  };

  function clearDetectUi() {
    if (!detectUi) return;
    detectUi.timers.forEach(clearTimeout);
    detectUi.overlay.remove();
    detectUi.dock.remove();
    detectUi = null;
  }

  async function autoDetect() {
    if (detecting) return;
    clearDetectUi();
    const had = fields.filter((f) => f.proposed).length; // kept until the new result replaces them
    detecting = true;
    detectBtn.classList.add("busy"); detectBtn.disabled = true;

    const ai = state.status.ai_available;
    const steps = [
      [0, nPages > 1 ? `Finding input boxes on ${nPages} pages` : "Finding input boxes"],
      [1.5, "Reading printed labels"],
      ...(ai ? [[3.5, "AI is naming fields and choosing data types"]] : []),
    ];
    const hints = ai ? [[14, "Dense page: labelling in parallel…"], [30, "Still working, nearly there…"]] : [[10, "Still working…"]];
    const stepEls = steps.map(([, label], i) => h("li", { style: { "--i": i } },
      h("span", { class: "tick" }, icon("check", "sm")), h("span", {}, label)));
    const iconEl = h("span", { class: "ds-icon" }, h("span", { class: "spin" }));
    const titleEl = h("strong", { class: "ds-title" }, "Detecting fields");
    const elapsedEl = h("span", { class: "elapsed", "aria-hidden": "true" }, "0s");
    const hintEl = h("div", { class: "ds-hint", hidden: true });
    const footEl = h("div", { class: "ds-foot" }, "Steps are estimated; the results arrive all at once.");
    const status = h("div", { class: "detect-status", role: "status", "aria-live": "polite" },
      h("div", { class: "ds-head" }, iconEl, titleEl, elapsedEl),
      h("ol", { class: "ds-steps" }, stepEls), hintEl, footEl);
    const dock = h("div", { class: "detect-dock" }, status);
    const overlay = h("div", { class: "scan-overlay" }, h("div", { class: "scan-grid" }), h("div", { class: "beam" }));
    pageWrap.append(overlay); // renderPage() carries it across page changes
    canvasScroll.prepend(dock);
    const ui = detectUi = { overlay, dock, timers: [] };

    const t0 = performance.now();
    const tick = () => {
      const s = (performance.now() - t0) / 1000;
      elapsedEl.textContent = `${Math.floor(s)}s`;
      const cur = steps.reduce((n, [at], i) => (s >= at ? i : n), 0);
      stepEls.forEach((el, i) => { el.className = i < cur ? "done" : i === cur ? "active" : ""; });
      const hint = hints.filter(([at]) => s >= at).pop()?.[1] || "";
      if (hintEl.textContent !== hint) { hintEl.textContent = hint; hintEl.hidden = !hint; }
    };
    tick();
    detectTimer = setInterval(tick, 250);

    try {
      const r = await api("POST", `/api/templates/${id}/autodetect`, { existing: fields.filter((f) => !f.proposed).map(({ label, ...f }) => f) });
      // Replace the old suggestions only now. Skip any that land on a field drawn or accepted mid-run.
      const keep = fields.filter((f) => !f.proposed);
      const added = r.proposals
        .filter((p) => !keep.some((f) => f.page === p.page && overlapRatio(f.rect, p.rect) > 0.6))
        .map((p) => ({ ...p, id: uid(), proposed: true }));
      fields = [...keep, ...added];
      for (const f of added) fresh.add(f.id);
      drafts = r.new_types;
      missed = r.missed;
      reviewTotal = added.length;
      detectNote = r.ai_used ? "" : "AI is off, so these come from printed labels only. Check the types carefully.";
      if (r.errors.length) toast(`Some pages fell back to label matching: ${r.errors.join("; ")}`, "err");
      const first = sortedFields(added)[0];
      if (first) { page = first.page; selId = first.id; sideTab = "field"; }
      else if (!fields.some((f) => f.id === selId)) selId = null;
      stopDetect();
      renderPage(); renderSide(); refreshDirty();
      revealBoxes();
      if (selId) select(selId, { reveal: true });

      const pages = new Set(added.map((f) => f.page)).size;
      const extra = [pages > 1 ? `Across ${plural(pages, "page")}` : "",
        drafts.length ? `${plural(drafts.length, "new data type")} to check` : ""].filter(Boolean).join(" · ");
      if (added.length) endDetect(ui, "ok", `Found ${plural(added.length, "field")}`, [extra, "Review each one, or accept them all."].filter(Boolean).join(". "), 2600);
      else endDetect(ui, "empty", r.candidates ? "No new fields found" : "No input boxes found",
        r.candidates ? "Every box on this template already has a field." : "Draw fields by dragging on the page.", 4000);
    } catch (e) {
      stopDetect();
      endDetect(ui, "failed", "Auto-detect didn't finish", e.message, 0,
        had ? `Your ${plural(had, "earlier suggestion")} ${had === 1 ? "is" : "are"} unchanged.` : "");
    }
  }

  function stopDetect() {
    detecting = false;
    clearInterval(detectTimer);
    detectBtn.classList.remove("busy"); detectBtn.disabled = false;
  }

  /** Swap the status card to its end state and fade the scan away. holdMs 0 keeps it until dismissed. */
  function endDetect(ui, kind, title, msg, holdMs, extra = "") {
    const { overlay, dock } = ui;
    const status = dock.firstChild;
    const later = (fn, ms) => ui.timers.push(setTimeout(fn, ms));
    overlay.classList.add(kind === "failed" ? "failed" : "done");
    later(() => overlay.remove(), 600);
    status.classList.add(kind);
    if (kind === "failed") status.setAttribute("role", "alert");
    const steps = status.querySelectorAll(".ds-steps li");
    steps.forEach((li) => {
      if (kind !== "failed") li.className = "done";
      else if (li.classList.contains("active")) li.className = "stopped";
    });
    status.querySelector(".ds-icon").replaceChildren(icon(kind === "ok" ? "checkCircle" : kind === "failed" ? "alert" : "info"));
    status.querySelector(".ds-title").textContent = title;
    const hint = status.querySelector(".ds-hint");
    hint.textContent = msg; hint.hidden = !msg;
    const foot = status.querySelector(".ds-foot");
    foot.textContent = extra; foot.hidden = !extra;
    const leave = () => {
      if (detectUi === ui) detectUi = null;
      ui.timers.forEach(clearTimeout);
      dock.classList.add("leaving");
      setTimeout(() => { dock.remove(); overlay.remove(); }, 260);
    };
    if (kind === "failed") {
      status.append(h("div", { class: "ds-actions" },
        h("button", { class: "xs", onclick: () => { leave(); autoDetect(); } }, icon("refresh", "sm"), "Try again"),
        h("button", { class: "xs ghost", onclick: leave }, "Dismiss")));
    }
    if (holdMs) later(leave, holdMs);
  }

  /** Pop the fresh boxes in one by one in reading order, capped so a dense page still lands in under a second. */
  function revealBoxes() {
    if (reducedMotion()) return;
    const byId = new Map(fields.map((f) => [f.id, f]));
    const els = [...pageWrap.querySelectorAll(".fbox.new")].filter((el) => byId.has(el.dataset.id));
    const at = (el) => byId.get(el.dataset.id).rect;
    els.sort((a, b) => Math.round(at(a)[1] / 12) - Math.round(at(b)[1] / 12) || at(a)[0] - at(b)[0]);
    const step = Math.min(40, 700 / Math.max(1, els.length));
    els.forEach((el, i) => { el.style.setProperty("--reveal-delay", `${Math.round(140 + i * step)}ms`); el.classList.add("reveal"); });
  }

  /** After accepting/rejecting a single suggestion, move on to the next one. */
  function advanceFrom(f) {
    const pending = sortedFields(fields.filter((x) => x.proposed));
    if (!pending.length) {
      reviewTotal = 0;
      selId = null;
      renderPage(); renderSide(); refreshDirty();
      toast("All suggestions reviewed. Save when you're ready.", "ok", { action: { label: "Save", fn: () => busy(saveBtn, save) } });
      return;
    }
    const after = pending.find((x) => x.page > f.page || (x.page === f.page && (x.rect[1] > f.rect[1] || (x.rect[1] === f.rect[1] && x.rect[0] > f.rect[0])))) || pending[0];
    renderPage(); refreshDirty();
    select(after.id, { reveal: true });
  }

  function acceptProposals(list) {
    for (const f of list) delete f.proposed;
    if (list.length === 1) return advanceFrom(list[0]);
    if (list.length > 1) toast(`Accepted ${plural(list.length, "field")}`, "ok");
    reviewTotal = 0;
    renderPage(); renderSide(); refreshDirty();
  }

  function rejectProposals(list) {
    const ids = new Set(list.map((f) => f.id));
    fields = fields.filter((f) => !ids.has(f.id));
    const stillUsed = new Set(fields.map((f) => f.draft_type).filter(Boolean));
    drafts = drafts.filter((d) => stillUsed.has(d.key));
    if (list.length === 1) { selId = null; return advanceFrom(list[0]); }
    if (ids.has(selId)) selId = null;
    reviewTotal = 0;
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
      onSaved: async (st) => {
        await loadTypes();
        drafts = drafts.filter((x) => x !== d);
        for (const f of fields) if (f.draft_type === d.key) { f.type = st.key; delete f.draft_type; }
        renderPage(); renderSide(); refreshDirty();
      },
    });
  }

  function suggestionsPanel() {
    const pending = fields.filter((f) => f.proposed);
    if (!pending.length && !drafts.length && !missed.length) return null;
    const usage = (d) => fields.filter((f) => f.draft_type === d.key).length;
    const selIsPending = pending.some((f) => f.id === selId);
    return h("div", { class: "ai-panel stack" },
      pending.length ? h("div", {},
        h("div", { class: "head" }, icon("sparkles"), `${plural(pending.length, "suggestion")} to review`),
        h("div", { class: "hint", style: { marginTop: "4px" } }, "Dashed boxes are suggestions. Nothing is saved until you accept and save."),
        h("div", { class: "row tight", style: { marginTop: "10px" } },
          !selIsPending ? h("button", { class: "sm primary", onclick: () => { reviewTotal = pending.length; select(sortedFields(pending)[0].id, { reveal: true }); } }, icon("play", "sm"), "Review one by one") : null,
          h("button", { class: "sm", onclick: () => acceptProposals(pending) }, icon("check", "sm"), "Accept all"),
          h("button", { class: "sm ghost", onclick: () => rejectProposals(pending) }, "Reject all")),
        detectNote ? h("div", { style: { marginTop: "10px" } }, notice("warn", detectNote)) : null) : null,
      drafts.length ? h("div", {},
        h("div", { class: "label" }, "Suggested new data types"),
        h("div", { class: "hint", style: { marginTop: 0, marginBottom: "6px" } }, "Nothing in your type list fitted these fields. Review each one, then create or dismiss it."),
        drafts.map((d) => h("div", { class: "draft-type" },
          h("div", {}, h("div", { class: "strong" }, d.label), h("div", { class: "muted tiny" },
            `${KIND_INFO[d.kind]?.[0] || d.kind} · used by ${plural(usage(d), "field")}`)),
          h("span", { class: "row tight nowrap" },
            h("button", { class: "xs primary", onclick: () => createDraft(d) }, "Review"),
            h("button", { class: "xs ghost", onclick: () => dismissDraft(d) }, "Dismiss"))))) : null,
      missed.length ? h("div", {},
        h("div", { class: "label" }, "Questions with no box found"),
        h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } },
          missed.map((m) => h("li", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); setPage(m.page); } }, `p${m.page + 1}`), ` “${m.label}”`, m.note ? h("span", { class: "muted" }, `: ${m.note}`) : null))),
        h("div", { class: "row between", style: { marginTop: "6px" } },
          h("span", { class: "hint", style: { margin: 0 } }, "Draw these yourself if needed."),
          h("button", { class: "xs ghost", onclick: () => { missed = []; renderSide(); } }, "Clear"))) : null);
  }

  // ------------------------------------------------------------ save / preview / generate
  async function save() {
    const pending = fields.filter((f) => f.proposed);
    if (pending.length) {
      toast(`Accept or reject the ${plural(pending.length, "suggestion")} first`, "err");
      reviewTotal = pending.length;
      select(sortedFields(pending)[0].id, { reveal: true });
      return false;
    }
    const problems = sortedFields(fields.filter(isBad));
    if (problems.length) {
      toast(`${plural(problems.length, "field")} still need a name and a data type`, "err");
      select(problems[0].id, { reveal: true });
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

  async function preview() {
    if (!fields.length) { toast("Add some fields first", "err"); return; }
    if (isDirty() && !(await busy(saveBtn, save))) return;
    let hw = store.get("dc.previewHw", 0);
    const seg = h("div", { class: "seg sm" });
    const drawSeg = () => seg.replaceChildren(...[[0, "Typed"], [100, "Handwritten"]].map(([v, l]) =>
      h("button", { class: hw === v ? "on" : "", onclick: () => { hw = v; store.set("dc.previewHw", v); drawSeg(); pv.reload(); } }, l)));
    drawSeg();
    const pv = previewModal({
      title: `Preview: ${name}`, sub: "A random synthetic record. Nothing is saved.", extra: seg,
      fetchBlob: () => api("POST", `/api/templates/${id}/preview`, { handwritten_pct: hw, seed: Math.floor(Math.random() * 1e9) }),
    });
  }

  async function goGenerate() {
    if (isDirty() && !(await busy(saveBtn, save))) return;
    generateFrom([id]);
  }

  renderPage();
  renderSide();
  refreshDirty();
  requestAnimationFrame(() => { if (!store.get("dc.zoom", null)) fitWidth(); });
}
