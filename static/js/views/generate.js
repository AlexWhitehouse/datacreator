// Generate: configure a run on the left, live summary + actions on the right.
import { api, busy, confirmDialog, countTo, emptyState, h, icon, menuButton, modal, notice, plural, previewModal, reducedMotion, setRangeFill, store, switchEl, toast } from "../ui.js";
import { loadTemplates, loadTypes, state, templateHealth } from "../state.js";
import { watchRuns } from "./runs.js";

// Sets duplicated and documents produced by run settings (mirrors runs.duplicated_count / document_count).
export function duplicatedSets(st) { return st.duplicate ? Math.floor((st.count * st.duplicate_pct) / 100 + 0.5) : 0; }
export function documentTotal(st, nTemplates) {
  const perSet = st.combine && nTemplates > 1 ? 1 : Math.max(1, nTemplates);
  const levels = Math.max(1, (st.duplicate_levels || []).length);
  return (st.count + duplicatedSets(st) * (levels - 1)) * perSet;
}
const fmtLevels = (levels) => levels.map((v) => (v === 100 ? "100% (clean)" : `${v}%`)).join(", ");
const LEVEL_PRESETS = [[100, 90, 70, 50, 30, 10], [100, 75, 50, 25], [100, 50], [90, 70, 50, 30, 10]];
const COUNT_PRESETS = [1, 10, 50, 100, 500, 1000];
const SCANS = [["none", "None"], ["light", "Light"], ["medium", "Medium"], ["heavy", "Heavy"], ["fax", "Fax"]];
const COLOURS = [["colour", "Colour"], ["greyscale", "Greyscale"], ["bw", "Black & white"]];
const FORMATS = [["pdf", "PDF"], ["png", "PNG per page"], ["tiff", "Multi-page TIFF"]];
// OCR stress effects (app/stress.py): each has an on/off key and a % key. Missing keys (older settings) mean off.
const STRESS_KEYS = ["crossouts", "overflow", "typos"];
const STRESS_NAMES = { crossouts: "crossings-out", overflow: "outside the box", typos: "typos" };
const STRESS_PRESETS = {
  off: { crossouts: false, overflow: false, typos: false },
  light: { crossouts: true, crossouts_pct: 5, overflow: true, overflow_pct: 10, typos: true, typos_pct: 3 },
  heavy: { crossouts: true, crossouts_pct: 20, overflow: true, overflow_pct: 35, typos: true, typos_pct: 12 },
};

export async function viewGenerate(view) {
  await Promise.all([loadTypes(), loadTemplates()]);
  const d = state.status.defaults;
  const saved = store.get("dc.generate", {});
  if (saved.missing_pct !== undefined && saved.missing_min === undefined) saved.missing_min = saved.missing_max = saved.missing_pct; // older single rate
  delete saved.missing_pct;
  const s = { ...d, ...saved };
  s.template_ids = (s.template_ids || []).filter((id) => state.templates.some((t) => t.id === id));
  s.fonts = (s.fonts || []).filter((f) => state.status.handwriting_fonts.includes(f));
  if (!s.fonts.length && !saved.fonts?.length) s.fonts = [...state.status.handwriting_fonts];
  if (s.layout === "merged") { s.combine = true; s.layout = "flat"; } // older saved settings
  const persist = () => store.set("dc.generate", s);

  const presetSlot = h("div", { class: "preset-bar" });
  view.append(h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Generate"), h("p", { class: "muted" }, "Produce filled-in synthetic documents from your templates. Settings are remembered between visits; save a preset to come back to them by name.")),
    presetSlot));

  if (!state.templates.length) {
    view.append(h("div", { class: "card" }, emptyState({
      icon: "layers", title: "No templates yet", text: "Upload and mark up a blank PDF first, then come back here to generate documents.",
      actions: [h("a", { class: "btn primary", href: "#/templates" }, icon("upload", "sm"), "Go to templates")],
    })));
    return;
  }

  const tpls = () => s.template_ids.map((id) => state.templates.find((t) => t.id === id)).filter(Boolean);
  const combining = () => s.combine && tpls().length > 1;
  const docCount = () => documentTotal(s, tpls().length);
  const allForms = (n) => (n === 2 ? "both forms" : `all ${n} forms`);
  const stressOn = () => STRESS_KEYS.some((k) => s[k] && s[`${k}_pct`] > 0);
  const stressText = () => (stressOn() ? STRESS_KEYS.filter((k) => s[k]).map((k) => `${STRESS_NAMES[k]} ${s[`${k}_pct`]}%`).join(", ") : "no OCR stress");

  function liveAiFields() {
    const names = new Set();
    for (const t of tpls()) for (const f of t.fields) {
      const ty = state.typeMap[f.type];
      if (ty && ty.kind === "ai" && ty.config.mode !== "pool") names.add(f.name);
    }
    return [...names];
  }

  /** Everything that blocks a run, keyed to the section that fixes it. */
  function problems() {
    const out = [];
    if (!tpls().length) out.push([1, "Choose at least one template"]);
    for (const t of tpls()) if (!templateHealth(t).ok) out.push([1, `“${t.name}”: ${templateHealth(t).label.toLowerCase()}`]);
    if (liveAiFields().length && !state.status.ai_available) out.push([2, "Live AI fields need an AI key: add one in Settings"]);
    if (s.age_min > s.age_max) out.push([3, "Patient age range is reversed"]);
    if (s.duplicate && !s.duplicate_levels.length) out.push([4, "Enter at least one accuracy level"]);
    if (s.handwritten_pct > 0 && !s.fonts.length) out.push([5, "Choose at least one handwriting style"]);
    if (!s.formats.length) out.push([6, "Choose at least one file format"]);
    return out;
  }

  // ------------------------------------------------------------ widgets
  const segs = (key, options, after) => h("div", { class: "seg" }, options.map(([v, l]) =>
    h("button", { type: "button", class: s[key] === v ? "on" : "", onclick: () => { s[key] = v; after ? after() : render(); } }, l)));

  function slider(label, key, { hint, after, min = 0, max = 100, unit = "%" } = {}) {
    const out = h("span", { class: "val" }, `${s[key]}${unit}`);
    const r = h("input", {
      type: "range", min, max, step: 1, value: s[key],
      oninput: (e) => { s[key] = +e.target.value; out.textContent = `${s[key]}${unit}`; setRangeFill(r); light(); after && after(); },
    });
    setRangeFill(r);
    return h("div", {}, h("div", { class: "label" }, label, out), r, hint ? h("div", { class: "hint" }, hint) : null);
  }

  function dualSlider(label, kLo, kHi, { min = 0, max = 100, unit = "%", fmt, after } = {}) {
    const out = h("span", { class: "val" });
    const fill = h("div", { class: "fill" });
    const lo = h("input", { type: "range", min, max, step: 1, value: s[kLo], "aria-label": `${label} from` });
    const hi = h("input", { type: "range", min, max, step: 1, value: s[kHi], "aria-label": `${label} to` });
    const sync = () => {
      const a = +lo.value, b = +hi.value;
      s[kLo] = Math.min(a, b); s[kHi] = Math.max(a, b);
      out.textContent = fmt ? fmt(s[kLo], s[kHi]) : s[kLo] === s[kHi] ? `${s[kLo]}${unit}` : `${s[kLo]}–${s[kHi]}${unit}`;
      const p = (v) => ((v - min) / (max - min)) * 100;
      fill.style.left = `${p(s[kLo])}%`;
      fill.style.width = `${p(s[kHi]) - p(s[kLo])}%`;
      // keep the handle you're dragging on top when both meet
      lo.style.zIndex = +lo.value >= max - 1 ? 3 : 1;
      hi.style.zIndex = 2;
    };
    for (const r of [lo, hi]) r.addEventListener("input", () => { sync(); light(); after && after(); });
    sync();
    return h("div", {}, h("div", { class: "label" }, label, out),
      h("div", { class: "dual-range" }, h("div", { class: "rail" }), fill, lo, hi),
      h("div", { class: "range-scale" }, h("span", {}, `${min}${unit}`), h("span", {}, `${max}${unit}`)));
  }

  function section(num, title, lead, ...content) {
    const bad = problems().some(([n]) => n === num);
    return h("div", { class: "card gsec", id: `sec-${num}` },
      h("div", { class: "card-head" }, h("div", { class: "sec-title" }, h("span", { class: `num${bad ? " bad" : ""}` }, num), h("h2", {}, title))),
      lead ? h("p", { class: "lead" }, lead) : null,
      h("div", { class: "content" }, ...content));
  }

  // ------------------------------------------------------------ sections
  let tpickScroll = 0; // the template list's own scroll position, kept across re-renders
  // Rows' screen positions before a change, so moved rows can glide to their new place (FLIP).
  let rowTops = null;
  const noteRowTops = () => { rowTops = new Map([...document.querySelectorAll(".tpick-row[data-id]")].map((r) => [r.dataset.id, r.getBoundingClientRect().top])); };
  function glideRows(list) {
    const before = rowTops; rowTops = null;
    if (!before || reducedMotion()) return;
    requestAnimationFrame(() => {
      for (const r of list.querySelectorAll(".tpick-row[data-id]")) {
        const dy = (before.get(r.dataset.id) ?? r.getBoundingClientRect().top) - r.getBoundingClientRect().top;
        if (Math.abs(dy) > 1) r.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 220, easing: "cubic-bezier(.2,.8,.2,1)" });
      }
    });
  }

  function templatesSection() {
    const sel = tpls();
    const pages = sel.reduce((n, t) => n + t.page_sizes.length, 0);
    const ordering = sel.length > 1; // selected rows can be dragged into order
    const setIds = (ids) => { noteRowTops(); s.template_ids = ids; render(); };
    const toggle = (t, on) => setIds(on ? [...s.template_ids, t.id] : s.template_ids.filter((x) => x !== t.id));
    const ready = state.templates.filter((t) => templateHealth(t).ok);
    const allOn = ready.length > 0 && ready.every((t) => s.template_ids.includes(t.id));

    // selected templates first, in their order (drag to reorder); the rest below in library order
    const rest = state.templates.filter((t) => !s.template_ids.includes(t.id));
    let dragId = null;
    const clearMarks = () => list.querySelectorAll(".drop-before, .drop-after").forEach((x) => x.classList.remove("drop-before", "drop-after"));
    const row = (t) => {
      const on = s.template_ids.includes(t.id);
      const health = templateHealth(t);
      const usable = health.ok || on;
      const pos = s.template_ids.indexOf(t.id);
      const drag = on && ordering;
      return h("label", {
        class: `tpick-row${on ? " on" : ""}${usable ? "" : " disabled"}${drag ? " draggable" : ""}`, title: drag ? `${t.name} · drag to change the order` : t.name,
        "data-id": t.id, draggable: drag ? "true" : null,
        onclick: usable ? null : (e) => { e.preventDefault(); toast(`“${t.name}”: ${health.label.toLowerCase()}`, "err", { action: { label: "Open editor", fn: () => (location.hash = `#/templates/${t.id}`) } }); },
        ondragstart: drag ? (e) => { dragId = t.id; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", t.name); e.currentTarget.classList.add("dragging"); } : null,
        ondragend: drag ? (e) => { dragId = null; e.currentTarget.classList.remove("dragging"); clearMarks(); } : null,
        ondragover: drag ? (e) => {
          if (dragId === null || dragId === t.id) return;
          e.preventDefault();
          const r = e.currentTarget.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2;
          clearMarks();
          e.currentTarget.classList.add(after ? "drop-after" : "drop-before");
        } : null,
        ondrop: drag ? (e) => {
          e.preventDefault();
          const after = e.currentTarget.classList.contains("drop-after");
          clearMarks();
          if (dragId === null || dragId === t.id) return;
          const ids = s.template_ids.filter((x) => x !== dragId);
          ids.splice(ids.indexOf(t.id) + (after ? 1 : 0), 0, dragId);
          setIds(ids);
        } : null,
      },
        drag ? h("span", { class: "grip", "aria-hidden": "true" }, icon("grip", "sm")) : h("span", { class: "grip" }),
        h("input", {
          type: "checkbox", checked: on, disabled: !usable, onchange: (e) => toggle(t, e.target.checked),
          "aria-label": drag ? `${t.name}, form ${pos + 1} of ${sel.length}. Alt+arrow keys change the order.` : t.name,
          onkeydown: drag ? (e) => {
            if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
            e.preventDefault();
            const j = pos + (e.key === "ArrowUp" ? -1 : 1);
            if (j < 0 || j >= sel.length) return;
            const ids = [...s.template_ids];
            [ids[pos], ids[j]] = [ids[j], ids[pos]];
            setIds(ids);
            requestAnimationFrame(() => document.querySelector(`.tpick-row[data-id="${t.id}"] input`)?.focus());
          } : null,
        }),
        h("span", { class: "thumb", style: { backgroundImage: `url(/api/templates/${t.id}/pages/0.png?dpi=30)` } }),
        h("span", { class: "tpick-name" }, t.name),
        health.ok ? null : h("span", { class: `pill ${health.kind}` }, health.label),
        h("span", { class: "tpick-meta" }, `${plural(t.page_sizes.length, "page")} · ${plural(t.fields.length, "field")}`),
        drag ? h("span", { class: "tpick-pos", title: combining() ? `Form ${pos + 1} in the combined PDF` : `Template ${pos + 1}` }, pos + 1) : h("span"));
    };
    const list = h("div", { class: "tpick", role: "group", "aria-label": "Templates" },
      sel.map(row),
      sel.length && rest.length ? h("div", { class: "tpick-sep" }, "Not selected") : null,
      rest.map(row));
    glideRows(list);

    // A long list scrolls inside its box: fade the cut-off edge and say how many rows are hidden below,
    // so it's obvious there's more. The scroll position survives the re-render that every click causes.
    const moreBtn = h("button", { type: "button", class: "tpick-more", onclick: () => list.scrollBy({ top: list.clientHeight * 0.8, behavior: "smooth" }) });
    const wrap = h("div", { class: "tpick-wrap" }, list, moreBtn);
    const updateMore = () => {
      const edge = list.scrollTop + list.clientHeight;
      const below = [...list.querySelectorAll(".tpick-row")].filter((r) => r.offsetTop + r.offsetHeight / 2 > edge).length;
      wrap.classList.toggle("more-below", below > 0);
      wrap.classList.toggle("more-above", list.scrollTop > 2);
      moreBtn.replaceChildren(`${below} more`, icon("chevD", "sm"));
      moreBtn.setAttribute("aria-label", `Scroll to see ${plural(below, "more template")}`);
    };
    list.addEventListener("scroll", () => { tpickScroll = list.scrollTop; updateMore(); }, { passive: true });
    // measure once the list is on screen (the first render happens before the page is attached), and again on resize
    let restored = false;
    const ro = new ResizeObserver(() => {
      if (!list.isConnected) { if (restored) ro.disconnect(); return; }
      if (!restored && list.clientHeight) { list.scrollTop = tpickScroll; restored = true; }
      updateMore();
    });
    ro.observe(list);

    const head = h("div", { class: "tpick-head" },
      h("span", { class: "muted small" }, sel.length ? `${sel.length} of ${state.templates.length} selected${ordering ? (combining() ? " · drag to set the order of forms in the PDF" : " · drag to reorder") : ""}` : "None selected"),
      ready.length > 1 ? h("button", { class: "xs ghost", onclick: () => setIds(allOn ? [] : [...s.template_ids, ...ready.map((t) => t.id).filter((id) => !s.template_ids.includes(id))]) }, allOn ? "Clear" : "Select all ready") : null);

    return section(1, "Templates", "Pick one template, or several to produce linked sets about the same synthetic person.",
      head, wrap,
      sel.length > 1 ? h("div", { class: "tpick-opts" },
        h("div", { class: "tpick-opt" }, h("span", { class: "label" }, "Each set becomes"),
          segs("combine", [[false, `${sel.length} separate documents`], [true, "One combined PDF"]]),
          h("span", { class: "hint" }, s.combine ? `${plural(pages, "page")} per set, forms in the order above` : "One file per template per set")),
        linkInfo(sel)) : null);
  }

  function linkInfo(sel) {
    const byName = new Map();
    for (const t of sel) for (const f of t.fields) {
      if (!byName.has(f.name)) byName.set(f.name, []);
      byName.get(f.name).push({ t: t.name, type: f.type, person: f.person || 1 });
    }
    const shared = [...byName].filter(([, v]) => new Set(v.map((x) => x.t)).size > 1);
    const conflicts = shared.filter(([, v]) => new Set(v.map((x) => `${x.type}/${x.person}`)).size > 1);
    const open = !!store.get("dc.generate.linkOpen", false);
    return h("div", { class: "stack" },
      h("details", { class: "link-line", open, ontoggle: (e) => store.set("dc.generate.linkOpen", e.currentTarget.open) },
        h("summary", {}, icon("link", "sm"),
          h("span", {}, h("strong", {}, "Linked sets"), ` · the same person on ${combining() ? "every form" : "every document"} in a set`),
          h("span", { class: "muted" }, shared.length ? ` · ${plural(shared.length, "shared field name")}` : " · no shared field names"),
          h("span", { class: "chev" }, icon("chevD", "sm"))),
        h("div", { class: "link-body" },
          h("p", { class: "hint" }, "Person-based types (names, date of birth, address, NHS number…) always agree within a set, even when field names differ. Fields with the same name also share one value."),
          shared.length ? h("div", { class: "chips" }, shared.map(([n]) => h("code", {}, n))) : h("p", { class: "hint" }, "Give fields the same name in both templates to share a value."))),
      conflicts.length ? notice("warn", "Same name, different type: ",
        conflicts.map(([n, v]) => `${n} (${v.map((x) => `${x.t}: ${x.type}`).join(" vs ")})`).join("; "), ". The first template's definition is used.") : null);
  }

  function volumeSection() {
    const multi = tpls().length > 1;
    const ai = liveAiFields();
    return section(2, "Volume", null,
      h("div", { class: "grid2" },
        h("div", {},
          h("label", { class: "field" }, h("span", {}, multi ? "Number of sets" : "Number of documents"),
            h("input", { type: "number", min: 1, max: 10000, value: s.count, oninput: (e) => { s.count = Math.max(1, Math.min(10000, +e.target.value || 1)); light(); } })),
          h("div", { class: "chips", style: { marginTop: "8px" } }, COUNT_PRESETS.map((n) =>
            h("button", { class: `chip${s.count === n ? " on" : ""}`, onclick: () => { s.count = n; render(); } }, n.toLocaleString())))),
        h("label", { class: "field" }, h("span", {}, "Seed"),
          h("div", { class: "row nowrap tight" },
            h("input", { type: "number", value: s.seed ?? "", placeholder: "Random each run", oninput: (e) => { s.seed = e.target.value === "" ? null : +e.target.value; light(); } }),
            h("button", { class: "icon", title: "Pick a seed to make this run repeatable", onclick: () => { s.seed = Math.floor(Math.random() * 1e6); render(); } }, icon("shuffle", "sm"))),
          h("small", {}, "Same seed + same settings = same data, apart from live AI text."))),
      ai.length ? h("div", { style: { marginTop: "14px" } }, state.status.ai_available
        ? notice("", `Live AI fields: ${ai.join(", ")}. This makes ${plural(s.count, "AI call")} (one per set) to ${state.status.ai_model}.`)
        : notice("err", `These templates use live AI fields (${ai.join(", ")}) but AI isn't configured.`)) : null);
  }

  const missingHint = h("p", { class: "hint" });
  function missingText() {
    const [lo, hi] = [s.missing_min, s.missing_max];
    const what = lo === hi ? (lo ? `${lo}% of fields are left blank in every document` : "Every field is filled in")
      : `Each document gets its own blank rate between ${lo}% and ${hi}%, so some are nearly complete and others sparse`;
    const dupNote = s.duplicate ? (s.duplicate_pct >= 100 ? " Not used while every set is duplicated: accuracy levels set the blanks." : " Applies to sets that aren't duplicated.") : "";
    return `${what}. Blanks are picked at random per document; required fields are always filled, and a field's own missing % overrides this.${dupNote}`;
  }

  function dataSection() {
    missingHint.textContent = missingText();
    return section(3, "Data", null,
      h("div", { class: "grid2" },
        h("div", {}, dualSlider("Missing fields", "missing_min", "missing_max", { after: () => (missingHint.textContent = missingText()) }), missingHint),
        dualSlider("Patient age", "age_min", "age_max", { min: 0, max: 105, unit: "", fmt: (a, b) => `${a}–${b} years` })));
  }

  const dupHint = h("div");
  function dupText() {
    const levels = s.duplicate_levels;
    if (!levels.length) return notice("warn", "Enter at least one accuracy level, e.g. 100, 90, 70.");
    const k = duplicatedSets(s), unit = combining() || tpls().length < 2 ? "" : " per template";
    return notice("", `${k.toLocaleString()} of ${plural(s.count, "set")} will be made ${levels.length} time${levels.length > 1 ? "s" : ""}${unit}: ${fmtLevels(levels)}. ` +
      "Lower levels blank the same fields as the level above plus more. Files end in _acc90 etc., and the ground truth records each copy's accuracy.");
  }
  function parseLevels(text) {
    const vals = text.split(/[^0-9.]+/).filter(Boolean).map((v) => Math.round(+v)).filter((v) => v >= 0 && v <= 100);
    return [...new Set(vals)].sort((a, b) => b - a);
  }

  function dupSection() {
    dupHint.replaceChildren(dupText());
    return section(4, "Duplicates", "Copies of a document at several accuracy levels. Copies share the record, writer, handwriting and scan, and differ only in how many fields are filled in.",
      switchEl(s.duplicate, (v) => { s.duplicate = v; render(); }, "Make duplicates at several accuracy levels"),
      s.duplicate ? h("div", { class: "stack lg", style: { marginTop: "16px" } },
        h("div", { class: "grid2" },
          slider("Sets to duplicate", "duplicate_pct", { hint: "Chosen at random. 100% duplicates every set.", after: () => { dupHint.replaceChildren(dupText()); missingHint.textContent = missingText(); } }),
          h("div", {},
            h("label", { class: "field" }, h("span", {}, "Accuracy levels (% of fields filled in)"),
              h("input", { type: "text", value: s.duplicate_levels.join(", "), placeholder: "100, 90, 70, 50", oninput: (e) => { s.duplicate_levels = parseLevels(e.target.value); dupHint.replaceChildren(dupText()); light(); } })),
            h("div", { class: "chips", style: { marginTop: "8px" } }, LEVEL_PRESETS.map((lv) =>
              h("button", { class: `chip${lv.join() === s.duplicate_levels.join() ? " on" : ""}`, onclick: () => { s.duplicate_levels = lv; render(); } }, lv.join(" · ")))))),
        dupHint) : null);
  }

  // Realism & OCR stress: who fills the form in, how it's scanned, and the mistakes that make it hard to read.
  function subsec(title, text, ...content) {
    return h("div", { class: "subsec" }, h("div", { class: "subsec-head" }, h("h3", {}, title), text ? h("p", { class: "hint" }, text) : null), ...content);
  }

  function effectRow(key, title, text, sliderLabel) {
    const on = !!s[key];
    return h("div", { class: `fx-row${on ? " on" : ""}` },
      h("div", { class: "fx-what" }, switchEl(on, (v) => { s[key] = v; render(); }, title), h("p", { class: "hint" }, text)),
      h("div", { class: "fx-amount", inert: !on },
        slider(sliderLabel, `${key}_pct`, { after: () => stressChips.replaceChildren(...stressPresetChips()) })));
  }

  const stressChips = h("div", { class: "chips" });
  function stressPresetChips() {
    const match = (p) => STRESS_KEYS.every((k) => (p[k] === false ? !s[k] : s[k] && s[`${k}_pct`] === p[`${k}_pct`]));
    const current = Object.entries(STRESS_PRESETS).find(([, p]) => match(p))?.[0];
    return [...Object.entries(STRESS_PRESETS).map(([name, p]) =>
      h("button", { class: `chip${current === name ? " on" : ""}`, onclick: () => { Object.assign(s, p); render(); } }, name[0].toUpperCase() + name.slice(1))),
    current ? null : h("span", { class: "pill" }, "Custom")].filter(Boolean);
  }

  function realismSection() {
    const all = state.status.handwriting_fonts;
    stressChips.replaceChildren(...stressPresetChips());
    const handOnly = (s.crossouts || s.overflow) && s.handwritten_pct === 0;
    return section(5, "Realism & OCR stress", "How the filled-in documents look, and the kinds of mess that make them harder to read. All of it follows the seed.",
      subsec("Handwriting", "Each document gets one ‘writer’: a single handwriting style, pen colour and slant, with natural variation per letter. Signatures are always handwritten.",
        h("div", { class: "grid3" },
          slider("Handwritten", "handwritten_pct", { hint: s.handwriting_mode === "document" ? "Of documents filled in by hand; the rest are typed." : "Of fields written by hand." }),
          h("div", {}, h("div", { class: "label" }, "Applies to"), segs("handwriting_mode", [["document", "Documents"], ["field", "Fields"]])),
          h("div", {}, h("div", { class: "label" }, "Ink"), segs("ink", [["mixed", "Mixed"], ["blue", "Blue"], ["black", "Black"]]))),
        h("div", { class: "opt-block", style: { marginTop: "20px" } },
          h("div", { class: "row between", style: { marginBottom: "8px" } },
            h("div", { class: "label", style: { margin: 0 } }, "Handwriting styles", h("span", { class: "pill" }, `${s.fonts.length} of ${all.length}`)),
            h("div", { class: "row tight" },
              h("button", { class: "xs ghost", onclick: () => { s.fonts = [...all]; render(); } }, "Select all"),
              h("button", { class: "xs ghost", onclick: () => { s.fonts = []; render(); } }, "Clear"))),
          h("div", { class: "font-grid" }, all.map((f) => {
            const on = s.fonts.includes(f);
            return h("label", { class: `font-opt${on ? " on" : ""}` },
              h("img", { src: `/api/fonts/${f}.png`, alt: f, loading: "lazy" }),
              h("span", { class: "fname" }, h("input", { type: "checkbox", checked: on, onchange: (e) => { s.fonts = e.target.checked ? [...s.fonts, f] : s.fonts.filter((x) => x !== f); render(); } }), f));
          }))),
        h("label", { class: "field opt-block", style: { maxWidth: "280px" } }, h("span", {}, "Typed font"),
          h("select", { onchange: (e) => { s.typed_font = e.target.value; light(); } },
            Object.entries(state.status.typed_fonts).map(([k, v]) => h("option", { value: k, selected: s.typed_font === k }, v))))),
      subsec("Scan", "Simulates scanning: skew, blur, noise and speckles. Scanned output is image-only, like a real scan.",
        h("div", { class: "grid2" },
          h("div", {}, h("div", { class: "label" }, "Scan effect"), segs("scan", SCANS)),
          h("div", {}, h("div", { class: "label" }, "Colour"), segs("colour", COLOURS)))),
      subsec("OCR stress", "Mistakes real form-fillers make. The ground truth records each one, so you can score how extraction copes.",
        h("div", { class: "row tight fx-presets" }, h("span", { class: "label", style: { margin: 0 } }, "Intensity"), stressChips),
        h("div", { class: "fx-list" },
          effectRow("crossouts", "Crossings-out", "A wrong or half-written value struck through, then the right one, in the same hand. Handwritten fields only; each document varies around this rate.", "Filled handwritten fields (average)"),
          effectRow("overflow", "Writing outside the box", "Handwriting that isn't shrunk to fit: it runs past the right edge or below the box, staying on the page.", "Handwritten text fields"),
          effectRow("typos", "Typos", "A swapped, dropped or doubled letter, a neighbouring key when typed, or a digit slip. Dates stay real dates.", "Filled text fields")),
        handOnly ? notice("warn", "Crossings-out and writing outside the box only happen in handwriting, and Handwritten is set to 0%.") : null));
  }

  function outputSection() {
    return section(6, "Output", null,
      h("div", { class: "grid3" },
        h("div", {}, h("div", { class: "label" }, "File formats"),
          h("div", { class: "chips" }, FORMATS.map(([v, l]) => {
            const on = s.formats.includes(v);
            return h("button", { class: `chip${on ? " on" : ""}`, onclick: () => { s.formats = on ? s.formats.filter((x) => x !== v) : [...s.formats, v]; render(); } }, on ? icon("check", "sm") : null, l);
          }))),
        h("div", {}, h("div", { class: "label" }, "Image resolution"), h("div", { class: "seg sm" }, [100, 150, 200, 300].map((v) =>
          h("button", { class: s.dpi === v ? "on" : "", onclick: () => { s.dpi = v; render(); } }, `${v}`))),
          h("div", { class: "hint" }, "DPI for PNG/TIFF and scanned PDFs.")),
        h("div", {}, h("div", { class: "label" }, "Layout"), segs("layout", [["folders", "Folder per set"], ["flat", "One folder"]]))));
  }

  // ------------------------------------------------------------ summary rail
  const bigNum = h("span", {});
  const bigWord = h("small", {});
  const breakdown = h("div", { class: "breakdown" });
  const rows = h("div", { class: "rows" });
  const why = h("div");
  const previewBtn = h("button", { class: "block", onclick: () => previewFirst() }, icon("eye", "sm"), "Preview one");
  const genBtn = h("button", { class: "primary block lg", onclick: (e) => busy(e.currentTarget, start) }, icon("zap", "sm"), "Generate");
  const summary = h("div", { class: "card summary" },
    h("div", { class: "hero" }, h("div", { class: "k" }, "This run makes"), h("div", { class: "big" }, bigNum, bigWord), breakdown),
    rows,
    h("div", { class: "acts" }, why, genBtn, previewBtn,
      h("div", { class: "gt" }, "Every run also writes ground_truth.json, ground_truth.csv and records.csv.")));

  function sumRow(num, ic, title, value, bad) {
    return h("button", { class: `sum-row${bad ? " bad" : ""}`, onclick: () => jump(num) },
      icon(ic, "sm"), h("div", {}, h("div", { class: "t" }, title), h("div", { class: "v" }, value)));
  }
  function jump(num) {
    const el = document.getElementById(`sec-${num}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    el.classList.remove("flash-sec"); void el.offsetWidth; el.classList.add("flash-sec");
  }

  function updateSummary() {
    const sel = tpls(), docs = docCount(), probs = problems();
    const badSec = new Set(probs.map(([n]) => n));
    countTo(bigNum, docs);
    bigWord.textContent = docs === 1 ? "document" : "documents";
    const parts = [];
    if (sel.length > 1) parts.push(combining() ? `${plural(s.count, "set")}, ${sel.length} forms each` : `${plural(s.count, "set")} × ${sel.length} templates`);
    else parts.push(plural(s.count, "record"));
    if (s.duplicate && s.duplicate_levels.length > 1) parts.push(`+ ${duplicatedSets(s).toLocaleString()} duplicated × ${s.duplicate_levels.length - 1} extra`);
    breakdown.textContent = parts.join(" ");
    const hw = s.handwritten_pct === 0 ? "All typed" : `${s.handwritten_pct}% of ${s.handwriting_mode === "document" ? "documents" : "fields"} by hand · ${plural(s.fonts.length, "style")} · ${s.ink} ink`;
    rows.replaceChildren(
      sumRow(1, "layers", "Templates", sel.length ? sel.map((t) => t.name).join(sel.length > 1 && combining() ? " + " : ", ") : "None selected", badSec.has(1)),
      sumRow(2, "hash", "Volume", `${plural(s.count, sel.length > 1 ? "set" : "document")} · seed ${s.seed ?? "random"}`, badSec.has(2)),
      sumRow(3, "percent", "Data", `${s.missing_min === s.missing_max ? `${s.missing_min}%` : `${s.missing_min}–${s.missing_max}%`} missing · age ${s.age_min}–${s.age_max}`, badSec.has(3)),
      sumRow(4, "copy", "Duplicates", s.duplicate ? `${s.duplicate_pct}% of sets at ${s.duplicate_levels.join(" / ")}%` : "Off", badSec.has(4)),
      sumRow(5, "pen", "Handwriting", hw, badSec.has(5)),
      sumRow(5, "sparkles", "Scan & OCR stress", `${SCANS.find(([v]) => v === s.scan)?.[1]} scan · ${COLOURS.find(([v]) => v === s.colour)?.[1].toLowerCase()} · ${stressText()}`, false),
      sumRow(6, "printer", "Output", `${s.formats.map((f) => f.toUpperCase()).join("+") || "no format"} · ${s.dpi} dpi · ${s.layout === "flat" ? "one folder" : "folder per set"}`, badSec.has(6)));
    why.replaceChildren(...(probs.length ? [h("div", { class: "why" }, icon("alert", "sm"), h("span", {}, probs[0][1], probs.length > 1 ? ` (+${probs.length - 1} more)` : ""))] : []));
    genBtn.disabled = probs.length > 0;
    genBtn.lastChild.textContent = probs.length ? "Generate" : `Generate ${plural(docs, "document")}`;
    previewBtn.disabled = !sel.length || probs.some(([n]) => n === 1);
    const what = `${combining() ? "Preview one set" : "Preview one"}${stressOn() ? " with OCR stress" : ""}`;
    previewBtn.lastChild.textContent = s.duplicate && s.duplicate_levels.length > 1 ? `${what} (${s.duplicate_levels.length} versions)` : what;
    for (let n = 1; n <= 6; n++) {
      const badge = document.querySelector(`#sec-${n} .num`);
      if (badge) badge.classList.toggle("bad", badSec.has(n));
    }
  }

  // ------------------------------------------------------------ render
  const left = h("div", { class: "stack lg" });
  view.append(h("div", { class: "gen-layout" }, left, summary));
  let lastSec = null;
  const noteSec = (e) => { lastSec = e.target.closest?.("[id^=sec-]")?.id || null; };
  left.addEventListener("pointerdown", noteSec, true);
  left.addEventListener("keydown", noteSec, true);
  function render() {
    // Every change rebuilds the form. Keep the page still while that happens:
    //  - hold the column's height so the page never gets briefly shorter (which made the browser clamp the scroll)
    //  - keep the section you clicked in at the same place on screen, even if text above it grew or shrank
    const y = window.scrollY;
    // the section last pressed or typed in (Safari doesn't focus buttons on click, so focus alone isn't enough)
    const anchorId = lastSec || document.activeElement?.closest?.("[id^=sec-]")?.id;
    const anchorTop = anchorId ? document.getElementById(anchorId).getBoundingClientRect().top : null;
    left.style.minHeight = left.childElementCount ? `${left.offsetHeight}px` : "";
    left.replaceChildren(templatesSection(), volumeSection(), dataSection(), dupSection(), realismSection(), outputSection());
    light();
    const anchor = anchorId && document.getElementById(anchorId);
    const target = y + (anchor ? anchor.getBoundingClientRect().top - anchorTop : 0);
    if (window.scrollY !== target) window.scrollTo(0, target);
    requestAnimationFrame(() => { left.style.minHeight = ""; if (window.scrollY !== target) window.scrollTo(0, target); });
  }
  function light() { persist(); updateSummary(); }

  function previewFirst() {
    const sel = tpls();
    previewModal({
      title: combining() ? "Preview one set" : `Preview: ${sel[0].name}`,
      sub: `One sample with these settings and a random record. Nothing is saved.${stressOn() ? " The bottom margin lists which fields got crossings-out, typos or writing outside the box." : ""}`,
      // a combined run previews the whole set; otherwise the first template
      fetchBlob: () => api("POST", "/api/preview", { ...s, seed: s.seed ?? Math.floor(Math.random() * 1e9) }),
    });
  }

  async function start() {
    try {
      const run = await api("POST", "/api/runs", s);
      if (run?.id) sessionStorage.setItem("dc.freshRun", run.id);
      watchRuns();
      location.hash = "#/runs";
    } catch (e) { toast(e.message, "err"); }
  }

  mountPresets(presetSlot, s, d, () => render());
  render();
  // the presets bar watches for edits to show "modified"
  view.addEventListener("input", () => presetSlot.refresh?.());
  view.addEventListener("click", () => setTimeout(() => presetSlot.refresh?.()));
}

// ------------------------------------------------------------ presets
/** Named saved settings. Applying one replaces the current settings (templates only if the preset has some). */
function mountPresets(slot, s, defaults, rerender) {
  let presets = [];
  let activeId = store.get("dc.generate.preset", null);
  const keys = Object.keys(defaults);
  const active = () => presets.find((p) => p.id === activeId) || null;
  /** The settings applying preset p gives (templates fall back to the current selection when it has none). */
  function resolved(p) {
    const out = { ...structuredClone(defaults), ...structuredClone(p.settings) };
    out.template_ids = (p.settings.template_ids?.length ? p.settings.template_ids : s.template_ids).filter((id) => state.templates.some((t) => t.id === id));
    out.fonts = (out.fonts || []).filter((f) => state.status.handwriting_fonts.includes(f));
    if (!out.fonts.length) out.fonts = [...state.status.handwriting_fonts];
    return out;
  }
  // a preset saved without a seed or templates doesn't care about them, so they don't count as changes
  const pick = (o, p) => JSON.stringify(keys.filter((k) => (k !== "template_ids" || p.settings.template_ids?.length) && (k !== "seed" || p.settings.seed != null))
    .map((k) => [k, o[k] ?? defaults[k]]));
  const modified = () => { const p = active(); return !!p && pick(resolved(p), p) !== pick(s, p); };
  const setActive = (id) => { activeId = id; store.set("dc.generate.preset", id); };

  const sel = h("select", { class: "preset-select", "aria-label": "Preset" });
  const dot = h("span", { class: "preset-mod", title: "Settings changed since this preset was applied or saved" }, "Modified");
  const saveBtn = h("button", { class: "sm", title: "Save the current settings into this preset" }, icon("save", "sm"), "Update");
  const newBtn = h("button", { class: "sm", title: "Save the current settings as a new preset" }, icon("plus", "sm"), "Save as preset");
  const more = h("span");

  function apply(p) {
    const next = resolved(p);
    for (const k of Object.keys(s)) delete s[k];
    Object.assign(s, next);
    const lost = (p.settings.template_ids || []).length - (p.settings.template_ids?.length ? s.template_ids.length : 0);
    setActive(p.id);
    rerender();
    toast(`Applied “${p.name}”${lost > 0 ? `: ${plural(lost, "template")} in it no longer exist` : ""}`, lost > 0 ? "err" : "ok");
    draw();
  }

  function nameDialog({ title, value = "", ok, withOptions }) {
    return new Promise((resolve) => {
      let close, result = null;
      const input = h("input", { type: "text", value, maxlength: 80, placeholder: "e.g. Fax stress test" });
      const inclT = h("input", { type: "checkbox", checked: true });
      const inclS = h("input", { type: "checkbox", checked: s.seed != null, disabled: s.seed == null });
      const submit = () => {
        if (!input.value.trim()) { input.focus(); return; }
        result = { name: input.value.trim(), templates: inclT.checked, seed: inclS.checked };
        close();
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
      close = modal({
        title, size: "sm",
        body: h("div", { class: "stack" },
          h("label", { class: "field" }, h("span", {}, "Name"), input),
          withOptions ? h("label", { class: "check preset-opt" }, inclT, h("span", {}, "Include the selected templates", h("div", { class: "hint" }, "Leave off to keep whichever templates are selected when you apply it."))) : null,
          withOptions ? h("label", { class: "check preset-opt" }, inclS, h("span", {}, `Include the seed${s.seed == null ? " (none set: each run gets a random one)" : ` (${s.seed})`}`)) : null),
        buttons: [h("button", { onclick: () => close() }, "Cancel"), h("button", { class: "primary", onclick: submit }, ok)],
        onClose: () => resolve(result),
      });
    });
  }

  const snapshot = (opts) => {
    const out = structuredClone(s);
    if (!opts.templates) out.template_ids = [];
    if (!opts.seed) out.seed = null;
    return out;
  };

  async function saveNew() {
    const r = await nameDialog({ title: "Save as preset", ok: "Save preset", withOptions: true });
    if (!r) return;
    try {
      const p = await api("POST", "/api/presets", { name: r.name, settings: snapshot(r) });
      presets = [...presets, p].sort((a, b) => a.name.localeCompare(b.name));
      setActive(p.id); draw();
      toast(`Saved preset “${p.name}”`, "ok");
    } catch (e) { toast(e.message, "err"); }
  }
  async function update() {
    const p = active(); if (!p) return;
    const opts = { templates: p.settings.template_ids?.length > 0, seed: p.settings.seed != null };
    try { replace(await api("PUT", `/api/presets/${p.id}`, { settings: snapshot(opts) })); toast(`Updated “${p.name}”`, "ok"); }
    catch (e) { toast(e.message, "err"); }
  }
  async function rename() {
    const p = active(); if (!p) return;
    const r = await nameDialog({ title: "Rename preset", value: p.name, ok: "Rename" });
    if (!r) return;
    try { replace(await api("PUT", `/api/presets/${p.id}`, { name: r.name })); } catch (e) { toast(e.message, "err"); }
  }
  async function remove() {
    const p = active(); if (!p) return;
    if (!(await confirmDialog(`“${p.name}” will be deleted. Your current settings stay as they are.`, { title: "Delete preset?" }))) return;
    try { await api("DELETE", `/api/presets/${p.id}`); presets = presets.filter((x) => x.id !== p.id); setActive(null); draw(); toast("Preset deleted", "ok"); }
    catch (e) { toast(e.message, "err"); }
  }
  function replace(p) { presets = presets.map((x) => (x.id === p.id ? p : x)).sort((a, b) => a.name.localeCompare(b.name)); draw(); }

  sel.addEventListener("change", () => {
    const p = presets.find((x) => String(x.id) === sel.value);
    if (p) apply(p); else { setActive(null); draw(); }
  });
  saveBtn.addEventListener("click", update);
  newBtn.addEventListener("click", saveNew);

  function draw() {
    const p = active();
    sel.replaceChildren(h("option", { value: "" }, presets.length ? "No preset" : "No presets yet"),
      ...presets.map((x) => h("option", { value: x.id, selected: x.id === activeId }, x.name)));
    sel.disabled = !presets.length;
    const mod = modified();
    dot.hidden = !mod;
    saveBtn.hidden = !mod;
    more.replaceChildren(p ? menuButton([
      { label: "Rename…", icon: "edit", fn: rename },
      { label: "Save as new preset…", icon: "plus", fn: saveNew },
      "sep",
      { label: "Delete preset", icon: "trash", danger: true, fn: remove },
    ], { title: "Preset actions" }) : "");
  }
  slot.refresh = () => { const mod = modified(); if (dot.hidden === mod) draw(); };
  slot.replaceChildren(h("span", { class: "preset-ic" }, icon("bookmark", "sm")), sel, dot, saveBtn, newBtn, more);
  api("GET", "/api/presets").then((list) => {
    presets = list;
    if (activeId && !active()) setActive(null);
    draw();
  }).catch(() => { slot.hidden = true; });
  draw();
}
