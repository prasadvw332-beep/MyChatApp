// Evaluation view: RAGAS scores for the test questions in backend/evals/dataset.jsonl
import { API_BASE, el, errorFrom } from "../lib.js";

const METRICS = {
  faithfulness: {
    label: "Faithfulness",
    hint: "Share of claims in the answer that the retrieved chunks support. Low means hallucination.",
  },
  answer_relevancy: {
    label: "Answer relevancy",
    hint: "How directly the answer addresses the question.",
  },
  context_precision: {
    label: "Context precision",
    hint: "Whether the useful chunks are ranked above the irrelevant ones.",
  },
  context_recall: {
    label: "Context recall",
    hint: "How much of the reference answer the retrieved chunks cover.",
  },
  answer_correctness: {
    label: "Answer correctness",
    hint: "Factual and semantic match between the answer and the reference.",
  },
};
const label = (m) => METRICS[m]?.label || m;
const grade = (v) => (v == null ? "na" : v >= 0.8 ? "good" : v >= 0.5 ? "fair" : "poor");
const fmt = (v) => (v == null ? "–" : v.toFixed(2));

function formatDate(iso) {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function formatDuration(s) {
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

async function getJSON(path, options) {
  const res = await fetch(API_BASE + path, options);
  if (!res.ok) throw new Error(await errorFrom(res));
  return res.json();
}

export function initEvals({ button, view, app, onToggle = () => {} }) {
  let info = null; // GET /evals: runs + dataset/judge info
  let run = null; // currently shown run, with rows
  let status = { running: false };
  let loadError = null;
  let pollTimer = null;
  const open = new Set(); // expanded question rows
  const options = { quick: false, extra: new Set() }; // extra: opt-in metrics

  const isOpen = () => app.classList.contains("view-evals");

  function show() {
    app.classList.add("view-evals");
    button.classList.add("active");
    view.hidden = false;
    onToggle();
    refresh();
  }
  function hide() {
    if (!isOpen()) return;
    app.classList.remove("view-evals");
    button.classList.remove("active");
    view.hidden = true;
    onToggle();
  }

  async function refresh(selectId) {
    try {
      [info, status] = await Promise.all([getJSON("/evals"), getJSON("/evals/status")]);
      loadError = null;
      const id = selectId || run?.id || info.runs[0]?.id;
      run = id ? await getJSON(`/evals/${id}`) : null;
    } catch (err) {
      loadError = err.message;
    }
    if (status.running) startPolling();
    render();
  }

  async function selectRun(id) {
    open.clear();
    try {
      run = await getJSON(`/evals/${id}`);
    } catch (err) {
      loadError = err.message;
    }
    render();
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        status = await getJSON("/evals/status");
      } catch {
        return; // server restarting; try again next tick
      }
      if (!status.running) {
        clearInterval(pollTimer);
        pollTimer = null;
        open.clear();
        await refresh(status.run_id);
        return;
      }
      if (isOpen()) renderProgress();
    }, 2000);
  }

  async function startRun() {
    const metrics = [...info.default_metrics, ...info.metrics.filter((m) => options.extra.has(m))];
    try {
      status = await getJSON("/evals/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ metrics, limit: options.quick ? 3 : null }),
      });
      startPolling();
    } catch (err) {
      status = { running: false, phase: "error", error: err.message };
    }
    render();
  }

  /* ---------- rendering ---------- */
  function render() {
    if (!isOpen()) return;
    view.replaceChildren();
    const wrap = el("div", "ev-wrap");
    view.appendChild(wrap);

    const head = el("div", "ev-head");
    const title = el("div");
    title.append(el("h1", null, "Evaluation"));
    title.append(el("p", "ev-sub", info
      ? `RAGAS scores on ${info.questions} test questions, judged by ${info.judge_model}.`
      : "RAGAS scores for the RAG pipeline."));
    head.append(title, buildControls());
    wrap.appendChild(head);

    if (loadError) {
      wrap.appendChild(el("div", "ev-alert error", "Can't load evaluations: " + loadError));
      return;
    }
    wrap.appendChild(el("div", "ev-progress-slot"));
    renderProgress();
    if (status.phase === "error" && status.error) {
      wrap.appendChild(el("div", "ev-alert error", "Last run failed: " + status.error));
    }
    if (!run) {
      wrap.appendChild(buildEmpty());
      return;
    }
    wrap.append(buildMeta(), buildCards());
    if (run.no_context) {
      wrap.appendChild(el("div", "ev-alert",
        `${run.no_context} question${run.no_context === 1 ? "" : "s"} retrieved no chunks (best match below MIN_SCORE), ` +
        "so the context metrics skip them. That usually means a retrieval miss."));
    }
    wrap.appendChild(buildTable());
  }

  function buildControls() {
    const box = el("div", "ev-controls");
    if (info?.runs.length) {
      const pick = el("select", "ev-select");
      pick.setAttribute("aria-label", "Evaluation run");
      info.runs.forEach((r) => {
        const o = el("option", null, `${formatDate(r.created_at)} · ${r.questions} q`);
        o.value = r.id;
        o.selected = r.id === run?.id;
        pick.appendChild(o);
      });
      pick.addEventListener("change", () => selectRun(pick.value));
      box.appendChild(pick);
    }
    const go = el("button", "btn primary", status.running ? "Running…" : "Run evaluation");
    go.type = "button";
    go.disabled = status.running || !info;
    go.addEventListener("click", startRun);
    box.appendChild(go);

    const opts = el("div", "ev-options");
    const check = (text, checked, onChange, hint) => {
      const lab = el("label", "ev-check");
      if (hint) lab.title = hint;
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = checked;
      cb.disabled = status.running;
      cb.addEventListener("change", () => onChange(cb.checked));
      lab.append(cb, text);
      return lab;
    };
    opts.append(check("Quick (first 3 questions)", options.quick, (on) => (options.quick = on)));
    (info?.metrics || []).filter((m) => !info.default_metrics.includes(m)).forEach((m) => {
      opts.append(check(label(m), options.extra.has(m),
        (on) => (on ? options.extra.add(m) : options.extra.delete(m)),
        METRICS[m]?.hint));
    });
    box.appendChild(opts);
    return box;
  }

  function renderProgress() {
    const slot = view.querySelector(".ev-progress-slot");
    if (!slot) return;
    slot.replaceChildren();
    if (!status.running) return;
    const total = status.total || 1;
    // generating answers is quick next to scoring, so it gets the first 20% of the bar
    const pct = status.phase === "scoring" ? 20 + (80 * status.done) / total
      : status.phase === "generating" ? (20 * status.done) / total : 0;
    const text = status.phase === "scoring" ? `Scoring answers with the judge model · ${status.done}/${total}`
      : status.phase === "generating" ? `Generating answers · ${status.done}/${total}`
      : "Starting…";
    const p = el("div", "ev-progress");
    const line = el("div", "ev-progress-text");
    line.append(el("span", "spinner"), text);
    const bar = el("div", "ev-bar");
    const fill = el("div");
    fill.style.width = pct.toFixed(1) + "%";
    bar.appendChild(fill);
    p.append(line, bar, el("p", "ev-progress-note", "Chat replies are slower while this runs, since both use Ollama."));
    slot.appendChild(p);
  }

  function buildEmpty() {
    const box = el("div", "ev-empty");
    box.append(
      el("strong", null, "No evaluation runs yet"),
      el("p", null, `Run an evaluation to answer the ${info?.questions ?? ""} test questions with your RAG pipeline and score them with RAGAS. ` +
        `The judge is ${info?.judge_model ?? "the chat model"}; on CPU a full run takes about 10–15 minutes.`)
    );
    return box;
  }

  function buildMeta() {
    const parts = [
      formatDate(run.created_at),
      `${run.rows.length} questions`,
      `answers by ${run.chat_model}`,
      `judged by ${run.judge_model}`,
      formatDuration(run.duration_s),
    ];
    return el("div", "ev-meta", parts.join(" · "));
  }

  function buildCards() {
    const idx = info.runs.findIndex((r) => r.id === run.id);
    const prev = idx >= 0 ? info.runs[idx + 1] : null; // next older run
    const grid = el("div", "ev-cards");
    run.metrics.forEach((m) => {
      const v = run.summary[m];
      const card = el("div", `ev-card ${grade(v)}`);
      card.title = METRICS[m]?.hint || "";
      const top = el("div", "ev-card-top");
      top.append(el("span", "ev-card-label", label(m)));
      const before = prev?.summary?.[m];
      if (v != null && before != null) {
        const d = v - before;
        if (Math.abs(d) >= 0.005) {
          top.append(el("span", `ev-delta ${d > 0 ? "up" : "down"}`, `${d > 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(2)}`));
        }
      }
      const bar = el("div", "ev-bar");
      const fill = el("div");
      fill.style.width = `${(v ?? 0) * 100}%`;
      bar.appendChild(fill);
      card.append(
        top,
        el("div", "ev-card-value", fmt(v)),
        bar,
        el("div", "ev-card-foot", `${run.scored[m]}/${run.rows.length} scored`),
        el("p", "ev-card-hint", METRICS[m]?.hint || "")
      );
      grid.appendChild(card);
    });
    return grid;
  }

  function buildTable() {
    const scroller = el("div", "ev-table-wrap");
    const table = el("table", "ev-table");
    const head = el("tr");
    head.append(el("th", "num", "#"), el("th", null, "Question"));
    run.metrics.forEach((m) => {
      const th = el("th", "score", label(m));
      th.title = METRICS[m]?.hint || "";
      head.appendChild(th);
    });
    head.appendChild(el("th", "score", "Chunks"));
    const thead = el("thead");
    thead.appendChild(head);
    const tbody = el("tbody");
    run.rows.forEach((r, i) => {
      const tr = el("tr", "ev-row" + (open.has(i) ? " open" : ""));
      tr.tabIndex = 0;
      tr.setAttribute("aria-expanded", String(open.has(i)));
      tr.append(el("td", "num", String(i + 1)), el("td", "q", r.question));
      run.metrics.forEach((m) => {
        const v = r.scores[m];
        const td = el("td", "score");
        const pill = el("span", `ev-pill ${grade(v)}`, fmt(v));
        if (v == null) {
          pill.title = r.errors.some((e) => e.startsWith(m + ":")) ? "Judge error, see details" : "Not scored: no chunks retrieved";
        }
        td.appendChild(pill);
        tr.appendChild(td);
      });
      tr.appendChild(el("td", "score muted", String(r.contexts.length)));
      const toggle = () => {
        open.has(i) ? open.delete(i) : open.add(i);
        render();
      };
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle();
        }
      });
      tbody.appendChild(tr);
      if (open.has(i)) tbody.appendChild(buildDetail(r, run.metrics.length + 3));
    });
    table.append(thead, tbody);
    scroller.appendChild(table);
    return scroller;
  }

  function buildDetail(r, span) {
    const tr = el("tr", "ev-detail");
    const td = el("td");
    td.colSpan = span;
    const pair = el("div", "ev-pair");
    const block = (title, text) => {
      const b = el("div", "ev-block");
      b.append(el("div", "ev-block-title", title), el("div", "ev-block-text", text || "(empty)"));
      return b;
    };
    pair.append(block("Answer", r.response), block("Reference", r.reference));
    td.appendChild(pair);

    if (r.errors.length) {
      const errs = el("div", "ev-alert error");
      errs.append(el("strong", null, "Judge errors"));
      r.errors.forEach((e) => errs.append(el("div", "ev-err", e)));
      td.appendChild(errs);
    }

    td.appendChild(el("div", "ev-block-title", r.contexts.length
      ? `Retrieved chunks (${r.contexts.length})` : "No chunks retrieved"));
    r.contexts.forEach((c, k) => {
      const d = el("details", "ev-chunk");
      const s = el("summary");
      s.append(el("span", "ev-chunk-rank", String(k + 1)),
        el("span", "ev-chunk-src", c.source + (c.page ? ` · p.${c.page}` : "")),
        el("span", "ev-chunk-peek", c.text.replace(/\s+/g, " ").slice(0, 140)));
      d.append(s, el("div", "ev-chunk-text", c.text));
      td.appendChild(d);
    });
    tr.appendChild(td);
    return tr;
  }

  button.addEventListener("click", () => (isOpen() ? hide() : show()));
  return { show, hide, isOpen };
}
