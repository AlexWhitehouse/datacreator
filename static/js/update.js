// Header update button + dialog: check GitHub for a newer version, then update and restart in place.
import { api, busy, h, icon, modal, notice, plural, relTime, toast } from "./ui.js";
import { nav, state } from "./state.js";

const RECHECK_MS = 30 * 60 * 1000;
let info = null;     // last /api/update response
let btn = null;
let dialog = null;   // { close, render } while the dialog is open

export function mountUpdateButton(container) {
  btn = h("button", { class: "update-btn ghost icon sm", title: "Check for updates", onclick: () => openDialog() }, icon("refresh", "sm"));
  container.prepend(btn);
  refresh(false);
  setInterval(() => refresh(false), RECHECK_MS);
}

async function refresh(force) {
  try { info = await api("GET", `/api/update${force ? "?force=1" : ""}`); }
  catch (e) { info = { supported: true, error: e.message, commits: [] }; }
  paintButton();
  if (dialog) dialog.render();
  return info;
}

function paintButton() {
  if (!btn) return;
  if (info && !info.supported) { btn.style.display = "none"; return; }
  btn.style.display = "";
  const ready = info && (info.available || info.pending_restart);
  btn.className = ready ? "update-btn ready sm" : "update-btn ghost icon sm";
  btn.replaceChildren(...(ready
    ? [h("span", { class: "pulse-dot" }), info.pending_restart && !info.available ? "Restart to update" : `Update${info.behind > 1 ? ` · ${info.behind}` : ""}`]
    : [icon("refresh", "sm")]));
  btn.title = !info ? "Check for updates"
    : info.error ? `Couldn't check for updates: ${info.error}`
      : ready ? `${plural(info.behind, "new change")} on GitHub` : `Up to date (${info.current?.short || "unknown version"})`;
}

// ------------------------------------------------------------------ dialog
function openDialog() {
  const body = h("div");
  const foot = h("div", { class: "row", style: { width: "100%" } });
  let phase = "info"; // info | updating
  const close = modal({
    title: "Updates", sub: versionLine(), size: "", body,
    buttons: [foot],
    onClose: () => { dialog = null; },
  });
  dialog = { close, render: () => { if (phase === "info") render(); } };
  render();
  if (!info || Date.now() - new Date(info.checked_at || 0).getTime() > 60_000) checkNow();

  async function checkNow(b) {
    const run = () => refresh(true);
    if (b) await busy(b, run); else { body.replaceChildren(loading("Checking GitHub…")); await run(); }
  }

  function render() {
    body.parentElement?.parentElement?.querySelector(".modal-head .sub")?.replaceChildren(versionLine());
    const later = h("button", { class: "ghost", onclick: () => close() }, "Close");
    const again = h("button", { onclick: (e) => checkNow(e.currentTarget) }, icon("refresh", "sm"), "Check again");
    if (!info) { body.replaceChildren(loading("Checking GitHub…")); foot.replaceChildren(h("span", { class: "grow" }), later); return; }
    if (info.error) {
      body.replaceChildren(notice("err", h("strong", {}, "Couldn't check for updates. "), info.error));
      foot.replaceChildren(h("span", { class: "grow" }), later, again);
      return;
    }
    if (!info.available && !info.pending_restart) {
      body.replaceChildren(h("div", { class: "update-ok" },
        h("div", { class: "bubble" }, icon("check", "lg")),
        h("div", {}, h("div", { class: "strong" }, "You're up to date"),
          h("div", { class: "muted small" }, `Running the latest ${info.branch || "version"} from GitHub. Checked ${relTime(info.checked_at)}.`,
            info.ahead ? ` This copy also has ${plural(info.ahead, "local commit")} not on GitHub.` : ""))));
      foot.replaceChildren(again, h("span", { class: "grow" }), h("button", { class: "primary", onclick: () => close() }, "Done"));
      return;
    }

    const dirtyEditor = nav.guard?.dirty();
    const blocked = info.blocker || info.active_runs;
    const shown = info.commits.slice(0, 8);
    body.replaceChildren(h("div", { class: "stack lg" },
      info.available ? h("div", {},
        h("div", { class: "label" }, `${plural(info.behind, "new change")} on GitHub`, h("span", { class: "val mono" }, `${info.current?.short} → ${info.latest}`)),
        h("ul", { class: "commit-list" }, shown.map((c) => h("li", {},
          h("span", { class: "subj" }, c.subject),
          h("span", { class: "meta" }, h("code", {}, c.sha), relTime(c.date))))),
        info.behind > shown.length ? h("div", { class: "muted small", style: { marginTop: "6px" } }, `and ${info.behind - shown.length} more`) : null)
        : notice("", "The new version is already downloaded. Restart the server to start using it."),
      info.blocker ? notice("warn", info.blocker) : null,
      info.active_runs ? notice("warn", `${plural(info.active_runs, "run is", "runs are")} in progress. Updating restarts the server, so wait for ${info.active_runs > 1 ? "them" : "it"} to finish.`) : null,
      !blocked && dirtyEditor ? notice("warn", "This template has unsaved changes. They'll be saved before updating.") : null,
      !blocked ? h("p", { class: "muted small", style: { margin: 0 } }, "The server restarts and this page reloads by itself; it takes a few seconds. Templates, data types and runs are kept.") : null));
    const go = h("button", { class: "primary", disabled: !!blocked, onclick: () => startUpdate() }, icon("download", "sm"),
      info.available ? (dirtyEditor ? "Save & update" : "Update & restart") : "Restart now");
    foot.replaceChildren(again, h("span", { class: "grow" }), later, go);
  }

  async function startUpdate() {
    if (nav.guard?.dirty() && !(await nav.guard.save())) return;
    phase = "updating";
    const steps = [
      ["download", info.available ? "Download changes from GitHub" : "Use the downloaded version"],
      ["deps", "Install updated packages"],
      ["restart", "Restart the server"],
      ["reload", "Reload this page"],
    ];
    const el = {};
    const list = h("ol", { class: "steps" }, steps.map(([k, label], i) => (el[k] = h("li", { class: "pending" }, h("span", { class: "ic" }, i + 1), h("span", { class: "t" }, label)))));
    const status = h("div", { class: "muted small", style: { marginTop: "14px", minHeight: "18px" } });
    body.replaceChildren(list, status);
    foot.replaceChildren(h("span", { class: "grow" }), h("span", { class: "muted small" }, "Please keep this tab open…"));
    const set = (k, st, note) => {
      el[k].className = st;
      el[k].querySelector(".ic").replaceChildren(st === "active" ? h("span", { class: "spin" }) : st === "done" ? icon("check", "sm") : st === "error" ? icon("x", "sm") : st === "skipped" ? "–" : String(steps.findIndex(([x]) => x === k) + 1));
      if (note !== undefined) { const n = el[k].querySelector(".note") || el[k].appendChild(h("span", { class: "note" })); n.textContent = note; }
    };

    set("download", "active");
    let result;
    try {
      result = await api("POST", "/api/update");
    } catch (e) {
      set("download", "error");
      status.replaceChildren(notice("err", e.message));
      foot.replaceChildren(h("span", { class: "grow" }), h("button", { onclick: () => { phase = "info"; refresh(true); } }, "Back"));
      return;
    }
    set("download", "done", result.version ? result.version.sha.slice(0, 7) : "");
    set("deps", result.deps_updated ? "done" : "skipped", result.deps_updated ? "" : "no changes");
    set("restart", "active");
    status.textContent = "Waiting for the server to come back…";

    const target = result.version?.sha;
    const t0 = Date.now();
    await new Promise((r) => setTimeout(r, 1500));
    while (Date.now() - t0 < 90_000) {
      try {
        const st = await api("GET", "/api/status");
        if (!target || st.version?.sha === target) {
          set("restart", "done");
          set("reload", "active");
          status.textContent = `Updated to ${target ? target.slice(0, 7) : "the latest version"}.`;
          nav.guard = null;
          setTimeout(() => location.reload(), 900);
          return;
        }
      } catch { /* still restarting */ }
      await new Promise((r) => setTimeout(r, 1000));
    }
    set("restart", "error");
    status.replaceChildren(notice("err", "The server didn't come back within 90 seconds. If it runs as a service, check its logs (journalctl --user -u datacreator), or start it again with ./run.sh."));
    foot.replaceChildren(h("span", { class: "grow" }), h("button", { class: "primary", onclick: () => location.reload() }, "Reload page"));
    toast("Update finished but the server didn't restart", "err");
  }
}

function versionLine() {
  const v = info?.current || state.status?.version;
  return v ? `Running ${v.short}, committed ${relTime(v.date)}` : "Version unknown";
}

function loading(text) {
  return h("div", { class: "row", style: { padding: "18px 0", justifyContent: "center", color: "var(--muted)" } }, h("span", { class: "spin" }), text);
}
