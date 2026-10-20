import json
from copy import deepcopy
from pathlib import Path

import pytest

from services.report_worker.observations.report import build_report
from services.report_worker.observations.review import enrich_report, validate_output


def example_snapshot():
    snapshot = json.loads(Path("docs/reports/S01-sample-records.json").read_text())["snapshot"]
    config = json.loads(Path("scenarios/versions/S01-v2.json").read_text())
    snapshot.update(review=config["review"], documents=config["documents"], brief=config["brief"])
    return snapshot


def scripted_review(payload):
    def finding(text, source_id):
        return {
            "text": text,
            "evidence": [{"source_id": source_id}],
        }

    final = next(
        (k for k, v in payload["sources"].items() if v["kind"] == "final_section"),
        next(iter(payload["sources"])),
    )
    output = {
        "summary": finding("제출물의 선택 근거와 남은 확인 사항을 구분했습니다.", final),
        "competencies": [],
        "deliverables": [],
    }
    for competency in payload["competencies"]:
        output["competencies"].append(
            {
                "competency_id": competency,
                "summary": finding(
                    "기록에 명시한 근거를 검토했습니다. 이 과제 범위의 해석입니다.", final
                ),
                "strength": None,
                "improvement": None,
            }
        )
    for criterion in payload["deliverable_criteria"]:
        source = next(
            k
            for k, v in payload["sources"].items()
            if v.get("section_id") in criterion["section_ids"]
        )
        output["deliverables"].append(
            {
                "criterion_id": criterion["id"],
                "judgment": "partial",
                "finding": finding("기록에 기재된 계획은 있으나 추가 확인이 필요합니다.", source),
            }
        )
    return output


def test_review_links_final_submission_and_preserves_fact_output():
    snapshot = example_snapshot()
    _, facts = build_report(snapshot)
    original = deepcopy(facts)
    enriched = enrich_report(snapshot, facts, scripted_review)
    assert facts == original
    assert enriched["ai_review"]["status"] == "ready"
    assert len(enriched["final_review"]["criteria"]) == 6
    assert all(i["ai_evaluation"]["status"] == "ready" for i in enriched["items"])
    assert enriched["analysis_mode"] == "evidence_with_ai_review"
    assert enriched["review_sources"]
    for item in enriched["items"]:
        for citation in item["ai_evaluation"]["summary"]["evidence"]:
            assert citation["quote"] == enriched["review_sources"][citation["source_id"]]["content"]
    assert enriched["ai_review"]["criteria_hash"]


@pytest.mark.parametrize(
    "violation", ["quote", "unknown_source", "competency", "criterion", "hiring", "section"]
)
def test_unverified_or_out_of_scope_ai_review_is_not_published(violation):
    snapshot = example_snapshot()
    _, facts = build_report(snapshot)

    def invalid(payload):
        output = scripted_review(payload)
        if violation == "quote":
            output["summary"]["evidence"][0]["quote"] = "존재하지 않는 원문 123456"
        if violation == "unknown_source":
            output["summary"]["evidence"][0]["source_id"] = "R9999999"
        if violation == "competency":
            output["competencies"][0]["competency_id"] = "C03"
        if violation == "criterion":
            output["deliverables"][0]["criterion_id"] = "invented"
        if violation == "hiring":
            output["summary"]["text"] = "채용 추천 대상입니다."
        if violation == "section":
            key = next(k for k, v in payload["sources"].items() if v.get("section_id") == "problem")
            target = next(d for d in output["deliverables"] if d["criterion_id"] == "execution")
            target["finding"]["evidence"] = [{"source_id": key}]
        with pytest.raises(ValueError):
            validate_output(output, payload)
        return output

    result = enrich_report(snapshot, facts, invalid)
    assert result["ai_review"]["status"] == "unavailable"
    assert result["final_review"]["summary"] is None
    assert all("summary" not in i["ai_evaluation"] for i in result["items"])
    assert result["items"][0]["evidence"] == facts["items"][0]["evidence"]


def test_empty_submission_and_unprovided_change_do_not_invoke_model():
    snapshot = example_snapshot()
    snapshot["events"] = []
    for section in snapshot["workspace"]["sections"]:
        section["content"] = ""
    _, facts = build_report(snapshot)

    def forbidden(_):
        raise AssertionError("No evidence must not call model")

    result = enrich_report(snapshot, facts, forbidden)
    assert result["ai_review"]["status"] == "insufficient_evidence"
    assert {c["status"] for c in result["final_review"]["criteria"]} == {
        "not_submitted",
        "not_applicable",
    }
    assert all(i["ai_evaluation"]["status"] == "no_opportunity" for i in result["items"])


def test_model_failure_does_not_create_fake_judgments():
    snapshot = example_snapshot()
    _, facts = build_report(snapshot)

    def unavailable(_):
        raise RuntimeError("MODEL_NOT_CONFIGURED")

    result = enrich_report(snapshot, facts, unavailable)
    assert result["ai_review"]["status"] == "unavailable"
    assert all(c["status"] == "unavailable" for c in result["final_review"]["criteria"])


def test_insufficient_evidence_has_no_invented_finding():
    snapshot = example_snapshot()
    _, facts = build_report(snapshot)

    def insufficient(payload):
        output = scripted_review(payload)
        output["deliverables"][-1].update(judgment="insufficient_evidence", finding=None)
        return output

    result = enrich_report(snapshot, facts, insufficient)
    assert result["ai_review"]["status"] == "ready"
    assert result["final_review"]["criteria"][-1]["status"] == "insufficient_evidence"
    assert result["final_review"]["criteria"][-1]["finding"] is None


def test_positive_judgment_without_evidence_is_rejected():
    snapshot = example_snapshot()
    _, facts = build_report(snapshot)

    def unsupported(payload):
        output = scripted_review(payload)
        output["deliverables"][0].update(judgment="met", finding=None)
        return output

    result = enrich_report(snapshot, facts, unsupported)
    assert result["ai_review"]["status"] == "unavailable"


def test_final_review_calls_never_receive_intermediate_drafts(monkeypatch):
    import services.report_worker.observations.review as module

    snapshot = example_snapshot()
    _, facts = build_report(snapshot)
    calls = []

    def request(payload):
        calls.append(payload)
        if payload["deliverable_criteria"]:
            assert not payload["competencies"]
            assert all(
                s["kind"] in ("final_section", "reference_document")
                for s in payload["sources"].values()
            )
            assert payload["deliverable_criteria"][0]["required_submission_source_ids"]
        if list(payload["competencies"]) == ["C06"]:
            assert not any(s["kind"] == "final_section" for s in payload["sources"].values())
        return module.validate_output(scripted_review(payload), payload)

    monkeypatch.setattr(module, "request_review", request)
    result = module.enrich_report(snapshot, facts)
    assert result["ai_review"]["status"] == "ready"
    assert len(calls) == 12


def test_provider_schema_limits_citations_to_registered_sources():
    from services.report_worker.observations.review import provider_schema

    snapshot = example_snapshot()
    _, facts = build_report(snapshot)
    captured = []

    def collect(payload):
        captured.append(payload)
        return scripted_review(payload)

    enrich_report(snapshot, facts, collect)
    payload = captured[0]
    schema = provider_schema(payload)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Citation"]["properties"]["source_id"]["enum"] == list(
        payload["sources"]
    )
    assert set(schema["required"]) == {"summary", "competencies", "deliverables"}
    assert "quote" not in schema["$defs"]["Citation"]["properties"]
    subset = provider_schema({**payload, "deliverable_criteria": []})
    assert subset["properties"]["deliverables"]["maxItems"] == 0
    assert subset["properties"]["competencies"]["minItems"] == len(payload["competencies"])
