import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.scenario.models import ScenarioConfig
from services.rag_mcp.retrieval import search
from services.report_worker.observations.report import build_report


def test_published_catalog_references_and_versions():
    files = list(Path("scenarios/versions").glob("*.json"))
    assert {p.stem for p in files} == {f"S0{i}-v{v}" for i in (1, 2, 3) for v in (1, 2)}
    for path in files:
        config = ScenarioConfig.model_validate_json(path.read_text())
        assert path.stem == config.id
        assert config.status == "published" and not config.is_fixture
        assert config.duration_seconds == 7200
        assert [e.at_seconds for e in config.events] == sorted(e.at_seconds for e in config.events)
        broken = config.model_dump()
        broken["observation_rules"][0]["event_id"] = "nonexistent"
        with pytest.raises(ValidationError):
            ScenarioConfig(**broken)
        broken = config.model_dump()
        broken["observation_rules"] = []
        with pytest.raises(ValidationError):
            ScenarioConfig(**broken)


def test_evidence_window_section_and_actor_boundaries():
    config = json.loads(Path("scenarios/versions/S01-v1.json").read_text())
    rule = next(r for r in config["observation_rules"] if r["competency_id"] == "C05")

    def change(id, seq, elapsed, section, assignee="candidate"):
        return {
            "id": id,
            "seq": seq,
            "elapsed": elapsed,
            "type": "document.updated",
            "payload": {"item": {"id": section, "assignee": assignee, "content": id}},
        }

    snapshot = {
        "competencies": [{"id": "C05", "name": "변화 대응"}],
        "rules": [rule],
        "events": [
            {
                "id": "trigger",
                "seq": 1,
                "type": "scenario.event",
                "elapsed": 2700,
                "payload": {"scenario_event_id": "capacity", "content": "변경 안내"},
            },
            change("wrong-section", 2, 2701, "problem"),
            change("peer-change", 3, 2702, "proposal", "colleague"),
            change("valid", 4, 2703, "proposal"),
            change("too-late", 5, 5000, "proposal"),
        ],
    }
    _, report = build_report(snapshot)
    assert [e["id"] for e in report["items"][0]["evidence"]] == ["valid"]
    assert build_report(snapshot) == build_report(snapshot)
    assert "자동 판단하지" in report["items"][0]["evidence"][0]["fact"]


def test_visible_scenario_document_search_without_import_db(monkeypatch):
    monkeypatch.setenv("DAYZERO_DEVELOPMENT", "true")
    monkeypatch.delenv("EMBEDDING_URL", raising=False)
    config = json.loads(Path("scenarios/versions/S01-v1.json").read_text())
    visible = [d for d in config["documents"] if d["release_after_seconds"] == 0]
    result = asyncio.run(search("신규 사용자 완료율 24%", visible, "synthetic-tenant", 4))
    assert result
    assert "S01-v1-change" not in {r["document_id"] for r in result}
    assert any("240명" in r["content"] for r in result)
    assert asyncio.run(search("가용량 변경", [], "synthetic-tenant", 4)) == []
