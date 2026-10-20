import asyncio

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from services.agent.graphs.conversation import build_graph
from services.agent.graphs.documents import build_import_graph


class Tools:
    definitions = {
        "rag__search_company_documents": {
            "type": "function",
            "function": {
                "name": "rag__search_company_documents",
                "description": "lookup",
                "parameters": {"type": "object"},
            },
        }
    }
    calls = 0

    async def call(self, name, arguments, command_id):
        self.calls += 1
        return {"results": [{"document_id": "d", "chunk_id": "d:0", "title": "fixture"}]}


class ScriptedProvider:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools, allow_tools=True):
        self.calls += 1
        if allow_tools:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call{self.calls}",
                        "type": "function",
                        "function": {
                            "name": "rag__search_company_documents",
                            "arguments": '{"query":"test"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "자료의 내용을 확인했습니다."}


def test_real_graph_tool_loop_limit_and_checkpoint(tmp_path):
    async def run():
        tools, provider = Tools(), ScriptedProvider()
        path = str(tmp_path / "checkpoint.sqlite")
        config = {"configurable": {"thread_id": "test"}}
        async with AsyncSqliteSaver.from_conn_string(path) as checkpoint:
            graph = build_graph(
                provider, tools, {"messages": [], "role_instruction": ""}, checkpoint
            )
            result = await graph.ainvoke({"input_id": "input"}, config)
            assert tools.calls == 3
            assert result["calls"] == 3
            assert len(result["citations"]) == 1
        async with AsyncSqliteSaver.from_conn_string(path) as checkpoint:
            restored = build_graph(provider, tools, {}, checkpoint)
            state = await restored.aget_state(config)
            assert state.values["output"] == "자료의 내용을 확인했습니다."
            assert not state.next

    asyncio.run(run())


def test_document_graph_interrupt_reject_and_approve(tmp_path):
    class DocumentTools:
        calls = []

        async def call(self, name, arguments, command_id):
            self.calls.append(name)
            return {"staged_id": "d", "title": "fixture", "content_hash": "hash"}

    async def run():
        tools = DocumentTools()

        async def get_document(identifier):
            return {
                "status": "approved",
                "approval_id": "approval",
                "content_hash": "hash",
            }

        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "document.sqlite")) as cp:
            graph = build_import_graph(tools, get_document, cp)
            config = {"configurable": {"thread_id": "document"}}
            state = await graph.ainvoke({"import_id": "d"}, config)
            assert state.get("__interrupt__")
            assert tools.calls == ["rag__stage_company_document"]
            await graph.ainvoke(Command(resume=False), config)
            assert tools.calls == ["rag__stage_company_document"]
            config2 = {"configurable": {"thread_id": "approved"}}
            await graph.ainvoke({"import_id": "d"}, config2)
            await graph.ainvoke(Command(resume=True), config2)
            assert tools.calls[-1] == "rag__index_approved_document"

    asyncio.run(run())
