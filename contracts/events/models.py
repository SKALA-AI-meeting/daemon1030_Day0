from typing import Literal

from pydantic import Field

from contracts.api.models import StrictModel


class ReportJob(StrictModel):
    session_id: str = Field(min_length=1, max_length=36)
    snapshot_id: str = Field(min_length=1, max_length=36)
    report_version: Literal["v1"] = "v1"
