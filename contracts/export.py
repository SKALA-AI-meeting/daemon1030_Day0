"""Export generated contracts; never requires DB connectivity or real credentials."""

import json
from pathlib import Path

from contracts.events.models import ReportJob
from contracts.reports.models import AIReview
from contracts.scenario.models import ScenarioConfig
from services.business_mcp.server import mcp as business
from services.gateway.main import create_app
from services.gateway.settings import Settings
from services.rag_mcp.server import mcp as rag


def export():
    root = Path(__file__).resolve().parent
    app = create_app(
        Settings(database_url="sqlite://", secret="schema-export-only-" * 3),
        background=False,
    )
    files = {
        "api/openapi.json": app.openapi(),
        "scenario/schema.json": ScenarioConfig.model_json_schema(),
        "reports/ai-review.schema.json": AIReview.model_json_schema(),
        "events/report-job.schema.json": ReportJob.model_json_schema(),
    }
    for name, server in [("business", business), ("rag", rag)]:
        tools = []
        for tool in server._tool_manager.list_tools():
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters,
                    "outputSchema": tool.output_schema,
                }
            )
        files[f"mcp/{name}.json"] = tools
    for name, value in files.items():
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    app.state.engine.dispose()


if __name__ == "__main__":
    export()
