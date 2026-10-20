"""Deterministic activity evidence, never a semantic competency judgment."""


def build_report(snapshot):
    rules = snapshot.get("rules", [])
    if not rules:
        return "not_configured", {
            "items": [],
            "activity_count": len(snapshot.get("events", [])),
            "is_fixture": snapshot.get("is_fixture", False),
            "message": "관찰 기준이 설정되지 않아 역량 분석을 생성하지 않았습니다.",
        }
    supported = {"message_after_event", "document_update_after_event"}
    selected = {c["id"] for c in snapshot["competencies"]}
    relevant = [r for r in rules if r.get("competency_id") in selected]
    if any(r.get("kind") not in supported for r in relevant) or not selected.issubset(
        {r.get("competency_id") for r in relevant}
    ):
        return "not_configured", {
            "items": [],
            "message": "선택 역량에 적용할 수 있는 관찰 규칙이 설정되지 않았습니다.",
        }
    items, events = [], snapshot.get("events", [])
    for competency in snapshot["competencies"]:
        evidence, opportunities, situations = [], 0, []
        for rule in relevant:
            if rule["competency_id"] != competency["id"]:
                continue
            trigger = next(
                (
                    e
                    for e in events
                    if e["type"] == "scenario.event"
                    and e["payload"].get("scenario_event_id") == rule.get("event_id")
                ),
                None,
            )
            situation = {
                "rule_id": rule.get("id", ""),
                "label": rule.get("label", ""),
                "rule_version": rule.get("version", "v1"),
                "status": "not_provided",
                "evidence_ids": [],
            }
            situations.append(situation)
            if not trigger:
                continue
            opportunities += 1
            situation.update(
                trigger_id=trigger["id"],
                content=trigger["payload"].get("content", ""),
                elapsed=trigger["elapsed"],
                status="no_record_in_window",
            )
            end = trigger["elapsed"] + rule.get("window_seconds", 900)
            for event in events:
                if event["seq"] <= trigger["seq"] or event["elapsed"] > end:
                    continue
                payload = event["payload"]
                is_message = (
                    rule["kind"] == "message_after_event"
                    and event["type"] == "message.created"
                    and payload.get("actor") == "candidate"
                    and payload.get("channel") == rule.get("channel", "team")
                )
                is_document = (
                    rule["kind"] == "document_update_after_event"
                    and event["type"] == "document.updated"
                    and payload.get("item", {}).get("assignee") == "candidate"
                    and (
                        not rule.get("section_id")
                        or payload["item"].get("id") == rule["section_id"]
                    )
                )
                if not (is_message or is_document):
                    continue
                quote = payload["content"] if is_message else payload["item"].get("content", "")
                if not quote.strip():
                    continue
                evidence.append(
                    {
                        "id": event["id"],
                        "elapsed": event["elapsed"],
                        "quote": quote,
                        "rule_id": rule.get("id", ""),
                        "trigger_id": trigger["id"],
                        "section_id": payload.get("item", {}).get("id"),
                        "fact": (
                            "상황 전달 이후 해당 채널에 메시지를 보냈습니다."
                            if is_message
                            else "상황 전달 이후 담당 문서를 수정했습니다."
                        )
                        + " 내용의 적절성·역량 수준을 자동 판단하지 않았습니다.",
                    }
                )
                situation["evidence_ids"].append(event["id"])
                situation["status"] = "recorded"
                if len(situation["evidence_ids"]) >= 5:
                    break
        summary = (
            "관찰 기회가 제공되지 않았습니다."
            if not opportunities
            else "설정된 관찰 시간 범위에서 연결할 발언·문서 변경을 찾지 못했습니다. 행동의 부재나 역량 부족을 뜻하지 않습니다."
        )
        if evidence:
            summary = "상황 이후의 활동 기록을 연결했습니다. 내용의 의미와 적절성은 원문을 확인해 검토해 주세요."
        items.append(
            {
                "competency_id": competency["id"],
                "name": competency["name"],
                "summary": summary,
                "evidence": evidence,
                "situations": situations,
                "rule_version": "v1",
            }
        )
    return "ready", {
        "items": items,
        "activity_count": len(events),
        "analysis_mode": "activity_evidence_only",
        "message": "상황과 활동 기록을 연결한 리포트입니다. 역량 점수·수준이나 질문·검증의 적절성을 자동 판정하지 않습니다.",
    }
