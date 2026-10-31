// Chats: the history in the sidebar, switching between chats, and saving them to the server.
// Chats live in PostgreSQL (see /chats) and are loaded once at startup by loadChats();
// only the id of the open chat is a per-browser preference.
import * as api from "../api.js";
import { $, el } from "../lib.js";
import { state } from "../state.js";
import { load, remove, store } from "../storage.js";
import { ICON } from "../ui/icons.js";
import { confirmDelete } from "../ui/widgets.js";
import { renderMessages } from "./messages.js";
import { closeSidebarOnMobile } from "./sidebar.js";

const chatList = $("chatlist");
const chatSearch = $("chatsearch");
let evals; // the evaluation view (features/evals.js): opening a chat closes it

const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

function createChat() {
  const c = { id: uid(), title: "New chat", messages: [], updatedAt: Date.now() };
  state.chats.unshift(c);
  return c;
}

/* ---------- saving ---------- */
// What the server has for each chat (as JSON), so persist() only sends chats that changed
const saved = new Map();
export const chatKey = (c) => JSON.stringify([c.title, c.titleBy, c.titledAt, c.messages]);
let syncFailed = false;

async function syncChat(c) {
  const key = chatKey(c);
  try {
    await api.saveChat(c);
    saved.set(c.id, key);
    syncFailed = false;
  } catch (err) {
    if (!syncFailed) console.warn("Could not save chat to the server:", err.message);
    syncFailed = true;
  }
}

export function persist() {
  store("activeChat", state.chat.id);
  for (const c of state.chats) {
    if (c.messages.length && saved.get(c.id) !== chatKey(c)) syncChat(c);
  }
}

export async function loadChats() {
  let remote = [];
  try {
    remote = await api.listChats();
  } catch (err) {
    console.warn("Could not load chats from the server:", err.message);
    chatList.replaceChildren(el("div", "empty-note error", "Can't load chat history: " + err.message));
    return;
  }
  remote.forEach((c) => saved.set(c.id, chatKey(c)));

  // one-time import of chats that were kept in this browser before they moved to the database
  const legacy = remote.length ? [] : load("chats", []).filter((c) => c.messages?.length);
  const local = state.chats.filter((c) => c.messages.length); // started typing before the load finished
  const startedFresh = state.chat;
  state.chats = [...remote, ...legacy, ...local];
  const wanted = load("activeChat", null);
  state.chat = local[0] || state.chats.find((c) => c.id === wanted) || state.chats[0] || startedFresh;
  if (!state.chats.includes(state.chat)) state.chats.unshift(state.chat);
  if (legacy.length) {
    persist();
    remove("chats");
  }
  renderAll();
}

/* ---------- switching ---------- */
function selectChat(id) {
  evals.hide();
  const target = state.chats.find((c) => c.id === id);
  if (!target || target === state.chat) return closeSidebarOnMobile();
  if (state.controller) state.controller.abort();
  if (state.chat.messages.length === 0) state.chats = state.chats.filter((c) => c !== state.chat);
  state.chat = target;
  persist();
  renderAll();
  closeSidebarOnMobile();
}

export function newChat() {
  evals.hide();
  if (state.chat.messages.length > 0) {
    if (state.controller) state.controller.abort();
    state.chat = createChat();
    persist();
    renderAll();
  }
  closeSidebarOnMobile();
  $("input").focus();
}

function deleteChat(id) {
  if (state.controller && state.chat.id === id) state.controller.abort();
  state.chats = state.chats.filter((c) => c.id !== id);
  saved.delete(id);
  api.deleteChat(id).catch(() => {});
  if (state.chat.id === id) state.chat = state.chats.find((c) => c.messages.length) || createChat();
  persist();
  renderAll();
}

/* ---------- the list ---------- */
export function groupLabel(ts, now = new Date()) {
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

export function renderList() {
  const q = chatSearch.value.trim().toLowerCase();
  const visible = [...state.chats]
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
    const item = el("div", "chat-item" + (c === state.chat && !evals.isOpen() ? " active" : ""));
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

export function renderAll() {
  renderList();
  renderMessages();
}

export function initChats({ evalsView }) {
  evals = evalsView;
  state.chat = createChat();
  $("newchat").addEventListener("click", newChat);
  chatSearch.addEventListener("input", () => renderList());
}
