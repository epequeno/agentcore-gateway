"""Deterministic code-based evaluator, deployed as a Lambda by the CDK stack.

AgentCore Evaluations invokes this with the session's spans and the ground-truth
assertions supplied at batch-evaluation time. It checks the assertions without a
judge model, so it is the control that proves the pipeline can fail: the seeded
violation in ``evals/sessions.jsonl`` (scenario ``s11``) must come back FAIL.

No third-party imports: the Lambda runtime has no AgentCore SDK, and the
contract is small enough to parse here.
"""

import json

CHOICE_EVENT = "gen_ai.choice"


def _unwrap(value):
    """Unwrap OTLP typed values (``{"stringValue": "x"}``) when present."""
    if isinstance(value, dict):
        for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if key in value:
                return value[key]
    return value


def _spans(event):
    return (event.get("evaluationInput") or {}).get("sessionSpans") or []


def _attributes(span):
    return {k: _unwrap(v) for k, v in (span.get("attributes") or {}).items()}


def _end_time(span):
    return span.get("endTimeUnixNano") or span.get("endTime") or 0


def _event_attributes(item):
    attrs = item.get("attributes") or {}
    return {k: _unwrap(v) for k, v in attrs.items()}


def _message_text(value):
    """Normalise a Strands content value into plain text."""
    if isinstance(value, (list, dict)):
        if isinstance(value, dict) and "text" in value:
            return str(value["text"])
        if isinstance(value, list):
            return "".join(_message_text(v) for v in value)
        if isinstance(value, dict) and "content" in value:
            return _message_text(value["content"])
    if isinstance(value, str):
        try:
            return _message_text(json.loads(value))
        except (ValueError, TypeError):
            return value
    return str(value) if value is not None else ""


def _final_response(spans):
    """Return the assistant text from the last choice event in the session."""
    choices = []
    for span in spans:
        for item in span.get("events") or span.get("spanEvents") or []:
            if item.get("name") != CHOICE_EVENT:
                continue
            message = _event_attributes(item).get("message")
            if message:
                choices.append((_end_time(span), _message_text(message)))
        attrs = _attributes(span)
        for key in ("gen_ai.output.messages", "gen_ai.completion"):
            if attrs.get(key):
                choices.append((_end_time(span), _message_text(attrs[key])))
    if not choices:
        return ""
    choices.sort(key=lambda pair: pair[0])
    return choices[-1][1]


def _assertions(event):
    texts = []
    for entry in event.get("evaluationReferenceInputs") or []:
        for assertion in entry.get("assertions") or []:
            text = assertion.get("text") if isinstance(assertion, dict) else assertion
            if text:
                texts.append(str(text))
    return texts


def _needle(assertion):
    """Extract the required substring from an assertion sentence."""
    lowered = assertion.lower()
    marker = "contains:"
    if marker not in lowered:
        return None
    return assertion[lowered.index(marker) + len(marker) :].strip().strip('".')


def echo_fidelity(event, context=None):
    """Assert that the final response contains every seeded needle."""
    spans = _spans(event)
    response = _final_response(spans).lower()
    assertions = _assertions(event)

    checked = [(text, _needle(text)) for text in assertions]
    missing = [text for text, needle in checked if needle and needle.lower() not in response]

    if not any(needle for _, needle in checked):
        return {
            "label": "PASS",
            "value": 1.0,
            "explanation": "No substring assertions supplied; nothing to check.",
        }

    if missing:
        return {
            "label": "FAIL",
            "value": 0.0,
            "explanation": "Final response is missing: " + "; ".join(missing),
        }

    return {
        "label": "PASS",
        "value": 1.0,
        "explanation": f"Final response contains all {len(checked)} expected substrings.",
    }
