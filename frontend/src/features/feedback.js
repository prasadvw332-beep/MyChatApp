// Thumbs up/down on answers. The rating is kept on the message (saved with the chat, so it is still
// shown after a reload) and sent to /feedback with a copy of the question and answer, so a thumbs down
// can be traced to its request's logs and trace (backend/app/models/feedback.py).
import * as api from "../api.js";
import { $, el } from "../lib.js";
import { state } from "../state.js";
import { persist } from "./chats.js";
import { renderMessages } from "./messages.js";

const box = $("messages");

function send(chat, i, comment = null) {
  const m = chat.messages[i];
  const question = chat.messages.slice(0, i).findLast((x) => x.role === "user")?.content ?? "";
  return api.sendFeedback({
    chatId: chat.id, position: i, rating: m.feedback ?? null, comment,
    mode: m.mode ?? null, question, answer: m.content, sources: m.sources ?? null, requestId: m.requestId ?? null,
  }).catch((err) => console.warn("Could not send feedback:", err.message));
}

// Clicking the thumb that is already on takes the rating back
export function rate(i, rating) {
  const chat = state.chat;
  const m = chat.messages[i];
  if (m.feedback === rating) delete m.feedback;
  else m.feedback = rating;
  persist();
  renderMessages(false);
  send(chat, i);
  if (m.feedback === "down") askWhy(chat, i);
}

// After a thumbs down: an optional "what was wrong?", sent as the rating's comment
function askWhy(chat, i) {
  const actions = box.querySelector(`.row[data-index="${i}"] .actions`);
  if (!actions) return;
  const form = el("div", "edit-box why");
  const ta = el("textarea", "edit-input");
  ta.rows = 2;
  ta.maxLength = 2000;
  ta.placeholder = "What was wrong with this answer? (optional)";
  ta.setAttribute("aria-label", "What was wrong with this answer?");

  const bar = el("div", "edit-bar");
  const skip = el("button", "btn", "Skip");
  skip.type = "button";
  skip.addEventListener("click", () => form.remove());
  const ok = el("button", "btn primary", "Send");
  ok.type = "button";
  ok.addEventListener("click", () => {
    const text = ta.value.trim();
    form.remove();
    if (text && chat.messages[i]?.feedback === "down") send(chat, i, text);
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Escape") skip.click();
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ok.click();
    }
  });
  bar.append(skip, ok);
  form.append(ta, bar);
  actions.after(form);
  ta.focus();
}
