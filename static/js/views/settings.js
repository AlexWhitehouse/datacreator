// Settings: AI connection (stored in .env on the server) and appearance (stored in this browser).
import { api, busy, confirmDialog, h, icon, toast } from "../ui.js";
import { nav } from "../state.js";
import { themeSelector } from "../theme.js";

const EFFORT_LABELS = { none: "None (recommended)", low: "Low", medium: "Medium", high: "High", "": "Model default" };

export async function viewSettings(view) {
  let s = await api("GET", "/api/settings");

  // ---------------------------------------------------------------- AI key
  const keyBox = h("div", { class: "key-box" });
  let editingKey = false;

  function renderKey() {
    if (editingKey) {
      const input = h("input", { type: "password", placeholder: "Paste the API key", autocomplete: "off", spellcheck: "false", "aria-label": "API key" });
      const reveal = h("button", { type: "button", class: "ghost icon sm", title: "Show key", onclick: () => {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        reveal.title = show ? "Hide key" : "Show key";
        reveal.replaceChildren(icon(show ? "eyeOff" : "eye", "sm"));
        input.focus();
      } }, icon("eye", "sm"));
      const save = h("button", { class: "primary sm", disabled: true, onclick: () => saveKey(save, input.value) }, icon("check", "sm"), "Save key");
      input.addEventListener("input", () => { save.disabled = !input.value.trim(); });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && input.value.trim()) saveKey(save, input.value);
        if (e.key === "Escape") { e.stopPropagation(); editingKey = false; renderKey(); }
      });
      keyBox.replaceChildren(
        h("div", { class: "row tight nowrap" },
          h("div", { class: "input-suffix grow key-input" }, input, h("span", { class: "reveal" }, reveal)),
          save,
          s.key_set ? h("button", { class: "ghost sm", onclick: () => { editingKey = false; renderKey(); } }, "Cancel") : null),
        h("small", { class: "hint" }, "Stored in .env on this machine (readable only by you). It's never sent back to the browser."));
      requestAnimationFrame(() => input.focus());
      return;
    }
    keyBox.replaceChildren(h("div", { class: "row between" },
      s.key_set
        ? h("div", { class: "key-state" }, h("span", { class: "key-ic ok" }, icon("key", "sm")),
          h("div", {}, h("div", { class: "strong" }, "Key set", s.key_hint ? h("span", { class: "mono key-hint" }, s.key_hint) : null),
            h("div", { class: "muted small" }, "AI data types are enabled.")))
        : h("div", { class: "key-state" }, h("span", { class: "key-ic" }, icon("key", "sm")),
          h("div", {}, h("div", { class: "strong" }, "No key"),
            h("div", { class: "muted small" }, "AI data types are off until you add one."))),
      h("div", { class: "row tight" },
        h("button", { class: s.key_set ? "sm" : "primary sm", onclick: () => { editingKey = true; renderKey(); } },
          icon(s.key_set ? "refresh" : "plus", "sm"), s.key_set ? "Replace" : "Add key"),
        s.key_set ? h("button", { class: "ghost danger sm", onclick: (e) => removeKey(e.currentTarget) }, icon("trash", "sm"), "Remove") : null)));
  }

  async function saveKey(btn, value) {
    if (!value.trim()) return;
    await busy(btn, async () => {
      try {
        s = await api("PUT", "/api/settings", { api_key: value });
        editingKey = false;
        renderKey();
        await nav.refreshStatus?.();
        toast("API key saved", "ok");
      } catch (e) { toast(e.message, "err"); }
    });
  }

  async function removeKey(btn) {
    const ok = await confirmDialog("AI data types will stop working until you add a key again.", { title: "Remove the API key?", ok: "Remove key" });
    if (!ok) return;
    await busy(btn, async () => {
      try {
        s = await api("PUT", "/api/settings", { remove_key: true });
        renderKey();
        await nav.refreshStatus?.();
        toast("API key removed", "ok");
      } catch (e) { toast(e.message, "err"); }
    });
  }

  // ---------------------------------------------------------------- connection fields
  const f = {
    base_url: h("input", { type: "text", value: s.base_url, spellcheck: "false", autocomplete: "off", placeholder: s.defaults.base_url }),
    model: h("input", { type: "text", value: s.model, spellcheck: "false", autocomplete: "off", placeholder: s.defaults.model }),
    concurrency: h("input", { type: "number", value: s.concurrency, min: 1, max: s.max_concurrency, step: 1 }),
    reasoning_effort: h("select", {}, ["none", "low", "medium", "high", ""].map((v) => h("option", { value: v }, EFFORT_LABELS[v]))),
  };
  f.reasoning_effort.value = s.reasoning_effort;
  const values = () => ({
    base_url: f.base_url.value.trim(), model: f.model.value.trim(),
    concurrency: f.concurrency.value.trim(), reasoning_effort: f.reasoning_effort.value,
  });
  const changed = () => {
    const v = values(), out = {};
    for (const k of Object.keys(v)) if (String(v[k]) !== String(s[k])) out[k] = v[k];
    return out;
  };
  const saveBtn = h("button", { class: "primary", disabled: true, onclick: () => saveFields() }, icon("save", "sm"), "Save changes");
  const revertBtn = h("button", { class: "ghost", disabled: true, onclick: () => { fill(s); refresh(); } }, "Discard");
  const defaultsBtn = h("button", { class: "ghost sm", onclick: () => { fill({ ...s, ...s.defaults }); refresh(); } }, icon("undo", "sm"), "Use defaults");
  const fill = (src) => { for (const k of Object.keys(f)) f[k].value = src[k]; };
  const refresh = () => {
    const dirty = Object.keys(changed()).length > 0;
    saveBtn.disabled = !dirty;
    revertBtn.disabled = !dirty;
    const d = s.defaults;
    defaultsBtn.disabled = Object.keys(d).every((k) => String(values()[k]) === String(d[k]));
  };
  for (const el of Object.values(f)) { el.addEventListener("input", refresh); el.addEventListener("change", refresh); }

  async function saveFields() {
    const body = changed();
    if (!Object.keys(body).length) return;
    await busy(saveBtn, async () => {
      try {
        s = await api("PUT", "/api/settings", body);
        fill(s);
        await nav.refreshStatus?.();
        toast("Settings saved and applied", "ok");
      } catch (e) { toast(e.message, "err"); }
    });
    refresh();
  }

  // ---------------------------------------------------------------- test connection
  const result = h("div", { class: "test-result", "aria-live": "polite" });
  const testBtn = h("button", { onclick: () => testConnection() }, icon("plug", "sm"), "Test connection");
  async function testConnection() {
    const v = values();
    const pendingKey = keyBox.querySelector("input")?.value.trim();
    result.replaceChildren();
    await busy(testBtn, async () => {
      let r;
      try { r = await api("POST", "/api/settings/test", { ...v, ...(pendingKey ? { api_key: pendingKey } : {}) }); }
      catch (e) { r = { ok: false, error: e.message }; }
      const unsaved = Object.keys(changed()).length || pendingKey ? h("div", { class: "muted small" }, "Tested with the values on this page. Save to use them.") : null;
      result.replaceChildren(r.ok
        ? h("div", { class: "notice ok" }, icon("checkCircle"), h("div", { class: "grow" },
            h("div", {}, h("b", {}, "Connected. "), `${r.model} answered in ${r.latency_ms.toLocaleString()} ms.`),
            r.note ? h("div", { class: "small" }, r.note) : null, unsaved))
        : h("div", { class: "notice err" }, icon("alert"), h("div", { class: "grow" },
            h("div", {}, h("b", {}, "Connection failed. "), r.error), unsaved)));
    });
  }

  // ---------------------------------------------------------------- layout
  const field = (label, el, hint) => h("label", { class: "field" }, h("span", {}, label), el, hint ? h("small", {}, hint) : null);
  renderKey();
  refresh();

  view.append(
    h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "Settings"),
      h("p", { class: "muted" }, "Connect the AI gateway and choose how DataCreator looks. AI changes apply straight away, no restart needed."))),
    h("div", { class: "settings-layout stack lg" },
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, h("span", { class: "sec-ic" }, icon("sparkles", "sm")), "AI connection")),
        h("p", { class: "muted small", style: { margin: "-6px 0 16px" } },
          "AI writes pick-lists and free-text fields through an OpenAI-compatible gateway. Only synthetic data is sent."),
        h("div", { class: "label" }, "API key"),
        keyBox,
        h("hr", { class: "divider" }),
        h("div", { class: "grid2" },
          field("Base URL", f.base_url, "OpenAI-compatible endpoint, ending in the API version."),
          field("Model", f.model, "The gateway's model ID."),
          field("Parallel AI calls", f.concurrency, `How many AI requests a run makes at once (1–${s.max_concurrency}).`),
          field("Reasoning effort", f.reasoning_effort, "“None” stops hidden reasoning from eating the token budget, so replies don't come back empty. It's also faster and cheaper.")),
        h("div", { class: "settings-actions" },
          testBtn, defaultsBtn, h("span", { class: "grow" }), revertBtn, saveBtn),
        result),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, h("span", { class: "sec-ic" }, icon("monitor", "sm")), "Appearance")),
        h("div", { class: "label" }, "Theme"),
        themeSelector(),
        h("small", { class: "hint" }, "System follows your computer's light or dark setting. Saved in this browser. The sun/moon button in the top bar switches it too.")),
      h("p", { class: "faint small settings-foot" }, icon("info", "sm"), h("span", {}, "AI settings are saved to ", h("code", {}, s.env_file), ". You can still edit that file by hand; restart the app afterwards."))));
}
