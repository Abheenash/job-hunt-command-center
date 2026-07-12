# Job Hunt Command Center — a serverless dashboard that tracks applications, remembers what you sent, and reads your inbox for you

Log every application with the exact résumé you applied with, watch its status, and let a scheduled Lambda scan your inbox to classify recruiter replies, rejections, and interview invites — so the whole search lives in one place instead of your head and a messy inbox.

> **Personal-use tool.** The *code and infrastructure* are public (it's a portfolio project); the *data* — applications, documents, email classifications — is private, single-user, and gated behind Cognito. Nothing personal lives in this repo.

**Status:** ✅ **All stages built and deployed live.** The tracker (CRUD API, Cognito auth, document storage, dashboard) is running and verified end-to-end; the inbox-scan and nudge Lambdas are deployed on their schedules; the DevSecOps pipeline + classifier unit tests are in place. The one human-in-the-loop step is dropping a Google App Password into Secrets Manager to switch on live email scanning (see [below](#turning-on-live-email-scanning)).

## Why this project

I built this to run my own job search — the best kind of project is one you actually use. It's also a full-stack serverless build: a real CRUD frontend, document storage, third-party OAuth (Gmail), scheduled jobs, notifications, and analytics. Everything is Terraform, and it ships through the **same security-gated CI/CD pipeline** as my other work (gitleaks · Checkov/tfsec · Trivy). Since it touches personal email and OAuth tokens, secure storage and least privilege are the point, not afterthoughts.

**Scope guardrail:** this *assists* the search — tracking, email intelligence, reminders, tailored drafts. It does **not** auto-submit applications (that breaks job-board ToS and produces spray-and-pray noise). A human stays in the loop for every apply.

## Target architecture

```
                 React SPA (S3 + CloudFront)
                          │  Cognito login (single user)
                          ▼
                 API Gateway → Lambda (CRUD)
                          │
            ┌─────────────┼──────────────────────┐
            ▼             ▼                       ▼
      DynamoDB       S3 "documents"        Secrets Manager
    applications    résumés · cover        (email token +
    email events    letters · JDs          client secret)
                    (versioned, private,
                     presigned URLs)
            ▲
   EventBridge (schedule)
        │
        ▼
   Lambda "inbox-scan" ──> Gmail (read-only, incremental) → classify → link
        │
        ▼
   EventBridge (schedule) → Lambda "nudge" → SES/SNS  (stale-application reminders)

   Shipped by: GitHub Actions (OIDC) · gitleaks · Checkov/tfsec · Trivy · unit tests
```

## How it works

1. You log an application in the dashboard and **attach the résumé you applied with** — the PDF is snapshotted to a private, versioned S3 bucket, so the as-sent copy is frozen even if you later edit that résumé. The dashboard supports search, filtering, and CSV export.
2. The full record (role details, JD, contacts, the résumé/cover-letter references) lives in DynamoDB behind Cognito auth.
3. A scheduled **inbox-scan Lambda** reads *new* mail (read-only), syncing **incrementally** so it never re-reads the whole inbox, classifies job-related messages (recruiter reply / rejection / interview / confirmation), and links them to the matching application.
4. A scheduled **nudge Lambda** finds applications with no response after N days and sends a follow-up reminder via SES/SNS.
5. The dashboard shows your pipeline (applied → screen → interview → offer/rejected) and analytics.
6. OAuth/email tokens live in **Secrets Manager**, documents in **S3** via presigned URLs; everything is Terraform + keyless-OIDC CI/CD.

## What each application stores

Every application is a rich record, not just a status line:

- **Role** — company, title, location, on-site/hybrid/remote, salary range, source (LinkedIn / referral / company site), posting URL.
- **The exact résumé sent** — an **immutable snapshot** of the PDF you applied with, versioned in S3, so "which résumé did I send them?" is never a mystery. Editing that résumé later never rewrites the as-sent copy.
- **Cover letter / attachments** — same snapshot treatment.
- **Job description** — the full JD text, kept for interview prep and (future) match scoring.
- **Contacts** — recruiter / referral name + email, linked to the classified inbox events.
- **Pipeline** — status (applied → screen → interview → offer / rejected / ghosted), an **activity timeline** of every touchpoint, and the **next action + due date**.
- **Sponsorship flag** — does the role sponsor / accept OPT? (a filter that actually matters for me).
- **Tags** — keywords for filtering and analytics.

## Services and why

| Service | Role |
|---|---|
| DynamoDB | Applications + email-event store (the rich record) |
| S3 (documents) | Immutable, versioned résumé / cover-letter / JD snapshots; presigned upload & download |
| Lambda | CRUD API, inbox-scan, nudge |
| API Gateway | HTTPS API for the dashboard |
| Cognito | Single-user auth (personal data isn't public) |
| EventBridge | Schedules the inbox-scan and nudge jobs |
| Secrets Manager | Email OAuth token / app password + client secret (never in code) |
| SES / SNS | Follow-up reminders |
| S3 + CloudFront | Hosts the React dashboard |
| Gmail (read-only, incremental) | Least-privilege inbox access |
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
- [x] **Stage 3** — Inbox-scan Lambda: **IMAP read-only**, `SINCE`-windowed, rule-based classification with **9 passing unit tests**, credential in Secrets Manager, events linked to applications by contact-email / company. No-ops safely until the credential is set.
- [x] **Stage 4** — Nudge Lambda (stale-application reminders via **SES**, daily EventBridge schedule) + analytics (funnel, response rate) + **CSV export** in the dashboard.
- [x] **Stage 5** — Clean Terraform (`validate` clean), **DevSecOps pipeline** (gitleaks · Checkov + tfsec · Trivy) + classifier unit tests + `terraform validate`; documented Checkov baseline.

## Turning on live email scanning

The inbox-scan Lambda is deployed and runs every 6 hours, but no-ops until it has a credential. To switch it on (one time):
1. In your Google account, enable **2-Step Verification**, then create an **App Password** (Google Account → Security → App passwords) for "Mail".
2. Make sure **IMAP is enabled** in Gmail (Settings → Forwarding and POP/IMAP).
3. Put it in the existing Secrets Manager secret `jobhunt/email-credentials`:
   ```
   aws secretsmanager put-secret-value --secret-id jobhunt/email-credentials \
     --secret-string '{"email":"you@gmail.com","app_password":"<16-char app password>","imap_host":"imap.gmail.com"}'
   ```
That's it — the next scheduled run will classify recent mail and link it to your applications. The App Password never expires, is read-only, and can be revoked anytime.

## Future scope — advanced

The MVP above is deliberately lean. Here's where it goes at the next level:

**AI / LLM (Amazon Bedrock)**
- Smarter classification + **entity extraction** — pull company, role, recruiter, and next-step dates straight from email bodies instead of keyword rules.
- **JD ↔ résumé match scoring** — paste a job description, get a fit score, a gap analysis, and which of my projects (and *which résumé variant*) to use.
- **Tailored drafts** — generate follow-ups, cover letters, and outreach from a JD + my résumé/project write-ups (RAG over my own docs).
- **Interview prep** — from a JD *and the exact résumé I sent that company*, generate likely questions with talking points mapped to my projects.

**Job ingestion (surface, never auto-apply)**
- Pull postings from board APIs / RSS (where ToS permits) into a deduped "to-apply" queue, ranked by match score — I still click apply.

**Analytics / ML**
- Full funnel conversion by source, role, and keyword; **which résumé version converts best**; response-rate and best-time-to-apply insights.

**Architecture & scale (the interesting part)**
- **Event-driven backbone** — EventBridge + **Step Functions** for the new-email → classify → extract → link → notify workflow; **SQS + DLQ** for reliable async processing.
- **Multi-provider email** — add Outlook via Microsoft Graph behind a provider abstraction.
- **Self-monitoring** — reuse my `cloud-observability-sre` patterns (dashboards, alarms, tracing) on this app, tying the portfolio together.
- **Multi-tenant SaaS mode** — Cognito user pools, per-tenant data isolation, usage metering, and Bedrock token budgeting + caching — turning a personal tool into something others could use. (This is also the point where the OAuth app leaves Testing status and *does* need Google's CASA assessment — see the work-authorization note before monetizing.)

**Security & compliance (advanced)**
- Per-user KMS keys, **Secrets Manager rotation**, CloudTrail audit logging, PII redaction, and data-retention / delete-my-data (GDPR-style) support.

## Cost

Built for the free tier: DynamoDB on-demand, Lambda, EventBridge, Cognito, S3, and SES/SNS at personal volume cost pennies; CloudFront is near-free. Incremental inbox sync keeps API quota (and re-processing cost) minimal. Bedrock (future scope) is pay-per-token — gate it behind budgets and caching. A budget alarm guards the account.

---

Built by Rajolu Abheenash — [github.com/Abheenash](https://github.com/Abheenash) · [abheenash.com](https://abheenash.com)
