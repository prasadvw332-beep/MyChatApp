// Helpers shared by the chat and evaluation views

export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export const $ = (id) => document.getElementById(id);

// Small helper for building DOM nodes
export function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

// Readable message from a failed fetch response (FastAPI sends {detail})
export async function errorFrom(res) {
  try {
    const body = await res.json();
    if (body.detail) return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {}
  return "Server error " + res.status;
}
