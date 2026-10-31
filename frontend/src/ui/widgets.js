// Small reusable UI pieces: two-step delete, copy-to-clipboard, icon action buttons
import { el } from "../lib.js";
import { ICON } from "./icons.js";

// Two-step delete: first click arms the button ("Delete"), second click confirms
export function confirmDelete(btn, onConfirm) {
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

export async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    return;
  }
  const old = btn.innerHTML;
  btn.innerHTML = ICON.check + (btn.dataset.label ? " Copied" : "");
  setTimeout(() => (btn.innerHTML = old), 1500);
}

export function actionButton(icon, title, onClick) {
  const b = el("button", "act");
  b.type = "button";
  b.title = title;
  b.setAttribute("aria-label", title);
  b.innerHTML = icon;
  b.addEventListener("click", onClick);
  return b;
}
