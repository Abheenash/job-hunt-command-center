# Automated Job-Search Pipeline — Plan (Phase 0 / RECON)

**Status:** proposal — awaiting your approval before any pipeline code is written.
**Author:** recon pass over the existing repo, 2026-07-29.

---

## 1. The single most important decision (read this first)

Your brief describes a **local, script-and-file pipeline**: named scripts (`job:scan`, `job:score`, …), `config/*.yaml`, `applications/<company>-<role>-<date>/`, `reports/`, `logs/`. None of those exist yet.

But this repo is **not greenfield**. `job-hunt-command-center` is a live, deployed serverless app that already implements most of Phases 1–4:

- `src/openings/lambda_function.py` — full multi-source ATS scanner (Greenhouse, Ashby, Lever, Workday, Amazon, GitHub new-grad feeds, Adzuna), dedup, sponsorship gating, deterministic fit scoring, geo tiering. **~660 lines, working, currently disabled** (`OPENINGS_MODE="launchpad"`, EventBridge rule `DISABLED`).
- `src/api/sponsorship.py` — live H-1B/LCA lookup + curated sponsor lists + cap-exempt detection + JD language scan.
- `src/resume_gen/` — Bedrock rewrites **only** from the `profile.py` corpus (never fabricates), renders LaTeX, compiles with tectonic, auto-fits to ≤2 pages.
- `src/api/lambda_function.py` — full CRUD API, versioned S3 document store, match scoring, interview prep, Cognito auth.

**Recommended architecture: a local pipeline that reuses the existing logic as libraries.**

Build the pipeline as **local Python scripts + config files + output directories exactly as your brief specifies** — that gives you a fast, inspectable, git-versioned daily driver you can run in one command. But instead of re-implementing sourcing/scoring/sponsorship/résumé from scratch, **import and refactor the existing Lambda modules into shared libraries** (`pipeline/lib/`) so there is *one* source list, *one* dedup rule, *one* sponsorship checker, *one* résumé corpus. Phase 7 writes back to the **live portal via its existing HTTP API** (not a second copy of your data).

This respects both the brief's shape and ~80% of code you already paid for. The alternative — re-enable the serverless scanner and drive everything through the web app — is *less* aligned with your "runs from named scripts, all config in files, logs to disk" operating rules, and harder to iterate on daily. **Confirm you want the local-scripts approach** (Assumption A1).

---

## 2. Where things live (proposed layout)

Pipeline **code** is public (portfolio); pipeline **output** is personal → git-ignored.

```
job-hunt-command-center/
├── pipeline/                     # NEW — the local pipeline (public code)
│   ├── scan.py     score.py     resume.py     pack.py
│   ├── portal_sync.py           outreach.py   verify_resume.py
│   ├── lib/                      # shared logic, refactored from src/ Lambdas
│   │   ├── sources.py           # ← extracted from src/openings (+ SmartRecruiters, Workable)
│   │   ├── dedupe.py            scoring.py    sponsorship.py   # ← reuse src/api/sponsorship.py
│   │   └── corpus.py            # ← import src/resume_gen/profile.py (the ONE résumé truth)
│   └── seen_jobs.sqlite         # persistent cross-run dedup store (git-ignored)
├── config/                       # NEW — all tunables in files (public)
│   ├── companies.yaml           # target list, seeded from H-1B/LCA filers
│   ├── sources.yaml             # which ATS boards / tokens / Workday tenants
│   └── scoring.yaml             # weights, hard-reject rules, tier thresholds
├── package.json                  # NEW — npm run job:scan → python pipeline/scan.py …
├── applications/  reports/  logs/  cache/    # NEW — OUTPUT, all git-ignored
docs/PIPELINE_PLAN.md            # this file
```

`applications/`, `reports/`, `logs/`, `cache/`, `pipeline/seen_jobs.sqlite`, `config/*.local.*` → added to `.gitignore` (same discipline as `_local/`). **Confirm** personal output stays out of the public repo (Assumption A2).

---

## 3. Schema additions

### 3a. `seen_jobs` (new local SQLite table — Phase 2 dedup)
| column | purpose |
|---|---|
| `canonical_key` (PK) | `norm(company)|norm(title)|norm(location)|reqId` |
| `content_hash` | sha256 of normalized JD body — catches cross-postings |
| `first_seen`, `last_seen` | freshness / 72h window |
| `source`, `url`, `req_id` | provenance |
| `disposition` | `new` / `scored` / `packed` / `in_portal` / `dismissed` |

Dedup is the union of: this table **+** the live portal (any status) **+** the existing `openings-suppress` DynamoDB table (so a "not interested" click still sticks). Nothing reaches a report twice.

### 3b. Portal application record (extend the existing JSON `body`, no table change)
The record is a free-form JSON blob keyed by `appId` — new fields cost nothing:
`fitScore`, `fitReason`, `tier` (A/B/C), `sponsorSignal`, `sourcePlatform`, `reqId`, `resumePath`, `answerPackPath`, `outreachPath`, `contentHash`, `discoveredAt`.

### 3c. **Status vocabulary change (needs your OK — Assumption A3)**
Frontend `STATUSES = ["applied","screen","interview","offer","rejected","ghosted"]`. Your Phase 7 wants entries created as **"Not Applied"** *before* you apply. Proposal: add `"not_applied"` as the new first status (and default), so the pipeline can pre-stage a job and you flip it to `applied` only when you actually submit. This is a one-line frontend edit + a create-default change — but it changes what you see in the portal, so I'm flagging it rather than doing it silently.

---

## 4. Source layer (Phase 1) — what I can realistically hit

**Already coded & working (reuse as-is):** Greenhouse, Ashby, Lever, Workday (per-tenant), Amazon (`amazon.jobs`), GitHub new-grad feeds (Simplify / vanshb03 — note: 🛂 = *does NOT sponsor*, we drop those), Adzuna aggregator (your free key already in `terraform/secrets.auto.tfvars`).

**New, genuinely addable (public JSON, your brief named them):**
- **SmartRecruiters** — `api.smartrecruiters.com/v1/companies/{co}/postings` (public).
- **Workable** — `apply.workable.com/api/v3/accounts/{co}/jobs` (public).

**Browser-only (no API), handle with care:**
- **LinkedIn / Indeed** — no compliant API; ToS forbids bulk scraping and (from your own history) **automated activity risks your account**. Plan: use `claude --chrome` on **logged-in LinkedIn only for reading a page you opened**, never bulk scraping, never auto-actions. Adzuna already surfaces many LinkedIn/Indeed *reposts* legitimately, which covers most of the value without the risk.

**Reliability rules (from your brief, already the pattern in the code):** one failing source never sinks the scan; every failure is logged to `logs/` and surfaced in the report's footer (never silently dropped); raw responses cached to `cache/` with a TTL; rate-limited per host; `robots.txt` respected for any non-API fetch.

---

## 5. `config/companies.yaml` — seeding from real H-1B filers (Phase 1)

You want ~300 sponsor-friendly companies, not 3000 random ones. Plan:
1. Start from the **curated lists already in the code** (`GREENHOUSE`, `ASHBY`, `LEVER`, `WORKDAY`, `BIG_SPONSORS` — already sponsor-vetted).
2. Enrich from **DOL LCA disclosure data** (public H-1B filing records) filtered to your SOC codes (15-1244 Network/Systems, 15-1252 Software, 15-1241 Computer Architects) and to employers with filings in the last 2 years → gives a defensible "has actually sponsored" flag per company.
3. Cross-reference the sweep-confirmed sponsors already in `_local/` (Red Hat, IBM, NVIDIA, Akamai, DigitalOcean, Fidelity, cap-exempt TX universities/hospitals).
4. Each entry: `{name, ats, ats_token, h1b_filings_2yr, cap_exempt, tier_hint}`.

`sponsorship.py` already does a **live per-company H-1B lookup at scoring time**, so `companies.yaml` is the *targeting* list and the live lookup is the *verification* — belt and suspenders.

**Assumption A4:** I'll pull the DOL LCA dataset (public) to build the filing-history flags. Confirm that's fine, or point me at a preferred source (h1bdata.info / MyVisaJobs are in your runbook).

---

## 6. Dedup (Phase 2) & Scoring (Phase 3) — deltas only

Dedup: extends the existing `sig` from `company|title` → `company|title|location|reqId` **plus** a JD `content_hash`, persisted in `seen_jobs`, unioned with portal + suppress table. (§3a)

Scoring: reuse the existing deterministic engine (hard-reject regex, sponsor verdict, 0-100 fit, one-line reason, geo tiers) and **add**:
- **<72h freshness** weight (postedAt is already captured from most sources).
- **A/B/C tiering** + **cap at 40** for the daily report (A = apply today, B = if time, C = stretch).
- Optional Bedrock re-score of the top ~40 for an honest match % (was removed for cost; can be re-enabled *just* for the shortlist, ~pennies/day). **Assumption A5:** deterministic-only (free) vs. AI-shortlist (~$0.05/day) — your call.

---

## 7. Résumé (P4), Answer Pack (P5), Forms (P6), Write-back (P7), Outreach (P8) — deltas

- **P4:** reuse `resume_gen` corpus + Bedrock rewrite + tectonic compile. **New: `verify_resume.py`** — compiles, asserts `pdfinfo` page count **== 2**, measures per-page text-block fill via `pdftotext -bbox` (page 1 ~100% & Skills ends on p1; page 2 90–95% with Projects + Certs), auto-adjusts spacing/content within tolerance, **fails loudly** otherwise. All local tools (`tectonic`, `pdfinfo`, `pdftotext`) are installed. Output → `applications/<company>-<role>-<date>/resume.pdf`.
- **P5:** `pack.py` → `answers.md` (cover letter, "why this company", "what I'd bring", behavioral prompts, researched salary range). Auth/visa/EEO/veteran/disability/criminal/references fields emitted as literal **`HUMAN — DO NOT AUTOFILL`** blocks.
- **P6:** Chrome extension fills fields from the pack; **never clicks Submit** — stops with a verification checklist. You own logins/2FA/CAPTCHA/file-picker.
- **P7:** `portal_sync.py` creates the app via the existing API as **"Not Applied"** with JD/url/source/resumePath/packPath/fitScore/date; idempotent (keyed on `canonical_key` → never double-creates); flips to `Applied` only on your explicit say-so.
- **P8:** `outreach.py` drafts 3–5 people per A-tier job (hiring manager, 2 engineers, UH alumni, recruiter) + <300-char connection notes + follow-ups into `outreach.md`. **Drafts only — never auto-sends** (protects your LinkedIn account).

---

## 8. Named scripts (Phase 0 target for the operating rules)

`package.json` maps your brief's names to the scripts:
`job:scan` → `pipeline/scan.py` · `job:score` → `score.py` · `job:resume <id>` · `job:pack <id>` · `job:portal-sync` · `job:outreach <id>`. All read `config/`, all log to `logs/YYYY-MM-DD-*.log`, all degrade gracefully.

**Daily output:** `reports/YYYY-MM-DD.md` — A/B/C tiers, ≤40 jobs, each row: company · title · location · comp (if listed) · sponsorship signal · fit · one-line reason · apply URL. Every job guaranteed new vs. the last run (via `seen_jobs`).

---

## 9. My honest recommendation on sequencing

Your Phases 1–3 (sourcing) are the parts **most already built and, by your own past finding, the lowest-leverage** — we turned the auto-scraper off because it was noisy. Your Phases 4/5/8 (résumé + answer pack + outreach) are where the "5 apps/week → 8-10/day" jump actually comes from, and outreach is your diagnosed bottleneck (53 cold apps, 0 referrals).

**I'd suggest a thin end-to-end slice first:** re-enable the existing scanner behind `config/`, add SmartRecruiters/Workable + `seen_jobs` dedup + A/B/C report (1–2 days), then spend the real effort on the **résumé verify script + answer pack + outreach generator**. That gets you a working daily loop in days, not weeks, and puts the work where the throughput is. Happy to follow your exact phase order instead — just flagging it.

---

## 10. Decisions (locked 2026-07-29)

- **A1 — Architecture: ✅ LOCKED — local scripts + files that reuse the existing Lambda logic as libraries, writing back to the live portal API.**
- **A3 — Status: ✅ LOCKED — add `"not_applied"` to the portal status vocab as the new first value + pre-stage default.**
- **A5 — Scoring cost: ✅ LOCKED — deterministic-only (free) to start; AI shortlist re-score deferred, can add later.**
- **A6 — Sequencing: ✅ LOCKED — thin end-to-end slice first (§9), then the real effort on résumé-verify + answer-pack + outreach.**

**Proceeding on these defaults (say so if any is wrong):**
- **A2 — Privacy:** pipeline output (`applications/`, `reports/`, `logs/`, `cache/`, `seen_jobs.sqlite`, `config/*.local.*`) is git-ignored; only pipeline code + `config/*.yaml` (public company names, weights) is committed. Same discipline as `_local/`.
- **A4 — Sponsorship data:** seed `companies.yaml` from the existing curated sponsor lists + `_local/` sweep-confirmed sponsors first; enrich with the public DOL LCA disclosure dataset for filing-history flags. Live per-company H-1B lookup (`sponsorship.py`) remains the verification at scoring time.
- **A7 — Contact sourcing (P8):** people-lists are drafted from pages *you* open in `claude --chrome` + public sources; **no bulk LinkedIn scraping, no auto-connect/auto-send** — you send everything manually.

**Next: build Phase 1 (thin slice) — pending your explicit "go".**
