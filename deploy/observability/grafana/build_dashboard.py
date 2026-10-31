"""Builds the Grafana dashboard dashboards/mychatapp.json. Edit this file, not the JSON.

    python deploy/observability/grafana/build_dashboard.py           # rewrite the JSON
    python deploy/observability/grafana/build_dashboard.py --check   # CI: fail if the JSON is out of date

The JSON is ~3000 lines of Grafana's format; written by hand it drifts (a panel copied with the wrong
query, inconsistent colours). Here every panel is one function call, with shared thresholds and colours.
Plain Python, no dependencies. Restart Grafana to load the result.

Layout (top to bottom, most important first):
  1. Health at a glance: stat tiles, green/orange/red, for the selected time range
  2. Traffic & errors (RED: rate, errors, duration)
  3. AI pipeline: where a chat answer's time goes
  3b. Tokens & cost (Prometheus totals + per-user table from PostgreSQL)
  3c. Guardrails: blocked / flagged, poisoned documents, guard model errors
  4. Document ingestion
  5. Traces: service map + recent requests
  6. Pods & resources (collapsed)
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).parent / "dashboards" / "mychatapp.json"
PROM = {"type": "prometheus", "uid": "prometheus"}
TEMPO = {"type": "tempo", "uid": "tempo"}

# app traffic only: health probes are frequent and fast and would flatten every number
APP = 'route!~"/health.*", route=~"$route"'
STREAMING = '"/chat|/agent/chat"'  # their duration is the whole generated answer

panels: list[dict] = []
y = 0
_next_id = 0


def _id():
    global _next_id
    _next_id += 1
    return _next_id


def th(*steps):
    """th("green", (0.99, "orange"), ...) -> threshold steps; first is the base colour."""
    out = [{"color": steps[0], "value": None}]
    out += [{"color": c, "value": v} for v, c in steps[1:]]
    return {"mode": "absolute", "steps": out}


def target(expr, legend="", instant=False, ref="A"):
    t = {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend or "__auto", "range": not instant,
         "instant": instant}
    return t


def row(title, collapsed=False):
    global y
    p = {"type": "row", "title": title, "id": _id(), "collapsed": collapsed,
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}
    panels.append(p)
    y += 1
    return p


def stat(title, expr, unit, x, w=4, h=4, thresholds=None, desc="", decimals=None, spark=None, calc="lastNotNull",
         color_mode="background", into=None):
    """A tile. With `spark` (a range query), the tile shows its trend as a sparkline."""
    targets = [target(expr, instant=spark is None)]
    p = {
        "type": "stat", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": thresholds or th("green"),
                                     "color": {"mode": "thresholds"}, "noValue": "–",
                                     **({"decimals": decimals} if decimals is not None else {})},
                        "overrides": []},
        "options": {"reduceOptions": {"calcs": [calc], "fields": "", "values": False},
                    "colorMode": color_mode, "graphMode": "area" if spark else "none", "textMode": "value",
                    "justifyMode": "center", "wideLayout": True, "showPercentChange": False},
    }
    if spark:
        p["targets"] = [target(spark)]
    (into if into is not None else panels).append(p)


def ts(title, targets, unit, x, w=12, h=8, desc="", stack=False, bars=False, overrides=None, min0=True,
       legend_calcs=("mean", "max"), into=None, links=None):
    p = {
        "type": "timeseries", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(e, legend, ref=chr(65 + i)) for i, (e, legend) in enumerate(targets)],
        "fieldConfig": {
            "defaults": {
                "unit": unit, "color": {"mode": "palette-classic"}, "noValue": "No data in this time range",
                **({"min": 0} if min0 else {}),
                "custom": {
                    "drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 70 if bars else 12,
                    "gradientMode": "opacity", "showPoints": "never", "spanNulls": True, "lineInterpolation": "smooth",
                    "barAlignment": 0, "axisSoftMin": 0,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                },
            },
            "overrides": overrides or [],
        },
        "options": {
            "legend": {"displayMode": "table", "placement": "right" if w >= 12 else "bottom", "showLegend": True,
                       "calcs": list(legend_calcs), "sortBy": legend_calcs[-1].capitalize() if legend_calcs else None,
                       "sortDesc": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }
    if links:
        p["links"] = links
    (into if into is not None else panels).append(p)


def bargauge(title, expr, legend, unit, x, w=12, h=8, desc="", thresholds=None, into=None):
    """Horizontal bars, longest first: 'which X is slowest' at a glance."""
    p = {
        "type": "bargauge", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [{**target(expr, legend, instant=True), "format": "table"}],
        "transformations": [
            {"id": "sortBy", "options": {"sort": [{"field": "Value", "desc": True}]}},
        ],
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": thresholds or th("green"),
                                     "color": {"mode": "thresholds"}, "noValue": "No data in this time range",
                                     "displayName": "${__field.labels." + legend + "}" if legend else ""},
                        "overrides": []},
        "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                    "valueMode": "color", "namePlacement": "left", "sizing": "manual", "minVizHeight": 18,
                    "maxVizHeight": 26, "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True}},
    }
    (into if into is not None else panels).append(p)


def color(name_regex, c):
    return {"matcher": {"id": "byRegexp", "options": name_regex},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}


def q(quant, metric, by="", rng="$__rate_interval", sel=""):
    by_ = f"le, {by}" if by else "le"
    return f"histogram_quantile({quant}, sum by ({by_}) (rate({metric}_bucket{{{sel}}}[{rng}])))"


LAT = th("green", (2, "orange"), (5, "red"))  # seconds, for whole requests
TRACES_LINK = [{"title": "Open slow traces in Explore", "url": "/explore?schemaVersion=1&panes=" + json.dumps(
    {"a": {"datasource": "tempo", "queries": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql",
                                               "query": '{resource.service.name=~"mychatapp-.*" && kind=server && duration > 2s}',
                                               "tableType": "traces", "limit": 20}],
           "range": {"from": "now-1h", "to": "now"}}}, separators=(",", ":")), "targetBlank": False}]

# ======================================================================== 1. health at a glance
row("Health at a glance  ·  selected time range")
stat("Availability", f'1 - ((sum(rate(http_requests_total{{status=~"5..", {APP}}}[$__range])) or vector(0)) '
     f'/ clamp_min(sum(rate(http_requests_total{{{APP}}}[$__range])), 1e-9))',
     "percentunit", 0, decimals=2, thresholds=th("red", (0.99, "orange"), (0.999, "green")),
     desc="Share of requests that did NOT fail with a server error (5xx). Health probes excluded. "
          "Industry target: 99.9% or better.")
stat("Requests / min", f"sum(rate(http_requests_total{{{APP}}}[$__rate_interval])) * 60", "short", 4,
     spark=f"sum(rate(http_requests_total{{{APP}}}[$__rate_interval])) * 60", calc="mean", color_mode="none",
     decimals=1, desc="Average requests per minute over the selected range (health probes excluded).")
stat("Server errors", f'sum(increase(http_requests_total{{status=~"5..", {APP}}}[$__range])) or vector(0)', "short", 8,
     thresholds=th("green", (1, "red")), decimals=0,
     desc="Number of 5xx responses. Anything above 0 is worth a look: open the Traces section below.")
stat("API latency p95", q(0.95, "http_request_duration_seconds", rng="$__range",
                          sel=f"route!~{STREAMING}, {APP}"), "s", 12,
     thresholds=th("green", (0.5, "orange"), (2, "red")), decimals=2,
     desc="95% of non-chat requests (login, history, documents...) were faster than this. "
          "Chat is left out: its duration is the whole generated answer.")
stat("First word p95", q(0.95, "llm_time_to_first_token_seconds", rng="$__range"), "s", 16,
     thresholds=th("green", (3, "orange"), (10, "red")), decimals=1,
     desc="How long users wait before the answer starts appearing (95th percentile). "
          "The single most important number for how fast the chatbot feels.")
stat("Full answer p95", q(0.95, "llm_stream_duration_seconds", rng="$__range"), "s", 20,
     thresholds=th("green", (15, "orange"), (45, "red")), decimals=1,
     desc="Time from question to the last word of the answer (95th percentile).")
y += 4
stat("Answers given", "sum(increase(llm_stream_duration_seconds_count[$__range])) or vector(0)", "short", 0,
     color_mode="none", decimals=0, desc="Chat + agent answers completed in the selected range.")
stat("No matching document", "sum(increase(retrieval_no_context_total[$__range])) "
     "/ clamp_min(sum(increase(retrieval_duration_seconds_count[$__range])), 1e-9)", "percentunit", 4,
     thresholds=th("green", (0.3, "orange"), (0.6, "red")), decimals=0,
     desc="Questions where no document chunk passed MIN_SCORE, so the model answered without your documents. "
          "High = users ask about things not in the knowledge base, or retrieval is broken.")
stat("Agent tool failures", '(sum(increase(agent_tool_calls_total{ok="false"}[$__range])) or vector(0)) '
     "/ clamp_min(sum(increase(agent_tool_calls_total[$__range])), 1e-9)", "percentunit", 8,
     thresholds=th("green", (0.05, "orange"), (0.2, "red")), decimals=0,
     desc="Share of agent tool calls that failed (unknown tool, tool error).")
stat("Documents indexed", 'sum(increase(ingest_documents_total{outcome="indexed"}[$__range])) or vector(0)', "short",
     12, color_mode="none", decimals=0, desc="Documents the ingestion workers finished in the selected range.")
stat("Documents failed", 'sum(increase(ingest_documents_total{outcome="failed"}[$__range])) or vector(0)', "short",
     16, thresholds=th("green", (1, "red")), decimals=0,
     desc="Documents given up on (unreadable file, or every retry failed). See the Ingestion section.")
stat("Pods up", 'sum(up{job=~"backend|worker"}) / count(up{job=~"backend|worker"})', "percentunit", 20,
     thresholds=th("red", (0.99, "orange"), (1, "green")), decimals=0,
     desc="Backend + worker pods answering Prometheus right now. Below 100%: a pod is down or restarting "
          "(details in 'Pods & resources').")
y += 4

# ======================================================================== 2. traffic & errors
row("Traffic & errors  ·  is the API healthy?")
ts("Requests / min by route", [(f"sum by (route) (rate(http_requests_total{{{APP}}}[$__rate_interval])) * 60",
                                "{{route}}")], "short", 0, stack=True,
   desc="Where the traffic goes. Use the Route filter at the top to focus on one endpoint.")
ts("Errors / min by route", [((f'sum by (route, status) (rate(http_requests_total{{status=~"[45]..", {APP}}}'
                               f"[$__rate_interval])) * 60"), "{{status}} {{route}}")], "short", 12, bars=True,
   stack=True, overrides=[color("/^5../", "red"), color("/^401/", "blue"), color("/^4../", "orange")],
   desc="Red = server errors (our bug or a dependency down). Orange = client errors (bad input, not found). "
        "Blue = 401 not logged in, usually harmless (expired session).")
y += 8
ts("Latency percentiles (all selected routes)", [
    (q(0.50, "http_request_duration_seconds", sel=APP), "p50 (typical)"),
    (q(0.95, "http_request_duration_seconds", sel=APP), "p95"),
    (q(0.99, "http_request_duration_seconds", sel=APP), "p99 (worst 1%)"),
], "s", 0, overrides=[color("p50.*", "green"), color("p95", "orange"), color("p99.*", "red")], links=TRACES_LINK,
   desc="p50: half of requests are faster. p99: only 1 in 100 is slower. "
        "A gap between p50 and p99 means a few users have a much worse experience.")
bargauge("Slowest routes (p95, selected range)",
         f"sort_desc({q(0.95, 'http_request_duration_seconds', 'route', rng='$__range', sel=APP)})", "route", "s", 12,
         thresholds=LAT, desc="Chat routes are naturally slow: they include the whole generated answer.")
y += 8

# ======================================================================== 3. AI pipeline
row("AI pipeline  ·  where does a chat answer's time go?")
bargauge("Time per step (p95), from traces",
         'sort_desc(histogram_quantile(0.95, sum by (le, span_name) (rate(traces_spanmetrics_latency_bucket'
         '{service="mychatapp-backend", span_kind!="SPAN_KIND_SERVER"}[$__range]))))', "span_name", "s", 0,
         thresholds=th("green", (1, "orange"), (5, "red")), h=9,
         desc="Every step inside a request, slowest first: ollama POST /api/chat = generating the answer, "
              "ollama POST /api/embed = embedding the question, qdrant ... = vector search, "
              "SELECT/INSERT/UPDATE = PostgreSQL, rag.retrieve = embedding + search together.")
ts("Time to first word vs full answer", [
    (q(0.95, "llm_time_to_first_token_seconds", "endpoint"), "first word p95 · {{endpoint}}"),
    (q(0.95, "llm_stream_duration_seconds", "endpoint"), "full answer p95 · {{endpoint}}"),
], "s", 12, h=9, desc="First word = how long the user stares at an empty bubble. "
                      "The gap to the full answer is the time spent streaming the text.",
   overrides=[color("first word.*", "green"), color("full answer.*", "purple")], links=TRACES_LINK)
y += 9
ts("Answers / min", [("sum by (endpoint) (rate(llm_stream_duration_seconds_count[$__rate_interval])) * 60",
                      "{{endpoint}}")], "short", 0, w=8, bars=True, stack=True)
ts("Document search latency", [(q(0.50, "retrieval_duration_seconds"), "p50"),
                               (q(0.95, "retrieval_duration_seconds"), "p95")], "s", 8, w=8,
   overrides=[color("p50", "green"), color("p95", "orange")],
   desc="Embedding the question (Ollama) + vector search (Qdrant).")
ts("Answered without documents", [
    (("sum(rate(retrieval_no_context_total[$__rate_interval])) "
      "/ clamp_min(sum(rate(retrieval_duration_seconds_count[$__rate_interval])), 1e-9)"), "no matching document")],
   "percentunit", 16, w=8, legend_calcs=("mean",),
   desc="A sudden jump usually means the index is empty or the embedding model changed.")
y += 8
ts("Agent tool calls / min", [('sum by (tool, ok) (rate(agent_tool_calls_total[$__rate_interval])) * 60',
                               "{{tool}} · ok={{ok}}")], "short", 0, w=24, h=7, bars=True, stack=True,
   overrides=[color(".*ok=false", "red")])
y += 7

# ======================================================================== 3a. caching
SC, EC = "semantic_cache_lookups_total", "embedding_cache_lookups_total"
row("Caching  ·  answers and embeddings the models did not have to compute")
stat("First questions answered from cache",
     f'sum(increase({SC}{{result="hit"}}[$__range])) / clamp_min(sum(increase({SC}[$__range])), 1e-9)',
     "percentunit", 0, w=6, color_mode="none", decimals=1,
     desc="Share of first questions answered without retrieval or the chat model. Follow-up questions are "
          "never cached. Low is normal right after a document upload: every upload empties the cache.")
stat("Embeddings from cache",
     f'sum(increase({EC}{{result!="miss"}}[$__range])) / clamp_min(sum(increase({EC}[$__range])), 1e-9)',
     "percentunit", 6, w=6, color_mode="none", decimals=1,
     desc="Texts whose vector came from memory or PostgreSQL instead of the embedding model.")
stat("Answers served from cache", f'sum(increase({SC}{{result="hit"}}[$__range])) or vector(0)', "short", 12,
     w=6, color_mode="none", decimals=0)
stat("Cache errors", 'sum(increase(cache_errors_total[$__range])) or vector(0)', "short", 18, w=6,
     thresholds=th("green", (1, "orange")), decimals=0,
     desc="Cache reads/writes that failed; requests went ahead without the cache. Errors in the semantic cache "
          "right after an upload mean it may not have been emptied: look for 'could not be cleared' in the logs.")
y += 4
ts("First questions / min", [(f"sum by (result) (rate({SC}[$__rate_interval])) * 60", "{{result}}")],
   "short", 0, bars=True, stack=True, overrides=[color("hit", "green"), color("miss", "orange")])
ts("Embeddings / min by source", [(f"sum by (result) (rate({EC}[$__rate_interval])) * 60", "{{result}}")],
   "short", 12, bars=True, stack=True,
   overrides=[color("memory", "green"), color("database", "blue"), color("miss", "orange")],
   desc="memory = this process already had it, database = another process or an earlier run computed it, "
        "miss = sent to the embedding model.")
y += 8

# ======================================================================== 3b. tokens & cost
PG = {"type": "grafana-postgresql-datasource", "uid": "postgres"}
row("Tokens & cost  ·  what the model is used for, and what it would cost")
stat("Tokens used", "sum(increase(llm_tokens_total[$__range])) or vector(0)", "short", 0, color_mode="none",
     decimals=0, desc="Input (prompt + documents + history) and output (generated) tokens, in the selected range.")
stat("Estimated cost", "sum(increase(llm_cost_usd_total[$__range])) or vector(0)", "currencyUSD", 4,
     color_mode="none", decimals=4,
     desc="Tokens x the prices in LLM_PRICE_INPUT_PER_MTOK / LLM_PRICE_OUTPUT_PER_MTOK. Ollama itself is free: "
          "by default this is what the same traffic would cost on a small paid model.")
stat("Cost per answer", "sum(increase(llm_cost_usd_total{endpoint=~\"chat|agent\"}[$__range])) "
     "/ clamp_min(sum(increase(llm_stream_duration_seconds_count[$__range])), 1e-9)", "currencyUSD", 8,
     color_mode="none", decimals=5, desc="Average estimated cost of one chat or agent answer.")
stat("Input tokens per answer", 'sum(increase(llm_tokens_total{kind="input", endpoint=~"chat|agent"}[$__range])) '
     "/ clamp_min(sum(increase(llm_stream_duration_seconds_count[$__range])), 1e-9)", "short", 12,
     thresholds=th("green", (4000, "orange"), (8000, "red")), decimals=0,
     desc="Prompt size of an average answer: instructions + retrieved chunks + chat history. "
          "Large and growing = long chats or too many chunks; it drives both cost and time to first word.")
stat("Output tokens per answer", 'sum(increase(llm_tokens_total{kind="output", endpoint=~"chat|agent"}[$__range])) '
     "/ clamp_min(sum(increase(llm_stream_duration_seconds_count[$__range])), 1e-9)", "short", 16,
     color_mode="none", decimals=0, desc="Length of an average answer.")
stat("Generation speed", q(0.5, "llm_output_tokens_per_second", rng="$__range"), "short", 20,
     thresholds=th("red", (5, "orange"), (15, "green")), decimals=1,
     desc="Median tokens per second the model generates (reported by Ollama). Under ~5/s answers feel slow; "
          "a GPU gives 50+.")
panels[-1]["fieldConfig"]["defaults"]["unit"] = "suffix: tok/s"
y += 4
ts("Tokens / min", [('sum by (kind) (rate(llm_tokens_total[$__rate_interval])) * 60', "{{kind}}")], "short", 0, w=8,
   bars=True, stack=True, overrides=[color("input", "blue"), color("output", "purple")],
   desc="Input = what we send (prompt, documents, history). Output = what the model writes.")
ts("Estimated cost / hour by feature", [('sum by (endpoint) (rate(llm_cost_usd_total[$__rate_interval])) * 3600',
                                        "{{endpoint}}")], "currencyUSD", 8, w=8, stack=True,
   desc="chat = normal answers, agent = agent mode (several model calls per answer), title = chat titles.")
ts("Generation speed", [(q(0.5, "llm_output_tokens_per_second", "model"), "p50 · {{model}}"),
                        (q(0.05, "llm_output_tokens_per_second", "model"), "slowest 5% · {{model}}")],
   "short", 16, w=8, desc="Tokens per second. A drop means the model server is overloaded or swapped to CPU.")
panels[-1]["fieldConfig"]["defaults"]["unit"] = "suffix: tok/s"
y += 8
panels.append({
    "type": "table", "title": "Usage by user (selected range)", "id": _id(), "datasource": PG,
    "description": "From the llm_usage table in PostgreSQL: one row per model call, with the user who made it. "
                   "Cost at the prices configured when each call was made.",
    "gridPos": {"h": 9, "w": 24, "x": 0, "y": y},
    "targets": [{"refId": "A", "datasource": PG, "format": "table", "rawQuery": True, "editorMode": "code",
                 "rawSql": (
                     "SELECT coalesce(u.username, '(deleted user)') AS \"User\",\n"
                     "       count(*) FILTER (WHERE l.endpoint IN ('chat', 'agent')) AS \"Answers\",\n"
                     "       sum(l.input_tokens) AS \"Input tokens\",\n"
                     "       sum(l.output_tokens) AS \"Output tokens\",\n"
                     "       sum(l.input_tokens + l.output_tokens) AS \"Total tokens\",\n"
                     "       sum(l.cost_usd) AS \"Est. cost\",\n"
                     "       max(l.created_at) AS \"Last used\"\n"
                     "FROM llm_usage l LEFT JOIN users u ON u.id = l.user_id\n"
                     "WHERE $__timeFilter(l.created_at)\n"
                     "GROUP BY u.username\n"
                     "ORDER BY \"Total tokens\" DESC\n"
                     "LIMIT 50")}],
    "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": [
        {"matcher": {"id": "byName", "options": "Est. cost"},
         "properties": [{"id": "unit", "value": "currencyUSD"}, {"id": "decimals", "value": 4}]},
        {"matcher": {"id": "byName", "options": "Total tokens"},
         "properties": [{"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "lcd"}},
                        {"id": "color", "value": {"mode": "continuous-BlPu"}}]},
        {"matcher": {"id": "byName", "options": "Last used"},
         "properties": [{"id": "unit", "value": "dateTimeFromNow"}]}]},
    "options": {"showHeader": True, "cellHeight": "sm"},
})
y += 9

# ======================================================================== 3c. guardrails
GR = "guardrail_events_total"
row("Guardrails  ·  what was blocked, and why")
stat("Blocked", f'sum(increase({GR}{{action="blocked"}}[$__range])) or vector(0)', "short", 0, w=6,
     color_mode="none", decimals=0,
     desc="Questions refused, document chunks dropped, tool results and answers withheld. Details (rule, file, "
          "user) are in the audit log: event=guardrail.blocked.")
stat("Flagged (monitor mode)", f'sum(increase({GR}{{action="flagged"}}[$__range])) or vector(0)', "short", 6, w=6,
     color_mode="none", decimals=0,
     desc="What would have been blocked with GUARDRAILS_MODE=enforce. Use it to check for false positives first.")
stat("Poisoned document chunks", f'sum(increase({GR}{{check="injection_document"}}[$__range])) or vector(0)',
     "short", 12, w=6, thresholds=th("green", (1, "red")), decimals=0,
     desc="Retrievals that dropped a chunk carrying instructions. Not zero = a file in the knowledge base contains "
          "a prompt injection: find it in the audit log (source=...) and delete it.")
stat("Guard model errors", f'sum(increase({GR}{{action="error"}}[$__range])) or vector(0)', "short", 18, w=6,
     thresholds=th("green", (1, "orange")), decimals=0,
     desc="Content checks skipped because GUARD_MODEL failed. The check fails open: those messages were not "
          "classified.")
y += 4
ts("Guardrail events / hour by check", [(f"sum by (check, action) (rate({GR}[$__rate_interval])) * 3600",
                                        "{{check}} · {{action}}")], "short", 0, w=24, h=7, bars=True, stack=True,
   overrides=[color(".*error", "orange")],
   desc="injection_input = the user's message, injection_document = a retrieved chunk, injection_tool = an agent "
        "tool result, prompt_leak = the answer repeated the system prompt, content_* = Llama Guard.")
y += 7

# ======================================================================== 4. ingestion
row("Document ingestion  ·  background workers")
ts("Documents / min by outcome", [("sum by (outcome) (rate(ingest_documents_total[$__rate_interval])) * 60",
                                   "{{outcome}}")], "short", 0, w=8, bars=True, stack=True,
   overrides=[color("indexed", "green"), color("retry", "orange"), color("failed", "red")],
   desc="indexed = done. retry = a dependency hiccup, it will be tried again. failed = given up.")
ts("Time per document", [(q(0.50, "ingest_duration_seconds"), "p50"), (q(0.95, "ingest_duration_seconds"), "p95")],
   "s", 8, w=8, overrides=[color("p50", "green"), color("p95", "orange")],
   desc="Download from MinIO + chunk + embed + store, for one document.")
ts("Chunks indexed / min", [("sum(rate(ingest_chunks_total[$__rate_interval])) * 60", "chunks")], "short", 16, w=8,
   legend_calcs=("mean", "max"))
y += 8

# ======================================================================== 5. traces
row("Traces  ·  one request in detail")
panels.append({
    "type": "nodeGraph", "title": "Service map (who calls whom)", "id": _id(), "datasource": TEMPO,
    "description": "Built from traces. Each circle is a service; arrows show calls with their rate and latency. "
                   "Red ring = errors.",
    "gridPos": {"h": 12, "w": 10, "x": 0, "y": y},
    "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "serviceMap"}],
    "options": {},
})
panels.append({
    "type": "table", "title": "Recent requests  ·  click a Trace ID to see its timeline", "id": _id(),
    "datasource": TEMPO,
    "description": "One row per request (or ingested document). Name is the endpoint; Duration is the total time. "
                   "Click the blue Trace ID to open the step-by-step timeline. "
                   "Use the arrow only if you want the raw span list.",
    "gridPos": {"h": 12, "w": 14, "x": 10, "y": y},
    "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "limit": 30, "tableType": "traces",
                 "query": '{resource.service.name=~"mychatapp-.*" && (kind=server || name="ingest.document")}'}],
    "fieldConfig": {"defaults": {}, "overrides": [
        {"matcher": {"id": "byName", "options": "Duration"},
         "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": th("green", (2000, "orange"), (10000, "red"))},
                        {"id": "color", "value": {"mode": "thresholds"}}]}]},
    "options": {"showHeader": True, "cellHeight": "sm",
                "sortBy": [{"displayName": "Start time", "desc": True}]},
})
y += 12

# ======================================================================== 6. resources (collapsed)
res = row("Pods & resources", collapsed=True)
ts("Memory per pod", [('process_resident_memory_bytes{job=~"backend|worker"}', "{{job}} {{instance}}")], "bytes", 0,
   w=8, into=res["panels"], legend_calcs=("lastNotNull", "max"),
   desc="Resident memory. The Kubernetes limit is 1 GiB: a pod near it will be OOM-killed.")
ts("CPU per pod (cores)", [('rate(process_cpu_seconds_total{job=~"backend|worker"}[$__rate_interval])',
                            "{{job}} {{instance}}")], "short", 8, w=8, into=res["panels"],
   legend_calcs=("mean", "max"))
panels_up = {
    "type": "state-timeline", "title": "Pod up / down", "id": _id(), "datasource": PROM,
    "gridPos": {"h": 8, "w": 8, "x": 16, "y": y},
    "targets": [target('up{job=~"backend|worker"}', "{{job}} {{instance}}")],
    "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": th("red", (1, "green")),
                                 "mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"},
                                                                           "1": {"text": "UP", "color": "green"}}}],
                                 "custom": {"fillOpacity": 80, "lineWidth": 0}}, "overrides": []},
    "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8, "legend": {"showLegend": False}},
}
res["panels"].append(panels_up)
y += 1

dashboard = {
    "uid": "mychatapp", "title": "MyChatApp · Service overview", "tags": ["mychatapp", "slo", "llm"],
    "description": "Health, latency, AI pipeline and ingestion of MyChatApp. Hover the (i) on any panel for "
                   "what it means.",
    "timezone": "browser", "schemaVersion": 39, "version": 2, "editable": False, "graphTooltip": 1,
    "refresh": "30s", "time": {"from": "now-3h", "to": "now"},
    "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m"]},
    "templating": {"list": [{
        "name": "route", "label": "Route", "type": "query", "datasource": PROM,
        "query": {"query": 'label_values(http_requests_total{route!~"/health.*"}, route)', "refId": "route"},
        "definition": 'label_values(http_requests_total{route!~"/health.*"}, route)',
        "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*",
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "sort": 1,
    }]},
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                              "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                              "name": "Annotations & Alerts", "type": "dashboard"}]},
    "links": [
        {"title": "Explore traces", "type": "link", "icon": "external link", "url": TRACES_LINK[0]["url"]
         .replace(" && kind=server && duration > 2s", "")},
        {"title": "Prometheus targets", "type": "link", "icon": "external link", "url": "http://localhost:9090/targets",
         "targetBlank": True},
    ],
    "panels": panels,
}
text = json.dumps(dashboard, indent=2) + "\n"
if "--check" in sys.argv:
    if OUT.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
        sys.exit(f"{OUT} is out of date: run python {Path(__file__).as_posix()} and commit the result")
    print(f"{OUT.name} is up to date")
else:
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(panels)} panels)")
