// The knowledge base panel: list, upload (button, attach, drag & drop anywhere) and delete documents.
// Upload progress is shown in this list, where the files end up.
import * as api from "../api.js";
import { $, el } from "../lib.js";
import { load, store } from "../storage.js";
import { docIcon, ICON } from "../ui/icons.js";
import { confirmDelete } from "../ui/widgets.js";
import { revealSidebar } from "./sidebar.js";

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

export function fmtSize(n) {
  if (!n) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return Math.round(n / 1024) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

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
    name.href = api.fileUrl(d.doc_id);
    name.target = "_blank";
    name.rel = "noopener noreferrer";
    const meta = d.status === "ready"
      ? [fmtSize(d.size_bytes), `${d.chunks} chunk${d.chunks === 1 ? "" : "s"}`].filter(Boolean).join(" · ")
      : { queued: "Queued", failed: "Failed" }[d.status] || "Indexing…";
    const metaEl = el("span", "doc-meta", meta);
    if (d.status === "failed" && d.error) metaEl.title = d.error;
    text.append(name, metaEl);

    const actions = el("div", "item-actions");
    const del = el("button", "danger");
    del.type = "button";
    del.title = "Remove from knowledge base";
    del.innerHTML = ICON.trash;
    confirmDelete(del, async () => {
      try {
        await api.deleteDocument(d.doc_id);
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

export async function refreshDocs() {
  try {
    docsCache = await api.listDocuments();
    renderDocs();
  } catch (err) {
    docCount.textContent = "!";
    docList.replaceChildren(el("div", "empty-note error", "Can't load documents: " + err.message));
  }
}

async function uploadFiles(files) {
  setDocsCollapsed(false);
  revealSidebar();
  const items = files.map((f) => ({ name: f.name, state: "queued" }));
  pending.push(...items);
  renderDocs();

  for (const [i, file] of files.entries()) {
    const p = items[i];
    p.state = "busy";
    renderDocs();
    try {
      const out = await api.uploadDocument(file);
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

// drag & drop files anywhere on the page
function initDragAndDrop() {
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
}

export function initDocuments() {
  setDocsCollapsed(load("docsCollapsed", false));
  $("docs-toggle").addEventListener("click", () => setDocsCollapsed(!docsSection.classList.contains("collapsed")));
  $("upload").addEventListener("click", pickFiles);
  $("attach").addEventListener("click", pickFiles);
  fileInput.addEventListener("change", () => {
    const files = [...fileInput.files];
    fileInput.value = "";
    if (files.length) uploadFiles(files);
  });
  initDragAndDrop();
}
