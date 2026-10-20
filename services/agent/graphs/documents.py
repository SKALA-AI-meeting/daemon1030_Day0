from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class ImportState(TypedDict, total=False):
    import_id: str
    staged: dict
    approved: bool
    result: dict


def build_import_graph(tools, get_document, checkpoint):
    async def prepare(state):
        result = await tools.call(
            "rag__stage_company_document",
            {"source_version_id": state["import_id"]},
            "stage:" + state["import_id"],
        )
        if result.get("error"):
            raise RuntimeError("Document preparation failed")
        return {"staged": result}

    def approval(state):
        decision = interrupt(
            {
                "import_id": state["import_id"],
                "content_hash": state["staged"]["content_hash"],
                "title": state["staged"]["title"],
            }
        )
        return {"approved": decision is True}

    async def index(state):
        item = await get_document(state["import_id"])
        if (
            item["status"] not in ("approved", "indexed")
            or item["content_hash"] != state["staged"]["content_hash"]
        ):
            raise RuntimeError("Approval does not match document")
        result = await tools.call(
            "rag__index_approved_document",
            {"staged_id": state["import_id"], "approval_id": item["approval_id"]},
            "index:" + state["import_id"],
        )
        if result.get("error"):
            raise RuntimeError("Document indexing failed")
        return {"result": result}

    graph = StateGraph(ImportState)
    graph.add_node("prepare", prepare)
    graph.add_node("approval", approval)
    graph.add_node("index", index)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "approval")
    graph.add_conditional_edges("approval", lambda s: "index" if s["approved"] else END)
    graph.add_edge("index", END)
    return graph.compile(checkpointer=checkpoint)
