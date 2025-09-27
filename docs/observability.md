# Observability: Prometheus metrics, readiness probes, and alerting

This project exposes Prometheus-compatible metrics and a readiness probe by default:
- Metrics endpoint: GET /metrics (Prometheus exposition format)
- Readiness probe: GET /readyz (200 OK when ready, 503 with a reason when not)

Important
- Metrics do not require authentication and should only be exposed inside trusted networks or behind an ingress with access control.
- Example alert rules for this app are provided at docs/observability/alerts.prometheus.yml. Load them with Prometheus rule_files (see configs below).

What you get out of the box
- Native Prometheus metrics for auth/session activity, quotas, crawl jobs, homepage tracking, and API operations.
- Default Python collectors (process and platform) from prometheus_client.
- A readiness probe that verifies DB connectivity and, optionally, OAuth configuration.
- Docker Compose healthcheck which hits /readyz.

Endpoints Summary
- /metrics: Prometheus exposition (Counter, Gauge, Histogram, etc.)
- /readyz: Readiness probe (DB connectivity; optionally requires OAuth config when WEBAPP_READYZ_REQUIRE_AUTH=true)

Quick reference: scrape targets and names
- Target: http://markdownify:8080/metrics (when running via docker-compose on the same network)
- Target: http://localhost:8080/metrics (when running locally without Compose networking)
- Example metric names you will see:
  - auth_attempts_total{provider, result}
  - active_sessions_count
  - rate_limit_hits_total{type}
  - crawl_job_duration_seconds_bucket|count|sum{scope, status}
  - homepage_analyze_submits_total{authed, public}
  - homepage_advanced_toggle_clicks_total{action}
  - homepage_signin_cta_clicks_total
  - result_share_clicks_total{type}
  - stale_finalize_attempts_total{scope}
  - stale_finalize_finished_total{scope, outcome}

Optional: Blackbox/HTTP probing for /readyz
- You can use Prometheus Blackbox Exporter to actively probe /readyz. This helps catch conditions where the process responds but is not fully healthy.

-------------------------------------------------------------------------------
Single-node Prometheus (local) minimal configuration
-------------------------------------------------------------------------------

prometheus.yml (place next to your Prometheus binary)
- Scrapes the app’s metrics on localhost:8080
- Loads the example alert rules from docs/observability/alerts.prometheus.yml

global:
  scrape_interval: 15s
  evaluation_interval: 30s

scrape_configs:
  - job_name: "markdownify"
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:8080"]

rule_files:
  - "docs/observability/alerts.prometheus.yml"

Start Prometheus:
./prometheus --config.file=prometheus.yml

-------------------------------------------------------------------------------
Docker Compose: Prometheus service example
-------------------------------------------------------------------------------

If you want to run Prometheus alongside the app locally, you can extend your docker-compose.yaml with a Prometheus service. Create a file prometheus/prometheus.yml with the following content:

global:
  scrape_interval: 15s
  evaluation_interval: 30s

scrape_configs:
  - job_name: "markdownify"
    metrics_path: /metrics
    static_configs:
      - targets: ["markdownify:8080"]

rule_files:
  - "/etc/prometheus/alerts.prometheus.yml"

Then add a Prometheus service to docker-compose.yaml:

prometheus:
  image: prom/prometheus:v2.54.1
  container_name: prometheus
  hostname: prometheus
  restart: always
  command:
    - --config.file=/etc/prometheus/prometheus.yml
  volumes:
    - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    - ./docs/observability/alerts.prometheus.yml:/etc/prometheus/alerts.prometheus.yml:ro
  ports:
    - "9090:9090"
  networks:
    - magics

Notes
- This assumes the existing markdownify service runs on the same docker network (magics) exposing :8080.
- Visiting http://localhost:9090 targets the Prometheus UI.

Optional: Add Blackbox Exporter to probe /readyz

Add a blackbox_config.yml:

modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_http_versions: ["HTTP/1.1", "HTTP/2"]
      method: GET
      fail_if_not_ssl: false
      preferred_ip_protocol: "ip4"

Extend docker-compose with blackbox-exporter:

blackbox:
  image: prom/blackbox-exporter:v0.25.0
  container_name: blackbox
  hostname: blackbox
  restart: always
  volumes:
    - ./prometheus/blackbox_config.yml:/etc/blackbox_exporter/config.yml:ro
  ports:
    - "9115:9115"
  networks:
    - magics

Update prometheus.yml to add a probe job:

scrape_configs:
  - job_name: "markdownify"
    metrics_path: /metrics
    static_configs:
      - targets: ["markdownify:8080"]

  - job_name: "blackbox-readyz"
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
        - http://markdownify:8080/readyz
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox:9115

-------------------------------------------------------------------------------
Kubernetes: scrape options
-------------------------------------------------------------------------------

Option A: Annotations-based scraping (vanilla Prometheus)

Annotate your Service which fronts the FastAPI app:

apiVersion: v1
kind: Service
metadata:
  name: markdownify
  labels:
    app: markdownify
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/path: "/metrics"
    prometheus.io/port: "8080"
spec:
  selector:
    app: markdownify
  ports:
    - port: 8080
      targetPort: 8080
      name: http

Configure your Prometheus scrape_config to honor annotations (typical with kube SD). Most kube-prometheus distributions already include this.

Option B: ServiceMonitor (Prometheus Operator / kube-prometheus-stack)

apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: markdownify
  labels:
    release: prometheus  # must match your Prometheus Operator/stack selector
spec:
  selector:
    matchLabels:
      app: markdownify
  endpoints:
    - port: http            # must match Service port name
      path: /metrics
      interval: 15s

If your Service’s port name is “http” (as in the Service above), this will scrape the metrics.

Readiness probe with Blackbox Exporter (Kubernetes)

Create a Blackbox Exporter Deployment/Service, then add a ServiceMonitor:

apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: blackbox
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app: blackbox-exporter
  endpoints:
    - port: http
      path: /probe
      interval: 30s
      params:
        module:
          - http_2xx
      relabelings:
        - sourceLabels: [__address__]
          targetLabel: __param_target
        - sourceLabels: [__param_target]
          targetLabel: instance
        - targetLabel: __address__
          replacement: blackbox-exporter.default.svc:9115
  namespaceSelector:
    matchNames: ["default"]

Then define a Probe CRD or add static targets to query http://markdownify.default.svc:8080/readyz.

-------------------------------------------------------------------------------
Alerting rules
-------------------------------------------------------------------------------

Load the provided alerts into Prometheus:

- Rule file: docs/observability/alerts.prometheus.yml
- prometheus.yml:
  rule_files:
    - "docs/observability/alerts.prometheus.yml"

The rules cover:
- OAuth failure spikes
- Login rate-limit spikes
- Active session anomalies
- Crawl duration P95 outliers
- Crawl failure ratio increases
- Readiness failures via blackbox probe

Tune thresholds, labels, and job names to match your environment.

-------------------------------------------------------------------------------
Grafana (optional)
-------------------------------------------------------------------------------

Suggested dashboards and panels:
- Prometheus “Python / Process” overview panels (process_cpu_seconds_total, process_resident_memory_bytes, etc.).
- App-specific:
  - Crawl duration: histogram_quantile(0.95, sum by (le) (rate(crawl_job_duration_seconds_bucket[5m])))
  - Crawl failures ratio: increase(crawl_job_duration_seconds_count{status="failed"}[10m]) / clamp_min(increase(crawl_job_duration_seconds_count[10m]), 1)
  - Auth attempts breakdown: sum by (provider, result) (increase(auth_attempts_total[5m]))
  - Active sessions: active_sessions_count
  - Quotas: sum by (type) (increase(rate_limit_hits_total[10m]))

-------------------------------------------------------------------------------
Security considerations
-------------------------------------------------------------------------------

- Do not expose /metrics publicly. Restrict access to Prometheus networks/namespaces only.
- Place the app behind a reverse proxy or service mesh and scope network policies appropriately.
- If you must expose metrics through an ingress, enforce mTLS, IP allowlists, or auth at the gateway.

-------------------------------------------------------------------------------
Operational notes
-------------------------------------------------------------------------------

- Readiness policy: If WEBAPP_AUTH_ENABLED=true and WEBAPP_READYZ_REQUIRE_AUTH=true, /readyz fails unless OAuth client credentials are set. In non-prod environments, leave WEBAPP_READYZ_REQUIRE_AUTH=false to avoid false negatives.
- The app’s Docker Compose healthcheck already probes /readyz every 30s.
- No Prometheus is bundled by default; this document provides examples to enable it as needed.

References
- Metrics endpoint: /metrics
- Readiness endpoint: /readyz
- Alerts: docs/observability/alerts.prometheus.yml