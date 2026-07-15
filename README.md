# Job Hunt Command Center — a serverless platform that generates JD-tailored résumés, tracks applications, and reads your inbox for you

Paste a job description and get a **tailored 2-page résumé** (LaTeX + PDF, written by Amazon Bedrock, scored against a match rubric and ATS keywords); log every application with the exact résumé you sent; and let an **event-driven pipeline** scan your inbox to classify recruiter replies, rejections, and interviews and auto-advance the right application — so the whole search lives in one place instead of your head and a messy inbox.

> **Personal-use tool.** The *code and infrastructure* are public (it's a portfolio project); the *data* — applications, documents, email classifications — is private, single-user, and gated behind Cognito. Nothing personal lives in this repo.

**Status:** ✅ **All stages built and deployed live.** Headline feature: an **AI résumé generator** — paste a JD, get a tailored 2-page résumé (LaTeX + server-compiled PDF) written by **Amazon Bedrock** (model-selectable: Sonnet / Haiku / Opus), scored with a weighted match rubric + ATS keyword rate, with AI-suggested tags, length auto-fit, and an optional cover letter. The tracker (CRUD API, Cognito auth, versioned document storage, dashboard, conversion analytics) is verified end-to-end. Inbox intelligence is an **event-driven pipeline** (EventBridge → Scanner → SQS+DLQ → Dispatcher → Step Functions: *Classify → Enrich*); the app **self-monitors** (CloudWatch golden-signals dashboard, SLO alarms → composite health alarm → SNS, X-Ray, runbook) with an **AWS Budgets** guard on Bedrock spend and a **weekly SES digest**. Shipped through a DevSecOps CI/CD pipeline. The one human-in-the-loop step is dropping a Google App Password into Secrets Manager to switch on live email scanning (see [below](#turning-on-live-email-scanning)).

## Why this project

I built this to run my own job search — the best kind of project is one you actually use. It's also a full-stack serverless build: a real CRUD frontend, document storage, third-party OAuth (Gmail), scheduled jobs, notifications, and analytics. Everything is Terraform, and it ships through the **same security-gated CI/CD pipeline** as my other work (gitleaks · Checkov/tfsec · Trivy). Since it touches personal email and OAuth tokens, secure storage and least privilege are the point, not afterthoughts.

**Scope guardrail:** this *assists* the search — tracking, email intelligence, reminders, tailored drafts. It does **not** auto-submit applications (that breaks job-board ToS and produces spray-and-pray noise). A human stays in the loop for every apply.

## Architecture

The tracker is a straightforward serverless CRUD app; the interesting part is the
**event-driven inbox pipeline** and the **self-monitoring** layer around it.

```
                 Static SPA (S3 + CloudFront)
                          │  Cognito login (single user, JWT)
                          ▼
                 API Gateway (HTTP API) → Lambda "api" ──► Amazon Bedrock (Claude Haiku)
                          │                                 JD extraction · résumé match · Ask-AI
            ┌─────────────┼──────────────┐
            ▼             ▼              ▼
      DynamoDB       S3 "documents"   Secrets Manager
   applications    résumés · JDs      (IMAP app password)
   email events   (versioned, private)

   INBOX PIPELINE (event-driven, one message per email):
   EventBridge (6h) → Lambda "scanner" ─────► SQS ──redrive(3x)──► DLQ
       (read-only IMAP, dedupe,                │
        cheap pre-filter — no Bedrock)         ▼  (SQS trigger, partial-batch failure reporting)
                                          Lambda "dispatcher" ─StartSyncExecution─► Step Functions (Express)
                                                                                    Classify (Bedrock)
                                                                                       ▼
                                                                                    Enrich (match app ·
                                                                                    auto-advance · record)
   EventBridge (daily) → Lambda "nudge" → SES   (stale-application reminders)

   OBSERVABILITY: CloudWatch golden-signals dashboard · SLO alarms (DLQ depth,
   workflow failures, API 5xx, pipeline errors) → composite health alarm → SNS ·
   X-Ray tracing on every Lambda + the state machine · docs/RUNBOOK.md

   Shipped by: GitHub Actions (OIDC) · gitleaks · Checkov/tfsec · Trivy · unit tests
```

**Why decomposed this way.** The old design was one scheduled Lambda that read the
inbox *and* called Bedrock *and* wrote to DynamoDB. Splitting it means the expensive,
failure-prone work (Bedrock, DB writes) is off the ingest path: SQS buffers and
retries each email independently, a poison message is quarantined in the DLQ instead
of failing the whole run, and Step Functions gives each step its own retry policy and
a visible execution history. Every email is now processed exactly-enough-times and
traceable end to end.

## How it works

1. You log an application in the dashboard and **attach the résumé you applied with** — the PDF is snapshotted to a private, versioned S3 bucket, so the as-sent copy is frozen even if you later edit that résumé. The dashboard supports search, filtering, and CSV export.
2. The full record (role details, JD, contacts, the résumé/cover-letter references) lives in DynamoDB behind Cognito auth.
3. A scheduled **scanner Lambda** reads *new* mail (read-only IMAP), skips anything already processed (idempotent) or obviously non-job (a cheap keyword/ATS pre-filter), and drops each candidate onto **SQS**. A **dispatcher** consumes the queue and runs the **`process-email` Step Functions workflow** per message: **Classify** (Amazon Bedrock reads the email and returns structured triage — recruiter reply / rejection / interview / offer / confirmation, plus extracted recruiter, pay, location, interview date), then **Enrich** (match the email to an application, auto-advance the status forward-only, fill missing fields, and record the event). Failures retry per message and land in a **DLQ** after 3 tries — nothing is silently lost.
4. A scheduled **nudge Lambda** finds applications with no response after N days and sends a follow-up reminder via SES.
5. The dashboard shows your pipeline (applied → screen → interview → offer/rejected) and analytics.
6. A **visa-sponsorship checker** (`POST /sponsorship`) fuses three signals into one verdict — a live **H-1B/LCA history** lookup on h1bdata.info (public DOL data: filing count, how many are tech roles, recent year, median wage), a **deterministic JD language scan** (kill-phrases like "…without sponsorship now or in the future" / citizenship / clearance vs. green flags like "will sponsor" / STEM-OPT), and **curated employer knowledge** (documented no-sponsors, offshore-caution firms, and lottery-exempt universities/hospitals). It's built for an F-1/OPT job search — check sponsorship in one place instead of bouncing between h1bdata / myvisajobs / h1bgrader.
7. An **interview-prep generator** (`POST /applications/{id}/interview-prep`) turns the stored JD + the résumé you sent into tailored prep via Bedrock — likely technical & behavioral questions, talking points mapped to *your* background, gaps to shore up, and sharp questions to ask them.
8. **Referral tracking** (referred-by + referral status, with its own filter and card badges) keeps the highest-converting channel visible, and a **duplicate-apply warning** stops the same company+title being logged twice.
9. OAuth/email tokens live in **Secrets Manager**, documents in **S3** via presigned URLs; everything is Terraform + keyless-OIDC CI/CD.

## What each application stores

Every application is a rich record, not just a status line:

- **Role** — company, title, location, on-site/hybrid/remote, salary range, source (LinkedIn / referral / company site), posting URL.
- **The exact résumé sent** — an **immutable snapshot** of the PDF you applied with, versioned in S3, so "which résumé did I send them?" is never a mystery. Editing that résumé later never rewrites the as-sent copy.
- **Cover letter / attachments** — same snapshot treatment.
- **Job description** — the full JD text, kept for interview prep and (future) match scoring.
- **Contacts** — recruiter / referral name + email, linked to the classified inbox events.
- **Pipeline** — status (applied → screen → interview → offer / rejected / ghosted), an **activity timeline** of every touchpoint, and the **next action + due date**.
- **Sponsorship verdict** — a one-click H-1B check (likely / possible / cap-exempt / caution / unlikely) with the employer's real LCA filing history, the reasons, and deep-links to verify — plus the simple sponsors/OPT filter that actually matters for me.
- **Tags** — keywords for filtering and analytics.

## Services and why

| Service | Role |
|---|---|
| DynamoDB | Applications + email-event store (the rich record) |
| S3 (documents) | Immutable, versioned résumé / cover-letter / JD snapshots; presigned upload & download |
| Lambda | CRUD API + **résumé generator** + inbox pipeline (scanner · dispatcher · classify · enrich) + nudge + digest |
| API Gateway | HTTPS API for the dashboard |
| Cognito | Single-user auth (personal data isn't public) |
| **Amazon Bedrock** (Claude) | **Résumé generation** (Sonnet/Haiku/Opus), email triage/extraction, JD field extraction, résumé↔JD match scoring, Ask-AI Q&A |
| **tectonic layer** | Server-side LaTeX→PDF compile for generated résumés (bundled package cache) |
| **SQS + DLQ** | Buffers one message per candidate email; retries and quarantines poison messages |
| **Step Functions** (Express) | Orchestrates `Classify → Enrich` per email, with per-step retries and execution history |
| EventBridge | Schedules the scanner (6h), nudge (daily), digest (weekly) jobs |
| Secrets Manager | IMAP app password (never in code) |
| SES | Follow-up reminders + weekly digest |
| **AWS Budgets** | Spend guard on Amazon Bedrock (email alert at threshold) |
| **CloudWatch + X-Ray** | Golden-signals dashboard, SLO + composite health alarms, distributed tracing |
| **SNS** | Alert fan-out for the composite service-health alarm |
| S3 + CloudFront | Hosts the dashboard SPA |
| Gmail (read-only IMAP) | Least-privilege inbox access |
| Terraform + GitHub Actions | IaC + keyless CI/CD with security gates (gitleaks · Checkov/tfsec · Trivy) + unit tests |

## Security decisions

- **Documents bucket is private** — no public access; every upload/download goes through a short-lived presigned URL (same pattern as my serverless-file-share project). Encrypted at rest (SSE-KMS), with **S3 versioning** for immutable as-sent snapshots.
- **Read-only mail access** — the app can *read* to classify, never send, modify, or delete mail.
- **Inbox-access approach is a deliberate Stage-3 decision.** Gmail API with `gmail.readonly` (a Google *restricted scope*) kept in **Testing** publishing status with myself as the sole test user avoids Google's paid CASA assessment — but Testing-mode refresh tokens **expire after 7 days**, which a scheduled Lambda can't tolerate silently. Alternative for a single-user tool: **IMAP + a Google App Password** (requires 2-Step Verification) — no consent screen, no CASA, a non-expiring credential. Both keep the credential in Secrets Manager; the trade-offs are documented when Stage 3 lands.
- **Credentials in Secrets Manager** — encrypted, never committed, rotatable.
- **App gated behind Cognito** — pipeline data is not public.
- **Data minimization** — store email classification + metadata, not full email bodies.
- **Shipped through the DevSecOps pipeline** — gitleaks (secrets), Checkov/tfsec (IaC), and Trivy (deps) block insecure code from merging; the email classifier has unit tests so a bad change can't silently mislabel mail.
- **Least privilege per Lambda**, DynamoDB encrypted at rest, CI via OIDC (no static keys).

## Roadmap

- [x] **Stage 0** — Repo, scoped OIDC deploy role (`jobhunt-*`); account already budget-guarded. Inbox auth path decided at Stage 3 = **IMAP + App Password** (no 7-day-token problem).
- [x] **Stage 1** — DynamoDB + Lambda CRUD API + vanilla-JS dashboard with **search / filter / stats / CSV export**; **attach the as-sent résumé** (snapshotted to a private, versioned S3 bucket via presigned URLs). Deployed + verified live.
- [x] **Stage 2** — Cognito auth (USER_PASSWORD_AUTH) + an HTTP API **JWT authorizer** so the dashboard and every route are private. Verified (no-auth → 401).
- [x] **Stage 3** — Inbox-scan Lambda: **IMAP read-only**, `SINCE`-windowed, rule-based classification with **10 passing unit tests** (now the keyword *fallback* under Bedrock), credential in Secrets Manager, events linked to applications by contact-email / company. No-ops safely until the credential is set.
- [x] **Stage 4** — Nudge Lambda (stale-application reminders via **SES**, daily EventBridge schedule) + analytics (funnel, response rate) + **CSV export** in the dashboard.
- [x] **Stage 5** — Clean Terraform (`validate` clean), **DevSecOps pipeline** (gitleaks · Checkov + tfsec · Trivy) + classifier unit tests + `terraform validate`; documented Checkov baseline.
- [x] **Stage 6 — Amazon Bedrock (Claude Haiku).** AI email triage + entity extraction (recruiter, pay, location, interview date) that **auto-advances and enriches** the matching application; **JD field extraction** (`/parse-jd`); **JD↔résumé match scoring** with gap analysis (`/{app}/match`); and a natural-language **Ask-AI** Q&A over applications (`/ask`). Keyword classifier kept as the graceful fallback.
- [x] **Stage 7 — Event-driven backbone.** Decomposed the monolithic scanner into `EventBridge → Scanner → SQS(+DLQ) → Dispatcher → Step Functions Express (Classify → Enrich)`: one message per email, per-message retries, poison-message DLQ isolation, per-step retry policies, and full execution history. Verified end-to-end (synthetic email → workflow SUCCEEDED).
- [x] **Stage 8 — Self-monitoring.** CloudWatch golden-signals dashboard, SLO alarms (DLQ depth, workflow failures, API 5xx, pipeline errors) rolled into a **composite service-health alarm → SNS**, **X-Ray** tracing on every Lambda + the state machine, and a per-alarm **[runbook](docs/RUNBOOK.md)** — reusing the `cloud-observability-sre` patterns on this app's own stack.
- [x] **Stage 9 — AI résumé generator.** Paste a JD → a dedicated async Lambda has **Amazon Bedrock** (model-selectable: Claude Sonnet 4.6 / Haiku / Opus) rewrite the résumé to fit it, returning **structured JSON** that the Lambda renders into LaTeX deterministically (so it always compiles and can never fabricate facts outside the candidate corpus). Produces a **2-page LaTeX + server-compiled PDF** (bundled **tectonic** Lambda layer) with **length auto-fit**, a **weighted match-score rubric + ATS keyword-match rate** (the same standard as the `/match` check), AI-suggested custom fields, and an optional cover letter. Async job + poll (Opus can exceed API Gateway's 30s cap); PDF auto-attaches to the application on save.
- [x] **Stage 10 — Analytics, digest & cost guards.** Conversion analytics (funnel, response rate by source, résumé-match-vs-outcome); a **weekly SES digest** of the pipeline; an **AWS Budgets** alarm on Amazon Bedrock spend; expanded unit tests (scanner + enrich + renderer) in CI. Prospect ingestion was prototyped and removed (kept the tool focused).

## Turning on live email scanning

The scanner Lambda is deployed and runs every 6 hours, but no-ops until it has a credential. To switch it on (one time):
1. In your Google account, enable **2-Step Verification**, then create an **App Password** (Google Account → Security → App passwords) for "Mail".
2. Make sure **IMAP is enabled** in Gmail (Settings → Forwarding and POP/IMAP).
3. Put it in the existing Secrets Manager secret `jobhunt/email-credentials`:
   ```
   aws secretsmanager put-secret-value --secret-id jobhunt/email-credentials \
     --secret-string '{"email":"you@gmail.com","app_password":"<16-char app password>","imap_host":"imap.gmail.com"}'
   ```
That's it — the next scheduled run will classify recent mail and link it to your applications. The App Password never expires, is read-only, and can be revoked anytime.

## Future scope

Most of the original "advanced" list is now shipped (Bedrock triage/extraction, match
scoring, Ask-AI — Stage 6; the event-driven backbone — Stage 7; self-monitoring — Stage 8).
What deliberately remains:

**Intentionally left manual (human-in-the-loop, by design)**
- **Tailored drafts** (follow-ups, cover letters) and **interview prep** are *mine to write* — the tool surfaces the context (the JD, the exact résumé I sent, the match gaps), but the words that go to a human are my job, not the model's. This is a scope choice, not a gap.

**Genuinely next (if this were more than a personal tool)**
- **Job ingestion (surface, never auto-apply)** — pull postings from board APIs / RSS (where ToS permits) into a deduped "to-apply" queue ranked by match score; I still click apply.
- **Richer analytics** — funnel conversion by source/role/keyword and **which résumé version converts best**.
- **Multi-provider email** — Outlook via Microsoft Graph behind a provider abstraction.
- **Multi-tenant SaaS mode** — Cognito user pools, per-tenant isolation, usage metering, Bedrock token budgeting + caching. (This is where the tool would leave personal-use status and *does* need Google's CASA assessment before monetizing.)
- **Security & compliance (advanced)** — per-user KMS keys, Secrets Manager rotation, CloudTrail audit logging, PII redaction, and delete-my-data (GDPR-style) support.

## Cost

Built for the free tier. DynamoDB on-demand, Lambda, EventBridge, Cognito, S3, SES, and SNS at personal volume cost pennies; CloudFront is near-free. The event-driven additions stay effectively free at this volume: **SQS** (1M requests/mo free), **Step Functions Express** (billed per request + duration — cents at a few emails/day), and **X-Ray** (100k traces/mo free). Observability sits inside the free tier too: this is the account's 2nd CloudWatch **dashboard** (3 free) and adds 4 metric **alarms** (10 free); the one paid item is the **composite alarm** (~$0.50/mo) — remove `aws_cloudwatch_composite_alarm.service_health` if you want strictly $0. **Bedrock** (Claude Haiku) is pay-per-token but tiny: triage runs only on pre-filtered candidate emails, capped at 300 output tokens each. A budget alarm guards the account.

---

Built by Rajolu Abheenash — [github.com/Abheenash](https://github.com/Abheenash) · [abheenash.com](https://abheenash.com)
