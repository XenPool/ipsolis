# Grafana dashboards & Prometheus alerts for ipSolis

Drop-in observability assets that pair with the metrics ipSolis exposes
at `GET /metrics` (Prometheus text format) and the OpenTelemetry traces
the API + worker emit when tracing is enabled.

## Files

| File | What it gives you |
|---|---|
| [`ipsolis-overview.json`](ipsolis-overview.json) | Grafana dashboard with 9 panels: HTTP rate / errors / p95 latency / pending approvals (top row); request rate by route + latency percentiles; orders by status + asset pool composition; Celery queue depth |
| [`prometheus-alerts.yaml`](prometheus-alerts.yaml) | Sample alert rules: high error rate, slow p95 latency, approval backlog, Celery queue backlog (warning + critical), per-pool capacity pressure |

## Prometheus scrape config

Add ipSolis to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: ipsolis
    metrics_path: /metrics
    scrape_interval: 30s
    static_configs:
      - targets: ['ipsolis.your-host:8000']
```

If you've enabled the per-feature `metrics.enabled` toggle (see
*Settings → Compliance → /metrics endpoint* in the admin UI), nothing
else is required server-side.

## Importing the dashboard

In Grafana:

1. Add your Prometheus instance as a datasource (one-time).
2. **Dashboards → New → Import**.
3. Upload [`ipsolis-overview.json`](ipsolis-overview.json) (or paste its content).
4. When prompted, pick the Prometheus datasource — the dashboard uses
   the `${DS_PROMETHEUS}` template variable so it works with any
   datasource UID.
5. Save under whatever folder you keep operational dashboards in.

The dashboard auto-refreshes every 30 seconds and defaults to a 6-hour
window. Both are easy to change at the top right.

## Importing alert rules

```bash
# Copy onto the Prometheus host or PVC
cp prometheus-alerts.yaml /etc/prometheus/rules/ipsolis-alerts.yaml

# Reference it in prometheus.yml
rule_files:
  - /etc/prometheus/rules/*.yaml

# Reload Prometheus
curl -X POST http://prometheus:9090/-/reload
```

Tune the thresholds and `for:` durations to your environment. The defaults
suit a small deployment (a few hundred orders per day):

| Alert | Default trigger |
|---|---|
| `IpsolisHighErrorRate` | 5xx ratio > 5% for 5 min |
| `IpsolisLatencyP95High` | p95 > 2s for 10 min |
| `IpsolisApprovalsBacklog` | > 25 pending for 30 min (info) |
| `IpsolisCeleryQueueBacklog` | > 50 in any queue for 5 min |
| `IpsolisCeleryQueueCritical` | > 200 in any queue for 2 min (page) |
| `IpsolisPoolNearCapacity` | per-pool fill > 95% for 10 min |

## OpenTelemetry traces

The dashboard intentionally doesn't include trace panels — Grafana's
trace UX comes from the Tempo / Jaeger / Zipkin **datasource** itself,
not from JSON dashboards. To wire up:

1. Run a collector (Tempo, Jaeger, or any OTLP-aware service).
2. In **ipSolis Admin UI → Settings → Compliance → OpenTelemetry Tracing**,
   set *Status* to Enabled, paste your collector's `/v1/traces`
   endpoint, restart the API + worker.
3. In Grafana, add a Tempo / Jaeger datasource pointing at the
   collector. The "Explore" view lets you query traces by
   `service.name=ipsolis-api` or `service.name=ipsolis-worker`.

A request that lands at the API and dispatches a Celery task produces
a single distributed trace spanning both processes via the
auto-instrumented Celery client/server spans.
