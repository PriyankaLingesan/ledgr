# Deployment

Both container images are production-shaped already: multi-stage builds, pinned base
images, non-root runtime users, health checks, no build toolchain in the final layer.

The target below is deliberately small. A ledger of this size does not need a service
mesh, a queue or a cache tier, and adding them to lengthen a technology list would be
architecture as decoration.

---

## Target topology (AWS)

```
                    Route 53
                       │
                       ▼
              Application Load Balancer  (TLS termination)
                   │              │
        /api/* ────┘              └──── /*
           │                             │
   ┌───────▼────────┐            ┌───────▼────────┐
   │ ECS Fargate    │            │ ECS Fargate    │
   │ ledgr-api      │            │ ledgr-web      │
   │ 2 tasks        │            │ 2 tasks        │
   └───────┬────────┘            └────────────────┘
           │ (private subnets)
   ┌───────▼──────────────┐
   │ RDS PostgreSQL 16    │  Multi-AZ, automated backups, PITR
   └──────────────────────┘

   Secrets Manager  →  DATABASE_URL injected as a task secret
   CloudWatch Logs  →  structured JSON from both services
   ECR              →  both images, immutable tags
```

### Why these services

| Service | Reason |
| --- | --- |
| **ECS Fargate** | Stateless HTTP containers with no node fleet to patch. Kubernetes would be a second platform to operate for two services. |
| **RDS PostgreSQL** | The ledger's correctness depends on PostgreSQL row locks, triggers and constraints. Managed backups and PITR matter more here than anywhere else in the stack. |
| **ALB** | Path-based routing lets the console and the API share one hostname, so the browser sees a single origin and CORS disappears in production. |
| **ECR** | Image registry with lifecycle policies and scan-on-push. |
| **CloudWatch Logs** | The app already emits structured JSON; log-group metric filters give alarms with no extra agent. |
| **Secrets Manager** | The database URL is the only real secret. |

Services deliberately **not** used: no ElastiCache (nothing needs a cache tier), no SQS or
EventBridge (nothing in the domain is asynchronous), no Lambda (a warm connection pool
against a relational database is worth more than per-request scaling), no DynamoDB (a
ledger's guarantees are relational).

---

## Configuration

Everything is environment-driven; the same image runs locally, in CI and on ECS.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/ledgr` — from Secrets Manager |
| `ENVIRONMENT` | appears in `/system/health` and in every log line |
| `LOG_LEVEL` | default `INFO` |
| `CORS_ORIGINS` | comma-separated; empty when the ALB serves one origin |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | sized against the RDS `max_connections` budget |
| `DB_LOCK_TIMEOUT_MS`, `DB_STATEMENT_TIMEOUT_MS` | defaults 5s / 15s |

The console's API base URL is baked at image build time (`VITE_API_BASE_URL`), because a
static SPA has no server-side configuration step. With ALB path routing the correct value
is simply `/api/v1`.

---

## Migrations

Migrations run as a **separate ECS task** (the same image, `alembic upgrade head`), not on
API container start. Otherwise N starting tasks race to migrate the same database, and a
failed migration takes the service down with it.

Deployment order:

1. `docker build` and push both images to ECR with an immutable tag (the commit SHA).
2. Run the migration task; wait for exit code 0.
3. Update the `ledgr-api` service to the new task definition; ECS rolls tasks with the ALB
   health check (`GET /api/v1/system/health`) gating each one.
4. Update `ledgr-web`.

This ordering requires migrations to be backward-compatible for the length of one
deployment — the old code must tolerate the new schema. Additive changes first, destructive
changes in a later release.

---

## Operational signals

The application already exposes what monitoring needs:

| Signal | Source | Alarm on |
| --- | --- | --- |
| Liveness + DB reachability | `GET /system/health` | non-200 from the ALB health check |
| **Ledger consistency** | `GET /system/integrity` | `cache_consistent = false` or `trial_balance_balanced = false` |
| Trial balance | `GET /system/stats` | `ledger_balanced = false` |
| Lock contention | log filter on `"code":"lock_timeout"` | sustained rate above baseline |
| Rejected postings | log filter on `"domain_error"` | spike in `unbalanced_transaction` or `insufficient_funds` |

`/system/integrity` is the one worth paging on. If the derived and cached balances ever
disagree, or the ledger stops netting to zero, something is wrong that no request-level
metric will show.

---

## Local production check

The compose stack builds and runs the same images the deployment would:

```bash
docker compose up --build
```

Console on `:8080`, API on `:8000`, PostgreSQL on `:5433`. The `migrate` service applies
migrations and seeds only if the ledger is empty, and both application services wait for
it to complete successfully.
