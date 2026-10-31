// Rendering the open chat: message bubbles (markdown, sanitised), code blocks, sources, agent steps
import DOMPurify from "dompurify";
import { marked } from "marked";
import { fileUrl } from "../api.js";
import { $, el } from "../lib.js";
import { state } from "../state.js";
import { ICON } from "../ui/icons.js";
import { actionButton, copyText } from "../ui/widgets.js";
import { regenerate, startEdit } from "./composer.js";

const box = $("messages");
const welcome = $("welcome");
const toBottom = $("tobottom");

marked.setOptions({ gfm: true, breaks: true });
// links in answers open in a new tab, without giving that page access to this one
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

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

export function setBubble(bubble, m) {
  if (m.role === "user" || m.ui) {
    bubble.textContent = m.content;
  } else if (!m.content) {
    bubble.innerHTML = '<span class="thinking">Thinking…</span>';
  } else {
    bubble.innerHTML = DOMPurify.sanitize(marked.parse(m.content));
    enhanceCode(bubble);
  }
}

function buildActions(m, i) {
  const isLast = i === state.chat.messages.length - 1;
  if (state.controller && isLast && m.role === "assistant") return null; // still streaming
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

const STEP_LABELS = {
  search_documents: ["Searching documents", "Searched documents"],
  list_documents: ["Listing documents", "Listed documents"],
  read_document: ["Reading a document", "Read a document"],
};

// The agent's tool calls, shown above its answer
export function buildSteps(steps) {
  const wrap = el("div", "steps");
  steps.forEach((s) => {
    const [busy, done] = STEP_LABELS[s.name] || [`Using ${s.name}`, `Used ${s.name}`];
    const step = el("div", `step ${s.state}`);
    step.title = s.name + " " + JSON.stringify(s.args);
    step.innerHTML = s.state === "busy" ? '<span class="spinner"></span>' : ICON.tool;
    step.append(s.state === "busy" ? busy + "…" : done);
    if (s.args?.query) step.appendChild(el("span", "step-arg", `“${s.args.query}”`));
    if (s.state === "failed") step.appendChild(el("span", "step-arg", "failed"));
    wrap.appendChild(step);
  });
  return wrap;
}

function buildRow(m, i) {
  const row = el("div", `row ${m.role === "user" ? "user" : "bot"}`);
  if (m.ui) row.classList.add(m.ui); // "error" | "stopped"
  if (m.role === "assistant" && i === state.chat.messages.length - 1) row.classList.add("last");
  row.dataset.index = i;

  if (m.role === "assistant") row.appendChild(el("div", "bot-mark", "✦"));
  const col = el("div", "col");
  const bubble = el("div", "bubble");
  setBubble(bubble, m);
  if (m.steps?.length) col.appendChild(buildSteps(m.steps));
  col.appendChild(bubble);
  if (m.sources?.length) col.appendChild(buildSources(m.sources));
  const actions = buildActions(m, i);
  if (actions) col.appendChild(actions);
  row.appendChild(col);
  return row;
}

/* ---------- scrolling ---------- */
export const distanceFromBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight;
export function scrollDown() {
  box.scrollTop = box.scrollHeight;
}
export function updateToBottom() {
  toBottom.classList.toggle("show", state.chat.messages.length > 0 && distanceFromBottom() > 200);
}

export function renderMessages(scroll = true) {
  box.querySelectorAll(".row").forEach((r) => r.remove());
  welcome.style.display = state.chat.messages.length ? "none" : "";
  state.chat.messages.forEach((m, i) => box.appendChild(buildRow(m, i)));
  if (scroll) scrollDown();
  updateToBottom();
}

export function initMessages() {
  box.addEventListener("scroll", updateToBottom);
  toBottom.addEventListener("click", () => box.scrollTo({ top: box.scrollHeight, behavior: "smooth" }));
}
