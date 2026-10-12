"use strict";
// Nine Tails phone app. No libraries. Everything she wrote is put on the page with textContent, never as HTML.
const $app = document.getElementById("app");
let SESSION = null, ROUTE = "", STATUS = {}, DUE = [], NOTIFIED = new Set();
const ICONS = {
  arrow: '<path d="M7 17 17 7M8 7h9v9"/>',
  home: '<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4v-5H9v5H5a1 1 0 0 1-1-1z"/>',
  notes: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  lists: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1.2 1.2L7 4.9M3.5 12l1.2 1.2L7 10.9M3.5 18l1.2 1.2L7 16.9"/>',
  reminders: '<path d="M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15z"/><path d="M10 21h4"/>',
  journal: '<path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z"/><path d="M5 18a2 2 0 0 1 2-2h12M10 8h5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"/>'
};
const TABS = [["home", "Home"], ["notes", "Notes"], ["lists", "Lists"], ["reminders", "Reminders"], ["journal", "Journal"], ["settings", "Settings"]];

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
      h("button", { class: "btn soft", onclick: () => { d.close(); res(false); } }, "Cancel"),
      h("button", { class: "btn " + (danger ? "pink" : ""), onclick: () => { d.close(); res(true); } }, label)));
    d.addEventListener("close", () => { d.remove(); res(false); }); document.body.append(d); d.showModal();
  });
}
function download(name, text, type = "text/plain") { const a = h("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name }); document.body.append(a); a.click(); a.remove(); }
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
function sw(on, label, change) { const b = h("button", { class: "sw" + (on ? " on" : ""), role: "switch", "aria-checked": String(!!on), "aria-label": label }, h("i")); b.onclick = () => { on = !on; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); change(on); }; return b; }
function seg(opts, cur, pick) { return h("div", { class: "seg", role: "tablist" }, opts.map(([k, l]) => h("button", { class: k === cur ? "on" : "", role: "tab", "aria-selected": String(k === cur), onclick: () => pick(k) }, l))); }
function field(label, el) { return h("div", {}, h("label", { class: "f", text: label }), el); }
function fmtAt(t, tz) { try { return new Date(t * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: tz || undefined }); } catch (e) { return when(t); } }
function fmtTime(t, tz) { try { return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: tz || undefined }); } catch (e) { return ""; } }
function localTz() { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (e) { return "UTC"; } }
function dueCards() {
  return DUE.map(o => h("div", { class: "due", role: "alert" }, h("div", { class: "small muted", text: (o.status === "missed" ? "Missed at " : "Now, ") + fmtTime(o.due_at) }), h("div", { class: "ti", style: "font-weight:650;font-size:18px", text: o.title }),
    o.text ? h("div", { class: "pv muted", text: o.text }) : null,
    h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "btn sm", onclick: async () => { try { await api("POST", `/api/occurrences/${o.id}/ack`, {}); await refreshDue(true); } catch (e) { toast(e.message); } } }, "Got it"),
      h("button", { class: "btn sm soft", onclick: async () => { try { await api("POST", `/api/occurrences/${o.id}/snooze`, {}); await refreshDue(true); toast("I'll remind you again."); } catch (e) { toast(e.message); } } }, "Snooze"))));
}
async function refreshDue(redraw) {
  if (!SESSION || !SESSION.authenticated) return;
  try {
    const [d, s] = await Promise.all([api("GET", "/api/reminders/due"), api("GET", "/api/status")]);
    DUE = d.due; STATUS = s;
    if ("Notification" in window && Notification.permission === "granted") for (const o of DUE) if (!NOTIFIED.has(o.id)) { NOTIFIED.add(o.id); try { new Notification(o.title, { body: o.text || "" }); } catch (e) { /* not allowed here */ } }
  } catch (e) { return; }
  if (redraw) route(); else { const slot = document.getElementById("dueslot"); if (slot) slot.replaceChildren(...dueCards()); document.querySelectorAll(".nav .count").forEach(c => c.remove());
    if (DUE.length) document.querySelectorAll(".nav[aria-label=Reminders]").forEach(n => n.append(h("span", { class: "count", text: String(DUE.length) }))); }
}
const KIND = { voice: ["Voice note", "pink"], dictated: ["Dictated", ""], written: ["Written", "gray"] };
function greeting() { const hr = new Date().getHours(); return hr < 5 ? "Hi" : hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening"; }
function shell(content, active) {
  const nav = cls => TABS.map(([k, l]) => h("button", { class: "nav" + (k === active ? " on" : ""), "aria-label": l, title: l, "aria-current": k === active ? "page" : false, onclick: () => go("/" + (k === "home" ? "" : k)) },
    svg(k), k === "reminders" && DUE.length ? h("span", { class: "count", text: String(DUE.length) }) : null));
  const st = SESSION && SESSION.demo ? ["warn", "Sample preview"] : ({ connected: ["ok", "Nine Tails is here"], asleep: ["warn", "Nine Tails is asleep"], offline: ["off", "Nine Tails is offline"] }[STATUS.robot] || ["", "Robot status unknown"]);
  const nm = (SESSION && SESSION.name) || "N";
  $app.replaceChildren(h("div", { class: "stage" },
    h("header", { class: "bar glass" }, h("div", { class: "brand" }, h("img", { src: "/icon.svg", alt: "" }), h("span", { text: "Nine Tails" })),
      h("span", { class: "pill" }, h("i", { class: "dot " + st[0] }), st[1]), h("div", { class: "avatar", "aria-hidden": "true", text: nm.slice(0, 1).toUpperCase() })),
    h("div", { id: "dueslot" }, dueCards()), content),
    h("nav", { class: "dock glass", "aria-label": "Sections" }, nav()), h("nav", { class: "rail glass", "aria-label": "Sections" }, nav()),
    h("button", { class: "orb", "aria-label": "Add something new", onclick: newMenu }, h("img", { src: "/icon.svg", alt: "" })));
  window.scrollTo(0, 0);
}
function newMenu() {
  const d = h("dialog", {}, h("h2", { text: "Add something" }), h("div", { class: "stack" },
    ...[["Write a note", "/notes/new"], ["Make a list", "/lists"], ["Set a reminder", "/reminders/new"]].map(([t, p]) => h("button", { class: "btn soft", style: "width:100%", onclick: () => { d.close(); go(p); } }, t)),
    h("button", { class: "btn sm soft", onclick: () => d.close() }, "Close")));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal();
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
  $app.replaceChildren(h("div", { class: "center" }, h("div", { class: "glass panel stack" },
    h("img", { class: "logo", src: "/icon.svg", alt: "Nine Tails" }),
    h("h1", { text: setup ? "Welcome" : "Welcome back" }),
    h("p", { class: "muted", text: setup ? "This is your private space. The pairing code is shown on the computer that runs Nine Tails. Then you choose your own passphrase, and only you will know it." : "Enter your passphrase to open your notes, lists and journal." }),
    setup ? code : null, pass, msg, h("button", { class: "btn", onclick: submit }, setup ? "Create my passphrase" : "Open"))));
}

// ---------- home
async function home() {
  const [n, l, j, up] = await Promise.all([api("GET", "/api/notes"), api("GET", "/api/lists"), api("GET", "/api/journal"), api("GET", "/api/reminders/upcoming?days=3").catch(() => ({ upcoming: [] }))]);
  const quick = h("textarea", { placeholder: "Write something down...", "aria-label": "Quick note", rows: "3" });
  const name = SESSION.name || "friend";
  shell(h("div", {},
    h("section", { class: "hero" }, h("h1", {}, greeting() + ",", h("br"), h("b", { text: name })), h("p", { text: "Capture a thought, check off a list, or read back what you chose to keep." })),
    h("div", { class: "glass panel" }, h("div", { class: "row between" }, h("h2", { text: "Coming up" }), h("button", { class: "go", "aria-label": "Open reminders", onclick: () => go("/reminders") }, svg("arrow"))),
      up.upcoming.length ? up.upcoming.slice(0, 3).map(u => h("div", { class: "card", onclick: () => go("/reminders/" + u.reminder_id) }, h("div", { class: "ti", text: u.title }), h("div", { class: "pv", text: fmtAt(u.at, u.tz) })))
        : h("div", { class: "empty", text: "Nothing in the next 3 days. Say \"Nine Tails, remind me to...\" or add one here." })),
    h("div", { class: "glass panel stack" }, h("h2", { text: "Quick note" }), quick,
      h("div", { class: "row between" }, h("span", { class: "small muted", text: 'Or say: "Nine Tails, write this down..."' }),
        h("button", { class: "btn sm", onclick: async () => { if (!quick.value.trim()) return; try { await api("POST", "/api/notes", { body: quick.value }); toast("Saved."); quick.value = ""; home(); } catch (e) { toast(e.message); } } }, "Save"))),
    h("div", { class: "glass panel" }, h("div", { class: "row between" }, h("h2", { text: "Recent notes" }), h("button", { class: "go", "aria-label": "Open notes", onclick: () => go("/notes") }, svg("arrow"))),
      n.notes.length ? n.notes.slice(0, 3).map(noteCard) : h("div", { class: "empty" }, h("div", { class: "em", text: "✎" }), "No notes yet. Tell Nine Tails to write something down, or add one here.")),
    h("div", { class: "glass panel" }, h("div", { class: "row between" }, h("h2", { text: "Lists" }), h("button", { class: "go", "aria-label": "Open lists", onclick: () => go("/lists") }, svg("arrow"))),
      l.lists.length ? l.lists.slice(0, 3).map(listCard) : h("div", { class: "empty", text: "No lists yet. Try: \"Nine Tails, make me a grocery list.\"" })),
    h("div", { class: "glass panel" }, h("div", { class: "row between" }, h("h2", { text: "Journal" }), h("button", { class: "go", "aria-label": "Open journal", onclick: () => go("/journal") }, svg("arrow"))),
      h("p", { class: "muted", text: j.entries.length ? `${j.entries.length} private ${j.entries.length === 1 ? "entry" : "entries"} you chose to keep. Only you can see them.` : "Nothing kept yet. When you say yes to keeping a note after a check-in, it appears here." }))), "home");
}
function noteCard(n) {
  const [lab, cls] = KIND[n.kind] || ["Note", "gray"];
  return h("a", { class: "card", href: "#/notes/" + n.id }, h("div", { class: "meta" }, h("span", { class: "badge " + cls, text: lab }), n.transcript_state === "pending" ? h("span", { class: "badge warn", text: "transcription pending" }) : null, h("span", { text: when(n.created_at) })),
    h("div", { class: "ti", text: n.title }), h("div", { class: "pv", text: n.body }), n.tags.length ? h("div", { class: "tagrow", style: "margin-top:6px" }, n.tags.map(t => h("span", { class: "badge gray", text: "#" + t }))) : null);
}
function listCard(l) {
  const pct = l.total ? Math.round(100 * l.done / l.total) : 0;
  return h("a", { class: "card", href: "#/lists/" + l.id }, h("div", { class: "row between" }, h("div", { class: "ti", text: l.name }), h("span", { class: "small muted", text: `${l.done}/${l.total} done` })), h("div", { class: "meter", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100" }, h("i", { style: `width:${pct}%` })));
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
  shell(h("div", {}, h("section", { class: "hero" }, h("h1", {}, h("b", { text: "Notes" }))), search, h("div", { class: "chips" }, chips),
    res.notes.length ? res.notes.map(noteCard) : h("div", { class: "glass panel empty" }, h("div", { class: "em", text: "✎" }), NOTE_Q ? "Nothing matches that." : "No notes here yet.")), "notes");
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
  shell(h("div", {}, h("a", { class: "pill", href: "#/notes", text: "‹ Notes" }),
    h("div", { class: "glass panel stack" }, h("div", { class: "row" }, h("span", { class: "badge " + cls, text: lab }), n.cleaned ? h("span", { class: "badge gray", text: "spacing and capitals tidied" }) : null,
      n.transcript_state === "pending" ? h("span", { class: "badge warn", text: "transcription pending" }) : null, isNew ? null : h("span", { class: "small muted", text: when(n.created_at) })),
      !isNew ? h("p", { class: "small muted", text: explain }) : null, title, body, tags, h("div", { class: "row" }, h("button", { class: "btn", onclick: save }, "Save"),
        isNew ? null : h("button", { class: "btn soft", onclick: () => download((n.title || "note").replace(/[^\w\- ]+/g, "") + ".txt", `${title.value}\n\n${body.value}\n`) }, "Export"))),
    isNew ? null : h("div", { class: "glass panel stack" }, h("h3", { text: "Add more" }), app, h("button", { class: "btn soft sm", onclick: async () => { if (!app.value.trim()) return; try { const r = await api("POST", `/api/notes/${id}/append`, { text: app.value }); body.value = r.body; app.value = ""; toast("Added."); } catch (e) { toast(e.message); } } }, "Add to note")),
    isNew ? null : h("div", { class: "glass panel stack" }, h("div", { class: "row between" }, h("span", { text: "Summary" }), h("span", { class: "badge gray", text: "not available yet" })), h("p", { class: "small muted", text: "Summaries need the language model. When they exist they will sit beside your note and never replace it." })),
    isNew ? null : h("div", { class: "row" }, h("button", { class: "btn soft", onclick: async () => { await api("PUT", "/api/notes/" + id, { archived: !n.archived }); toast(n.archived ? "Restored." : "Archived."); go("/notes"); } }, n.archived ? "Restore" : "Archive"),
      h("button", { class: "btn danger", onclick: async () => { if (await confirmBox("Delete this note? This can't be undone.")) { await api("DELETE", "/api/notes/" + id); toast("Deleted."); go("/notes"); } } }, "Delete"))), "notes");
}

// ---------- lists
let ITEM_FILTER = "active";
async function lists() {
  const res = await api("GET", "/api/lists");
  const name = h("input", { type: "text", placeholder: "New list, like Groceries", "aria-label": "New list name" });
  const add = async () => { if (!name.value.trim()) return; try { const l = await api("POST", "/api/lists", { name: name.value }); go("/lists/" + l.id); } catch (e) { toast(e.message); } };
  name.addEventListener("keydown", e => e.key === "Enter" && add());
  shell(h("div", {}, h("section", { class: "hero" }, h("h1", {}, h("b", { text: "Lists" }))), h("div", { class: "glass panel row" }, h("div", { class: "grow" }, name), h("button", { class: "btn", onclick: add }, "Add")),
    res.lists.length ? res.lists.map(listCard) : h("div", { class: "glass panel empty" }, h("div", { class: "em", text: "☑" }), "No lists yet. Make one here, or say \"Nine Tails, make me a grocery list.\"")), "lists");
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
  shell(h("div", {}, h("a", { class: "pill", href: "#/lists", text: "‹ Lists" }),
    h("div", { class: "glass panel" }, h("div", { class: "row between" }, h("h2", { text: l.name }), h("button", { class: "btn soft sm", onclick: async () => { const t = prompt("Rename list", l.name); if (t && t.trim()) { try { await api("PUT", "/api/lists/" + id, { name: t }); listDetail(id); } catch (e) { toast(e.message); } } } }, "Rename")),
      h("div", { class: "small muted", text: `${l.done} of ${l.total} done` }),
      h("div", { class: "chips" }, [["active", "To do"], ["done", "Done"], ["all", "All"]].map(([k, t]) => h("button", { class: "chip" + (ITEM_FILTER === k ? " on" : ""), onclick: () => { ITEM_FILTER = k; listDetail(id); } }, t))),
      rows.length ? rows : h("div", { class: "empty", text: ITEM_FILTER === "done" ? "Nothing completed yet." : "Nothing here. Add an item below." }),
      h("div", { class: "row", style: "margin-top:10px" }, h("div", { class: "grow" }, input), h("button", { class: "btn", onclick: addItem }, "Add"))),
    h("button", { class: "btn danger", onclick: async () => { if (await confirmBox("Delete this whole list and its items?")) { await api("DELETE", "/api/lists/" + id); go("/lists"); } } }, "Delete list")), "lists");
}

// ---------- journal
async function journal() {
  const r = await api("GET", "/api/journal");
  shell(h("div", {}, h("section", { class: "hero" }, h("h1", {}, h("b", { text: "Journal" }))),
    h("div", { class: "glass panel" }, h("p", { class: "muted", style: "margin:0", text: "Only the things you said yes to keeping appear here, in your own words. Only you can read or delete them." })),
    r.entries.length ? r.entries.map(e => h("div", { class: "entry" }, h("div", { class: "row between" }, h("div", { class: "small muted", text: day(e.created_at) }),
      h("button", { class: "icon", "aria-label": "Delete this entry", onclick: async () => { if (await confirmBox("Delete this entry? This can't be undone.")) { await api("DELETE", "/api/journal/" + e.id); journal(); } } }, "✕")),
      h("div", { class: "tx", text: e.text }))) : h("div", { class: "glass panel empty" }, h("div", { class: "em", text: "♡" }), "Nothing kept yet. Nine Tails only saves something here if you say yes.")), "journal");
}

// ---------- reminders
const REPEAT = [["once", "Once"], ["daily", "Every day"], ["weekly", "Certain days"], ["interval", "Hours"]];
const EVERY = [[30, "30 minutes"], [60, "hour"], [120, "2 hours"], [180, "3 hours"], [240, "4 hours"], [360, "6 hours"], [480, "8 hours"]];
let REM_TAB = "upcoming";
function remStatus(r) { return { scheduled: ["", "Scheduled"], paused: ["gray", "Paused"], completed: ["gray", "Done"], ended: ["gray", "Ended"] }[r.status] || ["gray", r.status]; }
function remCard(r) {
  const [cls, lab] = remStatus(r);
  return h("a", { class: "card", href: "#/reminders/" + r.id }, h("div", { class: "meta" }, h("span", { class: "badge " + (cls || ""), text: lab }), r.priority === "high" ? h("span", { class: "badge pink", text: "important" }) : null, h("span", { text: r.schedule_text })),
    h("div", { class: "ti", text: r.title }), h("div", { class: "pv", text: r.next_at ? "Next: " + fmtAt(r.next_at, r.tz) : "Nothing coming up" }),
    r.last_delivery ? h("div", { class: "small muted", text: "Last: " + r.last_delivery }) : null);
}
const occText = o => ({ pending: "waiting", delivered: "delivered", acknowledged: "done", snoozed: "snoozed", skipped: "skipped", missed: "missed" }[o.status] || o.status);
async function reminders() {
  const [all, st] = await Promise.all([api("GET", "/api/reminders"), api("GET", "/api/status")]);
  STATUS = st;
  let body;
  if (REM_TAB === "upcoming") {
    const up = (await api("GET", "/api/reminders/upcoming?days=7")).upcoming, groups = [];
    for (const u of up) { const k = new Date(u.at * 1000).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", timeZone: u.tz }); let g = groups.find(x => x[0] === k); if (!g) groups.push(g = [k, []]); g[1].push(u); }
    body = groups.length ? groups.map(([k, us]) => h("div", { class: "glass panel tight" }, h("h3", { text: k }), us.map(u => h("a", { class: "item", href: "#/reminders/" + u.reminder_id }, h("div", { class: "tx", text: u.title }), h("span", { class: "badge", text: fmtTime(u.at, u.tz) })))))
      : h("div", { class: "glass panel empty" }, h("div", { class: "em", text: "🔔" }), "Nothing in the next 7 days.");
  } else if (REM_TAB === "all") {
    body = all.reminders.length ? all.reminders.map(remCard) : h("div", { class: "glass panel empty" }, h("div", { class: "em", text: "🔔" }), "No reminders yet. Add one, or say \"Nine Tails, remind me to...\"");
  } else {
    const hist = (await api("GET", "/api/reminders/history")).history, names = Object.fromEntries(all.reminders.map(r => [r.id, r.title]));
    body = hist.length ? h("div", { class: "glass panel tight" }, hist.map(o => h("div", { class: "item" }, h("div", { class: "tx" }, h("div", { text: names[o.reminder_id] || "Deleted reminder" }), h("div", { class: "small muted", text: fmtAt(o.due_at) }), h("div", { class: "small muted", text: ReminderDelivery(o) })),
      h("span", { class: "badge " + (o.status === "missed" ? "warn" : o.status === "acknowledged" ? "" : "gray"), text: occText(o) }))))
      : h("div", { class: "glass panel empty" }, "Nothing has gone off yet.");
  }
  shell(h("div", {}, h("section", { class: "hero" }, h("h1", {}, h("b", { text: "Reminders" })), h("p", { text: "Nine Tails says them out loud, in her own words. Your phone shows them too while this app is open." })),
    seg([["upcoming", "Coming up"], ["all", "All"], ["history", "History"]], REM_TAB, k => { REM_TAB = k; reminders(); }),
    body, h("div", { class: "row", style: "margin-top:14px" }, h("button", { class: "btn pink", onclick: () => go("/reminders/new") }, "New reminder"))), "reminders");
}
function ReminderDelivery(o) {
  const p = [];
  if (o.robot === "spoken") p.push("spoken by Nine Tails"); else if (o.robot === "unavailable" || o.robot === "failed") p.push("Nine Tails could not say it"); else if (o.robot === "quiet") p.push("quiet hours");
  if (o.phone === "shown") p.push("seen on phone"); else if (o.phone === "pending") p.push("not seen on phone");
  return p.join(", ") || "nothing delivered";
}
async function reminderEditor(id) {
  const isNew = id === "new";
  const cfg = (await api("GET", "/api/reminders")).settings;
  const r = isNew ? { title: "", message: "", style: "auto", kind: "daily", time: "09:00", date: "", weekdays: [], interval_minutes: 120, window_start: "09:00", window_end: "21:00", start_date: "", end_date: "", tz: cfg.tz || localTz(), snooze_minutes: 10, grace_minutes: 30, priority: "normal", channels: { robot: true, phone: true }, enabled: true }
    : (await api("GET", "/api/reminders")).reminders.find(x => x.id === id);
  if (!r) { go("/reminders"); return; }
  const S = { ...r, weekdays: [...(r.weekdays || [])], channels: { ...r.channels } };
  const inp = (type, key, extra) => { const e = h("input", { type, value: S[key] || "", ...extra }); e.oninput = () => { S[key] = e.value; }; return e; };
  const title = inp("text", "title", { placeholder: "What should I remind you about?", "aria-label": "Title", maxlength: "80" });
  const time = inp("time", "time", { "aria-label": "Time" }), date = inp("date", "date", { "aria-label": "Date" });
  const ws = inp("time", "window_start", { "aria-label": "From" }), we = inp("time", "window_end", { "aria-label": "Until" });
  const sd = inp("date", "start_date", { "aria-label": "Starts on" }), ed = inp("date", "end_date", { "aria-label": "Ends on" }), tz = inp("text", "tz", { "aria-label": "Time zone", placeholder: "Area/City" });
  const msg = h("textarea", { placeholder: "Optional. Use {name}, {title} and {time}. Leave empty for her own wording.", "aria-label": "What she says", rows: "2", style: "min-height:70px" }); msg.value = S.message || ""; msg.oninput = () => { S.message = msg.value; };
  const every = h("select", { "aria-label": "How often" }, EVERY.map(([v, l]) => h("option", { value: String(v), text: "Every " + l }))); every.value = String(S.interval_minutes || 120); every.onchange = () => { S.interval_minutes = Number(every.value); };
  const style = h("select", { "aria-label": "Her style" }, [["auto", "Automatic (by kind of reminder)"], ["gentle", "Gentle"], ["plain", "Plain"], ["direct", "Direct"], ["playful", "Playful"], ["quiet", "Quiet (phone only)"]].map(([v, l]) => h("option", { value: v, text: l }))); style.value = S.style; style.onchange = () => { S.style = style.value; };
  const pr = h("select", { "aria-label": "Importance" }, [["normal", "Normal"], ["high", "Important"], ["low", "Low"]].map(([v, l]) => h("option", { value: v, text: l }))); pr.value = S.priority; pr.onchange = () => { S.priority = pr.value; };
  const sn = h("input", { type: "number", min: "1", max: "240", value: String(S.snooze_minutes), "aria-label": "Snooze minutes" }); sn.oninput = () => { S.snooze_minutes = Number(sn.value); };
  const gr = h("input", { type: "number", min: "5", max: "240", value: String(S.grace_minutes), "aria-label": "Minutes before it counts as missed" }); gr.oninput = () => { S.grace_minutes = Number(gr.value); };
  const when_ = h("div", { class: "stack" }), samples = h("div", {});
  const draw = () => {
    const chips = h("div", { class: "row" }, DAYS.map((d, i) => h("button", { class: "chip day" + (S.weekdays.includes(i) ? " on" : ""), "aria-label": d, "aria-pressed": String(S.weekdays.includes(i)), onclick: () => { S.weekdays = S.weekdays.includes(i) ? S.weekdays.filter(x => x !== i) : [...S.weekdays, i].sort(); draw(); } }, d[0])));
    when_.replaceChildren(...[seg(REPEAT, S.kind, k => { S.kind = k; draw(); }), S.kind === "once" ? field("Date", date) : null, S.kind === "weekly" ? field("Days", chips) : null,
      ...(S.kind === "interval" ? [field("Repeat", every), h("div", { class: "row" }, h("div", { class: "grow" }, field("From", ws)), h("div", { class: "grow" }, field("Until", we)))] : [field(S.kind === "once" ? "Time" : "At", time)])].filter(Boolean));
  };
  draw();
  const preview = async () => {
    try { const p = await api("POST", "/api/reminders/preview", { title: S.title, message: S.message, style: S.style, time: S.time, tz: S.tz });
      samples.replaceChildren(h("p", { class: "small muted", text: "How she might say it (the real wording picks one at the time):" }), ...p.samples.map(t => h("div", { class: "sample-line", text: t }))); } catch (e) { toast(e.message); }
  };
  const payload = () => { const o = { title: S.title, message: S.message, style: S.style, kind: S.kind, tz: S.tz, snooze_minutes: S.snooze_minutes, grace_minutes: S.grace_minutes, priority: S.priority, channels: S.channels, start_date: S.start_date || "", end_date: S.end_date || "" };
    if (S.kind === "interval") { o.interval_minutes = S.interval_minutes; o.window_start = S.window_start; o.window_end = S.window_end; } else o.time = S.time;
    if (S.kind === "once") o.date = S.date; if (S.kind === "weekly") o.weekdays = S.weekdays; return o; };
  const save = async () => { try { if (isNew) { const x = await api("POST", "/api/reminders", payload()); toast("Saved."); go("/reminders/" + x.id); } else { await api("PUT", "/api/reminders/" + id, payload()); toast("Saved."); go("/reminders"); } } catch (e) { toast(e.message); } };
  const act = async (path, msgText, method = "POST") => { try { await api(method, path, {}); toast(msgText); go("/reminders"); } catch (e) { toast(e.message); } };
  const hist = isNew ? [] : (await api("GET", "/api/reminders/history?id=" + id)).history.slice(0, 6);
  shell(h("div", {}, h("a", { class: "pill", href: "#/reminders", text: "‹ Reminders" }),
    h("div", { class: "glass panel stack" }, isNew ? h("h2", { text: "New reminder" }) : h("div", { class: "row" }, h("span", { class: "badge " + remStatus(r)[0], text: remStatus(r)[1] }), h("span", { class: "small muted", text: r.schedule_text })), title, when_,
      h("div", { class: "toggle" }, h("div", {}, h("div", { text: "Nine Tails says it" }), h("div", { class: "small muted", text: "Out loud, when she is awake and nearby." })), sw(S.channels.robot, "Nine Tails says it", v => { S.channels.robot = v; })),
      h("div", { class: "toggle" }, h("div", {}, h("div", { text: "Show it on my phone" }), h("div", { class: "small muted", text: "Only while this app is open." })), sw(S.channels.phone, "Show on phone", v => { S.channels.phone = v; })),
      field("How she says it", style), msg, h("div", { class: "row" }, h("button", { class: "btn soft sm", onclick: preview }, "Hear a few examples")), samples),
    h("details", { class: "glass panel" }, h("summary", { text: "More options" }), h("div", { class: "stack" }, field("Importance", pr), field("Snooze for (minutes)", sn), field("Counts as missed after (minutes)", gr),
      field("Starts on (optional)", sd), field("Ends on (optional)", ed), field("Time zone", tz))),
    h("div", { class: "row" }, h("button", { class: "btn pink", onclick: save }, "Save"), isNew ? null : h("button", { class: "btn soft", onclick: () => act(`/api/reminders/${id}/${r.enabled ? "pause" : "resume"}`, r.enabled ? "Paused." : "Resumed.") }, r.enabled ? "Pause" : "Resume")),
    isNew ? null : h("div", { class: "row" }, h("button", { class: "btn soft sm", onclick: () => act(`/api/reminders/${id}/skip`, "Skipping the next one.") }, "Skip next"), h("button", { class: "btn soft sm", onclick: () => act(`/api/reminders/${id}/duplicate`, "Copied.") }, "Duplicate"),
      h("button", { class: "btn danger sm", onclick: async () => { if (await confirmBox("Delete this reminder and its history?")) act("/api/reminders/" + id, "Deleted.", "DELETE"); } }, "Delete")),
    hist.length ? h("div", { class: "glass panel tight" }, h("h3", { text: "Recent" }), hist.map(o => h("div", { class: "item" }, h("div", { class: "tx" }, h("div", { text: fmtAt(o.due_at, r.tz) }), h("div", { class: "small muted", text: ReminderDelivery(o) })), h("span", { class: "badge gray", text: occText(o) })))) : null), "reminders");
}

// ---------- settings
async function settings() {
  const [s, rs, st] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/reminder-settings").catch(() => null), api("GET", "/api/status")]);
  STATUS = st;
  const theme = h("select", { "aria-label": "Colour mode" }, [["auto", "Match my phone"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => h("option", { value: v, text: l })));
  theme.value = localStorage.getItem("nt_theme") || "auto";
  theme.onchange = () => { localStorage.setItem("nt_theme", theme.value); applyTheme(); };
  const del = h("input", { type: "text", placeholder: "Type DELETE to confirm", "aria-label": "Type DELETE" });
  const put = async (k, v) => { try { await api("PUT", "/api/reminder-settings", { [k]: v }); toast("Saved."); } catch (e) { toast(e.message); } };
  const sel = (label, key, opts) => { const e = h("select", { "aria-label": label }, opts.map(([v, l]) => h("option", { value: v, text: l }))); e.value = rs[key]; e.onchange = () => put(key, e.value); return field(label, e); };
  const tm = (label, key) => { const e = h("input", { type: "time", value: rs[key] || "", "aria-label": label }); e.onchange = () => put(key, e.value); return field(label, e); };
  const tz = h("input", { type: "text", value: rs ? rs.tz : "", "aria-label": "Time zone" }); if (rs) tz.onchange = () => put("tz", tz.value);
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  const permBtn = h("button", { class: "btn soft sm", onclick: async () => { try { const p = await Notification.requestPermission(); toast(p === "granted" ? "Alerts allowed while the app is open." : "Alerts stay off."); settings(); } catch (e) { toast("Not available here."); } } }, "Allow alerts");
  shell(h("div", {}, h("section", { class: "hero" }, h("h1", {}, h("b", { text: "Settings" }))),
    h("div", { class: "glass panel stack" }, h("h2", { text: "Look" }), theme),
    rs ? h("div", { class: "glass panel stack" }, h("h2", { text: "Reminders" }),
      h("div", { class: "toggle" }, h("div", {}, h("div", { text: "Nine Tails speaks them" }), h("div", { class: "small muted", text: "Off means she stays quiet. Your phone still shows them." })), sw(rs.speak, "Spoken reminders", v => put("speak", v))),
      h("div", { class: "toggle" }, h("div", {}, h("div", { text: "Show them on my phone" })), sw(rs.phone, "Phone reminders", v => put("phone", v))),
      sel("If she could not say one", "fallback", [["phone", "Show it on my phone"], ["skip", "Let it go"]]),
      sel("Her default style", "style", [["auto", "Automatic"], ["gentle", "Gentle"], ["plain", "Plain"], ["direct", "Direct"], ["playful", "Playful"], ["quiet", "Quiet"]]),
      tm("Quiet hours start (she stays silent)", "quiet_start"), tm("Quiet hours end", "quiet_end"), field("Time zone", tz),
      h("p", { class: "small muted", text: "Phone alerts: this app can only alert you while it is open. Closed, it cannot wake your phone, so Nine Tails speaking is the dependable way. " + (perm === "granted" ? "Alerts are allowed." : perm === "denied" ? "Alerts are blocked in your browser settings." : perm === "unsupported" ? "This browser can't show alerts." : "") }),
      perm === "default" ? permBtn : null) : null,
    h("div", { class: "glass panel stack" }, h("h2", { text: "Dictation" }), h("div", { class: "toggle" }, h("div", {}, h("div", { text: "Lightly tidy dictated notes" }), h("div", { class: "small muted", text: "Fixes spacing, capital letters and the full stop. Never changes your words. Off means word for word." })),
      sw(s.dictation_cleanup, "Lightly tidy dictated notes", async v => { await api("PUT", "/api/settings", { dictation_cleanup: v }); toast(v ? "Dictation will be lightly tidied." : "Dictation stays word for word."); }))),
    h("div", { class: "glass panel stack" }, h("h2", { text: "Your data" }), h("p", { class: "small muted", text: "Export gives you everything in one file. Delete removes your notes, lists, reminders and journal for good." }),
      h("div", { class: "row" }, h("button", { class: "btn soft", onclick: async () => { const r = await fetch("/api/export", { headers: { "X-NT-App": "1" } }); if (r.ok) download("nine-tails-export.json", await r.text(), "application/json"); else toast("Export failed."); } }, "Export everything")),
      del, h("button", { class: "btn danger", onclick: async () => { try { const g = await api("POST", "/api/delete-all", { confirm: del.value }); toast(`Deleted ${g.notes} notes, ${g.lists} lists, ${g.reminders || 0} reminders, ${g.journal} journal entries.`); del.value = ""; } catch (e) { toast(e.message); } } }, "Delete all my data")),
    h("div", { class: "glass panel stack" }, h("h2", { text: "Privacy, honestly" }), h("p", { class: "small", text: "Only you can open this app: it asks for your passphrase. Nine Tails never shows your notes or journal to anyone else." }),
      h("p", { class: "small muted", text: SESSION.encrypted ? "Your data is encrypted on the computer's disk." : "Your data is not encrypted on the computer's disk yet. Whoever runs that computer could read its files. Encryption protects a stolen disk, not the person who runs the computer." })),
    SESSION.demo ? null : h("div", { class: "row" }, h("button", { class: "btn soft", onclick: async () => { await api("POST", "/api/logout", {}); SESSION = null; boot(); } }, "Log out"))), "settings");
}
function applyTheme() { const t = localStorage.getItem("nt_theme") || "auto"; if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t; }

// ---------- routing
async function route() {
  if (!SESSION) return;
  if (!SESSION.authenticated) return authScreen(SESSION.setup_needed);
  if (!route.first) { route.first = true; await refreshDue(false); }
  const p = location.hash.replace(/^#/, "") || "/";
  ROUTE = p;
  try {
    let m;
    if (p === "/") await home();
    else if (p === "/notes") await notes();
    else if ((m = p.match(/^\/notes\/([0-9a-f]{12}|new)$/))) await noteEditor(m[1]);
    else if (p === "/lists") await lists();
    else if ((m = p.match(/^\/lists\/([0-9a-f]{12})$/))) await listDetail(m[1]);
    else if (p === "/reminders") await reminders();
    else if ((m = p.match(/^\/reminders\/([0-9a-f]{12}|new)$/))) await reminderEditor(m[1]);
    else if (p === "/journal") await journal();
    else if (p === "/settings") await settings();
    else go("/");
  } catch (e) { if (SESSION) toast(e.message); }
}
async function boot() { route.first = false; try { SESSION = await api("GET", "/api/session"); } catch (e) { $app.textContent = "Can't reach Nine Tails right now."; return; } route(); }
window.addEventListener("hashchange", route);
applyTheme();
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) navigator.serviceWorker.register("/sw.js").catch(() => {});
boot();
setInterval(() => refreshDue(false), 30000);
