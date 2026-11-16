// DataCreator front end: plain ES modules, no build step.
import { $, api, h, icon, leaveDialog, notice } from "./js/ui.js";
import { loadTypes, nav, state } from "./js/state.js";
import { viewTemplates } from "./js/views/templates.js";
import { viewEditor } from "./js/views/editor.js";
import { viewTypes } from "./js/views/types.js";
import { viewGenerate } from "./js/views/generate.js";
import { viewRuns, watchRuns } from "./js/views/runs.js";
import { mountUpdateButton } from "./js/update.js";
import { viewSettings } from "./js/views/settings.js";
import { themeToggle } from "./js/theme.js";

// ------------------------------------------------------------------ router
const routes = [
  { re: /^#\/templates\/(\d+)$/, fn: (v, m) => viewEditor(v, +m[1]), full: true },
  { re: /^#\/templates$/, fn: (v) => viewTemplates(v), skel: "grid" },
  { re: /^#\/types$/, fn: (v) => viewTypes(v), skel: "grid" },
  { re: /^#\/generate$/, fn: (v) => viewGenerate(v), skel: "form" },
  { re: /^#\/runs$/, fn: (v) => viewRuns(v), skel: "list" },
  { re: /^#\/settings$/, fn: (v) => viewSettings(v), skel: "list" },
];
let lastHash = null;
let seq = 0;

async function route() {
  const hash = location.hash || "#/templates";
  const changed = hash !== lastHash;
  if (changed && nav.guard?.dirty()) {
    history.replaceState(null, "", lastHash);
    const choice = await leaveDialog();
    if (!choice) return;
    if (choice === "save" && !(await nav.guard.save())) return;
    history.replaceState(null, "", hash);
  }
  const r = routes.find((x) => x.re.test(hash));
  if (!r) { location.hash = "#/templates"; return; }
  nav.guard = null;
  if (nav.cleanup) { nav.cleanup(); nav.cleanup = null; }
  lastHash = hash;
  setActiveTab(hash.split("/")[1]);
  document.querySelector(".topbar-gear")?.classList.toggle("on", hash === "#/settings");

  const mine = ++seq;
  const main = $("#view");
  const view = h("div", { class: changed ? "view-enter" : "" });
  const skelTimer = setTimeout(() => {
    if (mine !== seq || !changed) return;
    main.classList.toggle("full", !!r.full);
    main.replaceChildren(skeleton(r.skel));
  }, 150);
  try { await r.fn(view, hash.match(r.re)); }
  catch (e) { view.append(notice("err", `Couldn't load this page: ${e.message}`)); }
  clearTimeout(skelTimer);
  if (mine !== seq) return;
  main.classList.toggle("full", !!r.full);
  main.replaceChildren(view);
  if (changed) window.scrollTo(0, 0);
}
nav.rerender = route;
window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", (e) => { if (nav.guard?.dirty()) { e.preventDefault(); e.returnValue = ""; } });

function skeleton(kind) {
  if (!kind) return h("div");
  const bar = (w, ht = 14, mt = 0) => h("div", { class: "skel", style: { width: w, height: `${ht}px`, marginTop: `${mt}px` } });
  const head = h("div", { class: "page-head" }, h("div", {}, bar("180px", 24), bar("420px", 13, 10)));
  if (kind === "grid") return h("div", { class: "skel-page" }, head, h("div", { class: "tpl-grid" }, [1, 2, 3, 4].map(() => h("div", { class: "skel", style: { height: "320px", borderRadius: "14px" } }))));
  if (kind === "list") return h("div", { class: "skel-page" }, head, h("div", { class: "run-list" }, [1, 2, 3].map(() => h("div", { class: "skel", style: { height: "76px", borderRadius: "14px" } }))));
  return h("div", { class: "skel-page" }, head, h("div", { class: "gen-layout" },
    h("div", { class: "stack lg" }, [1, 2, 3].map(() => h("div", { class: "skel", style: { height: "180px", borderRadius: "14px" } }))),
    h("div", { class: "skel", style: { height: "420px", borderRadius: "14px" } })));
}

// ------------------------------------------------------------------ nav
const TABS = [["templates", "Templates", "layers"], ["types", "Data types", "database"], ["generate", "Generate", "zap"], ["runs", "Runs", "history"]];
const pill = h("span", { class: "tab-pill" });
const underline = h("span", { class: "tab-underline" });

function buildNav() {
  const tabs = $(".tabs");
  tabs.replaceChildren(pill, underline, ...TABS.map(([k, label, ic]) => h("a", { href: `#/${k}`, "data-tab": k, "aria-label": label }, icon(ic, "sm"), h("span", { class: "tab-label" }, label),
    k === "runs" ? h("span", { class: "nav-badge", id: "runs-badge", title: "Runs in progress" }, h("span", { class: "spin" }), h("span", {}, "0")) : null)));
  // re-measure when tab widths change (e.g. the web font finishing loading after first paint)
  new ResizeObserver(() => setActiveTab(null)).observe(tabs);
  document.fonts?.ready.then(() => setActiveTab(null));
  window.addEventListener("resize", () => setActiveTab(null));
}

let activeTab = null;
function setActiveTab(tab) {
  if (tab) activeTab = tab;
  const links = document.querySelectorAll(".tabs a");
  let on = null;
  links.forEach((a) => { const m = a.dataset.tab === activeTab; a.classList.toggle("active", m); if (m) on = a; });
  if (!on) { pill.style.opacity = 0; underline.style.opacity = 0; return; }
  for (const el of [pill, underline]) {
    el.style.opacity = 1;
    el.style.left = `${on.offsetLeft}px`;
    el.style.width = `${on.offsetWidth}px`;
  }
}

// ------------------------------------------------------------------ AI status chip
const chipSlot = h("span", { class: "chip-slot" });
async function refreshStatus() {
  state.status = await api("GET", "/api/status");
  chipSlot.replaceChildren(state.status.ai_available
    ? h("a", { class: "ai-chip", href: "#/settings", title: `AI connected: ${state.status.ai_model}` }, h("span", { class: "dot" }), "AI connected")
    : h("a", { class: "ai-chip off", href: "#/settings", title: "Add an AI key in Settings to enable AI" }, h("span", { class: "dot" }), "AI off"));
}
nav.refreshStatus = refreshStatus;

// ------------------------------------------------------------------ boot
async function boot() {
  buildNav();
  const right = $("#ai-status");
  right.append(chipSlot, themeToggle(),
    h("a", { class: "btn ghost icon sm topbar-gear", href: "#/settings", title: "Settings", "aria-label": "Settings" }, icon("settings", "sm")));
  try {
    await refreshStatus();
  } catch (e) {
    $("#view").append(notice("err", `Can't reach the DataCreator server: ${e.message}`));
    return;
  }
  mountUpdateButton(right);
  await loadTypes();
  await route();
  watchRuns();
}
boot();
