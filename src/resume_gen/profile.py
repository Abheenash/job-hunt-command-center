"""The candidate corpus — the ONLY source of truth the résumé generator may use.

Everything here is real (extracted from the master résumé + portfolio). The
generator selects, reorders, and lightly rephrases from this data to fit a JD; it
must never invent experience, skills, dates, or metrics not present below. The
Lambda renders LaTeX deterministically from a structured AI selection, so the
model chooses *what* to include — it never writes raw LaTeX or free facts.
"""

CONTACT = {
    "name": "Rajolu Abheenash",
    "location": "Houston, TX",
    "phone": "832-891-4093",
    "email": "abheenash007@gmail.com",
    "site": "abheenash.com",
    "github": "github.com/Abheenash",
    "linkedin": "linkedin.com/in/abheenash",
}

# A base summary + the raw ingredients the model may re-emphasize per JD. The model
# returns a tailored summary, but only using facts present here.
SUMMARY_BASE = (
    "AWS Certified Solutions Architect -- Associate and Cloud Practitioner with an "
    "M.S. in Computer \\& Systems Engineering and professional experience as a DevOps "
    "Engineer in AWS cloud operations (HCLTech). Supported a production containerized "
    "platform on AWS --- weekly on-call incident response, Terraform migrations, and "
    "CI/CD automation --- and designed, built, and deployed production-grade AWS "
    "projects, each provisioned end-to-end with Terraform and shipped through "
    "security-gated CI/CD. Backed by a systems foundation in C++, multithreading, and "
    "performance engineering."
)

EDUCATION = [
    {
        "school": "University of Houston", "loc": "Houston, TX",
        "degree": "Master of Science in Computer \\& Systems Engineering; GPA: 3.60",
        "date": "Dec. 2025",
        "detail": "\\textbf{Relevant Coursework:} Advanced Hardware Design, Advanced Computer Architecture, VLSI Design, Principles of Internetworking, Introduction to Cybersecurity, Open Systems, Engineering Management",
    },
    {
        "school": "VRS \\& YRN College of Engineering and Technology", "loc": "Chirala, India",
        "degree": "Bachelor of Technology in Computer Science and Engineering",
        "date": "Apr. 2023", "detail": None,
    },
]

EXPERIENCE = [
    {
        "company": "HCLTech", "loc": "Hyderabad, India",
        "title": "DevOps Engineer --- AWS Cloud Operations", "date": "Apr. 2022 -- Dec. 2023",
        "bullets": [
            "Supported a US client's containerized B2B platform across development, staging, and production AWS environments --- multiple ECS Fargate services behind an Application Load Balancer, with RDS, Route 53, and Linux hosts --- troubleshooting incidents across compute, load balancing, databases, IAM, VPC networking, and the OS layer.",
            "Held a weekly on-call rotation: triaged CloudWatch and PagerDuty alerts, assessed customer impact, executed documented rollbacks and recoveries, and authored root-cause analyses for production incidents.",
            "Rebuilt a half-manual release process into an automated GitHub Actions pipeline --- tests, security scanning, image versioning, staged deploys, and a gated production rollout with automatic rollback --- shortening deployments and removing console-based production changes.",
            "Migrated manually-created AWS resources into reusable Terraform modules with remote state, state locking, pull-request plans, and drift detection --- ending configuration drift and making environments rebuildable from code.",
            "Improved observability by replacing static-threshold alarms with golden-signal and composite service-health alarms, each tied to a runbook --- cutting non-actionable alert noise and speeding triage.",
            "Automated recurring operations with Python, Boto3, Lambda, EventBridge, and Systems Manager (patch-compliance reporting, non-production scheduling, resource-health checks), and remediated IAM, encryption, and configuration findings from AWS Config, Inspector, GuardDuty, and Security Hub.",
        ],
    },
]

# Skill categories. The model returns an ordering of category keys (most JD-relevant
# first) and may drop categories that don't fit — but never adds skills not listed.
SKILLS = {
    "Cloud (AWS)": "Lambda, API Gateway, S3, DynamoDB, Cognito, ECS Fargate, EKS, ECR, EC2 \\& Auto Scaling, RDS, VPC, ALB, CloudFront, Route 53, KMS, Secrets Manager, IAM, WAF, CloudWatch, X-Ray, Synthetics, SNS, SES, EventBridge, Step Functions, SQS, Systems Manager (SSM), CloudTrail, GuardDuty, Config, Inspector, Security Hub, Amazon Bedrock",
    "GenAI / LLM": "Amazon Bedrock (Claude), prompt engineering, context-stuffing \\& prompt caching, structured / JSON extraction, RAG-style Q\\&A over private data, prompt-injection defense, LLM-in-the-loop automation",
    "Infrastructure as Code \\& CI/CD": "Terraform (official \\& custom modules, remote state \\& locking, drift detection, brownfield import), GitHub Actions, OIDC (keyless auth), branch protection, automated deployments \\& rollbacks",
    "DevSecOps \\& Security": "IAM least privilege, KMS/SSE encryption, client-side cryptography (WebCrypto / AES-256-GCM), zero-knowledge design, JWT / Cognito auth, Secrets Manager \\& rotation, AWS WAF, VPC isolation (private subnets, VPC endpoints), Checkov, tfsec, Trivy, gitleaks, TLS",
    "Containers, Kubernetes \\& Observability": "Docker, ECS Fargate, ECR, Kubernetes (Amazon EKS, HPA autoscaling, Helm, IRSA, ALB Ingress, Metrics Server), CloudWatch (dashboards, alarms, Logs Insights, RUM), X-Ray tracing, Synthetics canaries, SLOs \\& error budgets, on-call incident response, runbooks \\& RCAs, fault-injection drills, backup/restore testing",
    "Programming / Scripting": "Python (Boto3), Bash/Shell, SQL, JavaScript, C, C++",
    "Systems Programming": "C++ multithreading (std::thread, mutexes, condition variables), OpenMP, POSIX sockets (TCP), producer--consumer \\& thread-pool design, CMake, performance analysis (compute- vs.\\ memory-bound), race-free shared-state design",
    "Networking \\& Systems": "VPC, Subnetting, Routing, DNS, TCP/IP, Load Balancing, Linux/Unix",
    "Web \\& Databases": "React, Node.js, Flask, MySQL, MongoDB, REST APIs",
}

CERTIFICATIONS = [
    "\\textbf{AWS Certified Solutions Architect -- Associate (SAA-C03)} --- Amazon Web Services $\\cdot$ \\href{https://www.credly.com/badges/e499fee9-1b8b-4fce-a65c-bc4ddcb2f8b9/public_url}{\\underline{Verify}}",
    "\\textbf{AWS Certified Cloud Practitioner (CLF-C02)} --- Amazon Web Services $\\cdot$ \\href{https://www.credly.com/badges/a9a04423-7b7b-4e75-99c9-8edb3488d9cb/public_url}{\\underline{Verify}}",
    "\\textbf{NPTEL} --- Social Network Analysis (IIT Madras)",
]

# Projects, value-ranked (rank 1 = highest). Each carries the tech line, bullets, and
# links. The model picks the 4 most relevant to the JD and orders them; it may lightly
# rephrase bullets to surface JD-relevant angles but must not add facts.
PROJECTS = [
    {
        "id": "job-hunt-command-center", "rank": 1, "name": "Job Hunt Command Center",
        "tech": "Amazon Bedrock (Claude), Cognito, Lambda, API Gateway, DynamoDB, S3, SQS, Step Functions, EventBridge, Terraform",
        "domains": ["genai", "serverless", "full-stack", "event-driven", "security"],
        "link_code": "https://github.com/Abheenash/job-hunt-command-center", "link_live": None,
        "bullets": [
            "Built an AI-powered, full-stack serverless job-application tracker behind a private Cognito login (API Gateway JWT authorizer $\\rightarrow$ Lambda $\\rightarrow$ DynamoDB + versioned S3 with presigned document snapshots), provisioned entirely in Terraform and shipped through a DevSecOps CI/CD pipeline (gitleaks, Checkov/tfsec, Trivy).",
            "Integrated \\textbf{Amazon Bedrock (Claude)} in an event-driven pipeline (EventBridge $\\rightarrow$ scanner $\\rightarrow$ SQS+DLQ $\\rightarrow$ dispatcher $\\rightarrow$ Step Functions): reads the inbox read-only, classifies recruiter replies / interviews / rejections, and \\textbf{auto-advances and enriches} the matching application; added JD$\\leftrightarrow$résumé match scoring, JD extraction, and a natural-language ``Ask-AI'' Q\\&A.",
            "Made a defensible auth trade-off (IMAP + Google App Password over Gmail OAuth, whose Testing-mode restricted-scope refresh tokens expire every 7 days); least-privilege per-Lambda IAM, credential in Secrets Manager, and a human-in-the-loop design (never auto-applies).",
        ],
    },
    {
        "id": "serverless-file-share", "rank": 2, "name": "Serverless File Share (Zero-Knowledge)",
        "tech": "WebCrypto / AES-256-GCM, Lambda, API Gateway, S3, DynamoDB, KMS, Terraform",
        "domains": ["security", "serverless", "cryptography"],
        "link_code": "https://github.com/Abheenash/serverless-file-share", "link_live": "https://share.abheenash.com",
        "bullets": [
            "Designed a \\textbf{zero-knowledge}, self-destructing file / secret sharing app: files are encrypted in the browser with AES-256-GCM (WebCrypto) before upload and the key lives only in the share link's URL fragment --- so S3, the Lambdas, and the operator only ever hold ciphertext (even the filename is sealed). Live at share.abheenash.com.",
            "Enforced least privilege end-to-end (one scoped IAM role per Lambda, Block Public Access, HTTPS-only, short-lived presigned URLs) with SSE-KMS as defense-in-depth, and automatic expiry via DynamoDB TTL $\\rightarrow$ Streams $\\rightarrow$ ``reaper'' Lambda.",
            "Codified $\\sim$38 resources as Terraform with a keyless-OIDC GitHub Actions pipeline; diagnosed and fixed SSE-KMS uploads failing until forced to AWS SigV4 and a DynamoDB Streams ``LATEST'' race.",
        ],
    },
    {
        "id": "aws-eks-platform", "rank": 3, "name": "AWS EKS Platform",
        "tech": "Amazon EKS / Kubernetes, Terraform, ALB Ingress, IRSA, HPA, GitHub Actions (OIDC)",
        "domains": ["kubernetes", "containers", "platform", "iac"],
        "link_code": "https://github.com/Abheenash/aws-eks-platform", "link_live": None,
        "bullets": [
            "Provisioned a production-shaped Kubernetes platform on \\textbf{Amazon EKS} entirely in Terraform (official VPC + EKS modules): a managed node group, IRSA/OIDC, the AWS Load Balancer Controller (ALB Ingress via IRSA), Metrics Server, and a CPU Horizontal Pod Autoscaler.",
            "Deployed through a keyless GitHub Actions pipeline (OIDC $\\rightarrow$ ECR $\\rightarrow$ rollout) and ran it as a cost-controlled build $\\rightarrow$ prove $\\rightarrow$ destroy loop.",
            "Proved resilience with live drills: a deleted pod self-healed back to full capacity in $\\sim$7 seconds, and the HPA scaled 2 $\\rightarrow$ 6 pods under CPU load in $\\sim$60 seconds --- documented with honest findings (e.g., ALB deregistration behavior).",
        ],
    },
    {
        "id": "secure-container-pipeline", "rank": 4, "name": "Secure Container Pipeline",
        "tech": "ECS Fargate, VPC, WAF, Terraform, GitHub Actions",
        "domains": ["devsecops", "containers", "security", "cicd"],
        "link_code": "https://github.com/Abheenash/secure-container-pipeline", "link_live": None,
        "bullets": [
            "Built a GitHub Actions pipeline with three fail-the-build security gates --- gitleaks (secrets), Checkov/tfsec (IaC misconfiguration), and Trivy (image + dependency CVEs) --- enforced by branch protection; proved it by automatically blocking a PR that introduced a hardcoded AWS credential.",
            "Provisioned the full stack in Terraform: ECS Fargate in private subnets using VPC endpoints instead of a NAT gateway (lower cost, no internet egress), an ALB fronted by AWS WAF, DynamoDB, and Secrets Manager, with a least-privilege IAM role per task.",
            "Remediated real supply-chain CVEs surfaced by the pipeline and hardened the container (non-root, read-only root filesystem, HTTPS behind WAF managed + rate-based rules).",
        ],
    },
    {
        "id": "cloud-observability-sre", "rank": 5, "name": "Cloud Observability \\& Incident Response",
        "tech": "CloudWatch, X-Ray, Synthetics, RUM, SNS, Terraform",
        "domains": ["observability", "sre", "serverless"],
        "link_code": "https://github.com/Abheenash/cloud-observability-sre", "link_live": None,
        "bullets": [
            "Instrumented a live serverless service (API Gateway $\\rightarrow$ Lambda $\\rightarrow$ DynamoDB) with a CloudWatch golden-signals dashboard, X-Ray tracing, SLOs with an error budget, CloudWatch RUM, and saved Logs Insights queries --- all in Terraform.",
            "Built SLO-based alarms (5xx, p95 latency, per-function errors) plus a composite service-health alarm wired to SNS and mapped to an incident runbook, with an outside-in CloudWatch Synthetics canary.",
            "Validated it with a live failure-injection drill: throttled a Lambda to zero concurrency to induce a real outage; the alarm detected it in $\\sim$60 seconds and the service was recovered via the runbook.",
        ],
    },
    {
        "id": "aws-cloudops-lab", "rank": 6, "name": "AWS Cloud Operations \\& Recovery Lab",
        "tech": "EC2, ALB, RDS, CloudWatch, SSM, Terraform, Boto3",
        "domains": ["sre", "operations", "iac", "resilience"],
        "link_code": "https://github.com/Abheenash/aws-cloudops-lab", "link_live": None,
        "bullets": [
            "Provisioned a production-shaped stack in Terraform --- an EC2 Auto Scaling Group behind an ALB with an RDS Postgres backend --- plus a golden-signals dashboard, five alarms and a composite service-health alarm, and CloudWatch-agent log shipping.",
            "Authored the operations layer: a runbook per alarm, five incident drill plans, a timed RDS restore-test plan, an opt-in security baseline (GuardDuty, Config, Inspector, Security Hub), and Boto3/Lambda automation for patch compliance and resource health.",
            "Executed the full program live: five incident drills (5xx alarm in 177s, latency in 289s, DB-dependency in 166s), a timed RDS restore test (measured 6m36s RTO vs.\\ a 60-min target), and a Terraform brownfield import with drift detection --- then destroyed the stack.",
        ],
    },
    {
        "id": "portfolio-ai-assistant", "rank": 7, "name": "Portfolio AI Assistant",
        "tech": "Amazon Bedrock (Claude Haiku), Lambda, API Gateway, Terraform",
        "domains": ["genai", "serverless"],
        "link_code": "https://github.com/Abheenash/portfolio-ai-assistant", "link_live": None,
        "bullets": [
            "Built a serverless GenAI chatbot (API Gateway $\\rightarrow$ Lambda $\\rightarrow$ Amazon Bedrock, Claude Haiku) that answers recruiter questions about my background, grounded only in a curated knowledge base --- deployed live as a chat widget on my portfolio.",
            "Chose context-stuffing with Bedrock prompt caching over a vector database (right-sized for a few-KB corpus), and hardened it with an IAM role scoped to a single model, a grounded system prompt that refuses off-topic and prompt-injection attempts, input/output caps, and API throttling.",
        ],
    },
    {
        "id": "parallel-thread-pool", "rank": 8, "name": "Parallel Thread Pool",
        "tech": "C++, std::thread, mutexes, condition variables",
        "domains": ["systems", "cpp", "concurrency"],
        "link_code": "https://github.com/Abheenash/parallel-thread-pool", "link_live": None,
        "bullets": [
            "Implemented a C++ thread pool with persistent workers pulling from a mutex-protected task queue (condition-variable sleep for idle workers, graceful drain-and-join shutdown); benchmarked \\textbf{5.2$\\times$ speedup at 8 threads} on compute-bound workloads via scaling analysis.",
        ],
    },
    {
        "id": "parallel-heat-diffusion", "rank": 9, "name": "Parallel Heat Diffusion",
        "tech": "C++, std::thread, OpenMP",
        "domains": ["systems", "cpp", "concurrency", "hpc"],
        "link_code": "https://github.com/Abheenash/parallel-heat-diffusion", "link_live": None,
        "bullets": [
            "Parallelized a 2D finite-difference heat-diffusion simulation with both std::thread and OpenMP; achieved \\textbf{$\\sim$2.3$\\times$ speedup}, characterized it as memory-bound through scaling analysis, and proved it race-free with a checksum.",
        ],
    },
    {
        "id": "concurrent-kv-store", "rank": 10, "name": "Concurrent Key-Value Store",
        "tech": "C++, POSIX sockets, TCP, pthreads",
        "domains": ["systems", "cpp", "concurrency", "networking"],
        "link_code": "https://github.com/Abheenash/concurrent-kv-store", "link_live": None,
        "bullets": [
            "Built a multithreaded TCP key-value store in C++ on raw POSIX sockets (thread-per-connection) with a mutex-protected shared map, so concurrent clients see consistent, immediately-visible state.",
        ],
    },
]


def project_by_id(pid):
    for p in PROJECTS:
        if p["id"] == pid:
            return p
    return None
