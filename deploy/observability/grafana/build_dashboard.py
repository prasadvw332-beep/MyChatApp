"""Builds the Grafana dashboard dashboards/mychatapp.json. Edit this file, not the JSON.

    python deploy/observability/grafana/build_dashboard.py           # rewrite the JSON
    python deploy/observability/grafana/build_dashboard.py --check   # CI: fail if the JSON is out of date

The JSON is ~5000 lines of Grafana's format; written by hand it drifts (a panel copied with the wrong
query, inconsistent colours). Here every panel is one function call, with shared thresholds and colours.
Plain Python, no dependencies. Restart Grafana to load the result.

Layout (top to bottom, most important first; the same order an on-call engineer asks the questions):
  1. Service status: alarms firing, SLO tiles, one health lane per component, the active alerts
  2. Golden signals: the numbers to read before anything else, for the selected time range
  3. SLOs & error budget (availability 99.9%, API latency, time to first word; burn rate, alerts.yml)
  4. Traffic & errors (RED: rate, errors, duration)
  5. AI pipeline: where a chat answer's time goes
  6. Caching, Tokens & cost (Prometheus totals + per-user table from PostgreSQL), Guardrails
  7. Document ingestion: throughput (Prometheus) and the queue itself (PostgreSQL)
  8. Data stores & model server: PostgreSQL, Qdrant, MinIO, Ollama
  9. Traces: service map + recent requests
  10. Telemetry pipeline, Pods & resources (collapsed)

Colours: green/orange/red mean healthy/warning/broken and are used for nothing else. Series that are not a
status (input vs output tokens, hit vs miss...) get blue/purple/green/orange fixed per series, never by rank.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).parent / "dashboards" / "mychatapp.json"
PROM = {"type": "prometheus", "uid": "prometheus"}
TEMPO = {"type": "tempo", "uid": "tempo"}
PG = {"type": "grafana-postgresql-datasource", "uid": "postgres"}

# app traffic only: health probes are frequent and fast and would flatten every number
APP = 'route!~"/health.*", route=~"$route"'
STREAMING = '"/chat|/agent/chat"'  # their duration is the whole generated answer

# SLO targets; the same numbers as deploy/observability/alerts.yml
SLO_AVAILABILITY = 0.999
SLO_API_LATENCY = 0.99  # share of non-chat requests under 1 s
SLO_FIRST_WORD = 0.95  # share of answers whose first word arrives within 10 s
BUDGET = 1 - SLO_AVAILABILITY

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


def sql_target(sql, ref="A"):
    return {"refId": ref, "datasource": PG, "format": "table", "rawQuery": True, "editorMode": "code", "rawSql": sql}


def row(title, collapsed=False):
    global y
    p = {"type": "row", "title": title, "id": _id(), "collapsed": collapsed,
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}
    panels.append(p)
    y += 1
    return p


def stat(title, expr, unit, x, w=4, h=4, thresholds=None, desc="", decimals=None, spark=None, calc="lastNotNull",
         color_mode="background", into=None, sql=None, mappings=None, trend=False):
    """A tile. With `spark` (a range query), the tile shows its trend as a sparkline; `trend=True` does the same
    with `expr` itself, for values that are already a level (memory, connections, series), not a range total.
    With `sql`, it reads PostgreSQL instead of Prometheus (`expr` is then ignored)."""
    spark = spark or (expr if trend else None)
    targets = [sql_target(sql)] if sql else [target(spark or expr, instant=spark is None)]
    p = {
        "type": "stat", "title": title, "id": _id(), "datasource": PG if sql else PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": thresholds or th("green"),
                                     "color": {"mode": "thresholds"}, "noValue": "–",
                                     **({"mappings": mappings} if mappings else {}),
                                     **({"decimals": decimals} if decimals is not None else {})},
                        "overrides": []},
        "options": {"reduceOptions": {"calcs": [calc], "fields": "", "values": False},
                    "colorMode": color_mode, "graphMode": "area" if spark else "none", "textMode": "value",
                    "justifyMode": "center", "wideLayout": True, "showPercentChange": False},
    }
    (into if into is not None else panels).append(p)
    return p


def ts(title, targets, unit, x, w=12, h=8, desc="", stack=False, bars=False, overrides=None, min0=True,
       legend_calcs=("mean", "max"), into=None, links=None, lines=None, log=False):
    """A time series. `lines` (a th(...)) draws dashed threshold lines, e.g. an SLO target or an alert level.
    Bars are at least one minute wide: per 15 s scrape they turn into a jagged comb. `log` gives a log y axis, for
    latencies where a few slow outliers (a cold start) would flatten everything else."""
    p = {
        "type": "timeseries", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(e, legend, ref=chr(65 + i)) for i, (e, legend) in enumerate(targets)],
        "fieldConfig": {
            "defaults": {
                "unit": unit, "color": {"mode": "palette-classic"}, "noValue": "No data in this time range",
                **({"min": 0} if min0 and not log else {}),
                **({"thresholds": lines} if lines else {}),
                "custom": {
                    "drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 70 if bars else 22,
                    "gradientMode": "opacity", "showPoints": "never", "spanNulls": True, "lineInterpolation": "smooth",
                    "barAlignment": 0, "axisSoftMin": 0,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                    **({"thresholdsStyle": {"mode": "dashed"}} if lines else {}),
                    **({"scaleDistribution": {"type": "log", "log": 10}} if log else {}),
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
    if bars:
        p["interval"] = "1m"
    (into if into is not None else panels).append(p)
    return p


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


UPDOWN = [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red", "index": 0},
                                        "1": {"text": "UP", "color": "green", "index": 1}}}]


def updown(title, targets, x, w, h, desc="", into=None):
    """One lane per component, green while it is up, red while it is down."""
    p = {
        "type": "state-timeline", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(e, legend, ref=chr(65 + i)) for i, (e, legend) in enumerate(targets)],
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": th("red", (1, "green")),
                                     "mappings": UPDOWN, "noValue": "no data",
                                     "custom": {"fillOpacity": 80, "lineWidth": 0}}, "overrides": []},
        "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.75, "alignValue": "center",
                    "legend": {"showLegend": False}, "tooltip": {"mode": "single"}},
    }
    (into if into is not None else panels).append(p)


def table(title, x, w, h, desc="", sql=None, expr=None, overrides=None, transformations=None, no_value=None,
          into=None):
    """A table from PostgreSQL (`sql`) or from an instant Prometheus query (`expr`)."""
    tgt = sql_target(sql) if sql else {**target(expr, instant=True), "format": "table"}
    p = {
        "type": "table", "title": title, "id": _id(), "datasource": PG if sql else PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [tgt],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}, **({"noValue": no_value} if no_value else {})},
                        "overrides": overrides or []},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }
    if transformations:
        p["transformations"] = transformations
    (into if into is not None else panels).append(p)
    return p


def color(name_regex, c):
    return {"matcher": {"id": "byRegexp", "options": name_regex},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}


def by_name(name, *props):
    return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": k, "value": v} for k, v in props]}


def q(quant, metric, by="", rng="$__rate_interval", sel=""):
    by_ = f"le, {by}" if by else "le"
    return f"histogram_quantile({quant}, sum by ({by_}) (rate({metric}_bucket{{{sel}}}[{rng}])))"


def ratio(good, total):
    """good / total, 'no data' when nothing happened (not 0%, which would turn an SLO tile red at night)."""
    return f"({good}) / (({total}) > 0)"


def gauge(title, expr, unit, x, w=6, h=6, thresholds=None, desc="", decimals=None, lo=0, hi=1):
    """A dial for a number with a known range and a target: SLIs, burn rate, saturation."""
    p = {
        "type": "gauge", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr, instant=True)],
        "fieldConfig": {"defaults": {"unit": unit, "min": lo, "max": hi, "thresholds": thresholds or th("green"),
                                     "color": {"mode": "thresholds"}, "noValue": "–",
                                     **({"decimals": decimals} if decimals is not None else {})}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "showThresholdMarkers": True, "showThresholdLabels": False, "sizing": "auto", "minVizHeight": 75},
    }
    panels.append(p)
    return p


def heatmap(title, metric, unit, x, w=12, h=8, desc="", sel="", scheme="Blues"):
    """Every request as a dot in its latency bucket, over time: shows what percentiles hide (two populations,
    a slow tail that comes and goes). One hue, light = few, dark... in Grafana's dark theme: bright = many."""
    p = {
        "type": "heatmap", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y}, "interval": "1m",
        "targets": [{**target(f"sum by (le) (increase({metric}_bucket{{{sel}}}[$__interval]))", "{{le}}"),
                     "format": "heatmap"}],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {
            "calculate": False, "cellGap": 2, "cellValues": {"unit": "short"},
            "color": {"mode": "scheme", "scheme": scheme, "steps": 64, "exponent": 0.5, "scale": "exponential",
                      "fill": "dark-blue", "reverse": False},
            "filterValues": {"le": 1e-9}, "rowsFrame": {"layout": "auto"},
            "yAxis": {"unit": unit, "axisPlacement": "left", "reverse": False},
            "tooltip": {"mode": "single", "yHistogram": True, "showColorScale": True},
            "legend": {"show": True}, "exemplars": {"color": "rgba(255, 0, 255, 0.7)"},
        },
    }
    panels.append(p)


def donut(title, expr, legend, unit, x, w=6, h=8, desc="", overrides=None):
    """Share of a whole over the selected range: at most a handful of slices, labelled with their percent."""
    p = {
        "type": "piechart", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr, legend, instant=True)],
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"}, "noValue": "No data"},
                        "overrides": overrides or []},
        "options": {"pieType": "donut", "displayLabels": ["percent"],
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                               "values": ["value", "percent"]},
                    "tooltip": {"mode": "single", "sort": "none"}},
    }
    panels.append(p)


# ---------------------------------------------------------------- live system map (Canvas panel)
# Each node is a box tinted by a health field (green tint = up, red = down) with a live number inside; links are
# animated dashed arrows in the direction requests flow. Positions are percentages of the panel, so the map
# scales with the screen. Field names below are the queries' legends.
UP_TINT = th("rgba(242, 73, 92, 0.55)", (1, "rgba(115, 191, 105, 0.16)"))
NEUTRAL = "rgba(204, 204, 220, 0.04)"


def canvas(title, nodes, links, queries, units, h=12, desc="", field_thresholds=None):
    """nodes: (name, label, (left, top, width, height) in %, health field or None, value field).
    links: (from, to, colour). queries: (legend, expr). units: {legend: unit}."""
    elements = []
    for name, label, (left, top, width, height), health, value in nodes:
        place = {"left": left, "top": top,
                 "right": round(100 - left - width, 2), "bottom": round(100 - top - height, 2)}
        scale = {"horizontal": "scale", "vertical": "scale"}
        elements.append({
            "type": "rectangle", "name": name, "constraint": scale, "placement": place,
            "config": {"text": {"mode": "fixed", "fixed": label}, "color": {"fixed": "#ccccdc"}, "size": 13,
                       "align": "center", "valign": "top"},
            "background": {"color": {"field": health, "fixed": NEUTRAL} if health else {"fixed": NEUTRAL}},
            "border": {"color": {"fixed": "rgba(204, 204, 220, 0.18)"}, "width": 1, "radius": 10},
            "connections": [{
                "source": {"x": 1, "y": 0}, "target": {"x": -1, "y": 0}, "targetName": dst, "path": "straight",
                "color": {"fixed": c}, "size": {"fixed": 2, "min": 1, "max": 10}, "direction": "forward",
                "lineStyle": {"style": "dashed", "animate": True},
            } for src, dst, c in links if src == name],
        })
        # the live number, in the lower 2/3 of the box (a metric-value element on top of the rectangle)
        elements.append({
            "type": "metric-value", "name": name + "-value", "constraint": scale,
            "placement": {**place, "top": round(top + height * 0.3, 2)},
            "config": {"text": {"mode": "field", "field": value, "fixed": ""}, "size": 24, "align": "center",
                       "valign": "middle",
                       "color": {"field": value, "fixed": "#ffffff"} if value in (field_thresholds or {})
                       else {"fixed": "#ffffff"}},
            "background": {"color": {"fixed": "transparent"}}, "border": {"color": {"fixed": "transparent"}},
        })
    # dict.fromkeys, not a set: the order must be the same on every run, or `--check` fails in CI
    overrides = [by_name(f, ("thresholds", UP_TINT)) for f in dict.fromkeys(n[3] for n in nodes if n[3])]
    overrides += [by_name(f, ("unit", u)) for f, u in units.items()]
    overrides += [by_name(f, ("thresholds", t), ("color", {"mode": "thresholds"}))
                  for f, t in (field_thresholds or {}).items()]
    refs = [chr(65 + i) if i < 26 else "A" + chr(65 + i - 26) for i in range(len(queries))]
    panels.append({
        "type": "canvas", "title": title, "id": _id(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": 24, "x": 0, "y": y},
        "targets": [target(e, legend, ref=r) for r, (legend, e) in zip(refs, queries)],
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": th("#ffffff"), "decimals": 1,
                                     "noValue": "idle"}, "overrides": overrides},
        "options": {"inlineEditing": False, "showAdvancedTypes": True, "panZoom": False, "infinitePan": False,
                    "root": {"type": "frame", "name": "root", "elements": elements,
                             "background": {"color": {"fixed": "transparent"}},
                             "border": {"color": {"fixed": "transparent"}},
                             "constraint": {"horizontal": "left", "vertical": "top"},
                             "placement": {"left": 0, "top": 0, "width": 100, "height": 100}}},
    })


LAT = th("green", (2, "orange"), (5, "red"))
PCT = [color("p50.*", "light-blue"), color("p95.*", "purple"), color("p99.*", "dark-purple")]
# the mean of a percentile means nothing: show where it is now, and its worst
PCT_LEGEND = ("lastNotNull", "max")  # seconds, for whole requests
TRACES_LINK = [{"title": "Open slow traces in Explore", "url": "/explore?schemaVersion=1&panes=" + json.dumps(
    {"a": {"datasource": "tempo", "queries": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql",
                                               "query": '{resource.service.name=~"mychatapp-.*" && kind=server && duration > 2s}',
                                               "tableType": "traces", "limit": 20}],
           "range": {"from": "now-1h", "to": "now"}}}, separators=(",", ":")), "targetBlank": False}]
ERR = f'http_requests_total{{status=~"5..", {APP}}}'
ALL = f"http_requests_total{{{APP}}}"
FIRING = 'ALERTS{alertstate="firing"'

SC, EC = "semantic_cache_lookups_total", "embedding_cache_lookups_total"
# ======================================================================== 0. start here
# A 30-second guide for someone opening the dashboard for the first time. Collapsible, so regulars can fold it away.
guide = row("Start here  ·  how to read this dashboard in 30 seconds")
GUIDE = [  # section, the question it answers, look at first, healthy looks like
    ("Service status", "Is anything broken *right now*?", "Alarm tiles, the live map, component lanes",
     "No alarms, every box and lane green"),
    ("Golden signals", "How is the app doing in this time range?", "Server errors, API latency, Full answer",
     "0 errors, API under 0.5 s"),
    ("SLOs & error budget", "Are we keeping our promises to users?", "The four dials",
     "Dials in green, burn rate under 1×"),
    ("Traffic & errors", "Which endpoint is slow or failing?", "Errors by route, Slowest routes",
     "Only blue 401s, no red 5xx"),
    ("AI pipeline", "Where does an answer's time go?", "Time per step, First word heatmap",
     "Most time in *ollama /api/chat*, first word under 3 s"),
    ("Caching · Tokens & cost · Guardrails", "What does the AI cost, and is it being attacked?",
     "Cost per answer, Poisoned chunks", "Poisoned chunks = 0"),
    ("Ingestion · Data stores", "Are uploads processed, and are the databases OK?",
     "Ingest backlog, Connections used", "Backlog near 0, connections under 70%"),
    ("Traces", "What happened inside one slow request?", "Click a Trace ID", "The longest bar is where the time went"),
]
panels.append({
    "type": "text", "title": "", "id": _id(), "transparent": True,
    "gridPos": {"h": 11, "w": 24, "x": 0, "y": y},
    "options": {"mode": "markdown", "content": "\n".join([
        "**Read top to bottom: each section answers the next question an on-call engineer would ask.** "
        "Colours mean the same everywhere: **green** = healthy, **orange** = getting worse, **red** = act now. "
        "Every other colour just tells series apart. Hover the **(i)** on any panel for what it means.",
        "",
        "| Section | The question it answers | Look at first | Healthy looks like |",
        "|---|---|---|---|",
        *[f"| **{a}** | {b} | {c} | {d} |" for a, b, c, d in GUIDE],
        "",
        "**Something red?** Check *Active alerts* → find the matching red band on the graphs → click "
        "**Open slow traces** on a latency panel. **Everything empty?** Nobody is using the app: start the "
        "synthetic users (`--profile demo up -d --no-deps loadgen`, see docs/observability.md).",
    ])},
})
y += 11

# ======================================================================== 1. service status
row("Service status  ·  right now")
stat("Critical alarms", f'count({FIRING}, severity="critical"}}) or vector(0)', "short", 0,
     thresholds=th("green", (1, "red")), decimals=0, mappings=[{"type": "value", "options": {"0": {"text": "None"}}}],
     desc="Critical alerts firing now (alerts.yml): error budget burning fast, a component down, a poisoned "
          "document. Each one is listed in 'Active alerts' to the right.")
stat("Warnings", f'count({FIRING}, severity="warning"}}) or vector(0)', "short", 4,
     thresholds=th("green", (1, "orange")), decimals=0,
     mappings=[{"type": "value", "options": {"0": {"text": "None"}}}],
     desc="Warning alerts firing now: slow first word, saturation, ingest failures, cache errors.")
stat("Availability", "1 - " + ratio(f"sum(rate({ERR}[$__range])) or vector(0)", f"sum(rate({ALL}[$__range]))"),
     "percentunit", 8, decimals=2, thresholds=th("red", (0.99, "orange"), (SLO_AVAILABILITY, "green")),
     desc="Share of requests that did NOT fail with a server error (5xx), health probes excluded. "
          f"SLO: {SLO_AVAILABILITY:.1%}.")
stat("Error budget left", "1 - " + ratio(f"sum(increase({ERR}[$__range])) or vector(0)",
                                         f"sum(increase({ALL}[$__range]))") + f" / {BUDGET:g}",
     "percentunit", 12, decimals=0, thresholds=th("red", (0.25, "orange"), (0.5, "green")),
     desc=f"The SLO allows {BUDGET:.1%} of requests to fail. This is how much of that allowance is left, "
          "measured over the selected range. Below 0% = the SLO is missed for this period.")
stat("First word p95", q(0.95, "llm_time_to_first_token_seconds", rng="$__range"), "s", 16,
     thresholds=th("green", (3, "orange"), (10, "red")), decimals=1,
     desc="How long users wait before the answer starts appearing (95th percentile). "
          "The single most important number for how fast the chatbot feels.")
stat("Components up", "sum(up) / count(up)", "percentunit", 20, decimals=0,
     thresholds=th("red", (0.99, "orange"), (1, "green")),
     desc="Every target Prometheus scrapes (app pods, data stores, telemetry stack) answering right now. "
          "The lanes below show which one is not.", trend=True)
y += 4
NOT_HEALTH = 'route!~"/health.*"'
# columns (left %, width %): users | app | data stores & model | KPIs | KPIs / telemetry | headline
C0, C1, C2, C3, C4, C5 = (1, 11), (17, 12), (39, 12), (57, 12.5), (71, 12.5), (87, 12)
ROWS = (3, 28, 53, 78)  # top %, for boxes 19% high


def at(col, top, hgt=19):
    return col[0], top, col[1], hgt


canvas("Live system map  ·  requests flow left to right", nodes=[
    ("users", "Users", at(C0, 15.5), None, "users_rpm"),
    ("api", "API · backend", at(C1, 15.5), "api_up", "api_p95"),
    ("workers", "Ingestion workers", at(C1, 65.5), "workers_up", "workers_n"),
    ("ollama", "Ollama · model", at(C2, ROWS[0]), "ollama_ok", "ollama_tps"),
    ("qdrant", "Qdrant · vectors", at(C2, ROWS[1]), "qdrant_up", "qdrant_p95"),
    ("postgres", "PostgreSQL", at(C2, ROWS[2]), "pg_up", "pg_tps"),
    ("minio", "MinIO · files", at(C2, ROWS[3]), "minio_up", "minio_bytes"),
    ("collector", "OTel Collector", at(C3, ROWS[0]), "otel_up", "otel_sps"),
    ("tempo", "Tempo · traces", at(C4, ROWS[0]), "tempo_up", "tempo_live"),
    ("k_answers", "Answers / min", at(C3, ROWS[1]), None, "kpi_answers"),
    ("k_first", "First word p95", at(C4, ROWS[1]), None, "kpi_first"),
    ("k_tokens", "Tokens / min", at(C3, ROWS[2]), None, "kpi_tokens"),
    ("k_cost", "Est. cost / hour", at(C4, ROWS[2]), None, "kpi_cost"),
    ("prometheus", "Prometheus", at(C3, ROWS[3]), "prom_up", "prom_series"),
    ("grafana", "Grafana", at(C4, ROWS[3]), "grafana_up", "grafana_up"),
    ("k_avail", "Availability · 1 h", at(C5, 3, 44), None, "kpi_avail"),
    ("k_budget", "Error budget left · 6 h", at(C5, 53, 44), None, "kpi_budget"),
], links=[
    ("users", "api", "#5794F2"),
    *[("api", d, "#5794F2") for d in ("ollama", "qdrant", "postgres", "minio")],
    *[("workers", d, "#B877D9") for d in ("ollama", "qdrant", "postgres", "minio")],
    ("collector", "tempo", "#8e8e8e"),
    ("prometheus", "grafana", "#8e8e8e"),
], queries=[
    ("users_rpm", f"sum(rate(http_requests_total{{{NOT_HEALTH}}}[2m])) * 60"),
    ("api_up", 'min(up{job="backend"})'),
    ("api_p95", q(0.95, "http_request_duration_seconds", rng="5m", sel=f'{NOT_HEALTH}, route!~{STREAMING}')),
    ("workers_up", 'min(up{job="worker"})'),
    ("workers_n", 'sum(up{job="worker"})'),
    ("ollama_ok", '(sum(rate(traces_spanmetrics_calls_total{span_name=~"ollama.*", '
                  'status_code="STATUS_CODE_ERROR"}[5m])) or vector(0)) == bool 0'),
    ("ollama_tps", q(0.5, "llm_output_tokens_per_second", rng="15m")),
    ("qdrant_up", 'min(up{job="qdrant"})'),
    ("qdrant_p95", q(0.95, "rest_responses_duration_seconds", rng="5m", sel='endpoint=~".*/points/query"')),
    ("pg_up", "min(pg_up)"),
    ("pg_tps", 'sum(rate(pg_stat_database_xact_commit{datname="mychatapp"}[2m]))'),
    ("minio_up", 'min(up{job="minio"})'),
    ("minio_bytes", "sum(minio_bucket_usage_total_bytes)"),
    ("otel_up", 'min(up{job="otel-collector"})'),
    ("otel_sps", "sum(rate(otelcol_receiver_accepted_spans_total[2m]))"),
    ("tempo_up", 'min(up{job="tempo"})'),
    ("tempo_live", "sum(tempo_ingester_live_traces)"),
    ("prom_up", 'min(up{job="prometheus"})'),
    ("prom_series", "prometheus_tsdb_head_series"),
    ("grafana_up", 'min(up{job="grafana"})'),
    ("kpi_answers", "sum(rate(llm_stream_duration_seconds_count[5m])) * 60"),
    ("kpi_first", q(0.95, "llm_time_to_first_token_seconds", rng="15m")),
    ("kpi_tokens", "sum(rate(llm_tokens_total[5m])) * 60"),
    ("kpi_cost", "sum(rate(llm_cost_usd_total[15m])) * 3600"),
    ("kpi_avail", "1 - " + ratio(f'sum(rate(http_requests_total{{status=~"5..", {NOT_HEALTH}}}[1h])) or vector(0)',
                                 f"sum(rate(http_requests_total{{{NOT_HEALTH}}}[1h]))")),
    ("kpi_budget", f"1 - slo:http_error_ratio:rate6h / {BUDGET:g}"),
], units={"users_rpm": "suffix: req/min", "api_p95": "s", "workers_n": "suffix: up", "ollama_tps": "suffix: tok/s",
          "qdrant_p95": "s", "pg_tps": "suffix: tx/s", "minio_bytes": "bytes", "otel_sps": "suffix: spans/s",
          "tempo_live": "suffix: live", "prom_series": "short", "kpi_answers": "short", "kpi_first": "s",
          "kpi_tokens": "short", "kpi_cost": "currencyUSD", "kpi_avail": "percentunit",
          "kpi_budget": "percentunit"},
   field_thresholds={"kpi_first": th("green", (3, "orange"), (10, "red")),
                     "kpi_avail": th("red", (0.99, "orange"), (SLO_AVAILABILITY, "green")),
                     "kpi_budget": th("red", (0.25, "orange"), (0.5, "green")),
                     "api_p95": th("#ffffff", (0.5, "orange"), (2, "red"))},
   desc="The whole system on one screen, refreshed every 10 s. Box tint: green = up, red = down (Ollama: a "
        "model call failed in the last 5 min). Blue arrows: user requests through the API; purple: the "
        "ingestion workers. Numbers: API p95 excluding chat, Ollama median generation speed, Qdrant search p95, "
        "PostgreSQL commits per second, MinIO storage used.")
panels[-1]["fieldConfig"]["overrides"] += [by_name(f, ("decimals", 0)) for f in ("workers_n", "kpi_tokens")]
panels[-1]["fieldConfig"]["overrides"].append(  # text only: a mapping colour would replace the box tint
    by_name("grafana_up", ("mappings", [{"type": "value", "options": {"0": {"text": "DOWN"}, "1": {"text": "UP"}}}])))
y += 12
updown("Component health", [
    ('min(up{job="backend"})', "API (backend)"),
    ('min(up{job="worker"})', "Ingestion workers"),
    ("min(pg_up)", "PostgreSQL"),
    ('min(up{job="qdrant"})', "Qdrant (vectors)"),
    ('min(up{job="minio"})', "MinIO (files)"),
    ('(sum(rate(traces_spanmetrics_calls_total{span_name=~"ollama.*", status_code="STATUS_CODE_ERROR"}[5m])) '
     "or vector(0)) == bool 0", "Ollama (no failed calls)"),
    ('min(up{job="otel-collector"})', "OTel Collector"),
    ('min(up{job="tempo"})', "Tempo (traces)"),
    ('min(up{job="prometheus"})', "Prometheus"),
], 0, 15, 9, desc="Red = down (or, for Ollama, which has no metrics endpoint: a model call failed in the last "
                  "5 min, seen in traces). 'Ingestion workers' turns red when ANY worker is down.")
table("Active alerts", 15, 9, 9, expr=f"{FIRING}}}", no_value="No alarms firing",
      desc="Alerts firing right now, from deploy/observability/alerts.yml. Descriptions: "
           "http://localhost:9090/alerts. Their start/end are drawn on every graph as red marks.",
      transformations=[{"id": "organize", "options": {
          "excludeByName": {"Time": True, "Value": True, "__name__": True, "alertstate": True},
          "indexByName": {"alertname": 0, "severity": 1}, "renameByName": {"alertname": "Alert"}}}],
      overrides=[by_name("severity", ("custom.cellOptions", {"type": "color-background"}),
                         ("mappings", [{"type": "value", "options": {"critical": {"color": "red", "index": 0},
                                                                     "warning": {"color": "orange", "index": 1}}}]))])
y += 9

# ======================================================================== 2. golden signals
row("Golden signals  ·  selected time range")
stat("Requests / min", f"sum(rate({ALL}[$__rate_interval])) * 60", "short", 0,
     spark=f"sum(rate({ALL}[$__rate_interval])) * 60", calc="mean", color_mode="none",
     decimals=1, desc="Average requests per minute over the selected range (health probes excluded).")
stat("Server errors", f"sum(increase({ERR}[$__range])) or vector(0)", "short", 4,
     thresholds=th("green", (1, "red")), decimals=0,
     desc="Number of 5xx responses. Anything above 0 is worth a look: open the Traces section below.")
stat("API latency p95", q(0.95, "http_request_duration_seconds", rng="$__range",
                          sel=f"route!~{STREAMING}, {APP}"), "s", 8,
     thresholds=th("green", (0.5, "orange"), (2, "red")), decimals=2,
     desc="95% of non-chat requests (login, history, documents...) were faster than this. "
          "Chat is left out: its duration is the whole generated answer.")
stat("Full answer p95", q(0.95, "llm_stream_duration_seconds", rng="$__range"), "s", 12,
     thresholds=th("green", (15, "orange"), (45, "red")), decimals=1,
     desc="Time from question to the last word of the answer (95th percentile).")
stat("Answers given", "sum(increase(llm_stream_duration_seconds_count[$__range])) or vector(0)", "short", 16,
     color_mode="none", decimals=0, desc="Chat + agent answers completed in the selected range.")
stat("Active users", "", "short", 20, color_mode="none", decimals=0,
     sql="SELECT count(DISTINCT user_id) FROM llm_usage WHERE $__timeFilter(created_at)",
     desc="Users who got at least one model answer in the selected range (llm_usage table in PostgreSQL).")
y += 4
stat("No matching document", "sum(increase(retrieval_no_context_total[$__range])) "
     "/ clamp_min(sum(increase(retrieval_duration_seconds_count[$__range])), 1e-9)", "percentunit", 0,
     thresholds=th("green", (0.3, "orange"), (0.6, "red")), decimals=0,
     desc="Questions where no document chunk passed MIN_SCORE, so the model answered without your documents. "
          "High = users ask about things not in the knowledge base, or retrieval is broken.")
stat("Agent tool failures", '(sum(increase(agent_tool_calls_total{ok="false"}[$__range])) or vector(0)) '
     "/ clamp_min(sum(increase(agent_tool_calls_total[$__range])), 1e-9)", "percentunit", 4,
     thresholds=th("green", (0.05, "orange"), (0.2, "red")), decimals=0,
     desc="Share of agent tool calls that failed (unknown tool, tool error).")
stat("Estimated cost", "sum(increase(llm_cost_usd_total[$__range])) or vector(0)", "currencyUSD", 8,
     color_mode="none", decimals=3, desc="Model cost at the configured per-token prices. Details in Tokens & cost.")
stat("Documents indexed", 'sum(increase(ingest_documents_total{outcome="indexed"}[$__range])) or vector(0)', "short",
     12, color_mode="none", decimals=0, desc="Documents the ingestion workers finished in the selected range.")
stat("Documents failed", 'sum(increase(ingest_documents_total{outcome="failed"}[$__range])) or vector(0)', "short",
     16, thresholds=th("green", (1, "red")), decimals=0,
     desc="Documents given up on (unreadable file, or every retry failed). See the Ingestion section.")
stat("Ingest backlog", "", "short", 20, decimals=0, thresholds=th("green", (50, "orange"), (500, "red")),
     sql="SELECT count(*) FROM documents WHERE status IN ('queued', 'processing')",
     desc="Documents waiting for, or being processed by, a worker right now (PostgreSQL).")
y += 4
donut("Responses by status", f'sum by (class) (label_replace(increase({ALL}[$__range]), "class", "${{1}}xx", '
      '"status", "(.)..")) > 0', "{{class}}", "short", 0,
      overrides=[color("2xx", "green"), color("3xx", "blue"), color("4xx", "orange"), color("5xx", "red")],
      desc="2xx = fine, 4xx = the client's mistake (not logged in, not found), 5xx = ours.")
donut("Where the traffic goes", f"sum by (route) (increase({ALL}[$__range])) > 0", "{{route}}", "short", 6)
donut("Token mix", "sum by (kind) (increase(llm_tokens_total[$__range])) > 0", "{{kind}}", "short", 12,
      overrides=[color("input", "blue"), color("output", "purple")],
      desc="Input = prompt, documents and history; output = generated. Input dominating = big prompts.")
donut("Answer cache", f"sum by (result) (increase({SC}[$__range])) > 0", "{{result}}", "short", 18,
      overrides=[color("hit", "green"), color("miss", "orange")],
      desc="First questions answered from the semantic cache (hit) vs by the model (miss).")
y += 8

# ======================================================================== 3. SLOs
row("SLOs & error budget  ·  targets from alerts.yml")
gauge("Availability SLI", "1 - " + ratio(f"sum(rate({ERR}[$__range])) or vector(0)", f"sum(rate({ALL}[$__range]))"),
     "percentunit", 0, decimals=3, lo=0.99, thresholds=th("red", (0.995, "orange"), (SLO_AVAILABILITY, "green")),
     desc=f"Requests that were not a 5xx. Target {SLO_AVAILABILITY:.1%}: green = met for the selected range.")
gauge("API latency SLI", ratio(
    f'sum(rate(http_request_duration_seconds_bucket{{le="1.0", route!~{STREAMING}, {APP}}}[$__range]))',
    f"sum(rate(http_request_duration_seconds_count{{route!~{STREAMING}, {APP}}}[$__range]))"),
     "percentunit", 6, decimals=2, lo=0.9, thresholds=th("red", (0.97, "orange"), (SLO_API_LATENCY, "green")),
     desc=f"Non-chat requests that took under 1 s. Target {SLO_API_LATENCY:.0%}.")
gauge("First word SLI", ratio('sum(rate(llm_time_to_first_token_seconds_bucket{le="10.0"}[$__range]))',
                            "sum(rate(llm_time_to_first_token_seconds_count[$__range]))"),
     "percentunit", 12, decimals=1, lo=0.5, thresholds=th("red", (0.9, "orange"), (SLO_FIRST_WORD, "green")),
     desc=f"Answers whose first word arrived within 10 s. Target {SLO_FIRST_WORD:.0%}.")
gauge("Burn rate (1 h)", f"slo:http_error_ratio:rate1h / {BUDGET:g}", "x", 18, decimals=1, hi=20,
     thresholds=th("green", (6, "orange"), (14.4, "red")),
     desc="How fast the error budget is being spent over the last hour. 1x = exactly the budget over 30 days. "
          "6x pages a warning, 14.4x is critical (2% of the monthly budget in one hour).")
panels[-1]["fieldConfig"]["defaults"]["unit"] = "suffix:×"
y += 6
ts("Error budget burn rate", [(f"slo:http_error_ratio:rate5m / {BUDGET:g}", "5 min"),
                              (f"slo:http_error_ratio:rate1h / {BUDGET:g}", "1 hour"),
                              (f"slo:http_error_ratio:rate6h / {BUDGET:g}", "6 hours")], "suffix:×", 0,
   lines=th("transparent", (6, "orange"), (14.4, "red")), legend_calcs=("lastNotNull", "max"),
   overrides=[color("5 min", "blue"), color("1 hour", "purple"), color("6 hours", "green")],
   desc="Dashed lines: 6x (warning) and 14.4x (critical). An alert needs BOTH a long and a short window above "
        "the line, so a single bad minute does not page anyone.")
ts("SLIs over time", [
    ("1 - " + ratio(f"sum(rate({ERR}[$__rate_interval])) or vector(0)", f"sum(rate({ALL}[$__rate_interval]))"),
     "availability"),
    (ratio(f'sum(rate(http_request_duration_seconds_bucket{{le="1.0", route!~{STREAMING}, {APP}}}[$__rate_interval]))',
           f"sum(rate(http_request_duration_seconds_count{{route!~{STREAMING}, {APP}}}[$__rate_interval]))"),
     "API under 1 s"),
    (ratio('sum(rate(llm_time_to_first_token_seconds_bucket{le="10.0"}[$__rate_interval]))',
           "sum(rate(llm_time_to_first_token_seconds_count[$__rate_interval]))"), "first word under 10 s"),
], "percentunit", 12, min0=False, legend_calcs=("mean", "min"),
   overrides=[color("availability", "blue"), color("API.*", "purple"), color("first.*", "green")],
   desc=f"Each line should stay at or above its target: availability {SLO_AVAILABILITY:.1%}, "
        f"API latency {SLO_API_LATENCY:.0%}, first word {SLO_FIRST_WORD:.0%}.")
y += 8

# ======================================================================== 4. traffic & errors
row("Traffic & errors  ·  is the API healthy?")
ts("Requests / min by route", [(f"sum by (route) (rate({ALL}[$__rate_interval])) * 60", "{{route}}")], "short", 0,
   stack=True, desc="Where the traffic goes. Use the Route filter at the top to focus on one endpoint.")
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
], "s", 0, overrides=PCT, legend_calcs=PCT_LEGEND, log=True, links=TRACES_LINK,
   desc="p50: half of requests are faster. p99: only 1 in 100 is slower. "
        "A gap between p50 and p99 means a few users have a much worse experience.")
bargauge("Slowest routes (p95, selected range)",
         f"sort_desc({q(0.95, 'http_request_duration_seconds', 'route', rng='$__range', sel=APP)} > 0)", "route", "s",
         12,
         thresholds=LAT, desc="Chat routes are naturally slow: they include the whole generated answer.")
y += 8
heatmap("Request latency heatmap (non-chat)", "http_request_duration_seconds", "s", 0, w=24,
        sel=f"route!~{STREAMING}, {APP}",
        desc="Each column is one minute; each cell counts requests in that latency bucket (brighter = more). "
             "A second bright band higher up = a group of slow requests the p95 line would smooth away.")
y += 8

# ======================================================================== 5. AI pipeline
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
   overrides=[color("first word.*", "blue"), color("full answer.*", "purple")], links=TRACES_LINK)
y += 9
heatmap("Time to first word heatmap", "llm_time_to_first_token_seconds", "s", 0, scheme="Purples",
        desc="How long each answer took to start, minute by minute. Bright cells at the top = users waiting.")
heatmap("Full answer heatmap", "llm_stream_duration_seconds", "s", 12, scheme="Purples",
        desc="Time from question to the last word, for every answer.")
y += 8
ts("Answers / min", [("sum by (endpoint) (rate(llm_stream_duration_seconds_count[$__rate_interval])) * 60",
                      "{{endpoint}}")], "short", 0, w=8, bars=True, stack=True,
   overrides=[color("chat", "blue"), color("agent", "purple")],
   desc="Answers completed per minute: chat = normal answers, agent = agent mode.")
ts("Document search latency", [(q(0.50, "retrieval_duration_seconds"), "p50"),
                               (q(0.95, "retrieval_duration_seconds"), "p95")], "s", 8, w=8,
   overrides=PCT, legend_calcs=PCT_LEGEND, log=True, lines=th("transparent", (1, "orange")),
   desc="Embedding the question (Ollama) + vector search (Qdrant). Log scale: normal searches take ~100 ms, a "
        "cold embedding model several seconds. Dashed line: 1 s, above which a search is slow.")
ts("Answered without documents", [
    (("sum(rate(retrieval_no_context_total[$__rate_interval])) "
      "/ clamp_min(sum(rate(retrieval_duration_seconds_count[$__rate_interval])), 1e-9)"), "no matching document")],
   "percentunit", 16, w=8, legend_calcs=("mean",),
   desc="A sudden jump usually means the index is empty or the embedding model changed.")
panels[-1]["fieldConfig"]["defaults"]["max"] = 1  # a share: never let the axis run past 100%
y += 8
ts("Agent tool calls / min", [('sum by (tool, ok) (rate(agent_tool_calls_total[$__rate_interval])) * 60',
                               "{{tool}} · ok={{ok}}")], "short", 0, w=24, h=7, bars=True, stack=True,
   overrides=[color(".*ok=false", "red")])
y += 7

# ======================================================================== 6a. caching
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

# ======================================================================== 6b. tokens & cost
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
table("Usage by user (selected range)", 0, 24, 9,
      desc="From the llm_usage table in PostgreSQL: one row per model call, with the user who made it. "
           "Cost at the prices configured when each call was made.",
      sql=("SELECT coalesce(u.username, '(deleted user)') AS \"User\",\n"
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
           "LIMIT 50"),
      overrides=[by_name("Est. cost", ("unit", "currencyUSD"), ("decimals", 4)),
                 by_name("Total tokens", ("custom.cellOptions", {"type": "gauge", "mode": "lcd"}),
                         ("color", {"mode": "continuous-BlPu"})),
                 by_name("Last used", ("unit", "dateTimeFromNow"))])
y += 9

# ======================================================================== 6c. guardrails
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

# ======================================================================== 7. ingestion
row("Document ingestion  ·  background workers and their queue")
stat("Queued", "", "short", 0, color_mode="none", decimals=0,
     sql="SELECT count(*) FROM documents WHERE status = 'queued'",
     desc="Documents waiting for a worker (PostgreSQL, right now).")
stat("Processing", "", "short", 4, color_mode="none", decimals=0,
     sql="SELECT count(*) FROM documents WHERE status = 'processing'",
     desc="Documents a worker has claimed and is chunking / embedding right now.")
stat("Stuck > 15 min", "", "short", 8, decimals=0, thresholds=th("green", (1, "orange")),
     sql="SELECT count(*) FROM documents WHERE status = 'processing' AND claimed_at < now() - interval '15 minutes'",
     desc="Claimed by a worker more than 15 minutes ago and still not done: the worker probably died with it. "
          "It is handed to another worker once its claim expires.")
stat("Oldest wait", "", "s", 12, decimals=0, thresholds=th("green", (600, "orange"), (3600, "red")),
     sql="SELECT coalesce(extract(epoch FROM now() - min(b.created_at)), 0)\n"
         "FROM documents d JOIN ingest_batches b ON b.id = d.batch_id WHERE d.status = 'queued'",
     desc="How long the oldest queued document has been waiting. Growing steadily = not enough workers.")
stat("Failed (all time)", "", "short", 16, decimals=0, thresholds=th("green", (1, "orange")),
     sql="SELECT count(*) FROM documents WHERE status = 'failed'",
     desc="Documents left in 'failed'. The table below shows why; re-queue them once fixed.")
stat("Workers up", 'sum(up{job="worker"})', "short", 20, decimals=0, thresholds=th("red", (1, "orange"), (3, "green")),
     desc="Worker pods answering Prometheus. docker-compose and Kubernetes both run 3.", trend=True)
y += 4
ts("Documents / min by outcome", [("sum by (outcome) (rate(ingest_documents_total[$__rate_interval])) * 60",
                                   "{{outcome}}")], "short", 0, w=8, bars=True, stack=True,
   overrides=[color("indexed", "green"), color("retry", "orange"), color("failed", "red")],
   desc="indexed = done. retry = a dependency hiccup, it will be tried again. failed = given up.")
ts("Time per document", [(q(0.50, "ingest_duration_seconds"), "p50"), (q(0.95, "ingest_duration_seconds"), "p95")],
   "s", 8, w=8, overrides=PCT, legend_calcs=PCT_LEGEND,
   desc="Download from MinIO + chunk + embed + store, for one document.")
ts("Chunks indexed / min", [("sum(rate(ingest_chunks_total[$__rate_interval])) * 60", "chunks")], "short", 16, w=8,
   legend_calcs=("mean", "max"))
y += 8
table("Batches (unfinished, or created in the selected range)", 0, 14, 8,
      sql=("SELECT b.id AS \"Batch\", b.source AS \"Source\", b.status AS \"Status\",\n"
           "       count(d.doc_id) AS \"Documents\",\n"
           "       count(*) FILTER (WHERE d.status = 'ready') AS \"Ready\",\n"
           "       count(*) FILTER (WHERE d.status IN ('queued', 'processing')) AS \"Pending\",\n"
           "       count(*) FILTER (WHERE d.status = 'failed') AS \"Failed\",\n"
           "       b.created_at AS \"Created\"\n"
           "FROM ingest_batches b LEFT JOIN documents d ON d.batch_id = b.id\n"
           "WHERE b.status <> 'done' OR $__timeFilter(b.created_at)\n"
           "GROUP BY b.id ORDER BY b.id DESC LIMIT 20"),
      no_value="No batches", desc="filling = the producer is still adding documents, open = sealed and being "
                                  "worked on, done = every document ready or failed.",
      overrides=[by_name("Created", ("unit", "dateTimeFromNow")),
                 by_name("Failed", ("custom.cellOptions", {"type": "color-text"}),
                         ("thresholds", th("text", (1, "red"))), ("color", {"mode": "thresholds"}))])
table("Recent failures", 14, 10, 8,
      sql=("SELECT filename AS \"File\", attempts AS \"Attempts\", error AS \"Last error\"\n"
           "FROM documents WHERE status = 'failed'\n"
           "ORDER BY claimed_at DESC NULLS LAST LIMIT 20"),
      no_value="No failed documents", desc="The last error a worker recorded for each failed document.")
y += 8

# ======================================================================== 8. data stores & model server
row("Data stores & model server  ·  PostgreSQL, Qdrant, MinIO, Ollama")
DB = 'datname="mychatapp"'
stat("PostgreSQL", "min(pg_up)", "short", 0, w=4, mappings=UPDOWN, thresholds=th("red", (1, "green")),
     desc="postgres-exporter can connect and query. DOWN = login, chat history and usage all fail.")
stat("Connections used", "sum(pg_stat_activity_count) / max(pg_settings_max_connections)", "percentunit", 4,
     thresholds=th("green", (0.7, "orange"), (0.9, "red")), decimals=0,
     desc="Open connections vs max_connections. Near 100% new requests wait for or fail to get a connection.",
     trend=True)
stat("Cache hit ratio", ratio(f"sum(rate(pg_stat_database_blks_hit{{{DB}}}[$__range]))",
                              f"sum(rate(pg_stat_database_blks_hit{{{DB}}}[$__range])) "
                              f"+ sum(rate(pg_stat_database_blks_read{{{DB}}}[$__range]))"),
     "percentunit", 8, decimals=1, thresholds=th("red", (0.9, "orange"), (0.99, "green")),
     desc="Reads served from PostgreSQL's memory instead of disk. Below ~99% the database needs more RAM.")
stat("Database size", f"pg_database_size_bytes{{{DB}}}", "bytes", 12, color_mode="none", decimals=1, trend=True)
stat("Longest transaction", f"max(pg_stat_activity_max_tx_duration{{{DB}}})", "s", 16, decimals=0,
     thresholds=th("green", (60, "orange"), (300, "red")),
     desc="Age of the oldest open transaction. A long one holds locks and blocks vacuum.")
stat("Deadlocks", f"sum(increase(pg_stat_database_deadlocks{{{DB}}}[$__range])) or vector(0)", "short", 20,
     thresholds=th("green", (1, "red")), decimals=0)
y += 4
ts("PostgreSQL transactions / s", [(f"sum(rate(pg_stat_database_xact_commit{{{DB}}}[$__rate_interval]))", "commit"),
                                   (f"sum(rate(pg_stat_database_xact_rollback{{{DB}}}[$__rate_interval]))",
                                    "rollback")], "short", 0, w=8,
   overrides=[color("commit", "green"), color("rollback", "red")],
   desc="Rollbacks are normal for a failed request; a rising share means errors inside transactions.")
ts("PostgreSQL rows / s", [(f"sum(rate(pg_stat_database_tup_{op}{{{DB}}}[$__rate_interval]))", op)
                           for op in ("fetched", "inserted", "updated", "deleted")], "short", 8, w=8,
   overrides=[color("fetched", "blue"), color("inserted", "green"), color("updated", "purple"),
              color("deleted", "orange")])
ts("PostgreSQL connections by state", [(f'sum by (state) (pg_stat_activity_count{{{DB}, state!="disabled"}})',
                                        "{{state}}")], "short", 16, w=8, stack=True,
   overrides=[color("active", "green"), color("idle", "blue"), color("idle in transaction.*", "orange")],
   desc="'idle in transaction' = a connection holding a transaction open without doing anything: a leak.")
y += 8
stat("Qdrant", 'min(up{job="qdrant"})', "short", 0, w=4, mappings=UPDOWN, thresholds=th("red", (1, "green")),
     desc="Vector database. DOWN = no document search: answers come without your documents.")
stat("Vectors stored", "sum(collection_points)", "short", 4, color_mode="none", decimals=0,
     desc="Points (document chunks) across all collections.", trend=True)
stat("Search p95", q(0.95, "rest_responses_duration_seconds", rng="$__range", sel='endpoint=~".*/points/query"'),
     "s", 8, decimals=3, thresholds=th("green", (0.1, "orange"), (0.5, "red")),
     desc="Qdrant's own time for a vector search (95th percentile), without network or embedding time.")
stat("Qdrant errors", 'sum(increase(rest_responses_total{status=~"5.."}[$__range])) or vector(0)', "short", 12,
     thresholds=th("green", (1, "red")), decimals=0, desc="5xx responses from Qdrant's REST API.")
stat("Qdrant memory", 'memory_resident_bytes{job="qdrant"}', "bytes", 16, color_mode="none", decimals=0, trend=True)
stat("Optimizations running", "sum(collection_running_optimizations)", "short", 20, color_mode="none", decimals=0,
     desc="Background index rebuilds. Searches can be slower while they run (e.g. right after a large ingest).")
y += 4
ts("Qdrant requests / min by endpoint", [('sum by (endpoint) (rate(rest_responses_total[$__rate_interval])) * 60',
                                          "{{endpoint}}")], "short", 0, stack=True,
   desc="points/query = searches from chat, points (PUT) = chunks written by ingestion.")
ts("Qdrant latency p95 by endpoint", [(q(0.95, "rest_responses_duration_seconds", "endpoint"), "{{endpoint}}")],
   "s", 12)
y += 8
stat("MinIO", 'min(up{job="minio"})', "short", 0, w=4, mappings=UPDOWN, thresholds=th("red", (1, "green")),
     desc="Object storage for the original files. DOWN = uploads and ingestion fail; chat still works.")
stat("Files stored", "sum(minio_bucket_usage_object_total)", "short", 4, color_mode="none", decimals=0, trend=True)
stat("Storage used", "sum(minio_bucket_usage_total_bytes)", "bytes", 8, color_mode="none", decimals=1, trend=True)
stat("Disk free", ratio("sum(minio_cluster_capacity_usable_free_bytes)",
                        "sum(minio_cluster_capacity_usable_total_bytes)"), "percentunit", 12, decimals=0,
     thresholds=th("red", (0.1, "orange"), (0.25, "green")), desc="Free space on MinIO's volume.")
stat("Drives offline", "sum(minio_cluster_drive_offline_total)", "short", 16, decimals=0,
     thresholds=th("green", (1, "red")))
stat("Rejected requests", " + ".join(f"(sum(increase(minio_s3_requests_rejected_{why}_total[$__range])) or vector(0))"
                                    for why in ("auth", "header", "invalid", "timestamp")), "short", 20,
     decimals=0, thresholds=th("green", (1, "orange")),
     desc="S3 calls MinIO refused (bad credentials, clock skew, invalid request). Usually a wrong MINIO_* setting.")
y += 4
ts("MinIO traffic", [("sum(rate(minio_s3_traffic_received_bytes[$__rate_interval]))", "uploaded to MinIO"),
                     ("sum(rate(minio_s3_traffic_sent_bytes[$__rate_interval]))", "downloaded from MinIO")],
   "Bps", 0, w=8, overrides=[color("uploaded.*", "blue"), color("downloaded.*", "purple")])
ts("Ollama calls / min", [('sum by (span_name) (rate(traces_spanmetrics_calls_total{span_name=~"ollama.*"}'
                           "[$__rate_interval])) * 60", "{{span_name}}")], "short", 8, w=8, stack=True,
   desc="Ollama has no metrics endpoint: these come from the traces of our calls to it (Tempo span metrics). "
        "/api/chat = answers, /api/embed = embeddings.")
ts("Ollama call latency p95", [(q(0.95, "traces_spanmetrics_latency", "span_name", sel='span_name=~"ollama.*"'),
                                "{{span_name}}")], "s", 16, w=8,
   desc="Whole call, including streaming the answer. Compare with 'Generation speed' in Tokens & cost.")
y += 8

# ======================================================================== 9. traces
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

# ======================================================================== 10. telemetry pipeline (collapsed)
tel = row("Telemetry pipeline  ·  is the monitoring itself healthy?", collapsed=True)
ts("Spans / s through the collector", [
    ("sum(rate(otelcol_receiver_accepted_spans_total[$__rate_interval]))", "received from the apps"),
    ("sum(rate(otelcol_exporter_sent_spans_total[$__rate_interval]))", "sent to Tempo"),
    ("sum(rate(otelcol_receiver_refused_spans_total[$__rate_interval]))", "refused (collector overloaded)"),
    ("sum(rate(otelcol_exporter_send_failed_spans_total[$__rate_interval]))", "failed to send"),
    ("sum(rate(tempo_discarded_spans_total[$__rate_interval]))", "discarded by Tempo"),
], "short", 0, w=12, into=tel["panels"], overrides=[color("received.*", "blue"), color("sent.*", "green"),
                                                     color("refused.*|failed.*|discarded.*", "red")],
   desc="Received and sent should match. Anything red means traces are missing from the Traces section.")
ts("Prometheus active series", [("prometheus_tsdb_head_series", "series")], "short", 12, w=6, into=tel["panels"],
   legend_calcs=("lastNotNull", "max"),
   desc="Every label combination is one series held in memory. A sudden jump = a label with unbounded values "
        "(a user id, a raw path) was added to a metric.")
ts("Scrape duration by target", [("max by (job) (scrape_duration_seconds)", "{{job}}")], "s", 18, w=6,
   into=tel["panels"], legend_calcs=("max",))
y += 8
table("Scrape targets", 0, 24, 8, expr="up", into=tel["panels"],
      transformations=[{"id": "organize", "options": {
          "excludeByName": {"Time": True, "__name__": True},
          "renameByName": {"job": "Job", "instance": "Target", "Value": "State"}}}],
      overrides=[by_name("State", ("mappings", UPDOWN), ("custom.cellOptions", {"type": "color-background"}),
                         ("thresholds", th("red", (1, "green"))), ("color", {"mode": "thresholds"}))])
y += 8

# ======================================================================== 11. resources (collapsed)
res = row("Pods & resources", collapsed=True)
ts("Memory by component", [
    ('process_resident_memory_bytes{job=~"backend|worker"}', "{{job}} {{instance}}"),
    ('memory_resident_bytes{job="qdrant"}', "qdrant"),
    ("minio_node_process_resident_memory_bytes", "minio"),
    ("otelcol_process_memory_rss", "otel-collector"),
    ('process_resident_memory_bytes{job=~"tempo|prometheus|grafana"}', "{{job}}"),
], "bytes", 0, w=8, into=res["panels"], legend_calcs=("lastNotNull", "max"),
   desc="Resident memory. The Kubernetes limit for backend and worker pods is 1 GiB: near it, a pod is OOM-killed.")
ts("CPU per pod (cores)", [('rate(process_cpu_seconds_total{job=~"backend|worker"}[$__rate_interval])',
                            "{{job}} {{instance}}")], "short", 8, w=8, into=res["panels"],
   legend_calcs=("mean", "max"))
updown("Pod up / down", [('up{job=~"backend|worker"}', "{{job}} {{instance}}")], 16, 8, 8, into=res["panels"])
y += 1

dashboard = {
    "uid": "mychatapp", "title": "MyChatApp · Production overview", "tags": ["mychatapp", "slo", "llm", "production"],
    "description": "End-to-end operations view of MyChatApp: alerts and SLOs, API, AI pipeline, cost, guardrails, "
                   "ingestion, data stores, traces and the telemetry stack. Hover the (i) on any panel for what it "
                   "means.",
    "timezone": "browser", "schemaVersion": 39, "version": 3, "editable": False, "graphTooltip": 1,
    "refresh": "10s", "time": {"from": "now-1h", "to": "now"}, "liveNow": True,
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "templating": {"list": [{
        "name": "route", "label": "Route", "type": "query", "datasource": PROM,
        "query": {"query": 'label_values(http_requests_total{route!~"/health.*"}, route)', "refId": "route"},
        "definition": 'label_values(http_requests_total{route!~"/health.*"}, route)',
        "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*",
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "sort": 1,
    }]},
    "annotations": {"list": [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        # red marks on every graph while an alert fires: "did the alert line up with this spike?"
        {"datasource": PROM, "enable": True, "name": "Alerts firing", "iconColor": "red",
         "expr": f"{FIRING}}}", "step": "60s", "titleFormat": "{{alertname}}", "tagKeys": "severity,job",
         "useValueForTime": False},
        # blue marks where a pod (re)started: a deploy, a crash or an OOM kill
        {"datasource": PROM, "enable": True, "name": "Restarts / deploys", "iconColor": "blue",
         "expr": 'sum by (job) (changes(process_start_time_seconds{job=~"backend|worker"}[5m])) > 0', "step": "5m",
         "titleFormat": "{{job}} restarted", "tagKeys": "job", "useValueForTime": False},
    ]},
    "links": [
        {"title": "Explore traces", "type": "link", "icon": "external link", "url": TRACES_LINK[0]["url"]
         .replace(" && kind=server && duration > 2s", "")},
        {"title": "Prometheus alerts", "type": "link", "icon": "bolt", "url": "http://localhost:9090/alerts",
         "targetBlank": True},
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
