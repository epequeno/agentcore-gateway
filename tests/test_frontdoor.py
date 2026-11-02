import json
from types import SimpleNamespace

from evals import frontdoor


class FakePrepared:
    def __init__(self, url, body):
        self.url = url
        self.body = body
        self.headers = {"Content-Type": "application/json"}


class FakeAWSRequest:
    def __init__(self, method, url, data, headers):
        self.url = url
        self.body = data

    def prepare(self):
        return FakePrepared(self.url, self.body)


class FakeStream:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self.payload


def test_frontdoor_sends_bytes(monkeypatch):
    seen = {}

    class FakeSession:
        def get_credentials(self):
            return object()

    class FakeSigner:
        def __init__(self, credentials, service, region):
            pass

        def add_auth(self, request):
            pass

    def fake_urlopen(request, timeout):
        seen["data"] = request.data
        return FakeStream(json.dumps({"response": "ok"}).encode())

    monkeypatch.setattr(frontdoor, "AWSRequest", FakeAWSRequest)
    monkeypatch.setattr(frontdoor, "SigV4Auth", FakeSigner)
    monkeypatch.setattr(frontdoor.boto3, "Session", FakeSession)
    monkeypatch.setattr(frontdoor.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        frontdoor,
        "settings",
        SimpleNamespace(front_gateway_url="https://example.com", region="us-east-1"),
    )

    result = frontdoor.invoke_runtime("prompt", "token", "s" * 33, "actor")

    assert result["response"] == "ok"
    assert isinstance(seen["data"], bytes)
