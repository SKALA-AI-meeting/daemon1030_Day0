from typing import Literal

from pydantic import Field, model_validator

from contracts.api.models import StrictModel
from contracts.reports.models import ReviewConfig


class Section(StrictModel):
    id: str
    title: str
    assignee: Literal["candidate", "colleague"] = "candidate"


class CompanyDocument(StrictModel):
    id: str
    title: str
    content: str
    release_after_seconds: int = Field(default=0, ge=0)
    source: Literal["scenario"] = "scenario"


class ScenarioEvent(StrictModel):
    id: str
    at_seconds: int = Field(ge=0)
    actor: Literal["leader", "colleague", "system"] = "system"
    content: str
    actor_name: str = ""
    role_updates: dict[str, str] = Field(default_factory=dict)


class ObservationRule(StrictModel):
    id: str = ""
    version: str = "v1"
    competency_id: str
    kind: Literal["message_after_event", "document_update_after_event"]
    event_id: str
    label: str = ""
    channel: Literal["team", "dm"] = "team"
    section_id: str | None = None
    window_seconds: int = Field(default=900, ge=1, le=28800)


class ScenarioConfig(StrictModel):
    id: str
    title: str
    status: Literal["draft", "published"] = "draft"
    is_fixture: bool = False
    duration_seconds: int = Field(default=7200, ge=60, le=28800)
    competency_ids: list[str] = Field(default_factory=list)
    brief: str = ""
    role_instructions: dict[str, str] = Field(default_factory=dict)
    role_names: dict[str, str] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)
    documents: list[CompanyDocument] = Field(default_factory=list)
    events: list[ScenarioEvent] = Field(default_factory=list)
    observation_rules: list[ObservationRule] = Field(default_factory=list)
    review: ReviewConfig | None = None

    @model_validator(mode="after")
    def coherent_references(self):
        for items in (self.sections, self.documents, self.events):
            if len({i.id for i in items}) != len(items):
                raise ValueError("설정 ID는 중복될 수 없습니다.")
        if any(e.at_seconds >= self.duration_seconds for e in self.events):
            raise ValueError("사건 시점은 제한 시간보다 빨라야 합니다.")
        if any(d.release_after_seconds >= self.duration_seconds for d in self.documents):
            raise ValueError("자료 공개 시점은 제한 시간보다 빨라야 합니다.")
        if len(set(self.competency_ids)) != len(self.competency_ids):
            raise ValueError("역량 ID는 중복될 수 없습니다.")
        event_ids = {e.id for e in self.events}
        sections = {s.id for s in self.sections}
        for rule in self.observation_rules:
            if rule.event_id not in event_ids or rule.competency_id not in self.competency_ids:
                raise ValueError("관찰 규칙의 사건·역량 참조가 올바르지 않습니다.")
            if rule.section_id and rule.section_id not in sections:
                raise ValueError("관찰 규칙의 문서 참조가 올바르지 않습니다.")
        if self.status == "published" and not self.is_fixture:
            covered = {r.competency_id for r in self.observation_rules}
            if not self.competency_ids or set(self.competency_ids) != covered:
                raise ValueError("발행 시 모든 지원 역량의 관찰 기회·규칙이 필요합니다.")
        if self.review:
            if set(self.review.competency_criteria) != set(self.competency_ids):
                raise ValueError("AI 검토 기준은 지원 역량 전체와 일치해야 합니다.")
            ids = [r.id for r in self.review.deliverable_criteria]
            if len(ids) != len(set(ids)):
                raise ValueError("결과물 검토 기준 ID는 중복될 수 없습니다.")
            docs = {d.id for d in self.documents}
            for criterion in self.review.deliverable_criteria:
                if not set(criterion.section_ids).issubset(sections) or not set(
                    criterion.source_document_ids
                ).issubset(docs):
                    raise ValueError("결과물 검토 기준의 문서 참조가 올바르지 않습니다.")
                if criterion.after_event_id and criterion.after_event_id not in event_ids:
                    raise ValueError("결과물 검토 기준의 사건 참조가 올바르지 않습니다.")
        return self
