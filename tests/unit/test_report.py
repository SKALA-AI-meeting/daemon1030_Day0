from services.report_worker.observations.report import build_report


def test_missing_rules_never_fabricate_analysis():
    status, result = build_report({"rules": [], "events": [{"id": "event"}], "is_fixture": True})
    assert status == "not_configured"
    assert result["items"] == []
    assert result["activity_count"] == 1


def test_selected_competencies_and_missing_opportunity():
    snapshot = {
        "competencies": [{"id": "C01", "name": "의사소통"}],
        "rules": [
            {
                "kind": "message_after_event",
                "competency_id": "C01",
                "event_id": "trigger",
            }
        ],
        "events": [],
    }
    status, result = build_report(snapshot)
    assert status == "ready"
    assert len(result["items"]) == 1
    assert "기회가 제공되지" in result["items"][0]["summary"]
    snapshot["events"] = [
        {
            "id": "a",
            "seq": 1,
            "type": "scenario.event",
            "elapsed": 0,
            "payload": {"scenario_event_id": "trigger"},
        },
        {
            "id": "b",
            "seq": 2,
            "type": "message.created",
            "elapsed": 1,
            "payload": {
                "actor": "candidate",
                "channel": "team",
                "content": "확인했습니다.",
            },
        },
    ]
    _, result = build_report(snapshot)
    assert result["items"][0]["evidence"][0]["quote"] == "확인했습니다."
