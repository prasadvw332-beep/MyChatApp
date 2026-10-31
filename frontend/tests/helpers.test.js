// Pure helpers: no DOM interaction, no backend.
import { describe, expect, it } from "vitest";
import { chatKey, groupLabel } from "../src/features/chats.js";
import { applyAgentEvent } from "../src/features/composer.js";
import { fmtSize } from "../src/features/documents.js";
import { extOf } from "../src/ui/icons.js";

describe("groupLabel", () => {
  const now = new Date(2026, 9, 31, 15, 0); // 31 Oct 2026, 15:00
  const daysAgo = (n) => new Date(2026, 9, 31 - n, 10, 0).getTime();
  it.each([
    [0, "Today"],
    [1, "Yesterday"],
    [5, "Previous 7 days"],
    [20, "Previous 30 days"],
  ])("%i days ago -> %s", (n, label) => expect(groupLabel(daysAgo(n), now)).toBe(label));
  it("older chats are grouped by month", () => expect(groupLabel(daysAgo(90), now)).toMatch(/2026/));
});

describe("fmtSize", () => {
  it.each([
    [0, ""],
    [512, "512 B"],
    [2048, "2 KB"],
    [3 * 1024 * 1024, "3.0 MB"],
  ])("%i bytes -> %j", (n, text) => expect(fmtSize(n)).toBe(text));
});

describe("extOf", () => {
  it("knows pdf, txt and md, case-insensitively", () => expect(extOf("CV.PDF")).toBe("pdf"));
  it("treats anything else as text", () => expect(extOf("notes.docx")).toBe("txt"));
});

describe("chatKey", () => {
  it("changes when the messages change, so only changed chats are saved", () => {
    const c = { title: "t", messages: [{ role: "user", content: "hi" }] };
    const before = chatKey(c);
    c.messages.push({ role: "assistant", content: "hello" });
    expect(chatKey(c)).not.toBe(before);
  });
});

describe("applyAgentEvent", () => {
  it("builds the reply from the agent's event stream", () => {
    const reply = { content: "", steps: [] };
    const events = [
      { type: "tool", name: "search_documents", args: { query: "cv" } },
      { type: "tool_done", name: "search_documents", ok: true },
      { type: "token", text: "Pune " },
      { type: "token", text: "University." },
      { type: "sources", sources: [{ source: "cv.pdf", page: 1 }] },
    ];
    events.forEach((e) => applyAgentEvent(e, reply));
    expect(reply.content).toBe("Pune University.");
    expect(reply.steps).toEqual([{ name: "search_documents", args: { query: "cv" }, state: "done" }]);
    expect(reply.sources).toHaveLength(1);
  });

  it("marks a failed tool and appends an error to the text", () => {
    const reply = { content: "Partial", steps: [] };
    applyAgentEvent({ type: "tool", name: "read_document", args: {} }, reply);
    applyAgentEvent({ type: "tool_done", name: "read_document", ok: false }, reply);
    applyAgentEvent({ type: "error", detail: "model stopped" }, reply);
    expect(reply.steps[0].state).toBe("failed");
    expect(reply.content).toBe("Partial\n\n[model stopped]");
  });
});
