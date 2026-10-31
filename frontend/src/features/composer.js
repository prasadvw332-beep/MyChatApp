// Writing and answering: the input box, send/stop, edit and regenerate, streaming the reply
// (plain text from /chat, JSON events from /agent/chat), agent mode, and the automatic chat title.
import * as api from "../api.js";
import { $, el } from "../lib.js";
import { state } from "../state.js";
import { load, store } from "../storage.js";
import { newChat, persist, renderList } from "./chats.js";
import { buildSteps, distanceFromBottom, renderMessages, scrollDown, setBubble, updateToBottom } from "./messages.js";

const box = $("messages");
const form = $("form");
const input = $("input");
const send = $("send");
const agentBtn = $("agent");

/* ---------- agent mode (a per-browser preference) ---------- */
function setAgentMode(on) {
  state.agentMode = on;
  agentBtn.classList.toggle("on", on);
  agentBtn.setAttribute("aria-pressed", String(on));
  store("agentMode", on);
}

/* ---------- the input box ---------- */
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

export function updateSend() {
  send.disabled = !state.controller && !input.value.trim();
  send.classList.toggle("stopping", !!state.controller);
  send.title = state.controller ? "Stop" : "Send";
  send.setAttribute("aria-label", send.title);
}

function submit(text) {
  text = text.trim();
  if (!text || state.controller) return;
  const chat = state.chat;
  chat.messages.push({ role: "user", content: text, at: Date.now() });
  if (chat.messages.length === 1) chat.title = text.length > 40 ? text.slice(0, 40) + "…" : text;
  generate();
}

function sendFromInput() {
  if (state.controller || !input.value.trim()) return;
  const text = input.value;
  input.value = "";
  autoGrow();
  submit(text);
}

export function regenerate() {
  if (state.controller) return;
  const messages = state.chat.messages;
  while (messages.length && messages.at(-1).role === "assistant") messages.pop();
  if (messages.length) generate();
}

// Edit a sent message in place; sending it drops that message and everything after it
export function startEdit(i) {
  if (state.controller) return;
  const col = box.querySelector(`.row[data-index="${i}"] .col`);
  if (!col) return;
  const boxEl = el("div", "edit-box");
  const ta = el("textarea", "edit-input");
  ta.value = state.chat.messages[i].content;
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
    state.chat.messages.length = i; // drop this message and everything after it
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

/* ---------- streaming the answer ---------- */
async function generate() {
  const c = state.chat;
  const reply = { role: "assistant", content: "", at: Date.now() };
  c.messages.push(reply);
  c.updatedAt = Date.now();
  state.controller = new AbortController();
  updateSend();
  renderList();
  renderMessages();
  const bubble = box.lastElementChild.querySelector(".bubble");

  // repaint at most once per frame, however fast the tokens arrive
  let queued = false;
  const paint = () => {
    queued = false;
    if (c !== state.chat) return;
    const nearBottom = distanceFromBottom() < 120;
    setBubble(bubble, reply);
    if (reply.steps?.length) {
      const steps = buildSteps(reply.steps);
      const old = bubble.parentElement.querySelector(".steps");
      old ? old.replaceWith(steps) : bubble.before(steps);
    }
    if (nearBottom) scrollDown();
    updateToBottom();
  };
  const repaint = () => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(paint);
    }
  };

  const agent = state.agentMode;
  try {
    const res = await api.streamReply(agent, c.messages, state.controller.signal);
    // sent with a rating of this answer, to find its logs and trace (features/feedback.js)
    reply.mode = agent ? "agent" : "chat";
    reply.requestId = res.headers.get("X-Request-ID") || undefined;
    if (agent) {
      await readAgentEvents(res, reply, repaint);
    } else {
      try {
        const sources = JSON.parse(res.headers.get("X-Sources") || "[]");
        if (sources.length) reply.sources = sources;
      } catch {}
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        applyChatText(decoder.decode(value, { stream: true }), reply);
        repaint();
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

  state.controller = null;
  updateSend();
  c.updatedAt = Date.now();
  persist();
  renderList();
  if (c === state.chat) {
    renderMessages(false);
    input.focus();
  }
  if (!reply.ui) autoTitle(c);
}

// A guardrail withheld the answer (backend/app/guardrails/content.py): the notice replaces it, and
// with `ui` set it is never resent to the model or copied
function withhold(reply, notice) {
  reply.content = notice;
  reply.ui = "blocked";
  delete reply.sources;
}

// /chat streams plain text; a NUL byte means "replace everything so far with what follows"
export function applyChatText(text, reply) {
  if (reply.ui === "blocked") {
    reply.content += text; // the rest of the notice
    return;
  }
  const cut = text.indexOf("\0");
  if (cut < 0) reply.content += text;
  else withhold(reply, text.slice(cut + 1));
}

// /agent/chat streams one JSON event per line (see backend/app/agents/loop.py)
async function readAgentEvents(res, reply, changed) {
  reply.steps = [];
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // an incomplete line waits for the next read
    lines.filter((l) => l.trim()).forEach((l) => applyAgentEvent(JSON.parse(l), reply));
    changed();
  }
}

export function applyAgentEvent(e, reply) {
  if (e.type === "token") reply.content += e.text;
  else if (e.type === "tool") reply.steps.push({ name: e.name, args: e.args, state: "busy" });
  else if (e.type === "tool_done") {
    const step = reply.steps.findLast((s) => s.name === e.name && s.state === "busy");
    if (step) step.state = e.ok ? "done" : "failed";
  } else if (e.type === "sources") {
    if (e.sources.length) reply.sources = e.sources;
  } else if (e.type === "error") {
    reply.content += (reply.content ? "\n\n" : "") + `[${e.detail}]`;
  } else if (e.type === "withheld") {
    withhold(reply, e.detail);
  }
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
    const title = await api.suggestTitle(c.messages);
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

export function initComposer() {
  setAgentMode(load("agentMode", false));
  agentBtn.addEventListener("click", () => setAgentMode(!state.agentMode));

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.controller) state.controller.abort(); // the button acts as Stop while streaming
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
    } else if (e.key === "Escape" && state.controller) {
      state.controller.abort();
    }
  });

  document.querySelectorAll(".suggestion").forEach((s) =>
    s.addEventListener("click", () => submit(s.dataset.prompt))
  );
}
