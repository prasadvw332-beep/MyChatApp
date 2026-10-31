// Login / sign-up screen. The session is an httpOnly cookie set by the backend,
// so the page only needs to know who is logged in.
import { API_BASE, $, errorFrom } from "../lib.js";

async function post(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body && JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorFrom(res));
  return res.json();
}

// Resolves with the user once logged in (immediately if a session cookie is already valid)
export async function requireLogin() {
  try {
    const res = await fetch(API_BASE + "/auth/me");
    if (res.ok) {
      const user = await res.json();
      showScreen(false);
      return user;
    }
    if (res.status !== 401) throw new Error(await errorFrom(res));
  } catch (err) {
    showScreen(true);
    $("auth-error").textContent = "Can't reach the server: " + err.message;
    return new Promise(() => {}); // stay on the login screen until the page is reloaded
  }

  showScreen(true);
  return new Promise((resolve) => {
    let mode = "login";
    const form = $("auth-form");
    const setMode = (m) => {
      mode = m;
      $("auth-title").textContent = m === "login" ? "Log in" : "Create account";
      $("auth-submit").textContent = m === "login" ? "Log in" : "Sign up";
      $("auth-switch-text").textContent = m === "login" ? "No account?" : "Already have an account?";
      $("auth-switch").textContent = m === "login" ? "Sign up" : "Log in";
      $("auth-password").autocomplete = m === "login" ? "current-password" : "new-password";
      $("auth-error").textContent = "";
    };
    $("auth-switch").addEventListener("click", () => setMode(mode === "login" ? "register" : "login"));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("auth-submit");
      btn.disabled = true;
      $("auth-error").textContent = "";
      try {
        const user = await post(mode === "login" ? "/auth/login" : "/auth/register", {
          username: $("auth-username").value,
          password: $("auth-password").value,
        });
        $("auth-password").value = "";
        showScreen(false);
        resolve(user);
      } catch (err) {
        $("auth-error").textContent = err.message;
      } finally {
        btn.disabled = false;
      }
    });
    $("auth-username").focus();
  });
}

export async function logout() {
  try { await post("/auth/logout"); } catch {}
  location.reload(); // drop all in-memory state of the previous user
}

function showScreen(show) {
  $("auth").hidden = !show;
  $("layout").hidden = show;
}
