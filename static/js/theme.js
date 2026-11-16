// Light / dark / system theme. The preference lives in localStorage ("dc-theme"); "system" is stored as
// no value, follows prefers-color-scheme through CSS, and leaves <html> without a data-theme attribute.
// index.html applies a stored choice before first paint, so there's no flash of the wrong theme.
import { h, icon } from "./ui.js";

const KEY = "dc-theme";
export const THEMES = [["system", "System", "monitor"], ["light", "Light", "sun"], ["dark", "Dark", "moon"]];
const listeners = new Set();

export function getTheme() {
  try { const v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : "system"; }
  catch { return "system"; }
}

function apply(t) {
  const root = document.documentElement;
  // Switch without every button and panel animating its colours at once.
  root.classList.add("theme-switching");
  if (t === "system") delete root.dataset.theme; else root.dataset.theme = t;
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
  listeners.forEach((fn) => fn(t));
}

export function setTheme(t) {
  if (!THEMES.some(([k]) => k === t)) t = "system";
  try { if (t === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch {}
  apply(t);
}

/** Call fn(theme) whenever the preference changes (here or in another tab). Returns an unsubscribe. */
export function onThemeChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

window.addEventListener("storage", (e) => { if (e.key === KEY || e.key === null) apply(getTheme()); });

/** Topbar button that cycles System → Light → Dark. */
export function themeToggle() {
  const btn = h("button", { class: "ghost icon sm theme-toggle", onclick: () => {
    const i = THEMES.findIndex(([k]) => k === getTheme());
    setTheme(THEMES[(i + 1) % THEMES.length][0]);
  } });
  const paint = (t) => {
    const i = THEMES.findIndex(([k]) => k === t);
    const [, label, ic] = THEMES[i];
    const next = THEMES[(i + 1) % THEMES.length][1];
    btn.replaceChildren(icon(ic, "sm"));
    btn.title = `Theme: ${label} (click for ${next})`;
    btn.setAttribute("aria-label", btn.title);
  };
  paint(getTheme());
  onThemeChange(paint);
  return btn;
}

/** Segmented System / Light / Dark control for the Settings page. */
export function themeSelector() {
  const seg = h("div", { class: "seg", role: "radiogroup", "aria-label": "Theme" });
  const paint = (t) => seg.querySelectorAll("button").forEach((b) => {
    const on = b.dataset.v === t;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
  seg.append(...THEMES.map(([k, label, ic]) => h("button", { type: "button", role: "radio", "data-v": k, onclick: () => setTheme(k) }, icon(ic, "sm"), label)));
  paint(getTheme());
  const off = onThemeChange((t) => (seg.isConnected ? paint(t) : off())); // drops itself once the view is gone
  return seg;
}
