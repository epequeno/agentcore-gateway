"""MCP toolbox that keeps AgentCore Code Interpreter and Browser behind Gateway.

The Gateway treats this as an ordinary MCP server target. The runtime owns no
tool policy: it exposes two narrow operations, and the Gateway's authorizer,
interceptor and policy engine remain the only path to them.
"""

import json
import os
import time

import websockets.sync.client as ws_client
from bedrock_agentcore.tools import browser_session, code_session
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

REGION = os.environ.get("AWS_REGION", "us-east-1")

mcp = FastMCP(
    name="agentcore-toolbox",
    instructions="Sandbox tools exposed only through the AgentCore Gateway.",
    host="0.0.0.0",
    port=8000,
    stateless_http=True,
)


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request):
    return JSONResponse({"status": "healthy"})


class _CDP:
    """Minimal Chrome DevTools Protocol client for the managed browser."""

    def __init__(self, socket):
        self.socket = socket
        self.next_id = 0

    def call(self, method, params=None, session_id=None):
        self.next_id += 1
        request_id = self.next_id
        message = {"id": request_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self.socket.send(json.dumps(message))
        while True:
            reply = json.loads(self.socket.recv(timeout=30))
            if reply.get("id") != request_id:
                continue
            if "error" in reply:
                raise RuntimeError(f"{method}: {reply['error']}")
            return reply.get("result") or {}


def _connect_headers(headers):
    """Drop handshake headers websockets sets itself; keep SigV4 headers."""
    drop = {
        "host",
        "upgrade",
        "connection",
        "sec-websocket-version",
        "sec-websocket-key",
    }
    return {key: value for key, value in headers.items() if key.lower() not in drop}


def _browse(url):
    with browser_session(REGION) as browser:
        ws_url, headers = browser.generate_ws_headers()
        with ws_client.connect(
            ws_url,
            additional_headers=_connect_headers(headers),
            proxy=None,
            open_timeout=30,
        ) as socket:
            cdp = _CDP(socket)
            targets = cdp.call("Target.getTargets").get("targetInfos") or []
            page = next((target for target in targets if target.get("type") == "page"), None)
            if page:
                target_id = page["targetId"]
            else:
                target_id = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]

            attached = cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
            session_id = attached["sessionId"]
            cdp.call("Page.enable", session_id=session_id)
            cdp.call("Runtime.enable", session_id=session_id)
            cdp.call("Page.navigate", {"url": url}, session_id=session_id)

            expression = (
                "JSON.stringify({title: document.title, url: location.href, "
                "text: document.body ? document.body.innerText.slice(0, 2000) : ''})"
            )
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                result = cdp.call(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True},
                    session_id=session_id,
                ).get("result", {})
                value = result.get("value")
                if value:
                    page_state = json.loads(value)
                    if page_state.get("url") not in ("", "about:blank"):
                        return page_state
                time.sleep(0.5)
            raise TimeoutError(f"browser did not finish navigating to {url}")


@mcp.tool()
def execute_python(code: str) -> str:
    """Execute Python in an isolated AgentCore Code Interpreter session."""
    with code_session(REGION) as client:
        result = client.execute_code(code)
        events = [event.get("result", event) for event in result.get("stream", [])]
    return json.dumps({"sessionId": result.get("sessionId"), "events": events}, default=str)


@mcp.tool()
def browse_page(url: str) -> str:
    """Open an HTTPS URL in AgentCore Browser and return its title and visible text."""
    return json.dumps(_browse(url))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
