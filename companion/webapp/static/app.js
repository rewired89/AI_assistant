"use strict";
// Nine Tails phone app. No libraries. Everything she wrote is put on the page with textContent, never as HTML.
const $app = document.getElementById("app");
let SESSION = null, ROUTE = "", DATA = {};
const ICONS = {
  home: '<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4v-5H9v5H5a1 1 0 0 1-1-1z"/>',
  notes: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  lists: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1.2 1.2L7 4.9M3.5 12l1.2 1.2L7 10.9M3.5 18l1.2 1.2L7 16.9"/>',
  journal: '<path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z"/><path d="M5 18a2 2 0 0 1 2-2h12M10 8h5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"/>'
};
const TABS = [["home", "Home"], ["notes", "Notes"], ["lists", "Lists"], ["journal", "Journal"], ["settings", "Settings"]];

// ---------- tiny helpers
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") e.className = v; else if (k === "style") e.style.cssText = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = v; else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
function svg(name) { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); s.innerHTML = ICONS[name]; return s; }
function toast(t, ms = 3500) { const e = document.getElementById("toast"); e.textContent = t; e.style.display = "block"; clearTimeout(toast.t); toast.t = setTimeout(() => e.style.display = "none", ms); }
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: { "X-NT-App": "1", "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  let j = {}; try { j = await r.json(); } catch (e) { /* not json */ }
  if (r.status === 401 && !url.endsWith("/login")) { SESSION = null; route(); throw new Error("Please log in again."); }
  if (!r.ok) throw new Error(j.error || "Something went wrong.");
  return j;
}
const when = t => new Date(t * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
const day = t => new Date(t * 1000).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
function confirmBox(msg, label = "Delete", danger = true) {
  return new Promise(res => {
    const d = h("dialog", {}, h("p", { text: msg }), h("div", { class: "row between" },
      h("button", { class: "btn ghost", onclick: () => { d.close(); res(false); } }, "Cancel"),
      h("button", { class: "btn " + (danger ? "pink" : ""), onclick: () => { d.close(); res(true); } }, label)));
    d.addEventListener("close", () => { d.remove(); res(false); }); document.body.append(d); d.showModal();
  });
}
function download(name, text, type = "text/plain") { const a = h("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name }); document.body.append(a); a.click(); a.remove(); }
const KIND = { voice: ["Voice note", "pink"], dictated: ["Dictated", ""], written: ["Written", "gray"] };
function greeting() { const hr = new Date().getHours(); return hr < 5 ? "Hi" : hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening"; }
function shell(content, active, fab) {
  const grad = document.createElementNS("http://www.w3.org/2000/svg", "svg"); grad.setAttribute("width", "0"); grad.setAttribute("height", "0");
  grad.innerHTML = '<defs><linearGradient id="tabgrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#14b8a6"/><stop offset="1" stop-color="#ec4899"/></linearGradient></defs>';
  $app.replaceChildren(...[
    h("div", { class: "top" }, h("img", { src: "/icon.svg", alt: "" }), h("div", {}, h("div", { class: "t", text: "Nine Tails" }), h("div", { class: "sub", text: SESSION && SESSION.demo ? "Sample preview" : "Your notes, lists and journal" }))),
    SESSION && SESSION.demo ? h("div", { class: "sample", text: "Preview with SAMPLE data. Nothing here is real, and nothing is saved when you close it." }) : null,
    content, grad,
    h("nav", { class: "tabs", "aria-label": "Sections" }, TABS.map(([k, l]) => h("button", { class: "tab" + (k === active ? " on" : ""), "aria-current": k === active ? "page" : false, onclick: () => go("/" + (k === "home" ? "" : k)) }, svg(k), l))),
    fab ? h("button", { class: "fab", "aria-label": fab.label, onclick: fab.on }, "+") : null].filter(Boolean));
  window.scrollTo(0, 0);
}
function go(p) { location.hash = "#" + p; }

// ---------- sign in / set up
function authScreen(setup) {
  const pass = h("input", { type: "password", autocomplete: setup ? "new-password" : "current-password", placeholder: setup ? "Choose a passphrase (8+ characters)" : "Passphrase", "aria-label": "Passphrase" });
  const code = h("input", { type: "text", placeholder: "Pairing code, like K4M9-2XQP", autocomplete: "off", "aria-label": "Pairing code" });
  const msg = h("div", { class: "small", style: "color:var(--danger);min-height:20px" });
  const submit = async () => {
    msg.textContent = "";
    try {
      if (setup) await api("POST", "/api/setup", { code: code.value, passphrase: pass.value });
      await api("POST", "/api/login", { passphrase: pass.value });
      await boot();
    } catch (e) { msg.textContent = e.message; }
  };
  pass.addEventListener("keydown", e => e.key === "Enter" && submit());
  $app.replaceChildren(h("div", { class: "center" }, h("div", { class: "card stack" },
    h("img", { class: "logo", src: "/icon.svg", alt: "Nine Tails" }),
    h("h1", { text: setup ? "Welcome" : "Welcome back" }),
    h("p", { class: "muted", text: setup ? "This is your private space. The pairing code is shown on the computer that runs Nine Tails. Then you choose your own passphrase, and only you will know it." : "Enter your passphrase to open your notes, lists and journal." }),
    setup ? code : null, pass, msg, h("button", { class: "btn", onclick: submit }, setup ? "Create my passphrase" : "Open"))));
}

// ---------- home
async function home() {
  const [n, l, j] = await Promise.all([api("GET", "/api/notes"), api("GET", "/api/lists"), api("GET", "/api/journal")]);
  const quick = h("textarea", { placeholder: "Write something down...", "aria-label": "Quick note", rows: "3" });
  const name = SESSION.name || "friend";
  shell(h("div", {},
    h("div", { class: "card hero" }, h("h1", { text: `${greeting()}, ${name}` }), h("p", { text: "Capture a thought, check off a list, or read back what you chose to keep." }),
      h("div", { class: "row" }, h("button", { class: "btn white", onclick: () => go("/notes/new") }, "New note"), h("button", { class: "btn white", onclick: () => go("/lists") }, "My lists"))),
    h("div", { class: "card stack" }, h("h2", { text: "Quick note" }), quick,
      h("div", { class: "row between" }, h("span", { class: "small muted", text: 'Or say: "Nine Tails, write this down..."' }),
        h("button", { class: "btn small", onclick: async () => { if (!quick.value.trim()) return; try { await api("POST", "/api/notes", { body: quick.value }); toast("Saved."); quick.value = ""; home(); } catch (e) { toast(e.message); } } }, "Save"))),
    h("div", { class: "card" }, h("div", { class: "row between" }, h("h2", { text: "Recent notes" }), h("a", { href: "#/notes", text: "See all" })),
      n.notes.length ? n.notes.slice(0, 3).map(noteCard) : h("div", { class: "empty" }, h("div", { class: "big", text: "✎" }), "No notes yet. Tell Nine Tails to write something down, or add one here.")),
    h("div", { class: "card" }, h("div", { class: "row between" }, h("h2", { text: "Lists" }), h("a", { href: "#/lists", text: "Open" })),
      l.lists.length ? l.lists.slice(0, 3).map(listCard) : h("div", { class: "empty", text: "No lists yet. Try: \"Nine Tails, make me a grocery list.\"" })),
    h("div", { class: "card" }, h("h2", { text: "Journal" }), h("p", { class: "muted", text: j.entries.length ? `${j.entries.length} private ${j.entries.length === 1 ? "entry" : "entries"} you chose to keep. Only you can see them.` : "Nothing kept yet. When you say yes to keeping a note after a check-in, it appears here." }),
      h("button", { class: "btn ghost small", onclick: () => go("/journal") }, "Open journal")),
    h("div", { class: "card" }, h("h2", { text: "Reminders" }), h("p", { class: "muted", text: "Reminders are the next thing we're building. Nothing is scheduled yet." }))), "home");
}
function noteCard(n) {
  const [lab, cls] = KIND[n.kind] || ["Note", "gray"];
  return h("a", { class: "note", href: "#/notes/" + n.id }, h("div", { class: "meta" }, h("span", { class: "badge " + cls, text: lab }), n.transcript_state === "pending" ? h("span", { class: "badge warn", text: "transcription pending" }) : null, h("span", { text: when(n.created_at) })),
    h("div", { class: "ti", text: n.title }), h("div", { class: "pv", text: n.body }), n.tags.length ? h("div", { class: "tagrow", style: "margin-top:6px" }, n.tags.map(t => h("span", { class: "badge gray", text: "#" + t }))) : null);
}
function listCard(l) {
  const pct = l.total ? Math.round(100 * l.done / l.total) : 0;
  return h("a", { class: "note", href: "#/lists/" + l.id }, h("div", { class: "row between" }, h("div", { class: "ti", text: l.name }), h("span", { class: "small muted", text: `${l.done}/${l.total} done` })), h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100" }, h("i", { style: `width:${pct}%` })));
}

// ---------- notes
let NOTE_FILTER = "", NOTE_Q = "";
async function notes() {
  const arch = NOTE_FILTER === "archived";
  const qs = new URLSearchParams({ q: NOTE_Q, archived: arch ? "1" : "0" }); if (NOTE_FILTER && !arch) qs.set("kind", NOTE_FILTER);
  const res = await api("GET", "/api/notes?" + qs);
  const search = h("input", { type: "search", placeholder: "Search your notes", value: NOTE_Q, "aria-label": "Search notes" });
  let t; search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { NOTE_Q = search.value; notes().then(() => document.querySelector("input[type=search]").focus()); }, 300); });
  const chips = [["", "All"], ["voice", "Voice"], ["dictated", "Dictated"], ["written", "Written"], ["archived", "Archived"]].map(([k, l]) => h("button", { class: "chip" + (NOTE_FILTER === k ? " on" : ""), onclick: () => { NOTE_FILTER = k; notes(); } }, l));
  shell(h("div", {}, h("h1", { text: "Notes", style: "margin:6px 2px 10px" }), search, h("div", { class: "chips" }, chips),
    res.notes.length ? res.notes.map(noteCard) : h("div", { class: "card empty" }, h("div", { class: "big", text: "✎" }), NOTE_Q ? "Nothing matches that." : "No notes here yet.")), "notes", { label: "New note", on: () => go("/notes/new") });
}
async function noteEditor(id) {
  const isNew = id === "new";
  const n = isNew ? { title: "", body: "", tags: [], kind: "written", archived: false, created_at: Date.now() / 1000, cleaned: false } : await api("GET", "/api/notes/" + id);
  const title = h("input", { type: "text", value: n.title, placeholder: "Title (optional)", "aria-label": "Title" });
  const body = h("textarea", { "aria-label": "Note text", style: "min-height:220px" }); body.value = n.body;
  const tags = h("input", { type: "text", value: n.tags.join(", "), placeholder: "tags, separated by commas", "aria-label": "Tags" });
  const parse = () => tags.value.split(",").map(s => s.trim()).filter(Boolean);
  const [lab, cls] = KIND[n.kind] || ["Note", "gray"];
  const explain = { voice: "Spoken to Nine Tails over several turns and turned into text.", dictated: "Spoken to Nine Tails and saved as you said it.", written: "Written on your phone." }[n.kind];
  const save = async () => { try { if (isNew) { const r = await api("POST", "/api/notes", { title: title.value, body: body.value, tags: parse() }); toast("Saved."); go("/notes/" + r.id); } else { await api("PUT", "/api/notes/" + id, { title: title.value, body: body.value, tags: parse() }); toast("Saved."); } } catch (e) { toast(e.message); } };
  const app = h("textarea", { placeholder: "Add to the end of this note...", rows: "2", "aria-label": "Add to note", style: "min-height:60px" });
  shell(h("div", {}, h("a", { href: "#/notes", text: "‹ Notes" }),
    h("div", { class: "card stack" }, h("div", { class: "row" }, h("span", { class: "badge " + cls, text: lab }), n.cleaned ? h("span", { class: "badge gray", text: "spacing and capitals tidied" }) : null,
      n.transcript_state === "pending" ? h("span", { class: "badge warn", text: "transcription pending" }) : null, isNew ? null : h("span", { class: "small muted", text: when(n.created_at) })),
      !isNew ? h("p", { class: "small muted", text: explain }) : null, title, body, tags, h("div", { class: "row" }, h("button", { class: "btn", onclick: save }, "Save"),
        isNew ? null : h("button", { class: "btn ghost", onclick: () => download((n.title || "note").replace(/[^\w\- ]+/g, "") + ".txt", `${title.value}\n\n${body.value}\n`) }, "Export"))),
    isNew ? null : h("div", { class: "card stack" }, h("h3", { text: "Add more" }), app, h("button", { class: "btn ghost small", onclick: async () => { if (!app.value.trim()) return; try { const r = await api("POST", `/api/notes/${id}/append`, { text: app.value }); body.value = r.body; app.value = ""; toast("Added."); } catch (e) { toast(e.message); } } }, "Add to note")),
    isNew ? null : h("div", { class: "card stack" }, h("div", { class: "row between" }, h("span", { text: "Summary" }), h("span", { class: "badge gray", text: "not available yet" })), h("p", { class: "small muted", text: "Summaries need the language model. When they exist they will sit beside your note and never replace it." })),
    isNew ? null : h("div", { class: "row" }, h("button", { class: "btn ghost", onclick: async () => { await api("PUT", "/api/notes/" + id, { archived: !n.archived }); toast(n.archived ? "Restored." : "Archived."); go("/notes"); } }, n.archived ? "Restore" : "Archive"),
      h("button", { class: "btn danger", onclick: async () => { if (await confirmBox("Delete this note? This can't be undone.")) { await api("DELETE", "/api/notes/" + id); toast("Deleted."); go("/notes"); } } }, "Delete"))), "notes");
}

// ---------- lists
let ITEM_FILTER = "active";
async function lists() {
  const res = await api("GET", "/api/lists");
  const name = h("input", { type: "text", placeholder: "New list, like Groceries", "aria-label": "New list name" });
  const add = async () => { if (!name.value.trim()) return; try { const l = await api("POST", "/api/lists", { name: name.value }); go("/lists/" + l.id); } catch (e) { toast(e.message); } };
  name.addEventListener("keydown", e => e.key === "Enter" && add());
  shell(h("div", {}, h("h1", { text: "Lists", style: "margin:6px 2px 10px" }), h("div", { class: "card row" }, h("div", { class: "grow" }, name), h("button", { class: "btn", onclick: add }, "Add")),
    res.lists.length ? res.lists.map(listCard) : h("div", { class: "card empty" }, h("div", { class: "big", text: "☑" }), "No lists yet. Make one here, or say \"Nine Tails, make me a grocery list.\"")), "lists");
}
async function listDetail(id) {
  const l = await api("GET", "/api/lists/" + id);
  const vis = l.items.filter(i => ITEM_FILTER === "all" || (ITEM_FILTER === "active" ? !i.done : i.done));
  const input = h("input", { type: "text", placeholder: "Add an item", "aria-label": "New item" });
  const addItem = async () => { if (!input.value.trim()) return; try { await api("POST", `/api/lists/${id}/items`, { text: input.value }); listDetail(id); } catch (e) { toast(e.message); } };
  input.addEventListener("keydown", e => e.key === "Enter" && addItem());
  const rows = vis.map(i => h("div", { class: "item" + (i.done ? " done" : "") },
    h("button", { class: "check" + (i.done ? " on" : ""), role: "checkbox", "aria-checked": String(i.done), "aria-label": (i.done ? "Restore " : "Complete ") + i.text, onclick: async () => { await api("PUT", "/api/items/" + i.id, { done: !i.done }); listDetail(id); } }, i.done ? "✓" : ""),
    h("div", { class: "tx", text: i.text, onclick: async () => { const t = prompt("Edit item", i.text); if (t && t.trim() && t !== i.text) { try { await api("PUT", "/api/items/" + i.id, { text: t }); listDetail(id); } catch (e) { toast(e.message); } } } }),
    h("button", { class: "icon", "aria-label": "Move up", onclick: async () => { await api("POST", `/api/items/${i.id}/move`, { direction: "up" }); listDetail(id); } }, "↑"),
    h("button", { class: "icon", "aria-label": "Move down", onclick: async () => { await api("POST", `/api/items/${i.id}/move`, { direction: "down" }); listDetail(id); } }, "↓"),
    h("button", { class: "icon", "aria-label": "Delete item", onclick: async () => { await api("DELETE", "/api/items/" + i.id); listDetail(id); } }, "✕")));
  shell(h("div", {}, h("a", { href: "#/lists", text: "‹ Lists" }),
    h("div", { class: "card" }, h("div", { class: "row between" }, h("h1", { text: l.name }), h("button", { class: "btn ghost small", onclick: async () => { const t = prompt("Rename list", l.name); if (t && t.trim()) { try { await api("PUT", "/api/lists/" + id, { name: t }); listDetail(id); } catch (e) { toast(e.message); } } } }, "Rename")),
      h("div", { class: "small muted", text: `${l.done} of ${l.total} done` }),
      h("div", { class: "chips" }, [["active", "To do"], ["done", "Done"], ["all", "All"]].map(([k, t]) => h("button", { class: "chip" + (ITEM_FILTER === k ? " on" : ""), onclick: () => { ITEM_FILTER = k; listDetail(id); } }, t))),
      rows.length ? rows : h("div", { class: "empty", text: ITEM_FILTER === "done" ? "Nothing completed yet." : "Nothing here. Add an item below." }),
      h("div", { class: "row", style: "margin-top:10px" }, h("div", { class: "grow" }, input), h("button", { class: "btn", onclick: addItem }, "Add"))),
    h("button", { class: "btn danger", onclick: async () => { if (await confirmBox("Delete this whole list and its items?")) { await api("DELETE", "/api/lists/" + id); go("/lists"); } } }, "Delete list")), "lists");
}

// ---------- journal
async function journal() {
  const r = await api("GET", "/api/journal");
  shell(h("div", {}, h("h1", { text: "Journal", style: "margin:6px 2px 10px" }),
    h("div", { class: "card" }, h("p", { class: "muted", style: "margin:0", text: "Only the things you said yes to keeping appear here, in your own words. Only you can read or delete them." })),
    r.entries.length ? r.entries.map(e => h("div", { class: "entry" }, h("div", { class: "row between" }, h("div", { class: "small muted", text: day(e.created_at) }),
      h("button", { class: "icon", "aria-label": "Delete this entry", onclick: async () => { if (await confirmBox("Delete this entry? This can't be undone.")) { await api("DELETE", "/api/journal/" + e.id); journal(); } } }, "✕")),
      h("div", { class: "tx", text: e.text }))) : h("div", { class: "card empty" }, h("div", { class: "big", text: "♡" }), "Nothing kept yet. Nine Tails only saves something here if you say yes.")), "journal");
}

// ---------- settings
async function settings() {
  const s = await api("GET", "/api/settings");
  const theme = h("select", { "aria-label": "Colour mode" }, [["auto", "Match my phone"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => h("option", { value: v, text: l })));
  theme.value = localStorage.getItem("nt_theme") || "auto";
  theme.onchange = () => { localStorage.setItem("nt_theme", theme.value); applyTheme(); };
  const clean = h("input", { type: "checkbox", role: "switch", "aria-label": "Lightly tidy dictated notes" }); clean.checked = s.dictation_cleanup;
  clean.onchange = async () => { await api("PUT", "/api/settings", { dictation_cleanup: clean.checked }); toast(clean.checked ? "Dictation will be lightly tidied." : "Dictation stays word for word."); };
  const del = h("input", { type: "text", placeholder: "Type DELETE to confirm", "aria-label": "Type DELETE" });
  shell(h("div", {}, h("h1", { text: "Settings", style: "margin:6px 2px 10px" }),
    h("div", { class: "card stack" }, h("h2", { text: "Look" }), theme),
    h("div", { class: "card stack" }, h("h2", { text: "Dictation" }), h("div", { class: "toggle" }, h("div", {}, h("div", { text: "Lightly tidy dictated notes" }), h("div", { class: "small muted", text: "Fixes spacing, capital letters and the full stop. Never changes your words. Off means word for word." })), clean)),
    h("div", { class: "card stack" }, h("h2", { text: "Your data" }), h("p", { class: "small muted", text: "Export gives you everything in one file. Delete removes your notes, lists and journal for good." }),
      h("div", { class: "row" }, h("button", { class: "btn ghost", onclick: async () => { const r = await fetch("/api/export", { headers: { "X-NT-App": "1" } }); if (r.ok) download("nine-tails-export.json", await r.text(), "application/json"); else toast("Export failed."); } }, "Export everything")),
      del, h("button", { class: "btn danger", onclick: async () => { try { const g = await api("POST", "/api/delete-all", { confirm: del.value }); toast(`Deleted ${g.notes} notes, ${g.lists} lists, ${g.journal} journal entries.`); del.value = ""; } catch (e) { toast(e.message); } } }, "Delete all my data")),
    h("div", { class: "card stack" }, h("h2", { text: "Privacy, honestly" }), h("p", { class: "small", text: "Only you can open this app: it asks for your passphrase. Nine Tails never shows your notes or journal to anyone else." }),
      h("p", { class: "small muted", text: SESSION.encrypted ? "Your data is encrypted on the computer's disk." : "Your data is not encrypted on the computer's disk yet. Whoever runs that computer could read its files. Encryption protects a stolen disk, not the person who runs the computer." })),
    SESSION.demo ? null : h("div", { class: "row" }, h("button", { class: "btn ghost", onclick: async () => { await api("POST", "/api/logout", {}); SESSION = null; boot(); } }, "Log out"))), "settings");
}
function applyTheme() { const t = localStorage.getItem("nt_theme") || "auto"; if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t; }

// ---------- routing
async function route() {
  if (!SESSION) return;
  if (!SESSION.authenticated) return authScreen(SESSION.setup_needed);
  const p = location.hash.replace(/^#/, "") || "/";
  ROUTE = p;
  try {
    let m;
    if (p === "/") await home();
    else if (p === "/notes") await notes();
    else if ((m = p.match(/^\/notes\/([0-9a-f]{12}|new)$/))) await noteEditor(m[1]);
    else if (p === "/lists") await lists();
    else if ((m = p.match(/^\/lists\/([0-9a-f]{12})$/))) await listDetail(m[1]);
    else if (p === "/journal") await journal();
    else if (p === "/settings") await settings();
    else go("/");
  } catch (e) { if (SESSION) toast(e.message); }
}
async function boot() { try { SESSION = await api("GET", "/api/session"); } catch (e) { $app.textContent = "Can't reach Nine Tails right now."; return; } route(); }
window.addEventListener("hashchange", route);
applyTheme();
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) navigator.serviceWorker.register("/sw.js").catch(() => {});
boot();
