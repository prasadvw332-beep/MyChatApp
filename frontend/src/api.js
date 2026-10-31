// Every call to the backend, in one place. Functions return parsed data, or throw an Error whose
// message is the server's (FastAPI's {detail}). Auth (features/auth.js) and evaluation
// (features/evals.js) keep their own small calls.
import { API_BASE, errorFrom } from "./lib.js";

const JSON_HEADERS = { "Content-Type": "application/json" };
const enc = encodeURIComponent;

async function request(path, init) {
  const res = await fetch(API_BASE + path, init);
  if (!res.ok) throw new Error(await errorFrom(res));
  return res;
}

// what the model sees of a chat: no UI-only bubbles ("Stopped.", errors), only role + text
const forModel = (messages) =>
  messages.filter((m) => !m.ui && m.content).map(({ role, content }) => ({ role, content }));

export const fileUrl = (docId) => `${API_BASE}/documents/${enc(docId)}/file`;

/* ---------- chats ---------- */
export const listChats = async () => (await request("/chats")).json();

export const saveChat = (c) =>
  request(`/chats/${enc(c.id)}`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      title: c.title, titleBy: c.titleBy ?? null, titledAt: c.titledAt ?? null,
      updatedAt: c.updatedAt, messages: c.messages,
    }),
  });

export const deleteChat = (id) => request(`/chats/${enc(id)}`, { method: "DELETE" });

/* ---------- answers ---------- */
// The raw response, for streaming: /chat streams plain text (sources in the X-Sources header),
// /agent/chat streams one JSON event per line (see backend/app/agents/loop.py).
export const streamReply = (agent, messages, signal) =>
  request(agent ? "/agent/chat" : "/chat", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ messages: forModel(messages) }),
    signal,
  });

// A short title for the chat, or "" when the model gave nothing usable
export const suggestTitle = async (messages) =>
  (await (await request("/title", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ messages: forModel(messages) }),
  })).json()).title;

// A thumbs up/down on an answer (rating null takes it back); see features/feedback.js
export const sendFeedback = (body) =>
  request("/feedback", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(body) });

/* ---------- documents ---------- */
export const listDocuments = async () => (await request("/documents")).json();

export async function uploadDocument(file) {
  const body = new FormData();
  body.append("file", file);
  return (await request("/upload", { method: "POST", body })).json();
}

export const deleteDocument = (docId) => request(`/documents/${enc(docId)}`, { method: "DELETE" });
