from typing import Literal

from pydantic import Field

from contracts.api.models import StrictModel


class DeliverableCriterion(StrictModel):
    id: str
    title: str
    description: str
    section_ids: list[str] = Field(min_length=1)
    source_document_ids: list[str]
    after_event_id: str | None = None


class ReviewConfig(StrictModel):
    version: str
    competency_criteria: dict[str, list[str]]
    deliverable_criteria: list[DeliverableCriterion] = Field(min_length=1)


class Citation(StrictModel):
    source_id: str


class Finding(StrictModel):
    text: str = Field(min_length=1, max_length=600)
    evidence: list[Citation] = Field(min_length=1, max_length=4)


class CompetencyReview(StrictModel):
    competency_id: str
    summary: Finding
    strength: Finding | None
    improvement: Finding | None


class DeliverableReview(StrictModel):
    criterion_id: str
    judgment: Literal["met", "partial", "not_met", "insufficient_evidence"]
    finding: Finding | None


class AIReview(StrictModel):
    summary: Finding
    competencies: list[CompetencyReview]
    deliverables: list[DeliverableReview]
