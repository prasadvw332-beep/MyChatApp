# Running MyChatApp with Docker and Kubernetes

## The big picture

```
                 browser
                    │  http://localhost  (on AWS: the load balancer's address)
                    ▼
        ┌────────────────────────┐
        │ Service "frontend"     │  type LoadBalancer  ← the only way in from outside
        └───────────┬────────────┘
                    ▼
        frontend pods (nginx) x2  ── serves the HTML/JS, and forwards /api/* ──┐
                                                                               ▼
                                                     ┌────────────────────────────┐
                                                     │ Service "backend"          │ ClusterIP (internal)
                                                     └─────────────┬──────────────┘
                                                                   ▼
                                                        backend pods (FastAPI) x2
                                                                   │
            ┌──────────────────────┬───────────────────────┬───────┴──────────────┐
            ▼                      ▼                       ▼                      ▼
     Service "postgres"     Service "qdrant"        Service "minio"        Service "ollama"
     StatefulSet + disk     Deployment + PVC        Deployment + PVC       Deployment + PVC
```

| File | Kind | What it's for |
|---|---|---|
| `backend/Dockerfile` | — | Recipe for the backend **image** (Python + your code) |
| `frontend/Dockerfile` | — | Multi-stage: Node builds the site, nginx serves it |
| `frontend/nginx.conf` | — | nginx: static files + proxy `/api` → backend |
| `deploy/docker-compose.yml` | — | Run everything on one machine with Docker only |
| `k8s/namespace.yaml` | Namespace | A "folder" for all our objects |
| `k8s/configmap.yaml` | ConfigMap | Non-secret settings → env variables |
| `k8s/secret.yaml` | Secret | Passwords and keys → env variables |
| `k8s/*-deployment.yaml` | Deployment | "Keep N copies of this container running" |
| `k8s/*-service.yaml` | Service | Stable name + load balancing for those pods |
| `k8s/worker-deployment.yaml` | Deployment | 3 ingestion workers (no Service: they pull work from the queue) |
| `k8s/postgres.yaml` | StatefulSet + Service | Database with its own persistent disk |
| `k8s/qdrant, minio, ollama.yaml` | PVC + Deployment + Service | Stateful helpers |
| `k8s/ollama.yaml` (bottom) | Job | Downloads the models once, then exits |
| `k8s/kustomization.yaml` | — | Lists all files so one command applies them |
| `observability/` | ConfigMaps + Deployments | Prometheus, Grafana, Tempo, OTel Collector (see [Observability](#observability-prometheus-opentelemetry-grafana)) |

**Image vs container vs pod:** an *image* is the packaged app (a template). A *container* is a running image.
A *pod* is Kubernetes' smallest unit: one (or a few) containers that share an IP.

## Step 1: Docker only (docker compose)

Start Docker Desktop, then from the project root:

```bash
cd deploy
docker compose up --build        # build the images and start all containers
```

The first start downloads the models (watch with `docker compose logs -f ollama-pull`).
Open http://localhost:8080. Stop with `Ctrl+C`; `docker compose down -v` also deletes the data.

## Step 2: Kubernetes on your laptop

Docker Desktop → Settings → Kubernetes → **Enable Kubernetes**. Then:

```bash
# 1. Build the images. Docker Desktop's Kubernetes can use local images directly.
docker build -t mychatapp-backend:1.0 ./backend
docker build -t mychatapp-frontend:1.0 ./frontend

# 2. Create everything
kubectl apply -k deploy/k8s/

# 3. Watch the pods start (Ctrl+C to stop watching)
kubectl get pods -n mychatapp -w
```

Open http://localhost once the `frontend` and `backend` pods show `1/1 Running`.

### Commands worth trying

```bash
kubectl get all -n mychatapp                         # everything at a glance
kubectl describe pod <pod-name> -n mychatapp         # why is a pod not starting? (see Events)
kubectl logs deploy/backend -n mychatapp -f          # backend logs
kubectl logs job/ollama-pull-models -n mychatapp -f  # model download progress
kubectl exec -it deploy/backend -n mychatapp -- sh   # a shell inside a backend container

kubectl scale deploy/backend --replicas=3 -n mychatapp    # scale out
kubectl delete pod <a-backend-pod> -n mychatapp           # watch Kubernetes replace it

kubectl port-forward svc/minio 9001:9001 -n mychatapp     # MinIO console at http://localhost:9001

# Deploy a new version: rebuild with a new tag, then
docker build -t mychatapp-backend:1.1 ./backend
kubectl set image deploy/backend backend=mychatapp-backend:1.1 -n mychatapp
kubectl rollout status deploy/backend -n mychatapp
kubectl rollout undo deploy/backend -n mychatapp          # roll back

kubectl delete namespace mychatapp                        # remove everything
```

## Step 3 (imagined): the same app on AWS

| Local | AWS |
|---|---|
| Docker Desktop Kubernetes | **EKS** (managed Kubernetes): `eksctl create cluster` |
| `docker build -t mychatapp-backend:1.0` | push to **ECR**: `docker tag ... <account>.dkr.ecr.<region>.amazonaws.com/mychatapp-backend:1.0` + `docker push`, and use that name in `image:` |
| `k8s/postgres.yaml` | **RDS for PostgreSQL**: delete the file, put the RDS address in `DATABASE_URL` |
| `minio.yaml` | **S3**: same API, delete the file, give the backend an IAM role |
| PVC disks | **EBS** volumes, created automatically by the EBS CSI driver |
| Service `type: LoadBalancer` | an AWS **Load Balancer** with a public DNS name |
| `k8s/secret.yaml` | **Secrets Manager** + External Secrets Operator |
| Ollama on CPU | a **GPU node group** (e.g. g5.xlarge), or Amazon Bedrock instead |
| http | an **Ingress** (AWS Load Balancer Controller) + **ACM** certificate for https; then set `COOKIE_SECURE: "true"` |

The Deployments and Services barely change. That portability is the main point of Kubernetes.

## Step 4: CI/CD with GitHub Actions

[`.github/workflows/ci-cd.yml`](../.github/workflows/ci-cd.yml) automates steps 1–3:

```
git push ──► GitHub Actions ──► build both images ──► push to ghcr.io ──► kubectl apply (EKS)
              (on GitHub's servers)   (every PR: build only)   (main only)    (only if DEPLOY_ENABLED)
```

| By hand | In the workflow |
|---|---|
| `docker build -t mychatapp-backend:1.0` | build on every push; tag = the git commit SHA |
| image stays on your laptop | pushed to `ghcr.io/prasadvw332-beep/mychatapp-backend` (Packages on your GitHub profile) |
| `deploy/k8s/secret.yaml` with dev passwords | GitHub **Secrets** → `kubectl create secret` (secret.yaml is skipped) |
| `image: mychatapp-backend:1.0` | `kustomize edit set image` swaps in the ghcr.io image + SHA tag |
| `kubectl rollout undo` | still works; or re-run the workflow of an older commit |

The deployment YAML files stay the same. Only the image name and tag change, and CI does that at deploy time, so local `kubectl apply -k deploy/k8s/` keeps working.

To use a pushed image from your local cluster, make the package public (GitHub → your profile → Packages → package settings → Change visibility).
Otherwise give Kubernetes a pull secret:

```bash
kubectl create secret docker-registry ghcr -n mychatapp \
  --docker-server=ghcr.io --docker-username=prasadvw332-beep --docker-password=<a PAT with read:packages>
# and add to the pod spec:   imagePullSecrets: [{ name: ghcr }]
```

To turn on the deploy job (once you have a cluster), add these in GitHub → Settings → Secrets and variables → Actions:
- Variable `DEPLOY_ENABLED` = `true`
- Secrets `AWS_DEPLOY_ROLE_ARN`, `DATABASE_URL`, `JWT_SECRET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`

## Observability: Prometheus, OpenTelemetry, Grafana

```
 backend pods ─┬─ :9100/metrics ◄──── scraped every 15s ──── Prometheus ◄─┐
 worker pods  ─┤                                                          ├── Grafana  (dashboards, Explore)
               └─ spans (OTLP/HTTP :4318) ──► OTel Collector ──► Tempo ◄──┘
```

| Piece | What it answers | Where |
|---|---|---|
| **Prometheus** | *How much, how fast, how often?* Numbers over time: requests/s, p95 latency, errors, LLM time to first token, documents indexed/min | http://localhost:9090 |
| **OpenTelemetry** | *What happened in this one request?* A trace per request (or per ingested document), with a span for every PostgreSQL query and every call to Ollama and Qdrant | the SDK in the backend ([app/core/telemetry.py](../backend/app/core/telemetry.py)) + the Collector |
| **Tempo** | Stores the traces | queried through Grafana |
| **Grafana** | Shows both: the **MyChatApp** dashboard, and Explore → Tempo for traces | http://localhost:3000 (anonymous viewer; `admin` / `GRAFANA_ADMIN_PASSWORD` to edit) |

All metrics are defined in [app/core/metrics.py](../backend/app/core/metrics.py). They are served on port **9100**, not
8000: nginx forwards all of `/api/*` to the backend, so a `/metrics` route there would be public.
Tracing is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set (compose and the ConfigMap set it).

Config files live in [observability/](observability/) and are shared: compose mounts them, Kubernetes turns them
into ConfigMaps ([observability/kustomization.yaml](observability/kustomization.yaml)). Only Prometheus differs
(`prometheus.yml` vs `prometheus-k8s.yml`), because the DNS names differ.

**Tokens & cost** ([app/services/usage.py](../backend/app/services/usage.py)): every model call (chat answer, agent
run, chat title) records the tokens Ollama reports, plus a cost estimate at `LLM_PRICE_INPUT_PER_MTOK` /
`LLM_PRICE_OUTPUT_PER_MTOK` (USD per million tokens). Totals go to Prometheus. Per user, they go to the `llm_usage`
table, which Grafana reads through its PostgreSQL data source. They're kept out of Prometheus because a user id
label would mean one time series per user.

On Kubernetes nothing is public; use port-forwarding:

```bash
kubectl port-forward svc/grafana 3000:3000 -n mychatapp
kubectl port-forward svc/prometheus 9090:9090 -n mychatapp
```

### From a slow request to its cause

1. The dashboard shows p95 latency of `/chat` going up.
2. Its trace: Grafana → Explore → Tempo → search `{ name = "POST /chat" && duration > 5s }`. The span tree shows
   whether the time went to `rag.retrieve` (embedding + Qdrant), the PostgreSQL queries, or the Ollama call.
3. Its log lines: every log line written during a traced request carries the trace id
   (`"trace_id": ...` in production JSON, `t=1a2b3c4d` in dev text). Search the logs for it
   (`kubectl logs deploy/backend -n mychatapp | grep <trace id>`). And the other way around: the `request_id` from a
   user's error message is a span attribute, so `{ span.request_id = "..." }` in Tempo finds the trace.

The dashboard lives in git: edit [mychatapp.json](observability/grafana/dashboards/mychatapp.json)
(or save a copy in the UI, edit that, and export its JSON over this file), then restart Grafana. Changes made directly in the UI to the provisioned dashboard can't be saved.

## Notes

- The backend image installs `requirements.txt`, which leaves out dev dependencies like `ragas`. Evaluation runs therefore don't work inside the container. Listing past results still does.
- Eval results are written to the container's filesystem, so they're lost when the pod restarts.
- With 2 backend replicas, uploads and chats go to whichever pod the Service picks. That works because all state lives in Postgres/Qdrant/MinIO, not in the pod. This is the "stateless app" rule that makes scaling possible.
