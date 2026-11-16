// Data types library + the create/edit type dialog.
import { api, busy, clone, confirmDialog, debounce, emptyState, h, icon, modal, notice, plural, toast } from "../ui.js";
import { KIND_INFO, loadTypes, nav, sortedCategories, state } from "../state.js";

export async function viewTypes(view) {
  await loadTypes();
  let query = "";
  let cat = "all";
  const examples = {};
  const custom = state.types.filter((t) => !t.builtin);
  const builtin = state.types.filter((t) => t.builtin);
  const cats = sortedCategories(new Set(builtin.map((t) => t.category)));

  const search = h("input", { type: "search", placeholder: "Search types, keys and descriptions", oninput: (e) => { query = e.target.value.trim().toLowerCase(); draw(); } });
  const chipRow = h("div", { class: "chips" });
  const body = h("div");

  const matches = (t) => !query || [t.label, t.key, t.description, t.category, KIND_INFO[t.kind]?.[0]].some((s) => (s || "").toLowerCase().includes(query));

  function exBox(t) {
    const live = t.kind === "ai" && t.config?.mode !== "pool";
    const vals = examples[t.key];
    return h("div", { class: "ex", "data-key": t.key },
      live ? h("div", { class: "muted" }, "Written by AI during runs")
        : vals ? vals.map((v) => h("div", {}, v || h("span", { class: "faint" }, "(blank)")))
          : [h("div", { class: "skel", style: { height: "12px", width: "70%", marginTop: "3px" } }), h("div", { class: "skel", style: { height: "12px", width: "45%", marginTop: "8px" } })]);
  }

  function customTile(t) {
    return h("div", { class: "type-tile", tabindex: 0, onclick: () => typeEditor(t), onkeydown: (e) => { if (e.key === "Enter") typeEditor(t); } },
      h("div", { class: "top" },
        h("div", {}, h("div", { class: "lbl" }, t.label), h("code", {}, t.key)),
        h("span", { class: "pill accent" }, icon(KIND_INFO[t.kind]?.[2] || "type", "sm"), KIND_INFO[t.kind]?.[0] || t.kind)),
      exBox(t),
      t.description ? h("div", { class: "desc" }, t.description) : null,
      h("div", { class: "acts" },
        h("button", { class: "xs", onclick: (e) => { e.stopPropagation(); typeEditor(t); } }, icon("edit", "sm"), "Edit"),
        h("button", { class: "xs ghost danger", onclick: (e) => { e.stopPropagation(); remove(t); } }, icon("trash", "sm"), "Delete")));
  }

  function builtinTile(t) {
    return h("div", {
      class: "type-tile", tabindex: 0, title: "Click to copy the key",
      onclick: () => copyKey(t.key), onkeydown: (e) => { if (e.key === "Enter") copyKey(t.key); },
    },
      h("div", { class: "top" }, h("div", { class: "lbl" }, t.label), h("code", {}, t.key)),
      exBox(t));
  }

  async function copyKey(key) {
    try { await navigator.clipboard.writeText(key); toast(`Copied “${key}”. Use it as {${key}} in a combined type.`, "ok"); }
    catch { toast(`Key: ${key}`); }
  }

  async function remove(t) {
    if (!(await confirmDialog(`Templates using “${t.label}” will need a new type before they can be generated.`, { title: "Delete data type?" }))) return;
    try { await api("DELETE", `/api/types/${t.key}`); toast("Data type deleted", "ok"); nav.rerender(); } catch (e) { toast(e.message, "err"); }
  }

  function draw() {
    const counts = { all: state.types.filter(matches).length, custom: custom.filter(matches).length };
    for (const c of cats) counts[c] = builtin.filter((t) => t.category === c && matches(t)).length;
    chipRow.replaceChildren(...[["all", "All"], ["custom", "Your types"], ...cats.map((c) => [c, c])].map(([k, l]) =>
      h("button", { class: `chip${cat === k ? " on" : ""}`, onclick: () => { cat = k; draw(); } }, l, h("span", { class: "n" }, counts[k]))));

    body.replaceChildren();
    const showCustom = cat === "all" || cat === "custom";
    const cu = custom.filter(matches);
    if (showCustom && (cu.length || !query)) {
      body.append(h("div", { class: "cat-head", style: { marginTop: "4px" } }, h("h2", {}, "Your types"), h("span", { class: "n" }, cu.length)));
      body.append(cu.length ? h("div", { class: "type-grid stagger" }, cu.map(customTile))
        : h("div", { class: "card" }, emptyState({
          icon: "database", title: "No custom types yet",
          text: "Built-in types cover most common fields. Create your own for pick-lists, codes, number or date ranges, combined values and AI-written text.",
          actions: [h("button", { class: "primary", onclick: () => typeEditor(null) }, icon("plus", "sm"), "New data type")],
        })));
    }
    for (const c of cats) {
      if (cat !== "all" && cat !== c) continue;
      const list = builtin.filter((t) => t.category === c && matches(t));
      if (!list.length) continue;
      body.append(h("div", { class: "cat-head" }, h("h2", {}, c), h("span", { class: "n" }, `${list.length} built-in`)),
        h("div", { class: "type-grid" }, list.map(builtinTile)));
    }
    if (!body.children.length) body.append(h("div", { class: "card" }, emptyState({ icon: "search", title: "No types match", text: `Nothing matches “${query}”.` })));
  }

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Data types"),
        h("p", { class: "muted" }, "What can be generated for a field. Built-in types read from one consistent synthetic person per record, so names, dates of birth and addresses agree.")),
      h("button", { class: "primary", onclick: () => typeEditor(null) }, icon("plus", "sm"), "New data type")),
    h("div", { class: "toolbar-row" }, h("div", { class: "input-icon" }, icon("search", "sm"), search), chipRow),
    body);
  draw();

  api("GET", "/api/types/examples").then((ex) => {
    Object.assign(examples, ex);
    for (const box of body.querySelectorAll(".ex[data-key]")) {
      const t = state.typeMap[box.dataset.key];
      if (t) box.replaceWith(exBox(t));
    }
  }).catch(() => {});
}

// ================================================================== type editor dialog
export function typeEditor(existing, opts = {}) {
  const isNew = !existing || !!opts.isNew;
  const t = existing ? clone(existing) : { key: "", label: "", kind: "", category: "Custom", description: "", config: {} };
  const aiOk = state.status.ai_available;
  const content = h("div");
  const saveBtn = h("button", { class: "primary", onclick: (e) => busy(e.currentTarget, saveType) }, icon("check", "sm"), "Save type");
  const backBtn = h("button", { class: "ghost", onclick: () => pickKind() }, icon("arrowL", "sm"), "Change kind");
  const cats = [...new Set(state.types.map((x) => x.category))];

  const close = modal({
    title: opts.isNew && existing ? `New data type: “${t.label}”` : isNew ? "New data type" : `Edit “${t.label}”`,
    sub: opts.isNew && existing ? "Suggested by auto-detect. Check the settings, then create it." : null,
    size: "wide", body: content,
    buttons: [backBtn, h("span", { class: "grow" }), h("button", { onclick: () => close() }, "Cancel"), saveBtn],
  });


  function pickKind() {
    saveBtn.style.display = "none";
    backBtn.style.display = "none";
    content.replaceChildren(
      h("p", { class: "muted", style: { marginTop: "-4px" } }, "What kind of values should this type produce?"),
      h("div", { class: "kind-grid stagger" }, Object.entries(KIND_INFO).map(([k, [l, d, ic]]) =>
        h("button", { class: "kind-card", onclick: () => { if (t.kind !== k) t.config = {}; t.kind = k; configure(); } },
          h("span", { class: "ic" }, icon(ic)), h("span", {}, h("div", { class: "t" }, l), h("div", { class: "d" }, d))))));
  }

  // ------------------------------------------------------------ step 2: configure
  const cfgBox = h("div", { class: "stack" });
  const samplesBox = h("div", { class: "samples" });
  let sampleSeq = 0;
  const refreshSamples = debounce(async () => {
    const seq = ++sampleSeq;
    samplesBox.replaceChildren(h("div", { class: "lbl" }, "Live examples", h("span", { class: "spin", style: { width: "10px", height: "10px", borderWidth: "1.5px" } })), ...[...samplesBox.querySelectorAll(".v")]);
    try {
      const r = await api("POST", "/api/types/sample", { kind: t.kind, config: t.config, n: 6 });
      if (seq !== sampleSeq) return;
      samplesBox.replaceChildren(h("div", { class: "lbl" }, "Live examples", h("button", { class: "xs ghost icon", title: "New examples", onclick: () => refreshSamples() }, icon("shuffle", "sm"))),
        ...r.values.map((v) => h("div", { class: "v" }, v || h("span", { class: "faint" }, "(blank)"))));
    } catch (e) {
      if (seq !== sampleSeq) return;
      samplesBox.replaceChildren(h("div", { class: "lbl" }, "Live examples"), h("div", { class: "v muted" }, e.message));
    }
  }, 350);

  function configure() {
    saveBtn.style.display = "";
    backBtn.style.display = isNew && !opts.isNew ? "" : "none";
    const keyInput = h("input", { type: "text", value: t.key, disabled: !isNew, placeholder: "e.g. drug_name", oninput: (e) => { t.key = e.target.value.trim(); keyInput.dataset.touched = "1"; } });
    const labelInput = h("input", {
      type: "text", value: t.label, placeholder: "e.g. Drug name",
      oninput: (e) => {
        t.label = e.target.value;
        if (isNew && !keyInput.dataset.touched) { t.key = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "t_$1"); keyInput.value = t.key; }
      },
    });
    const [kl, kd, kic] = KIND_INFO[t.kind];
    content.replaceChildren(h("div", { class: "type-editor" },
      h("div", { class: "stack lg", oninput: () => refreshSamples(), onchange: () => refreshSamples() },
        h("div", { class: "row between" }, h("span", { class: "kind-badge" }, icon(kic), kl), h("span", { class: "muted small grow", style: { textAlign: "right" } }, kd)),
        h("div", { class: "grid2" },
          h("label", { class: "field" }, h("span", {}, "Name"), labelInput),
          h("label", { class: "field" }, h("span", {}, "Key"), keyInput, h("small", {}, isNew ? "Used in templates. Can't be changed later." : "Keys can't be changed."))),
        h("div", { class: "grid2" },
          h("label", { class: "field" }, h("span", {}, "Category"),
            h("input", { type: "text", value: t.category, list: "dl-cats", oninput: (e) => (t.category = e.target.value) }),
            h("datalist", { id: "dl-cats" }, cats.map((c) => h("option", { value: c })))),
          h("label", { class: "field" }, h("span", {}, "Description (optional)"),
            h("input", { type: "text", value: t.description, oninput: (e) => (t.description = e.target.value) }))),
        h("hr", { class: "divider", style: { margin: "4px 0" } }),
        cfgBox),
      h("div", { class: "preview-col" }, samplesBox)));
    renderCfg();
    refreshSamples();
  }

  function aiListBox(onValues, defaultDesc = "") {
    if (!aiOk) return notice("warn", "AI generation is unavailable: no AI key is set. Add one in Settings (the gear icon, top right).");
    const desc = h("input", { type: "text", value: defaultDesc, placeholder: "e.g. UK oral antibiotic with a typical adult dose" });
    const n = h("input", { type: "number", value: 100, min: 5, max: 500, style: { width: "84px" }, title: "How many values" });
    const btn = h("button", {
      class: "primary",
      onclick: (e) => busy(e.currentTarget, async () => {
        if (!desc.value.trim()) { toast("Describe what to generate", "err"); desc.focus(); return; }
        try { const r = await api("POST", "/api/ai/list", { description: desc.value, n: +n.value }); onValues(r.values); toast(`Generated ${plural(r.values.length, "value")}`, "ok"); refreshSamples(); }
        catch (err) { toast(err.message, "err"); }
      }),
    }, icon("sparkles", "sm"), "Generate");
    return h("div", { class: "ai-panel", style: { margin: 0 } },
      h("div", { class: "head", style: { marginBottom: "8px" } }, icon("sparkles"), "Let AI write the list"),
      h("div", { class: "row nowrap tight" }, h("div", { class: "grow" }, desc), n, btn),
      h("div", { class: "hint" }, `Uses ${state.status.ai_model}. Only your description is sent. Review the list before saving.`));
  }

  function renderCfg() {
    const c = t.config;
    cfgBox.replaceChildren();
    if (t.kind === "picklist") {
      const toText = () => (c.values || []).map((v, i) => (c.weights && c.weights[i] !== undefined && c.weights[i] !== 1 ? `${v} | ${c.weights[i]}` : v)).join("\n");
      const ta = h("textarea", { rows: 9, value: toText(), oninput: () => parse() });
      const count = h("span", { class: "pill" });
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
        count.textContent = plural(vals.length, "value");
      };
      parse();
      cfgBox.append(
        h("label", { class: "field" }, h("span", {}, "Values, one per line", h("span", { style: { marginLeft: "auto" } }, count)), ta,
          h("small", {}, "Optional weight after a bar: ", h("code", {}, "Penicillin | 3"), " is 3× as likely as a weight-1 value.")),
        aiListBox((vals) => { ta.value = (ta.value.trim() ? ta.value.trim() + "\n" : "") + vals.join("\n"); parse(); }, t.description || t.label));
    } else if (t.kind === "pattern") {
      cfgBox.append(h("label", { class: "field" }, h("span", {}, "Pattern"),
        h("input", { type: "text", class: "mono", value: c.pattern || "", placeholder: "MRN-####-??", oninput: (e) => (c.pattern = e.target.value) }),
        h("small", {}, h("code", {}, "#"), " digit · ", h("code", {}, "?"), " letter A–Z · ", h("code", {}, "*"), " letter or digit · ",
          h("code", {}, "[ABC]"), " one of A, B, C · ", h("code", {}, "\\#"), " a literal #")));
    } else if (t.kind === "number_range") {
      cfgBox.append(h("div", { class: "grid3" },
        numIn("Minimum", "min", 0), numIn("Maximum", "max", 100), numIn("Decimal places", "decimals", 0)),
        h("div", { class: "grid2" }, txtIn("Prefix", "prefix"), txtIn("Suffix", "suffix", "e.g. mg")));
    } else if (t.kind === "date_range") {
      c.mode ||= "relative_today";
      cfgBox.append(h("div", {}, h("div", { class: "label" }, "Range"),
        h("div", { class: "seg" }, [["relative_today", "Relative to today"], ["absolute", "Between two dates"], ["relative_to", "Relative to a field"]]
          .map(([v, l]) => h("button", { class: c.mode === v ? "on" : "", onclick: () => { c.mode = v; renderCfg(); refreshSamples(); } }, l)))));
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
          h("p", { class: "hint", style: { marginTop: "-4px" } }, "Negative = before, positive = after. E.g. −90 to −1 is ‘in the last 3 months’."));
      }
    } else if (t.kind === "composite") {
      const inp = h("input", { type: "text", class: "mono", value: c.template || "", placeholder: "{title} {first_name} {last_name}", oninput: (e) => (c.template = e.target.value) });
      cfgBox.append(h("label", { class: "field" }, h("span", {}, "Template"), inp,
        h("small", {}, "Use ", h("code", {}, "{key}"), " for a type or field name. Dates accept a format: ", h("code", {}, "{date_of_birth:%d %b %Y}"))),
        h("div", {}, h("div", { class: "label" }, "Click to insert"),
          h("div", { class: "chips" }, state.types.filter((x) => x.builtin).slice(0, 40).map((x) =>
            h("button", { class: "chip", type: "button", title: x.label, onclick: () => { inp.value += `{${x.key}}`; c.template = inp.value; inp.focus(); refreshSamples(); } }, x.key)))));
    } else if (t.kind === "boolean") {
      const out = h("span", { class: "val" }, `${c.true_pct ?? 50}%`);
      const r = h("input", { type: "range", min: 0, max: 100, value: c.true_pct ?? 50, oninput: (e) => { c.true_pct = +e.target.value; out.textContent = `${c.true_pct}%`; e.target.style.setProperty("--p", `${c.true_pct}%`); } });
      r.style.setProperty("--p", `${c.true_pct ?? 50}%`);
      cfgBox.append(h("div", {}, h("div", { class: "label" }, "Records that are ‘yes’", out), r));
    } else if (t.kind === "ai") {
      c.mode ||= "live";
      cfgBox.append(
        h("label", { class: "field" }, h("span", {}, "Instructions for the AI"),
          h("textarea", { rows: 4, value: c.prompt || "", placeholder: "e.g. A 1–2 sentence reason for referral to a homecare service, written by a clinician", oninput: (e) => (c.prompt = e.target.value) })),
        h("div", { class: "grid2" },
          h("div", {}, h("div", { class: "label" }, "Mode"),
            h("div", { class: "seg block" }, [["live", "Live per record"], ["pool", "Pool, reused"]].map(([v, l]) =>
              h("button", { class: c.mode === v ? "on" : "", onclick: () => { c.mode = v; renderCfg(); refreshSamples(); } }, l)))),
          numIn("Max words (optional)", "max_words", "")),
        c.mode === "live"
          ? notice("", "Each record gets fresh text that fits the person (age, sex, conditions, medication…). All of a record's AI fields are written in one call per set. Runs with the same seed will still differ.")
          : h("div", { class: "stack" },
            notice("", "A pool is generated once and sampled during runs: faster, free, and repeatable with a seed, but not tailored to each record."),
            h("label", { class: "field" }, h("span", {}, `Pool (${plural((c.pool || []).length, "value")}, one per line)`),
              h("textarea", { rows: 7, value: (c.pool || []).join("\n"), oninput: (e) => (c.pool = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean)) })),
            aiListBox((vals) => { c.pool = vals; renderCfg(); }, c.prompt || "")),
        !aiOk ? notice("warn", "AI is not configured, so live AI types can't be used in runs. Add a key in Settings (the gear icon, top right).") : null);
    }
  }
  const numIn = (label, k, ph) => h("label", { class: "field" }, h("span", {}, label),
    h("input", { type: "number", value: t.config[k] ?? "", placeholder: String(ph), oninput: (e) => { if (e.target.value === "") delete t.config[k]; else t.config[k] = +e.target.value; } }));
  const txtIn = (label, k, ph = "") => h("label", { class: "field" }, h("span", {}, label),
    h("input", { type: "text", value: t.config[k] ?? "", placeholder: ph, oninput: (e) => (t.config[k] = e.target.value) }));

  if (!t.kind) pickKind(); else configure();

  async function saveType() {
    try {
      const body = { key: t.key, label: t.label, kind: t.kind, category: t.category, description: t.description, config: t.config };
      const saved = isNew ? await api("POST", "/api/types", body) : await api("PUT", `/api/types/${t.key}`, body);
      toast(`Saved “${t.label || t.key}”`, "ok");
      close();
      if (opts.onSaved) await opts.onSaved(saved); else nav.rerender();
    } catch (e) { toast(e.message, "err"); }
  }
}
