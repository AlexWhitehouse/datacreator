// App-wide state, domain constants and small shared widgets.
import { api, h } from "./ui.js";

export const state = { status: null, types: [], typeMap: {}, templates: [] };

/** Navigation hooks set by the current view. guard: { dirty(): bool, save(): Promise<bool> } */
export const nav = { guard: null, cleanup: null };

export async function loadTypes() {
  state.types = await api("GET", "/api/types");
  state.typeMap = Object.fromEntries(state.types.map((t) => [t.key, t]));
}
export async function loadTemplates() { state.templates = await api("GET", "/api/templates"); }

export const KIND_INFO = {
  picklist: ["Pick-list", "Choose randomly from a list of values, optionally weighted. AI can write the list.", "list"],
  pattern: ["Pattern", "Codes and reference numbers, e.g. MRN-####-??", "hash"],
  number_range: ["Number range", "A random number between a minimum and a maximum.", "sliders"],
  date_range: ["Date range", "A random date between two dates, relative to today, or relative to another field.", "calendar"],
  composite: ["Combined", "Build a value from other fields or types, e.g. {title} {first_name} {last_name}.", "link"],
  boolean: ["Yes / No", "True a set percentage of the time. Good for tick boxes.", "toggle"],
  ai: ["AI-generated text", "Free text written by AI to fit each record (e.g. clinical notes), or a pre-generated pool.", "sparkles"],
};
export const RENDER_INFO = {
  text: ["Single line", "Line"], multiline: ["Multi-line", "Multi"], comb: ["Character boxes", "Boxes"],
  checkbox: ["Tick box", "Tick"], signature: ["Signature", "Sign"],
};
export const PERSONS = {
  1: "Patient (person 1)", 2: "Person 2: relative (next of kin / carer)", 3: "Person 3: e.g. prescriber",
  4: "Person 4", 5: "Person 5", 6: "Person 6", 7: "Person 7", 8: "Person 8",
};
export const CATEGORY_ORDER = ["Person", "Contact", "Address", "GP", "Clinical", "Dates", "Other"];

export function typeLabel(key) {
  const t = state.typeMap[key];
  return t ? t.label : key ? `${key} (missing)` : "No type";
}

export function sortedCategories(cats) {
  return [...cats].sort((a, b) => {
    const ia = CATEGORY_ORDER.indexOf(a), ib = CATEGORY_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
}

export function typeSelect(value, onChange) {
  const groups = {};
  for (const t of state.types) (groups[t.category] ||= []).push(t);
  const sel = h("select", { onchange: (e) => onChange(e.target.value) },
    h("option", { value: "" }, "Choose a data type…"),
    sortedCategories(Object.keys(groups)).map((c) => h("optgroup", { label: c },
      groups[c].map((t) => h("option", { value: t.key, selected: t.key === value }, t.label)))));
  sel.value = value || "";
  return sel;
}

/** Template readiness, used on the templates list and the generate page. */
export function templateHealth(t) {
  const untyped = t.fields.filter((f) => !state.typeMap[f.type]).length;
  if (!t.fields.length) return { ok: false, kind: "warn", label: "No fields yet" };
  if (untyped) return { ok: false, kind: "err", label: `${untyped} field${untyped > 1 ? "s" : ""} need a type` };
  return { ok: true, kind: "ok", label: "Ready" };
}

/** Pre-select templates on the generate page, then go there. */
export function generateFrom(ids) {
  let s = {};
  try { s = JSON.parse(localStorage.getItem("dc.generate") || "{}"); } catch {}
  s.template_ids = ids;
  try { localStorage.setItem("dc.generate", JSON.stringify(s)); } catch {}
  location.hash = "#/generate";
}
