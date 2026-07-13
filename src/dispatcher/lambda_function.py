"""Dispatcher Lambda — the SQS -> Step Functions bridge with failure isolation.

Triggered by the email-processing queue (SQS event source). For each message it
runs the 'process-email' Express workflow synchronously; on any failure it reports
that single message back to SQS (partial-batch-response) so only the failed one is
retried and — after maxReceiveCount — moved to the DLQ. The rest of the batch is
deleted normally. This is the piece that turns "buffered in SQS" into "reliably
processed exactly-enough-times, poison messages quarantined."
"""
import json
import os

import boto3

sfn = boto3.client("stepfunctions")
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]


def handler(event, _ctx):
    failures = []
    for rec in event.get("Records", []):
        mid = rec.get("messageId")
        try:
            # The SQS body is already the email JSON; pass it straight through as
            # the Express execution input (start_sync_execution wants a JSON string).
            r = sfn.start_sync_execution(stateMachineArn=STATE_MACHINE_ARN, input=rec["body"])
            if r.get("status") != "SUCCEEDED":
                raise RuntimeError(f"workflow {r.get('status')}: {r.get('error')} / {r.get('cause')}")
        except Exception as e:  # noqa: BLE001 — isolate this message, keep the batch moving
            print(f"dispatch failed for {mid}: {type(e).__name__}: {e}")
            failures.append({"itemIdentifier": mid})
    return {"batchItemFailures": failures}
