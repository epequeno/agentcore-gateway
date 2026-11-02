"""Phase 2 evidence for AgentCore Identity over an MCP server target.

1. An unauthenticated MCP request is rejected by the Gateway.
2. A Cognito access token opens the Gateway and lists the MCP target's tool.
3. The agent calls the MCP tool; API Gateway validates the passed-through JWT
   and the tool returns the Cognito principal.
4. ``--revoke`` deletes the Gateway target (a control-plane change, no Runtime
   redeploy) and confirms the tool is gone from the catalog.
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
import uuid

import boto3

from agent.config import settings

CRM_TOOL = "crm-mcp___getAccount"


def access_token() -> str:
    """Mint the test user's Cognito access token without a browser."""
    missing = [
        name
        for name, value in (
            ("COGNITO_USER_POOL_ID", settings.user_pool_id),
            ("COGNITO_CLIENT_ID", settings.cognito_client_id),
            ("IDENTITY_TEST_PASSWORD", settings.identity_password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"set {', '.join(missing)}")

    client = boto3.client("cognito-idp", region_name=settings.region)
    response = client.admin_initiate_auth(
        UserPoolId=settings.user_pool_id,
        ClientId=settings.cognito_client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": settings.identity_user,
            "PASSWORD": settings.identity_password,
        },
    )
    return response["AuthenticationResult"]["AccessToken"]


def token_subject(token: str) -> str:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return claims["sub"]


def mcp_url() -> str:
    url = settings.gateway_url.rstrip("/")
    return url if url.endswith("/mcp") else f"{url}/mcp"


def _post_mcp(token: str | None, body: dict) -> tuple[int, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
        "x-amzn-bedrock-agentcore-policy-session-id": f"identity-{uuid.uuid4().hex[:8]}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        mcp_url(),
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def tools_with_token(token: str) -> list[str]:
    from strands.tools.mcp import MCPClient

    client = MCPClient(
        url=mcp_url(),
        headers={
            "Authorization": f"Bearer {token}",
            "x-amzn-bedrock-agentcore-policy-session-id": f"identity-{uuid.uuid4().hex[:8]}",
        },
    )
    with client:
        return [tool.tool_name for tool in client.list_tools_sync()]


def invoke_agent(prompt: str, token: str) -> str:
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=settings.runtime_arn,
        runtimeSessionId=f"identity-{uuid.uuid4().hex}",
        payload=json.dumps({"prompt": prompt, "authToken": token, "actorId": "identity-check"}),
    )
    stream = response["response"]
    raw = stream.read() if hasattr(stream, "read") else b"".join(stream)
    result = json.loads(raw.decode())
    if "error" in result:
        raise RuntimeError(result["error"])
    return result.get("response", "")


def revoke_target() -> str:
    """Delete the MCP target; the Gateway catalog changes without a redeploy."""
    control = boto3.client("bedrock-agentcore-control", region_name=settings.region)
    items = control.list_gateway_targets(gatewayIdentifier=settings.gateway_id)["items"]
    target = next(item for item in items if item["name"] == "crm-mcp")
    control.delete_gateway_target(
        gatewayIdentifier=settings.gateway_id, targetId=target["targetId"]
    )
    return target["targetId"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--revoke", action="store_true", help="delete the crm-mcp target and re-check"
    )
    args = parser.parse_args(argv)

    if not settings.gateway_url:
        print("AGENTCORE_GATEWAY_URL is required", file=sys.stderr)
        return 2

    token = access_token()
    subject = token_subject(token)

    status, body = _post_mcp(None, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    print(f"unauthenticated tools/list: HTTP {status}")
    if status not in (400, 401, 403):
        print("FAIL: the Gateway accepted an unauthenticated request", file=sys.stderr)
        return 1

    names = tools_with_token(token)
    print(f"authenticated tools/list: {len(names)} tools: {', '.join(names)}")
    if CRM_TOOL not in names:
        print(f"FAIL: {CRM_TOOL} is not in the catalog", file=sys.stderr)
        return 1

    response = invoke_agent(
        "Use the CRM tool to look up the signed-in user's account. "
        "Report the principal and account exactly as the tool returned them.",
        token,
    )
    print(f"agent response: {response}")
    if subject not in response:
        print(
            f"FAIL: the token subject {subject} did not reach the tool response",
            file=sys.stderr,
        )
        return 1

    if args.revoke:
        if not settings.gateway_id:
            print("AGENTCORE_GATEWAY_ID is required for --revoke", file=sys.stderr)
            return 2
        target_id = revoke_target()
        print(f"deleted crm-mcp target {target_id}")
        after = tools_with_token(token)
        print(f"after revocation: {', '.join(after)}")
        if CRM_TOOL in after:
            print("FAIL: the tool survived revocation", file=sys.stderr)
            return 1
        print("revocation removed the tool without redeploying the Runtime")

    print("identity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
