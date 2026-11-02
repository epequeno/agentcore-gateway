"""Invoke the IAM-authenticated Runtime front door used by A/B tests.

A Gateway runtime target is an HTTP endpoint, not an MCP endpoint, and the
front door uses AWS_IAM. ``invoke_gateway`` is not in the installed boto3
service model, so sign a normal HTTP request with SigV4. The A/B test assigns
a configuration-bundle variant from the session header on this request.
"""

import json
import urllib.error
import urllib.request
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from agent.config import settings


def invoke_runtime(
    prompt: str,
    token: str,
    session_id: str,
    actor_id: str,
) -> dict[str, Any]:
    """POST one prompt through the front door and return the Runtime payload."""
    if not settings.front_gateway_url:
        raise RuntimeError("AGENTCORE_FRONT_GATEWAY_URL is not set")

    url = f"{settings.front_gateway_url.rstrip('/')}/agent/invocations"
    body = json.dumps(
        {
            "prompt": prompt,
            "authToken": token,
            "actorId": actor_id,
            "sessionId": session_id,
        }
    )
    request = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
    )
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials in the current session")
    SigV4Auth(credentials, "bedrock-agentcore", settings.region).add_auth(request)
    prepared = request.prepare()

    body_bytes = prepared.body
    if isinstance(body_bytes, str):
        body_bytes = body_bytes.encode("utf-8")
    http_request = urllib.request.Request(
        prepared.url,
        data=body_bytes,
        headers=dict(prepared.headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"front door returned {exc.code}: {detail}") from exc

    result = json.loads(raw)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result
