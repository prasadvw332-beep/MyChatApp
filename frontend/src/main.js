import "./style.css";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { API_BASE, $, el, errorFrom } from "./lib.js";
import { initEvals } from "./evals.js";

const API = API_BASE + "/chat";
const fileUrl = (docId) => `${API_BASE}/documents/${encodeURIComponent(docId)}/file`;
const box = $("messages");
const form = $("form");
const input = $("input");
const send = $("send");
const welcome = $("welcome");
const chatList = $("chatlist");
const chatSearch = $("chatsearch");
const layout = $("layout");
const toBottom = $("tobottom");
const root = document.documentElement;

marked.setOptions({ gfm: true, breaks: true });
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

const svg = (body, size = 16) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
const ICON = {
  copy: svg('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
  check: svg('<path d="M20 6 9 17l-5-5"/>'),
  refresh: svg('<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>'),
  edit: svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
  pencil: svg('<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>', 14),
  trash: svg('<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>', 14),
  file: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
  close: svg('<path d="M18 6 6 18M6 6l12 12"/>', 12),
};

// Two-step delete: first click arms the button ("Delete"), second click confirms
function confirmDelete(btn, onConfirm) {
  const icon = btn.innerHTML;
  let timer;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (btn.classList.contains("confirm")) {
      clearTimeout(timer);
      onConfirm();
      return;
    }
    btn.classList.add("confirm");
    btn.textContent = "Delete";
    timer = setTimeout(() => {
      btn.classList.remove("confirm");
      btn.innerHTML = icon;
    }, 3000);
  });
}

function extOf(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return ["pdf", "txt", "md"].includes(ext) ? ext : "txt";
}
function docIcon(name) {
  const ext = extOf(name);
  return el("div", `doc-icon ${ext}`, ext.toUpperCase());
}

/* ---------- storage ---------- */
function load(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v ? JSON.parse(v) : fallback;
  } catch {
    return fallback;
  }
}
function store(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}

/* ---------- theme ---------- */
let savedTheme = null;
try { savedTheme = localStorage.getItem("theme"); } catch {}
root.dataset.theme = savedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
$("theme").addEventListener("click", () => {
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch {}
});

/* ---------- chats state ---------- */
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
let chats = load("chats", []);
let chat = chats.find((c) => c.id === load("activeChat", null)) || chats[0];
let controller = null; // set while a reply is streaming

function createChat() {
  const c = { id: uid(), title: "New chat", messages: [], updatedAt: Date.now() };
  chats.unshift(c);
  return c;
}
if (!chat) chat = createChat();

function persist() {
  store("chats", chats);
  store("activeChat", chat.id);
}

/* ---------- sidebar ---------- */
const mobile = matchMedia("(max-width: 800px)");
const closeSidebarOnMobile = () => mobile.matches && layout.classList.remove("sidebar-open");
layout.classList.toggle("sidebar-open", !mobile.matches && load("sidebarOpen", true));
function setSidebar(open) {
  layout.classList.toggle("sidebar-open", open);
  if (!mobile.matches) store("sidebarOpen", open);
}
$("menu").addEventListener("click", () => setSidebar(true));
$("collapse").addEventListener("click", () => setSidebar(false));
$("backdrop").addEventListener("click", () => layout.classList.remove("sidebar-open"));

const evals = initEvals({ button: $("evals-btn"), view: $("evals"), app: $("app"), onToggle: () => renderList() });
$("evals-btn").addEventListener("click", closeSidebarOnMobile);

function selectChat(id) {
  evals.hide();
  const target = chats.find((c) => c.id === id);
  if (!target || target === chat) return closeSidebarOnMobile();
  if (controller) controller.abort();
  if (chat.messages.length === 0) chats = chats.filter((c) => c !== chat);
  chat = target;
  persist();
  renderAll();
  closeSidebarOnMobile();
}

function newChat() {
  evals.hide();
  if (chat.messages.length > 0) {
    if (controller) controller.abort();
    chat = createChat();
    persist();
    renderAll();
  }
  closeSidebarOnMobile();
  input.focus();
}

function deleteChat(id) {
  if (controller && chat.id === id) controller.abort();
  chats = chats.filter((c) => c.id !== id);
  if (chat.id === id) chat = chats.find((c) => c.messages.length) || createChat();
  persist();
  renderAll();
}

$("newchat").addEventListener("click", newChat);
chatSearch.addEventListener("input", () => renderList());

function groupLabel(ts) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86400000;
  if (ts >= today) return "Today";
  if (ts >= today - day) return "Yesterday";
  if (ts >= today - 7 * day) return "Previous 7 days";
  if (ts >= today - 30 * day) return "Previous 30 days";
  return new Date(ts).toLocaleString(undefined, { month: "long", year: "numeric" });
}

function startRename(item, c) {
  const field = el("input", "rename-input");
  field.value = c.title;
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const title = field.value.trim();
    if (save && title && title !== c.title) {
      c.title = title;
      c.titleBy = "user"; // never auto-rename a chat the user named
      persist();
    }
    renderList();
  };
  field.addEventListener("click", (e) => e.stopPropagation());
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  field.addEventListener("blur", () => finish(true));
  item.replaceChildren(field);
  field.focus();
  field.select();
}

function renderList() {
  const q = chatSearch.value.trim().toLowerCase();
  const visible = [...chats]
    .filter((c) => c.messages.length) // empty "New chat"s stay out of the history
    .filter((c) => !q || c.title.toLowerCase().includes(q) ||
      c.messages.some((m) => m.content.toLowerCase().includes(q)))
    .sort((a, b) => b.updatedAt - a.updatedAt);

  chatList.innerHTML = "";
  if (!visible.length) {
    chatList.appendChild(el("div", "empty-note", q ? "No matching chats" : "Your chats will appear here"));
    return;
  }
  let lastGroup = null;
  visible.forEach((c) => {
    const group = groupLabel(c.updatedAt);
    if (group !== lastGroup) {
      chatList.appendChild(el("div", "group-label", group));
      lastGroup = group;
    }
    const item = el("div", "chat-item" + (c === chat && !evals.isOpen() ? " active" : ""));
    item.title = c.title;
    const actions = el("div", "item-actions");
    const rename = el("button");
    rename.type = "button";
    rename.title = "Rename";
    rename.innerHTML = ICON.pencil;
    rename.addEventListener("click", (e) => {
      e.stopPropagation();
      startRename(item, c);
    });
    const del = el("button", "danger");
    del.type = "button";
    del.title = "Delete chat";
    del.innerHTML = ICON.trash;
    confirmDelete(del, () => deleteChat(c.id));
    actions.append(rename, del);
    item.append(el("span", "title", c.title), actions);
    item.addEventListener("click", () => selectChat(c.id));
    chatList.appendChild(item);
  });
}

/* ---------- message rendering ---------- */
async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    return;
  }
  const old = btn.innerHTML;
  btn.innerHTML = ICON.check + (btn.dataset.label ? " Copied" : "");
  setTimeout(() => (btn.innerHTML = old), 1500);
}

// Wrap each <pre> in a block with a language label and Copy button
function enhanceCode(bubble) {
  bubble.querySelectorAll("pre").forEach((pre) => {
    const code = pre.querySelector("code");
    const lang = (code?.className.match(/language-(\S+)/) || [])[1] || "code";
    const wrap = el("div", "codeblock");
    const head = el("div", "codehead");
    const btn = el("button");
    btn.type = "button";
    btn.dataset.label = "1";
    btn.innerHTML = ICON.copy + " Copy";
    btn.addEventListener("click", () => copyText((code || pre).innerText, btn));
    head.append(el("span", null, lang), btn);
    pre.replaceWith(wrap);
    wrap.append(head, pre);
  });
}

function setBubble(bubble, m) {
  if (m.role === "user" || m.ui) {
    bubble.textContent = m.content;
  } else if (!m.content) {
    bubble.innerHTML = '<span class="thinking">Thinking…</span>';
  } else {
    bubble.innerHTML = DOMPurify.sanitize(marked.parse(m.content));
    enhanceCode(bubble);
  }
}

function actionButton(icon, title, onClick) {
  const b = el("button", "act");
  b.type = "button";
  b.title = title;
  b.setAttribute("aria-label", title);
  b.innerHTML = icon;
  b.addEventListener("click", onClick);
  return b;
}

function buildActions(m, i) {
  const isLast = i === chat.messages.length - 1;
  if (controller && isLast && m.role === "assistant") return null; // still streaming
  const a = el("div", "actions");
  if (m.role === "user") {
    a.append(
      actionButton(ICON.copy, "Copy", (e) => copyText(m.content, e.currentTarget)),
      actionButton(ICON.edit, "Edit", () => startEdit(i))
    );
  } else {
    if (!m.ui) a.append(actionButton(ICON.copy, "Copy", (e) => copyText(m.content, e.currentTarget)));
    if (isLast) a.append(actionButton(ICON.refresh, "Regenerate", regenerate));
  }
  return a.children.length ? a : null;
}

function buildSources(sources) {
  const wrap = el("div", "sources");
  wrap.appendChild(el("span", "sources-label", sources.length === 1 ? "Source" : "Sources"));
  sources.forEach((s) => {
    const chip = el(s.doc_id ? "a" : "span", "src");
    chip.title = s.doc_id ? "Open the original document" : "Answer based on this document";
    if (s.doc_id) {
      chip.href = fileUrl(s.doc_id) + (s.page ? `#page=${s.page}` : "");
      chip.target = "_blank";
      chip.rel = "noopener noreferrer";
    }
    chip.innerHTML = ICON.file;
    chip.append(s.source + (s.page ? ` · p.${s.page}` : ""));
    wrap.appendChild(chip);
  });
  return wrap;
}

function buildRow(m, i) {
  const row = el("div", `row ${m.role === "user" ? "user" : "bot"}`);
  if (m.ui) row.classList.add(m.ui); // "error" | "stopped"
  if (m.role === "assistant" && i === chat.messages.length - 1) row.classList.add("last");
  row.dataset.index = i;

  if (m.role === "assistant") row.appendChild(el("div", "bot-mark", "✦"));
  const col = el("div", "col");
  const bubble = el("div", "bubble");
  setBubble(bubble, m);
  col.appendChild(bubble);
  if (m.sources?.length) col.appendChild(buildSources(m.sources));
  const actions = buildActions(m, i);
  if (actions) col.appendChild(actions);
  row.appendChild(col);
  return row;
}

const distanceFromBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight;
function scrollDown() {
  box.scrollTop = box.scrollHeight;
}
function updateToBottom() {
  toBottom.classList.toggle("show", chat.messages.length > 0 && distanceFromBottom() > 200);
}
box.addEventListener("scroll", updateToBottom);
toBottom.addEventListener("click", () => box.scrollTo({ top: box.scrollHeight, behavior: "smooth" }));

function renderMessages(scroll = true) {
  box.querySelectorAll(".row").forEach((r) => r.remove());
  welcome.style.display = chat.messages.length ? "none" : "";
  chat.messages.forEach((m, i) => box.appendChild(buildRow(m, i)));
  if (scroll) scrollDown();
  updateToBottom();
}

function renderAll() {
  renderList();
  renderMessages();
}

/* ---------- sending / streaming ---------- */
function autoGrow() {
  if (!input.value) {
    input.style.height = ""; // back to the one-line height from CSS
    input.style.overflowY = "hidden";
  } else {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
    input.style.overflowY = input.scrollHeight > 200 ? "auto" : "hidden";
  }
  updateSend();
}

function updateSend() {
  send.disabled = !controller && !input.value.trim();
  send.classList.toggle("stopping", !!controller);
  send.title = controller ? "Stop" : "Send";
  send.setAttribute("aria-label", send.title);
}

function submit(text) {
  text = text.trim();
  if (!text || controller) return;
  chat.messages.push({ role: "user", content: text });
  if (chat.messages.length === 1) chat.title = text.length > 40 ? text.slice(0, 40) + "…" : text;
  generate();
}

function sendFromInput() {
  if (controller || !input.value.trim()) return;
  const text = input.value;
  input.value = "";
  autoGrow();
  submit(text);
}

function regenerate() {
  if (controller) return;
  while (chat.messages.length && chat.messages.at(-1).role === "assistant") chat.messages.pop();
  if (chat.messages.length) generate();
}

function startEdit(i) {
  if (controller) return;
  const col = box.querySelector(`.row[data-index="${i}"] .col`);
  if (!col) return;
  const boxEl = el("div", "edit-box");
  const ta = el("textarea", "edit-input");
  ta.value = chat.messages[i].content;
  const fit = () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 240) + "px";
  };
  ta.addEventListener("input", fit);

  const bar = el("div", "edit-bar");
  const cancel = el("button", "btn", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", () => renderMessages(false));
  const save = el("button", "btn primary", "Send");
  save.type = "button";
  save.addEventListener("click", () => {
    const text = ta.value.trim();
    if (!text) return;
    chat.messages.length = i; // drop this message and everything after it
    submit(text);
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Escape") renderMessages(false);
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      save.click();
    }
  });
  bar.append(cancel, save);
  boxEl.append(ta, bar);
  col.replaceChildren(boxEl);
  col.style.width = "100%";
  fit();
  ta.focus();
}

async function generate() {
  const c = chat;
  const reply = { role: "assistant", content: "" };
  c.messages.push(reply);
  c.updatedAt = Date.now();
  controller = new AbortController();
  updateSend();
  renderList();
  renderMessages();
  const bubble = box.lastElementChild.querySelector(".bubble");

  let queued = false;
  const paint = () => {
    queued = false;
    if (c !== chat) return;
    const nearBottom = distanceFromBottom() < 120;
    setBubble(bubble, reply);
    if (nearBottom) scrollDown();
    updateToBottom();
  };

  try {
    const res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: c.messages
          .filter((m) => !m.ui && m.content)
          .map(({ role, content }) => ({ role, content })),
      }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(await errorFrom(res));
    try {
      const sources = JSON.parse(res.headers.get("X-Sources") || "[]");
      if (sources.length) reply.sources = sources;
    } catch {}
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      reply.content += decoder.decode(value, { stream: true });
      if (!queued) {
        queued = true;
        requestAnimationFrame(paint);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      if (!reply.content) {
        reply.ui = "stopped";
        reply.content = "Stopped.";
      }
    } else if (!reply.content) {
      reply.ui = "error";
      reply.content = "Error: " + err.message;
    }
  }

  controller = null;
  updateSend();
  c.updatedAt = Date.now();
  persist();
  renderList();
  if (c === chat) {
    renderMessages(false);
    input.focus();
  }
  if (!reply.ui) autoTitle(c);
}

// Ask the model for a short chat title: as soon as there is a real question,
// then once more at the 3rd exchange when the topic is clearer.
async function autoTitle(c) {
  if (c.titleBy === "user") return;
  const turns = c.messages.filter((m) => m.role === "user").length;
  const due = !c.titleBy || turns === 3;
  if (!due || c.titledAt === turns) return;
  // nothing to name yet if the user has only said "hi"; the 3rd-exchange title will catch it
  const asked = c.messages.filter((m) => m.role === "user").map((m) => m.content).join(" ");
  if (asked.replace(/[^\p{L}\p{N}]/gu, "").length < 12) return;
  c.titledAt = turns;
  try {
    const res = await fetch(API_BASE + "/title", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: c.messages.filter((m) => !m.ui && m.content).map(({ role, content }) => ({ role, content })),
      }),
    });
    if (!res.ok) return;
    const { title } = await res.json();
    if (!title) {
      c.titledAt = undefined; // model gave no usable title; try again after a later reply
      return;
    }
    if (c.titleBy !== "user") {
      c.title = title;
      c.titleBy = "ai";
      persist();
      renderList();
    }
  } catch {} // keep the current title if the server is unreachable
}

/* ---------- knowledge base (upload / list / delete) ---------- */
const docsSection = $("docs");
const docList = $("doclist");
const docCount = $("doccount");
const fileInput = $("file");
let docsCache = []; // last list from the server
let pending = []; // uploads in progress / failed: {name, state: "queued"|"busy"|"error", message}
const fresh = new Set(); // doc_ids to highlight briefly after upload
const pickFiles = () => fileInput.click();

function setDocsCollapsed(collapsed) {
  docsSection.classList.toggle("collapsed", collapsed);
  $("docs-toggle").setAttribute("aria-expanded", String(!collapsed));
  store("docsCollapsed", collapsed);
}
setDocsCollapsed(load("docsCollapsed", false));
$("docs-toggle").addEventListener("click", () => setDocsCollapsed(!docsSection.classList.contains("collapsed")));

function pendingRow(p) {
  const item = el("div", `doc-item pending ${p.state}`);
  const text = el("div", "doc-text");
  const meta = el("span", "doc-meta");
  if (p.state === "busy") meta.append(el("span", "spinner"), "Indexing…");
  else if (p.state === "queued") meta.append("Waiting…");
  else meta.append(p.message);
  text.append(el("span", "doc-name", p.name), meta);
  item.append(docIcon(p.name), text);
  if (p.state === "error") {
    const actions = el("div", "item-actions");
    const dismiss = el("button");
    dismiss.type = "button";
    dismiss.title = "Dismiss";
    dismiss.innerHTML = ICON.close;
    dismiss.addEventListener("click", () => {
      pending = pending.filter((x) => x !== p);
      renderDocs();
    });
    actions.appendChild(dismiss);
    item.appendChild(actions);
  }
  return item;
}

function renderDocs() {
  const docs = docsCache;
  docCount.textContent = docs.length;
  docList.innerHTML = "";
  pending.forEach((p) => docList.appendChild(pendingRow(p)));
  if (!docs.length && !pending.length) {
    const empty = el("button", "upload-empty");
    empty.type = "button";
    empty.append("Upload documents", el("span", null, "PDF, TXT or MD · or drop files anywhere"));
    empty.addEventListener("click", pickFiles);
    docList.appendChild(empty);
    return;
  }
  docs.forEach((d) => {
    const item = el("div", "doc-item" + (fresh.has(d.doc_id) ? " fresh" : ""));
    const text = el("div", "doc-text");
    const name = el("a", "doc-name", d.filename);
    name.title = "Open " + d.filename;
    name.href = fileUrl(d.doc_id);
    name.target = "_blank";
    name.rel = "noopener noreferrer";
    text.append(name, el("span", "doc-meta", `${d.chunks} chunk${d.chunks === 1 ? "" : "s"}`));

    const actions = el("div", "item-actions");
    const del = el("button", "danger");
    del.type = "button";
    del.title = "Remove from knowledge base";
    del.innerHTML = ICON.trash;
    confirmDelete(del, async () => {
      try {
        const res = await fetch(`${API_BASE}/documents/${d.doc_id}`, { method: "DELETE" });
        if (!res.ok) throw new Error(await errorFrom(res));
      } catch (err) {
        pending.push({ name: d.filename, state: "error", message: "Delete failed: " + err.message });
      }
      refreshDocs();
    });
    actions.appendChild(del);
    item.append(docIcon(d.filename), text, actions);
    docList.appendChild(item);
  });
}

async function refreshDocs() {
  try {
    const res = await fetch(API_BASE + "/documents");
    if (!res.ok) throw new Error(await errorFrom(res));
    docsCache = await res.json();
    renderDocs();
  } catch (err) {
    docCount.textContent = "!";
    docList.replaceChildren(el("div", "empty-note error", "Can't load documents: " + err.message));
  }
}

// Upload progress is shown in the Knowledge base list, where the files end up
async function uploadFiles(files) {
  setDocsCollapsed(false);
  if (!mobile.matches) layout.classList.add("sidebar-open"); // show progress without changing the saved preference
  const items = files.map((f) => ({ name: f.name, state: "queued" }));
  pending.push(...items);
  renderDocs();

  for (const [i, file] of files.entries()) {
    const p = items[i];
    p.state = "busy";
    renderDocs();
    const body = new FormData();
    body.append("file", file);
    try {
      const res = await fetch(API_BASE + "/upload", { method: "POST", body });
      if (!res.ok) throw new Error(await errorFrom(res));
      const out = await res.json();
      pending = pending.filter((x) => x !== p);
      fresh.add(out.doc_id);
      setTimeout(() => {
        fresh.delete(out.doc_id);
        renderDocs();
      }, 2500);
      await refreshDocs();
    } catch (err) {
      p.state = "error";
      p.message = err.message;
      renderDocs();
    }
  }
}

$("upload").addEventListener("click", pickFiles);
$("attach").addEventListener("click", pickFiles);
fileInput.addEventListener("change", () => {
  const files = [...fileInput.files];
  fileInput.value = "";
  if (files.length) uploadFiles(files);
});

// drag & drop files anywhere on the page
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  dragDepth++;
  document.body.classList.add("dragging");
});
window.addEventListener("dragleave", (e) => {
  if (!hasFiles(e)) return;
  if (--dragDepth <= 0) document.body.classList.remove("dragging");
});
window.addEventListener("dragover", (e) => hasFiles(e) && e.preventDefault());
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  uploadFiles([...e.dataTransfer.files]);
});

/* ---------- events ---------- */
form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (controller) controller.abort(); // the button acts as Stop while streaming
  else sendFromInput();
});

// Enter sends, Shift+Enter makes a new line
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendFromInput();
  }
});
input.addEventListener("input", autoGrow);

// Shortcuts: Ctrl+Shift+O new chat, Esc stops a streaming reply
window.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "o") {
    e.preventDefault();
    newChat();
  } else if (e.key === "Escape" && controller) {
    controller.abort();
  }
});

document.querySelectorAll(".suggestion").forEach((s) =>
  s.addEventListener("click", () => submit(s.dataset.prompt))
);

renderAll();
updateSend();
refreshDocs();
