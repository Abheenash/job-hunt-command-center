"""Classify task — first state of the 'process-email' Step Functions workflow.

Amazon Bedrock (Claude Haiku) reads one email and returns structured triage JSON
(is it job-related, what category, plus extracted recruiter / pay / location /
interview-date). If Bedrock is unavailable it degrades to a keyword classifier so
the pipeline never hard-fails on a model hiccup — the message still flows to Enrich.

Input  (state input): the email object {from, subject, snippet, eventId, ...}
Output (state result): {"msg": <email>, "classification": <triage JSON>}
"""
import json
import os
import re

import boto3

from classifier import classify  # keyword fallback if Bedrock is unavailable

bedrock = boto3.client("bedrock-runtime")
BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Bedrock reads the whole email and decides what it's actually about.
AI_CLASSIFY_SYSTEM = (
    "You triage one email for a job-application tracker. Read it and judge whether "
    "it is genuinely about THIS person's job search — i.e. an application confirmation, "
    "a recruiter reaching out, an interview invite/scheduling, a job offer, or a "
    "rejection. Newsletters, order receipts, bills, promotions, security alerts, "
    "CI/CD or app notifications, and personal mail are NOT job-related, even if they "
    "contain words like 'offer', 'application', or 'opportunity'. Also EXTRACT any "
    "details useful for a tracker (recruiter, pay, location, interview date). Use "
    "empty strings when absent — never invent. Reply with ONLY a compact JSON object, "
    "no prose: "
    '{"jobRelated":bool,"category":"interview"|"offer"|"rejection"|"recruiter_reply"'
    '|"confirmation"|"other","company":str,"role":str,"summary":str (one short sentence),'
    '"confidence":number 0-1,"recruiterName":str (the sender/signer person, or ""),'
    '"salary":str (any compensation/pay stated, or ""),"location":str,'
    '"workMode":"Remote"|"Hybrid"|"On-site"|"","interviewDate":str '
    "(YYYY-MM-DD only if a specific interview date is stated, else \"\"),"
    '"nextAction":str (what the candidate should do next, short, or "")}'
)


def handler(event, _ctx):
    msg = event  # the workflow input IS the email object
    return {"msg": msg, "classification": _ai_classify(msg)}


def _ai_classify(msg):
    payload = {
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 300,
        "system": AI_CLASSIFY_SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text",
            "text": f"FROM: {msg.get('from', '')}\nSUBJECT: {msg.get('subject', '')}\n\nBODY:\n{(msg.get('snippet') or '')[:1500]}"}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
        text = json.loads(resp["body"].read())["content"][0]["text"]
        return json.loads(_first_json(text))
    except Exception as e:  # noqa: BLE001 — degrade to keyword classifier
        print(f"ai_classify fell back to keywords ({type(e).__name__}: {e})")
        cat, conf, _ = classify(msg.get("subject", ""), msg.get("snippet", ""), msg.get("from", ""))
        return {"jobRelated": cat != "other", "category": cat, "company": "",
                "summary": msg.get("subject", ""), "confidence": conf}


def _first_json(text):
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else "{}"
