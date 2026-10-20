"""Public/internal wire contracts. Contains no persistence or workflow logic."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Registration(StrictModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    role: Literal["company", "individual"]
    company_name: str | None = Field(default=None, max_length=100)


class Login(StrictModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class Selection(StrictModel):
    competency_ids: list[str] = Field(min_length=3, max_length=6)

    @field_validator("competency_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("역량을 중복 선택할 수 없습니다.")
        return values


class ProjectCreate(Selection):
    title: str = Field(min_length=1, max_length=120)
    scenario_version_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=8, max_length=100)


class InvitationCreate(StrictModel):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    alias: str = Field(min_length=1, max_length=80)


class Redeem(StrictModel):
    token: str = Field(min_length=16, max_length=200)


class Consent(StrictModel):
    accepted: Literal[True]
    version: Literal["v1"]


class Command(StrictModel):
    command_id: str = Field(min_length=8, max_length=100)


class MessageCreate(Command):
    channel: Literal["team", "dm", "assistant"] = "team"
    recipient: Literal["leader", "colleague"] = "colleague"
    content: str = Field(min_length=1, max_length=12000)


class DocumentUpdate(Command):
    content: str = Field(max_length=60000)
    expected_revision: int = Field(ge=0)
    source_response_id: str | None = Field(default=None, max_length=100)


class TaskUpdate(Command):
    status: Literal["todo", "doing", "done"]
    expected_revision: int = Field(ge=0)


class DocumentImport(StrictModel):
    title: str = Field(min_length=1, max_length=150)
    content: str = Field(min_length=1, max_length=80000)


class ApprovalDecision(StrictModel):
    approved: bool
    content_hash: str


class AssignmentDecision(StrictModel):
    approved: bool


class AgentRun(StrictModel):
    session_id: str
    message_id: str
    role: Literal["colleague", "assistant", "leader"]


class ToolCommand(StrictModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
