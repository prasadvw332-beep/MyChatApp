// Smoke test: the real index.html + main.js in a simulated browser (jsdom) against a fake backend.
// It proves the modules are wired together: login check, documents and chat history load,
// and sending a message streams the answer into the page and saves the chat.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it, vi } from "vitest";

const API = "http://localhost:8000";
const calls = [];
const feedback = []; // bodies sent to PUT /feedback

function fakeBackend(url, init = {}) {
  const method = init.method || "GET";
  calls.push(`${method} ${url.replace(API, "")}`);
  const json = (body, status = 200) => new Response(JSON.stringify(body), { status });
  if (url === `${API}/auth/me`) return json({ id: 1, username: "tester" });
  if (url === `${API}/documents`) {
    return json([{ doc_id: "d1", filename: "cv.pdf", status: "ready", chunks: 3, size_bytes: 2048 }]);
  }
  if (url === `${API}/chats` && method === "GET") {
    return json([{ id: "c1", title: "Earlier chat", updatedAt: Date.now(),
      messages: [{ role: "user", content: "Old question" }, { role: "assistant", content: "Old answer" }] }]);
  }
  if (url === `${API}/chat`) {
    const body = new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(new TextEncoder().encode("Streamed "));
        ctrl.enqueue(new TextEncoder().encode("**answer**"));
        ctrl.close();
      },
    });
    return new Response(body, { headers: { "X-Sources": JSON.stringify([{ source: "cv.pdf", page: 2, doc_id: "d1" }]) } });
  }
  if (url === `${API}/title`) return json({ title: "A title" });
  if (url.startsWith(`${API}/chats/`) && method === "PUT") return json({ ok: true });
  if (url === `${API}/feedback`) {
    feedback.push(JSON.parse(init.body));
    return json({ ok: true });
  }
  return json({ detail: "not faked: " + url }, 404);
}

beforeAll(async () => {
  const html = readFileSync(join(process.cwd(), "index.html"), "utf8");
  document.body.innerHTML = html.slice(html.indexOf("<body>") + 6, html.indexOf("</body>"));
  window.fetch = vi.fn(async (input, init) => fakeBackend(typeof input === "string" ? input : input.url, init));
  await import("../src/main.js");
});

describe("the app", () => {
  it("shows the logged-in user, the documents and the chat history", async () => {
    await vi.waitFor(() => expect(document.getElementById("whoami").textContent).toBe("tester"));
    await vi.waitFor(() => expect(document.querySelector("#doclist .doc-name")?.textContent).toBe("cv.pdf"));
    expect(document.querySelector("#doclist .doc-meta").textContent).toBe("2 KB · 3 chunks");
    await vi.waitFor(() => expect(document.querySelector("#chatlist .chat-item .title")?.textContent).toBe("Earlier chat"));
    expect(document.querySelectorAll("#messages .row")).toHaveLength(2); // the open chat is shown
  });

  it("sends a message, streams the answer as markdown with its source, and saves the chat", async () => {
    const input = document.getElementById("input");
    input.value = "What is in my CV?";
    input.dispatchEvent(new Event("input"));
    document.getElementById("form").dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() => {
      const last = [...document.querySelectorAll("#messages .row.bot .bubble")].at(-1);
      expect(last.innerHTML).toContain("Streamed <strong>answer</strong>");
    });
    expect(document.querySelector("#messages .row.bot.last .sources").textContent).toContain("cv.pdf · p.2");
    await vi.waitFor(() => expect(calls).toContain("PUT /chats/c1"));
    expect(input.value).toBe("");
  });

  it("rates an answer: thumbs down asks what was wrong and sends it with the answer", async () => {
    const lastBot = () => [...document.querySelectorAll("#messages .row.bot")].at(-1);
    lastBot().querySelector('[aria-label="Bad answer"]').click();
    await vi.waitFor(() => expect(feedback).toHaveLength(1));
    expect(feedback[0]).toMatchObject({
      chatId: "c1", position: 3, rating: "down", mode: "chat", question: "What is in my CV?",
      answer: "Streamed **answer**", sources: [{ source: "cv.pdf", page: 2, doc_id: "d1" }],
    });
    expect(lastBot().querySelector('[aria-label="Bad answer"]').getAttribute("aria-pressed")).toBe("true");

    lastBot().querySelector(".why textarea").value = "Wrong page";
    lastBot().querySelector(".why .btn.primary").click();
    await vi.waitFor(() => expect(feedback).toHaveLength(2));
    expect(feedback[1]).toMatchObject({ rating: "down", comment: "Wrong page" });
    expect(lastBot().querySelector(".why")).toBeNull();

    lastBot().querySelector('[aria-label="Bad answer"]').click(); // the same thumb again takes it back
    await vi.waitFor(() => expect(feedback).toHaveLength(3));
    expect(feedback[2].rating).toBeNull();
  });
});
