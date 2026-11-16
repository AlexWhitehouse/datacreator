// Templates list: upload, readiness at a glance, jump to editor or generate.
import { api, confirmDialog, emptyState, h, icon, menuButton, plural, relTime, store, toast } from "../ui.js";
import { generateFrom, loadTemplates, loadTypes, nav, state, templateHealth } from "../state.js";

export async function viewTemplates(view) {
  await Promise.all([loadTemplates(), loadTypes()]);
  const fileInput = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, style: { display: "none" } });

  async function upload(files) {
    files = [...files].filter((f) => /\.pdf$/i.test(f.name) || f.type === "application/pdf");
    if (!files.length) { toast("Only PDF files can be uploaded", "err"); return; }
    tile.classList.add("busy");
    tileText.replaceChildren(h("strong", {}, `Uploading ${files.length > 1 ? plural(files.length, "file") : files[0].name}…`), h("span", { class: "small" }, "Reading pages and any fillable fields"));
    tileBubble.replaceChildren(h("span", { class: "spin", style: { width: "22px", height: "22px" } }));
    const done = [];
    for (const file of files) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("name", "");
      try { done.push(await api("POST", "/api/templates", fd)); } catch (e) { toast(`${file.name}: ${e.message}`, "err"); }
    }
    if (done.length === 1) {
      toast(`Uploaded “${done[0].name}”. Next, mark up its fields.`, "ok");
      location.hash = `#/templates/${done[0].id}`;
    } else {
      if (done.length) toast(`Uploaded ${plural(done.length, "template")}`, "ok");
      viewTemplatesReload();
    }
  }
  fileInput.addEventListener("change", () => upload(fileInput.files));

  const tileBubble = h("div", { class: "bubble" }, icon("upload", "lg"));
  const tileText = h("div", {}, h("strong", {}, "Upload a blank PDF"),
    h("div", { class: "small", style: { marginTop: "4px" } }, "Drop files anywhere on this page, or click to choose. Multi-page PDFs are fine."));
  const tile = h("div", { class: "upload-tile", tabindex: 0, role: "button", onclick: () => fileInput.click(), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } } },
    tileBubble, tileText);

  // page-wide drop target
  const overlay = h("div", { class: "drop-overlay" }, h("div", { class: "inner" }, icon("upload", "xl"), "Drop PDFs to upload"));
  document.body.append(overlay);
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  const onEnter = (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; overlay.classList.add("on"); };
  const onOver = (e) => { if (hasFiles(e)) e.preventDefault(); };
  const onLeave = () => { if (--depth <= 0) { depth = 0; overlay.classList.remove("on"); } };
  const onDrop = (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth = 0; overlay.classList.remove("on"); upload(e.dataTransfer.files); };
  window.addEventListener("dragenter", onEnter);
  window.addEventListener("dragover", onOver);
  window.addEventListener("dragleave", onLeave);
  window.addEventListener("drop", onDrop);
  nav.cleanup = () => {
    overlay.remove();
    window.removeEventListener("dragenter", onEnter);
    window.removeEventListener("dragover", onOver);
    window.removeEventListener("dragleave", onLeave);
    window.removeEventListener("drop", onDrop);
  };

  const cards = state.templates.map((t) => {
    const health = templateHealth(t);
    const pages = t.page_sizes.length;
    const card = h("div", { class: "tpl-card" },
      h("div", { class: "tpl-thumb", onclick: () => (location.hash = `#/templates/${t.id}`) },
        h("img", { src: `/api/templates/${t.id}/pages/0.png?dpi=40`, alt: "", loading: "lazy" }),
        h("div", { class: "open" }, h("span", {}, icon("edit", "sm"), "Open editor"))),
      h("div", { class: "tpl-body" },
        h("div", { class: "name" }, t.name),
        h("div", { class: "muted small" }, `${plural(pages, "page")} · ${plural(t.fields.length, "field")} · edited ${relTime(t.updated_at)}`),
        h("div", { class: "tpl-meta" }, h("span", { class: `pill ${health.kind}` }, h("span", { class: "dot" }), health.label))),
      h("div", { class: "tpl-actions" },
        h("a", { class: "btn sm", href: `#/templates/${t.id}` }, icon("edit", "sm"), "Edit"),
        h("button", { class: "sm", disabled: !health.ok, title: health.ok ? "Generate documents from this template" : "Finish marking up this template first", onclick: () => generateFrom([t.id]) }, icon("zap", "sm"), "Generate"),
        h("span", { class: "grow" }),
        menuButton([
          { label: "Duplicate", icon: "copy", fn: () => duplicate(t) },
          "sep",
          { label: "Delete", icon: "trash", danger: true, fn: () => remove(t, card) },
        ])));
    card.dataset.search = `${t.name} ${t.fields.map((f) => f.name).join(" ")}`.toLowerCase();
    card.dataset.name = t.name.toLowerCase();
    card.dataset.updated = t.updated_at;
    card.dataset.ready = health.ok ? "1" : "0";
    return card;
  });

  // search (name or field names) and sort, client-side: the whole library is already loaded
  const showTools = state.templates.length > 3; // search and sort only once there's something to search
  let savedQ = ""; try { savedQ = sessionStorage.getItem("dc.tpl.q") || ""; } catch {}
  const filt = { text: showTools ? savedQ : "", sort: showTools ? store.get("dc.tpl.sort", "recent") : "recent" };
  const grid = h("div", { class: "tpl-grid stagger" });
  const noMatch = h("div", { class: "card", hidden: true }, emptyState({ icon: "search", title: "No matching templates", text: "Search matches template names and their field names." }));
  const search = h("input", { type: "search", placeholder: "Search templates or field names…", value: filt.text, "aria-label": "Search templates" });
  const sortSel = h("select", { "aria-label": "Sort templates" },
    [["recent", "Recently edited"], ["name", "Name"], ["ready", "Ready first"]].map(([v, l]) => h("option", { value: v, selected: filt.sort === v }, l)));
  function drawGrid() {
    const words = filt.text.toLowerCase().split(/\s+/).filter(Boolean);
    const shown = cards.filter((c) => words.every((w) => c.dataset.search.includes(w)));
    const by = {
      recent: (a, b) => b.dataset.updated.localeCompare(a.dataset.updated),
      name: (a, b) => a.dataset.name.localeCompare(b.dataset.name),
      ready: (a, b) => b.dataset.ready - a.dataset.ready || b.dataset.updated.localeCompare(a.dataset.updated),
    }[filt.sort];
    grid.replaceChildren(...shown.sort(by), ...(words.length ? [] : [tile]));
    noMatch.hidden = shown.length > 0 || !words.length;
  }
  search.addEventListener("input", () => { filt.text = search.value; try { sessionStorage.setItem("dc.tpl.q", filt.text); } catch {} drawGrid(); });
  search.addEventListener("keydown", (e) => { if (e.key === "Escape" && search.value) { search.value = ""; search.dispatchEvent(new Event("input")); } });
  sortSel.addEventListener("change", () => { filt.sort = sortSel.value; store.set("dc.tpl.sort", filt.sort); drawGrid(); });

  async function duplicate(t) {
    try { const c = await api("POST", `/api/templates/${t.id}/duplicate`); toast(`Duplicated as “${c.name}”`, "ok"); viewTemplatesReload(); }
    catch (e) { toast(e.message, "err"); }
  }
  async function remove(t, card) {
    if (!(await confirmDialog(`“${t.name}” and its ${plural(t.fields.length, "field")} will be deleted. Past runs keep their files.`, { title: "Delete template?" }))) return;
    try {
      await api("DELETE", `/api/templates/${t.id}`);
      card.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(.94)" }], { duration: 200, easing: "ease-in" }).onfinish = viewTemplatesReload;
      toast("Template deleted", "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  view.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Templates"),
        h("p", { class: "muted" }, "Blank PDF forms, marked up with where each piece of data goes.")),
      state.templates.length ? h("button", { class: "primary", onclick: () => fileInput.click() }, icon("upload", "sm"), "Upload PDF") : null),
    showTools
      ? h("div", { class: "toolbar-row" }, h("div", { class: "input-icon" }, icon("search", "sm"), search), sortSel)
      : "",
    state.templates.length
      ? h("div", {}, grid, noMatch)
      : h("div", { class: "card" }, emptyState({
        icon: "file", title: "Start with a blank form",
        text: "Upload a blank PDF template. Next you'll mark where data goes (auto-detect can do most of it), then generate as many filled-in copies as you need.",
        actions: [h("button", { class: "primary", onclick: () => fileInput.click() }, icon("upload", "sm"), "Upload a PDF")],
      }), h("p", { class: "muted small", style: { textAlign: "center", margin: 0 } }, "Upload blank templates only. You can also drop files anywhere on this page.")),
    fileInput);
  drawGrid();
}

function viewTemplatesReload() { nav.rerender(); }
