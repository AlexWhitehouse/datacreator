// Shared UI building blocks: DOM helper, API client, icons, toasts, modals, menus.

export const $ = (sel, root = document) => root.querySelector(sel);

export function h(tag, attrs = {}, ...children) {
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

export async function api(method, url, body) {
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

export const clone = (o) => JSON.parse(JSON.stringify(o));
export const uid = () => Math.random().toString(16).slice(2, 12);
export const plural = (n, word, many = `${word}s`) => `${Number(n).toLocaleString()} ${n === 1 ? word : many}`;
export const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
export const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
export function relTime(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 6) return `${Math.round(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------------ icons (24px grid, stroke)
const P = (d) => d.split("|").map((x) => `<path d="${x}"/>`).join("");
const ICONS = {
  upload: P("M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4|M17 8l-5-5-5 5|M12 3v12"),
  download: P("M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4|M7 10l5 5 5-5|M12 15V3"),
  file: P("M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z|M14 2v6h6"),
  layers: P("M12 2 2 7l10 5 10-5-10-5z|M2 17l10 5 10-5|M2 12l10 5 10-5"),
  edit: P("M12 20h9|M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"),
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/>' + P("M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"),
  trash: P("M3 6h18|M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6|M10 11v6|M14 11v6|M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"),
  plus: P("M12 5v14|M5 12h14"),
  chevL: P("m15 18-6-6 6-6"), chevR: P("m9 18 6-6-6-6"), chevD: P("m6 9 6 6 6-6"), chevU: P("m18 15-6-6-6 6"),
  arrowL: P("M19 12H5|M12 19l-7-7 7-7"), arrowR: P("M5 12h14|M12 5l7 7-7 7"),
  arrowUp: P("M12 19V5|M5 12l7-7 7 7"), arrowDown: P("M12 5v14|M19 12l-7 7-7-7"),
  zoomIn: '<circle cx="11" cy="11" r="7"/>' + P("m21 21-4.3-4.3|M11 8v6|M8 11h6"),
  zoomOut: '<circle cx="11" cy="11" r="7"/>' + P("m21 21-4.3-4.3|M8 11h6"),
  fit: P("M8 3H5a2 2 0 0 0-2 2v3|M21 8V5a2 2 0 0 0-2-2h-3|M3 16v3a2 2 0 0 0 2 2h3|M16 21h3a2 2 0 0 0 2-2v-3"),
  sparkles: P("M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z|M19 3v4|M17 5h4|M5 17v4|M3 19h4"),
  eye: P("M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z") + '<circle cx="12" cy="12" r="3"/>',
  check: P("M20 6 9 17l-5-5"),
  x: P("M18 6 6 18|M6 6l12 12"),
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  refresh: P("M21 12a9 9 0 1 1-3-6.7L21 8|M21 3v5h-5"),
  search: '<circle cx="11" cy="11" r="7"/>' + P("m21 21-4.3-4.3"),
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  alert: P("M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z|M12 9v4|M12 17h.01"),
  info: '<circle cx="12" cy="12" r="10"/>' + P("M12 16v-4|M12 8h.01"),
  checkCircle: '<circle cx="12" cy="12" r="10"/>' + P("m9 12 2 2 4-4"),
  xCircle: '<circle cx="12" cy="12" r="10"/>' + P("m15 9-6 6|M9 9l6 6"),
  ban: '<circle cx="12" cy="12" r="10"/>' + P("m4.9 4.9 14.2 14.2"),
  clock: '<circle cx="12" cy="12" r="10"/>' + P("M12 6v6l4 2"),
  folder: P("M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"),
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/>' + P("M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5|M3 12c0 1.7 4 3 9 3s9-1.3 9-3"),
  zap: P("M13 2 3 14h9l-1 8 10-12h-9z"),
  history: P("M3 12a9 9 0 1 0 3-6.7L3 8|M3 3v5h5|M12 7v5l4 2"),
  list: P("M8 6h13|M8 12h13|M8 18h13|M3 6h.01|M3 12h.01|M3 18h.01"),
  type: P("M4 7V4h16v3|M9 20h6|M12 4v16"),
  hash: P("M4 9h16|M4 15h16|M10 3 8 21|M16 3l-2 18"),
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/>' + P("M16 2v4|M8 2v4|M3 10h18"),
  link: P("M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7|M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"),
  grip: [[9, 6], [15, 6], [9, 12], [15, 12], [9, 18], [15, 18]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="1.4" fill="currentColor" stroke="none"/>`).join(""),
  toggle: '<rect x="2" y="6" width="20" height="12" rx="6"/><circle cx="16" cy="12" r="3"/>',
  sliders: P("M4 21v-7|M4 10V3|M12 21v-9|M12 8V3|M20 21v-5|M20 12V3|M1 14h6|M9 8h6|M17 16h6"),
  user: '<circle cx="12" cy="8" r="4"/>' + P("M4 21a8 8 0 0 1 16 0"),
  users: '<circle cx="9" cy="8" r="4"/>' + P("M2 21a7 7 0 0 1 14 0|M16 3.1a4 4 0 0 1 0 7.8|M22 21a7 7 0 0 0-4-6.3"),
  printer: P("M6 9V2h12v7|M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2") + '<rect x="6" y="14" width="12" height="8"/>',
  save: P("M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z|M17 21v-8H7v8|M7 3v5h8"),
  bookmark: P("M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"),
  undo: P("M3 7v6h6|M21 17a9 9 0 0 0-15-6.7L3 13"),
  pen: P("M12 19l7-7 3 3-7 7-3-3z|M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z|M2 2l7.6 7.6") + '<circle cx="11" cy="11" r="2"/>',
  box: '<rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="4 3"/>',
  percent: P("M19 5 5 19") + '<circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  shuffle: P("M16 3h5v5|M4 20 21 3|M21 16v5h-5|M15 15l6 6|M4 4l5 5"),
  external: P("M15 3h6v6|M10 14 21 3|M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"),
  keyboard: '<rect x="2" y="5" width="20" height="14" rx="2"/>' + P("M6 9h.01|M10 9h.01|M14 9h.01|M18 9h.01|M7 15h10"),
  settings: '<circle cx="12" cy="12" r="3"/>' + P("M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"),
  sun: '<circle cx="12" cy="12" r="4"/>' + P("M12 2v2|M12 20v2|m4.93 4.93 1.41 1.41|m17.66 17.66 1.41 1.41|M2 12h2|M20 12h2|m6.34 17.66-1.41 1.41|m19.07 4.93-1.41 1.41"),
  moon: P("M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"),
  monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/>' + P("M8 21h8|M12 17v4"),
  key: '<circle cx="7.5" cy="15.5" r="5.5"/>' + P("m21 2-9.6 9.6|m15.5 7.5 3 3L22 7l-3-3"),
  eyeOff: P("M9.9 4.2A10.9 10.9 0 0 1 12 4c6.5 0 10 8 10 8a18.5 18.5 0 0 1-2.2 3.2|M6.6 6.6A18.4 18.4 0 0 0 2 12s3.5 8 10 8a10.6 10.6 0 0 0 5.4-1.4|M9.9 9.9a3 3 0 0 0 4.2 4.2|M2 2l20 20"),
  plug: P("M12 22v-5|M9 8V2|M15 8V2|M18 8v5a6 6 0 0 1-12 0V8z"),
};
export function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", `i ${cls}`.trim());
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}

// ------------------------------------------------------------------ toasts
const TOAST_ICON = { ok: "checkCircle", err: "alert", "": "info" };
export function toast(msg, kind = "", opts = {}) {
  const t = h("div", { class: `toast ${kind}`, role: kind === "err" ? "alert" : "status" });
  const dismiss = () => {
    if (t.classList.contains("leaving")) return;
    t.classList.add("leaving");
    setTimeout(() => t.remove(), 220);
  };
  t.append(icon(TOAST_ICON[kind] ?? "info"), h("div", { class: "msg" }, msg));
  if (opts.action) t.append(h("button", { class: "act", onclick: () => { dismiss(); opts.action.fn(); } }, opts.action.label));
  t.append(h("button", { class: "x", title: "Dismiss", onclick: dismiss }, icon("x", "sm")));
  $("#toasts").append(t);
  const ms = opts.duration ?? (kind === "err" ? 7000 : opts.action ? 6000 : 3500);
  let timer = setTimeout(dismiss, ms);
  t.addEventListener("mouseenter", () => clearTimeout(timer));
  t.addEventListener("mouseleave", () => { timer = setTimeout(dismiss, 2000); });
  return dismiss;
}

// ------------------------------------------------------------------ modals (stackable)
const modalStack = [];
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && modalStack.length) { e.stopImmediatePropagation(); modalStack[modalStack.length - 1](); }
}, true);
export const modalOpen = () => modalStack.length > 0;

/** modal({title, sub, body, buttons, size, onClose}) -> close() */
export function modal({ title, sub, body, buttons = [], size = "", onClose } = {}) {
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    modalStack.splice(modalStack.indexOf(close), 1);
    backdrop.classList.add("leaving");
    setTimeout(() => backdrop.remove(), 200);
    onClose && onClose();
  };
  const backdrop = h("div", { class: "modal-backdrop", onmousedown: (e) => { if (e.target === backdrop) close(); } },
    h("div", { class: `modal ${size}`, role: "dialog", "aria-modal": "true" },
      title ? h("div", { class: "modal-head" },
        h("div", {}, h("h2", {}, title), sub ? h("div", { class: "sub" }, sub) : null),
        h("button", { class: "ghost icon sm", onclick: close, title: "Close (Esc)" }, icon("x"))) : null,
      h("div", { class: "modal-body" }, body),
      buttons.length ? h("div", { class: "modal-foot" }, buttons) : null));
  $("#modal-root").append(backdrop);
  modalStack.push(close);
  requestAnimationFrame(() => {
    const f = backdrop.querySelector(".modal-body input:not([disabled]), .modal-body textarea, .modal-foot button.primary, .modal-foot button.danger-solid");
    if (f) f.focus({ preventScroll: true });
  });
  return close;
}

/** Styled confirmation. Resolves true/false. */
export function confirmDialog(message, { title = "Are you sure?", ok = "Delete", tone = "danger" } = {}) {
  return new Promise((resolve) => {
    let close;
    const done = (v) => { close(); resolve(v); };
    close = modal({
      size: "sm",
      body: h("div", { class: "confirm-body" },
        h("div", { class: `bubble ${tone === "warn" ? "warn" : ""}` }, icon(tone === "warn" ? "alert" : "trash", "lg")),
        h("div", {}, h("h2", { style: { margin: "2px 0 6px" } }, title), h("p", { class: "muted", style: { margin: 0 } }, message))),
      buttons: [
        h("button", { onclick: () => done(false) }, "Cancel"),
        h("button", { class: tone === "danger" ? "danger-solid" : "primary", onclick: () => done(true) }, ok),
      ],
      onClose: () => resolve(false),
    });
  });
}

/** Unsaved-changes prompt. Resolves "save" | "discard" | null. */
export function leaveDialog() {
  return new Promise((resolve) => {
    let close, val = null;
    const done = (v) => { val = v; close(); };
    close = modal({
      size: "sm",
      body: h("div", { class: "confirm-body" },
        h("div", { class: "bubble warn" }, icon("alert", "lg")),
        h("div", {}, h("h2", { style: { margin: "2px 0 6px" } }, "Unsaved changes"),
          h("p", { class: "muted", style: { margin: 0 } }, "This template has changes that haven't been saved."))),
      buttons: [
        h("button", { class: "ghost", onclick: () => done(null) }, "Keep editing"),
        h("span", { class: "grow" }),
        h("button", { onclick: () => done("discard") }, "Discard"),
        h("button", { class: "primary", onclick: () => done("save") }, icon("save", "sm"), "Save & leave"),
      ],
      onClose: () => resolve(val),
    });
  });
}

/** Run an async action with a spinner on the button. */
export async function busy(btn, fn) {
  if (!btn) return fn();
  btn.classList.add("busy");
  btn.disabled = true;
  try { return await fn(); } finally { btn.classList.remove("busy"); btn.disabled = false; }
}

/** In-app PDF preview with "new sample" and "open in tab". fetchBlob() -> Blob */
export function previewModal({ title, sub, fetchBlob, extra }) {
  let url = null;
  const frame = h("iframe", { class: "preview-frame", title: "Preview" });
  const loading = h("div", { class: "preview-loading" }, h("div", { class: "box" }, h("span", { class: "spin" }), h("span", {}, "Generating a sample…")));
  const stage = h("div", { class: "preview-stage" }, frame, loading);
  const again = h("button", { onclick: () => load() }, icon("shuffle", "sm"), "New sample");
  const openTab = h("button", { class: "ghost", disabled: true, onclick: () => url && window.open(url, "_blank") }, icon("external", "sm"), "Open in new tab");
  async function load() {
    stage.append(loading);
    again.disabled = true;
    try {
      const blob = await fetchBlob();
      if (url) URL.revokeObjectURL(url);
      url = URL.createObjectURL(blob);
      frame.src = `${url}#view=FitH&toolbar=0&navpanes=0`;
      openTab.disabled = false;
      frame.onload = () => loading.remove();
      setTimeout(() => loading.remove(), 1500);
    } catch (e) {
      loading.replaceChildren(h("div", { class: "box" }, icon("alert", "xl"), h("span", {}, e.message)));
    } finally { again.disabled = false; }
  }
  const close = modal({
    title, sub, size: "xl", body: stage,
    buttons: [extra || null, h("span", { class: "grow" }), openTab, again, h("button", { class: "primary", onclick: () => close() }, "Done")].filter(Boolean),
    onClose: () => setTimeout(() => url && URL.revokeObjectURL(url), 30000),
  });
  load();
  return { close, reload: load };
}

/** Pop-over menu anchored to a button. items: [{label, icon, fn, danger}] or "sep" */
export function menuButton(items, { title = "More", cls = "ghost icon sm" } = {}) {
  // The menu lives on <body> with fixed positioning so cards with overflow:hidden can't clip it.
  const wrap = h("span", { class: "menu-wrap" });
  let menu = null;
  const close = () => {
    if (!menu) return;
    menu.remove(); menu = null;
    document.removeEventListener("mousedown", outside, true);
    window.removeEventListener("scroll", close, true);
    window.removeEventListener("resize", close);
    document.removeEventListener("keydown", onEsc, true);
  };
  const onEsc = (e) => { if (e.key === "Escape") { e.stopImmediatePropagation(); close(); btn.focus(); } };
  const outside = (e) => { if (!wrap.contains(e.target) && !menu?.contains(e.target)) close(); };
  const place = () => {
    const r = btn.getBoundingClientRect(), m = { width: menu.offsetWidth, height: menu.offsetHeight }; // offset* ignore the open animation's scale
    const up = r.bottom + 4 + m.height > window.innerHeight - 8 && r.top - 4 - m.height > 8;
    menu.classList.toggle("up", up);
    menu.style.top = `${up ? r.top - 4 - m.height : r.bottom + 4}px`;
    menu.style.left = `${Math.max(8, Math.min(r.right - m.width, window.innerWidth - m.width - 8))}px`;
  };
  const btn = h("button", {
    class: cls, title, "aria-haspopup": "menu",
    onclick: (e) => {
      e.stopPropagation();
      if (menu) return close();
      menu = h("div", { class: "menu", role: "menu" }, items.map((it) => it === "sep" ? h("hr") :
        h("button", { class: it.danger ? "danger" : "", role: "menuitem", onclick: (ev) => { ev.stopPropagation(); close(); it.fn(); } },
          it.icon ? icon(it.icon, "sm") : null, it.label)));
      document.body.append(menu);
      place();
      document.addEventListener("mousedown", outside, true);
      window.addEventListener("scroll", close, true);
      window.addEventListener("resize", close);
      document.addEventListener("keydown", onEsc, true);
    },
  }, icon("more"));
  wrap.append(btn);
  return wrap;
}

/** Animate a number in an element from its previous value. */
export function countTo(el, to, fmt = (n) => n.toLocaleString()) {
  const from = +(el.dataset.n ?? 0);
  el.dataset.n = to;
  if (from === to || reducedMotion()) { el.textContent = fmt(to); return; }
  const t0 = performance.now(), dur = 420;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = fmt(Math.round(from + (to - from) * e));
    if (k < 1 && el.dataset.n == to) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

export function emptyState({ icon: ic = "file", title, text, actions = [] }) {
  return h("div", { class: "empty" },
    h("div", { class: "bubble" }, icon(ic, "xl")),
    h("h3", {}, title), text ? h("p", {}, text) : null,
    actions.length ? h("div", { class: "row" }, actions) : null);
}

export function notice(kind, ...children) {
  const ic = { warn: "alert", err: "alert", ok: "checkCircle", plain: "info" }[kind] || "info";
  return h("div", { class: `notice ${kind}` }, icon(ic), h("div", { class: "grow" }, ...children));
}

export function switchEl(checked, onChange, label) {
  return h("label", { class: "switch" },
    h("input", { type: "checkbox", checked, onchange: (e) => onChange(e.target.checked) }),
    h("span", { class: "track" }), label ? h("span", {}, label) : null);
}

export function setRangeFill(input) {
  const min = +input.min || 0, max = +input.max || 100;
  input.style.setProperty("--p", `${((+input.value - min) / (max - min)) * 100}%`);
}
