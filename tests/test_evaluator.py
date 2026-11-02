"""The code evaluator must be deterministic and must be able to fail."""

from evals.evaluator.handler import echo_fidelity


def _event(final_text: str, assertions: list[str]) -> dict:
    return {
        "evaluationInput": {
            "sessionSpans": [
                {
                    "endTimeUnixNano": "2",
                    "attributes": {
                        "gen_ai.operation.name": {"stringValue": "invoke_agent"},
                        "session.id": {"stringValue": "s1"},
                    },
                    "events": [
                        {
                            "name": "gen_ai.choice",
                            "attributes": {"message": {"stringValue": final_text}},
                        }
                    ],
                }
            ]
        },
        "evaluationReferenceInputs": [{"assertions": [{"text": a} for a in assertions]}],
    }


def test_passes_when_needle_present() -> None:
    result = echo_fidelity(_event("echoed: confirmed", ["The final response contains: confirmed"]))
    assert result["label"] == "PASS"
    assert result["value"] == 1.0


def test_fails_on_seeded_violation() -> None:
    result = echo_fidelity(
        _event("echoed: seed check", ["The final response contains: seeded-violation-token"])
    )
    assert result["label"] == "FAIL"
    assert result["value"] == 0.0
    assert "seeded-violation-token" in result["explanation"]


def test_last_choice_wins_when_many_spans() -> None:
    event = _event("", ["The final response contains: second"])
    event["evaluationInput"]["sessionSpans"] = [
        {
            "endTimeUnixNano": "1",
            "events": [{"name": "gen_ai.choice", "attributes": {"message": "first"}}],
        },
        {
            "endTimeUnixNano": "2",
            "events": [{"name": "gen_ai.choice", "attributes": {"message": "second"}}],
        },
    ]
    assert echo_fidelity(event)["label"] == "PASS"


def test_no_assertions_is_a_pass_with_an_explanation() -> None:
    result = echo_fidelity(_event("anything", []))
    assert result["label"] == "PASS"
    assert "No substring assertions" in result["explanation"]


def test_evaluator_id_from_arn() -> None:
    from evals.run import evaluator_id

    arn = "arn:aws:bedrock-agentcore:us-east-1:123:evaluator/EchoFidelity-abc1234567"
    assert evaluator_id(arn) == "EchoFidelity-abc1234567"
    assert evaluator_id("Builtin.Correctness") == "Builtin.Correctness"
