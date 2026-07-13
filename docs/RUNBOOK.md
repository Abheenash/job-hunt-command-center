# Runbook — Job Hunt Command Center

Every alarm below publishes to the `jobhunt-alerts` SNS topic and rolls up into the
`jobhunt-service-health` composite alarm. Dashboard: **CloudWatch → Dashboards →
`jobhunt-health`**. Traces: **X-Ray** (Active on every Lambda + the state machine).

The pipeline: `EventBridge (6h) → scanner → SQS(+DLQ) → dispatcher → Step Functions
Express (classify → enrich)`.

---

## 🚨 `jobhunt-dlq-not-empty` — messages in the dead-letter queue
**Means:** one or more emails failed the `process-email` workflow 3× and are now
quarantined in `jobhunt-email-dlq`. No data is lost; nothing is retrying them.

1. Open the **`jobhunt-health`** dashboard → "Inbox queue" widget to see DLQ depth.
2. **Find the cause:** X-Ray service map + the `process-email` execution history
   (Step Functions console → filter Failed), or the state-machine log group
   `/aws/vendedlogs/states/jobhunt-process-email`.
3. Common causes: Bedrock throttling/permission change (classify), a malformed
   application record (enrich), or a DynamoDB permission/throttle.
4. **After fixing the root cause,** redrive the DLQ back to the main queue:
   SQS console → `jobhunt-email-dlq` → **Start DLQ redrive**. Watch executions
   succeed on the dashboard, then confirm the alarm returns to OK.

## 🚨 `jobhunt-workflow-failed` — Step Functions executions failing
**Means:** `process-email` is failing *after* its per-step retries (messages will
head to the DLQ next).

1. Step Functions console → `jobhunt-process-email` → **Executions** → open a
   Failed one. The graph shows whether **Classify** or **Enrich** threw.
2. Check that Lambda's log group and its X-Ray trace.
3. If it's a bad deploy, roll back the last change (git revert + pipeline) or fix
   forward. Messages stay safely in SQS/DLQ meanwhile.

## 🚨 `jobhunt-api-5xx` — dashboard API returning 5xx
**Means:** the `jobhunt-api` Lambda behind API Gateway is erroring (≥5 in 5 min).

1. Dashboard → "API" widgets (5xx count, latency). X-Ray trace the failing route.
2. Check `jobhunt-api` logs — usual suspects: a DynamoDB/S3/Bedrock permission or
   an unhandled input on one route (`/parse-jd`, `/ask`, `/{app}/match`).
3. The frontend is a static SPA on CloudFront — it stays up; only API calls fail.

## 🚨 `jobhunt-pipeline-errors` — unhandled errors in any pipeline function
**Means:** `scanner`, `dispatcher`, `classify`, or `enrich` raised (metric-math sum ≥1).

1. Dashboard → "Pipeline Lambdas — errors" (stacked, so you see which one).
2. **scanner:** almost always IMAP/Secrets — verify the app password is valid and
   IMAP is still enabled in Gmail. (Scanner no-ops safely if the secret is a
   placeholder; a hard error means a real credential went bad.)
   **dispatcher:** `states:StartSyncExecution` permission or the state machine ARN.
   **classify:** Bedrock (throttle/permission) — note it *falls back to keywords*,
   so a hard error here is unusual.
   **enrich:** DynamoDB permission/throttle or a malformed application record.

---

## Notes
- **Idempotency:** the scanner skips any email already in the `email-events` table,
  so a redrive or a re-run never double-applies a status change.
- **Silence is healthy:** every alarm uses `treat_missing_data = notBreaching` — no
  email traffic simply means no data, not an alarm.
- **Turning the pipeline on:** see the README "Turning on live email scanning" — until
  the Secrets Manager credential is real, the scanner no-ops and the pipeline is idle.
