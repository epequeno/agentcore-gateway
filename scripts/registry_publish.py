"""Submit the stack's registry records for approval.

CloudFormation creates a ``CfnRegistryRecord`` in DRAFT. The registry is
configured with ``APPROVE_ALL``, so one submit moves an approved record to
APPROVED and makes it discoverable. This is the admin-side publish step, kept
separate from ``evals/registry.py`` so that the acceptance check only exercises
the consumer/data-plane API.
"""

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

RECORD_NAMES = {"agentcore-gateway-agent", "agentcore-gateway-tools"}


def _records(client, registry_arn: str) -> list[dict]:
    records = []
    for page in client.get_paginator("list_registry_records").paginate(registryId=registry_arn):
        records.extend(page.get("registryRecords", []))
    return records


def main() -> int:
    registry_arn = os.environ.get("AGENTCORE_REGISTRY_ARN", "")
    if not registry_arn:
        print("AGENTCORE_REGISTRY_ARN is required", file=sys.stderr)
        return 2

    client = boto3.client(
        "agent-registry-control", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    try:
        records = _records(client, registry_arn)
    except ClientError as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    found = {record["name"] for record in records}
    if not RECORD_NAMES.issubset(found):
        print(
            f"FAIL: missing records: {', '.join(sorted(RECORD_NAMES - found))}",
            file=sys.stderr,
        )
        return 1

    for record in records:
        if record["name"] not in RECORD_NAMES:
            continue
        status = record.get("status")
        if status == "APPROVED":
            print(f"{record['name']}: already APPROVED")
            continue
        if status != "DRAFT":
            print(f"FAIL: {record['name']} is {status}, expected DRAFT", file=sys.stderr)
            return 1
        client.submit_registry_record_for_approval(
            registryId=registry_arn, recordId=record["recordId"]
        )
        for _ in range(30):
            current = client.get_registry_record(
                registryId=registry_arn, recordId=record["recordId"]
            )["status"]
            if current == "APPROVED":
                print(f"{record['name']}: submitted and APPROVED")
                break
            if current in ("REJECTED", "CREATE_FAILED"):
                print(f"FAIL: {record['name']} became {current}", file=sys.stderr)
                return 1
            time.sleep(1)
        else:
            print(f"FAIL: {record['name']} did not reach APPROVED", file=sys.stderr)
            return 1

    print("registry records published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
