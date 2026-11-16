// Runs: live progress, results and downloads. Also a global watcher for the nav badge + finish toasts.
import { $, api, confirmDialog, debounce, emptyState, fmtDate, h, icon, menuButton, plural, relTime, store, toast } from "../ui.js";
import { loadTemplates, nav, state } from "../state.js";
import { documentTotal, duplicatedSets } from "./generate.js";

const ACTIVE = ["queued", "running", "cancelling"];
const isActive = (r) => ACTIVE.includes(r.status);

// ------------------------------------------------------------------ global watcher
let known = null;          // id -> status of the runs that were active at the previous poll
let timer = null;
const listeners = new Set();

/** Poll the active runs while any exist or a view is listening. Safe to call any time.
 *  Listeners get (activeRuns, changed): changed is true when a run started, finished or changed status. */
export async function watchRuns() {
  clearTimeout(timer);
  let active;
  try { active = await api("GET", "/api/runs/active"); } catch { timer = setTimeout(watchRuns, 5000); return; }
  let changed = !known;
  if (known) {
    const now = new Map(active.map((r) => [r.id, r.status]));
    for (const [id, prev] of known) {
      if (now.has(id)) { if (now.get(id) !== prev) changed = true; continue; }
      changed = true;
      api("GET", `/api/runs/${id}`).then(finished).catch(() => {}); // gone: finished, or deleted
    }
    for (const id of now.keys()) if (!known.has(id)) changed = true;
  }
  known = new Map(active.map((r) => [r.id, r.status]));
  const badge = $("#runs-badge");
  if (badge) { badge.classList.toggle("on", active.length > 0); badge.lastChild.textContent = active.length; }
  for (const fn of listeners) fn(active, changed);
  if (active.length || listeners.size) timer = setTimeout(watchRuns, active.length ? 1000 : 5000);
}

function finished(r) {
  if (location.hash === "#/runs") return; // the card shows it
  if (r.status === "done") toast(`Run finished: ${plural(r.total || 0, "document")}`, "ok", { duration: 10000, action: { label: "Download", fn: () => (location.href = `/api/runs/${r.id}/download`) } });
  else if (r.status === "failed") toast(`Run failed: ${r.message || "see Runs"}`, "err", { action: { label: "View", fn: () => (location.hash = "#/runs") } });
}

// ------------------------------------------------------------------ view
const PAGE = 25;
const FILTERS = [["", "All"], ["active", "Active"], ["done", "Done"], ["failed", "Failed"], ["cancelled", "Cancelled"]];

export async function viewRuns(view) {
  await loadTemplates();
  const list = h("div", { class: "run-list" });
  const q = { text: sessionStorage.getItem("dc.runs.q") || "", status: sessionStorage.getItem("dc.runs.status") || "", offset: 0 };
  let page = null;      // last /api/runs response
  let fetchSeq = 0;
  const expanded = new Set(store.get("dc.expanded", []));
  const cards = new Map(); // id -> {el, sig, refs}
  const freshId = sessionStorage.getItem("dc.freshRun");
  sessionStorage.removeItem("dc.freshRun");
  let first = true;

  async function fetchPage() {
    const mine = ++fetchSeq;
    const params = new URLSearchParams({ q: q.text, status: q.status, limit: PAGE, offset: q.offset });
    let res;
    try { res = await api("GET", `/api/runs?${params}`); } catch (e) { toast(e.message, "err"); return; }
    if (mine !== fetchSeq) return; // a newer search is on its way
    if (!res.items.length && res.total && q.offset) { q.offset = Math.max(0, Math.floor((res.total - 1) / PAGE) * PAGE); return fetchPage(); }
    page = res;
    update(res.items);
    drawToolbar();
  }

  function update(runs) {
    if (!runs.length) {
      cards.clear();
      const searching = q.text || q.status;
      list.replaceChildren(h("div", { class: "card" }, searching
        ? emptyState({ icon: "search", title: "No matching runs", text: "Try other words, or a different status. Search matches template names, seeds, run ids and messages.",
          actions: [h("button", { onclick: () => { search.value = ""; q.text = ""; q.status = ""; q.offset = 0; remember(); fetchPage(); } }, "Clear search")] })
        : emptyState({
          icon: "history", title: "No runs yet", text: "Runs appear here with live progress. Each one downloads as a zip with the documents and their ground truth.",
          actions: [h("a", { class: "btn primary", href: "#/generate" }, icon("zap", "sm"), "Generate documents")],
        })));
      return;
    }
    if (!cards.size) list.replaceChildren();
    const seen = new Set();
    let prevEl = null;
    for (const r of runs) {
      seen.add(r.id);
      const sig = JSON.stringify([r.status, !!r.summary, r.message && !isActive(r) ? r.message : ""]);
      let c = cards.get(r.id);
      if (!c || c.sig !== sig) {
        const next = buildCard(r);
        if (c) c.el.replaceWith(next.el); else (prevEl ? prevEl.after(next.el) : list.prepend(next.el));
        if (!c && !first) next.el.classList.add("fresh");
        if (r.id === freshId && first) next.el.classList.add("fresh");
        c = { ...next, sig };
        cards.set(r.id, c);
      }
      if (isActive(r)) updateProgress(c.refs, r);
      prevEl = c.el;
    }
    for (const [id, c] of cards) if (!seen.has(id)) { c.el.remove(); cards.delete(id); }
    first = false;
  }

  function updateProgress(refs, r) {
    if (!refs.bar) return;
    const pct = r.total ? Math.round((100 * r.progress) / r.total) : 0;
    refs.bar.style.width = `${Math.max(2, pct)}%`;
    refs.pct.textContent = `${pct}%`;
    refs.msg.textContent = r.status === "cancelling" ? "Cancelling…"
      : r.status === "queued" ? queuedText(r)
      : r.message || "Working…";
  }

  function buildCard(r) {
    const s = r.settings, sum = r.summary || {};
    const names = sum.templates || s.template_ids.map((id) => state.templates.find((t) => t.id === id)?.name || `#${id}`);
    const docs = documentTotal(s, names.length);
    const open = expanded.has(r.id);
    const issues = (sum.warnings?.length || 0) + (sum.ai_errors?.length || 0);
    const toggle = () => {
      const o = !expanded.has(r.id);
      o ? expanded.add(r.id) : expanded.delete(r.id);
      store.set("dc.expanded", [...expanded]);
      coll.classList.toggle("open", o);
      chev.style.transform = o ? "rotate(180deg)" : "";
      chev.title = o ? "Hide details" : "Show details";
    };
    const refs = {};
    let coll;
    const statusIc = isActive(r) ? h("div", { class: "run-ic active" }, h("span", { class: "spin" }))
      : h("div", { class: `run-ic ${r.status}` }, icon({ done: "check", failed: "x", cancelled: "ban" }[r.status] || "clock", "lg"));

    const statusPill = r.status === "done"
      ? (issues ? h("span", { class: "pill warn" }, icon("alert", "sm"), plural(issues, "issue")) : h("span", { class: "pill ok" }, h("span", { class: "dot" }), "Done"))
      : h("span", { class: `pill ${{ failed: "err", cancelled: "" }[r.status] ?? "accent"}` }, h("span", { class: "dot" }), r.status[0].toUpperCase() + r.status.slice(1));

    const actions = [];
    if (r.status === "done") actions.push(h("a", { class: "btn sm primary", href: `/api/runs/${r.id}/download` }, icon("download", "sm"), "Download"));
    if (["queued", "running"].includes(r.status)) {
      actions.push(h("button", { class: "sm", onclick: async (e) => { e.currentTarget.disabled = true; await api("POST", `/api/runs/${r.id}/cancel`); watchRuns(); } }, icon("x", "sm"), "Cancel"));
    }
    const chev = h("button", { class: "sm ghost icon", title: open ? "Hide details" : "Show details", onclick: toggle, style: { transform: open ? "rotate(180deg)" : "" } }, icon("chevD"));
    actions.push(chev);
    if (!isActive(r)) actions.push(menuButton([
      { label: "Reuse settings (new data)", icon: "refresh", fn: () => reuse(s, false) },
      { label: `Reproduce exactly (seed ${s.seed})`, icon: "copy", fn: () => reuse(s, true) },
      "sep",
      { label: "Delete run", icon: "trash", danger: true, fn: () => remove(r) },
    ]));

    const detailItems = [
      ["Seed", s.seed],
      ["Missing", `${s.missing_min ?? s.missing_pct}${s.missing_max !== undefined && s.missing_max !== s.missing_min ? `–${s.missing_max}` : ""}%`],
      ["Duplicates", s.duplicate ? `${s.duplicate_pct}% of sets at ${s.duplicate_levels.join("/")}%` : "Off"],
      ["Handwriting", `${s.handwritten_pct}% (${s.handwriting_mode === "document" ? "per document" : "per field"})`],
      ["Scan", `${s.scan} · ${s.colour}`],
      ["OCR stress", [s.crossouts && `crossings-out ${s.crossouts_pct}%`, s.overflow && `spilling ${s.overflow_pct}%`, s.typos && `typos ${s.typos_pct}%`].filter(Boolean).join(" · ") || "Off"],
      ["Files", `${s.formats.map((f) => f.toUpperCase()).join(" + ")} @ ${s.dpi} dpi`],
      ["Layout", `${s.combine ? "combined, " : ""}${s.layout === "flat" ? "one folder" : "folder per set"}`],
      ["Patient age", `${s.age_min}–${s.age_max}`],
      sum.seconds !== undefined ? ["Took", `${sum.seconds}s${sum.ai_calls ? ` · ${plural(sum.ai_calls, "AI call")}` : ""}`] : null,
    ].filter(Boolean);

    const el = h("div", { class: "run-card" },
      h("div", { class: "run-main" },
        statusIc,
        h("div", { class: "grow" },
          h("div", { class: "run-title" }, names.join(" + ")),
          h("div", { class: "run-sub" },
            statusPill,
            h("span", { title: fmtDate(r.created_at) }, relTime(r.created_at)),
            h("span", { class: "sep" }),
            h("span", {}, [names.length < 2 ? "" : s.combine ? `${names.length} forms per document` : `${plural(s.count, "set")} × ${names.length}`,
              s.duplicate ? `${duplicatedSets(s)} sets × ${s.duplicate_levels.length} versions` : ""].filter(Boolean).join(" · ") || `seed ${s.seed}`))),
        h("div", { class: "run-stats" },
          h("div", { class: "run-stat" }, h("div", { class: "n" }, docs.toLocaleString()), h("div", { class: "l" }, docs === 1 ? "document" : "documents")),
          sum.seconds !== undefined ? h("div", { class: "run-stat" }, h("div", { class: "n" }, `${sum.seconds}s`), h("div", { class: "l" }, "time")) : null),
        h("div", { class: "run-acts" }, actions)),
      isActive(r) ? h("div", { class: "run-progress" },
        h("div", { class: "progress" }, (refs.bar = h("div", { style: { width: "2%" } }))),
        h("div", { class: "meta" }, (refs.msg = h("span", {})), h("b", {}, (refs.pct = h("span", {}, "0%"))))) : null,
      !isActive(r) && r.status !== "done" && r.message ? h("div", { class: "run-msg" }, r.message) : null,
      (coll = h("div", { class: `collapse${open ? " open" : ""}` }, h("div", {},
        h("div", { class: "run-detail" },
          h("dl", {}, detailItems.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, String(v))))),
          sum.output_dir ? h("div", { class: "block" }, h("dt", {}, "Folder"), h("dd", {}, h("code", {}, sum.output_dir))) : null,
          sum.linked_fields?.length ? h("div", { class: "block" }, h("dt", {}, "Linked fields"), h("dd", { class: "chips", style: { marginTop: "4px" } }, sum.linked_fields.map((f) => h("code", {}, f)))) : null,
          sum.warnings?.length ? h("div", { class: "block" }, h("dt", {}, "Warnings"), h("ul", {}, sum.warnings.map((w) => h("li", {}, w)))) : null,
          sum.ai_errors?.length ? h("div", { class: "block" }, h("dt", {}, "AI errors (fields left blank and marked in ground truth)"), h("ul", {}, sum.ai_errors.map((w) => h("li", {}, w)))) : null,
          h("div", { class: "block muted tiny mono" }, `Run ${r.id} · ${fmtDate(r.created_at)}`))))));
    return { el, refs };
  }

  function reuse(settings, exact) {
    const next = { ...settings, seed: exact ? settings.seed : null };
    store.set("dc.generate", next);
    toast(exact ? `Settings loaded with seed ${settings.seed}: generating gives the same documents.` : "Settings loaded with a random seed.", "ok");
    location.hash = "#/generate";
  }

  async function remove(r) {
    if (!(await confirmDialog("The run's documents and ground truth files will be deleted from disk.", { title: "Delete this run?" }))) return;
    try {
      await api("DELETE", `/api/runs/${r.id}`);
      const c = cards.get(r.id);
      if (c) await c.el.animate([{ opacity: 1 }, { opacity: 0, transform: "translateX(12px)" }], { duration: 180, easing: "ease-in" }).finished;
      toast("Run deleted", "ok");
      fetchPage();
    } catch (e) { toast(e.message, "err"); }
  }

  // ---- search, status filter and paging
  const remember = () => { try { sessionStorage.setItem("dc.runs.q", q.text); sessionStorage.setItem("dc.runs.status", q.status); } catch {} };
  const search = h("input", { type: "search", placeholder: "Search template, seed, run id, message…", value: q.text, "aria-label": "Search runs" });
  const runSearch = debounce(() => { q.text = search.value.trim(); q.offset = 0; remember(); fetchPage(); }, 250);
  search.addEventListener("input", runSearch);
  search.addEventListener("keydown", (e) => { if (e.key === "Escape" && search.value) { search.value = ""; runSearch(); } });
  const seg = h("div", { class: "seg sm", role: "group", "aria-label": "Filter by status" });
  const pager = h("div", { class: "pager" });
  const toolbar = h("div", { class: "toolbar-row runs-toolbar" }, h("div", { class: "input-icon" }, icon("search", "sm"), search), seg, h("span", { class: "grow" }));
  const footer = h("div", { class: "runs-footer" }, pager);

  function drawToolbar() {
    const counts = page?.counts || {};
    seg.replaceChildren(...FILTERS.map(([k, label]) => h("button", {
      class: q.status === k ? "on" : "", "aria-pressed": String(q.status === k),
      onclick: () => { if (q.status === k) return; q.status = k; q.offset = 0; remember(); fetchPage(); },
    }, label, counts[k || "all"] !== undefined ? h("span", { class: "seg-count" }, counts[k || "all"].toLocaleString()) : null)));
    const total = page?.total || 0;
    toolbar.hidden = !total && !q.text && !q.status;
    footer.hidden = total <= PAGE;
    if (total <= PAGE) { pager.replaceChildren(); return; }
    const from = q.offset + 1, to = Math.min(q.offset + PAGE, total);
    const go = (off) => { q.offset = off; fetchPage().then(() => list.scrollIntoView({ block: "start", behavior: "smooth" })); };
    pager.replaceChildren(
      h("span", { class: "muted small" }, `${from.toLocaleString()}–${to.toLocaleString()} of ${total.toLocaleString()}`),
      h("button", { class: "sm", disabled: q.offset === 0, onclick: () => go(Math.max(0, q.offset - PAGE)) }, icon("chevL", "sm"), "Newer"),
      h("button", { class: "sm", disabled: to >= total, onclick: () => go(q.offset + PAGE) }, "Older", icon("chevR", "sm")));
  }

  // the watcher reports the active runs: refresh the page when something starts, finishes or changes status,
  // otherwise just move the progress bars of the active runs shown on this page
  function onActive(active, changed) {
    if (changed) { fetchPage(); return; }
    for (const r of active) { const c = cards.get(r.id); if (c) updateProgress(c.refs, r); }
  }

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Runs"), h("p", { class: "muted" }, "Generated batches, run one at a time in order. Each download includes the documents, ground_truth.json/.csv and records.csv.")),
      h("a", { class: "btn primary", href: "#/generate" }, icon("plus", "sm"), "New run")),
    toolbar, list, footer);
  toolbar.hidden = true;
  footer.hidden = true;
  await fetchPage();
  listeners.add(onActive);
  nav.cleanup = () => listeners.delete(onActive);
  watchRuns();
}

function queuedText(r) {
  if (!r.queue_position) return "Queued…";
  return r.queue_position === 1 ? "Queued: starts when the current run finishes" : `Queued: ${r.queue_position - 1} ahead of it`;
}
