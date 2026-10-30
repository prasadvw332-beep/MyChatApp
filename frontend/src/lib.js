// Helpers shared by the chat and evaluation views

export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Every request to the backend carries the login cookie (and a 401 sends the user back to the login screen)
const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  if (!url.startsWith(API_BASE)) return nativeFetch(input, init);
  const res = await nativeFetch(input, { ...init, credentials: "include" });
  if (res.status === 401 && !url.startsWith(API_BASE + "/auth/")) location.reload();
  return res;
};

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
