import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from services.agent.harness.guardrails import SYSTEM, guard


class ConversationState(TypedDict, total=False):
    input_id: str
    messages: list[dict]
    calls: int
    pending_tools: list[dict]
    citations: list[dict]
    output: str
    trace: list[dict]


def build_graph(provider, tools, context, checkpoint):
    async def prepare(state):
        messages = [
            {
                "role": "system",
                "content": SYSTEM
                + "\n역할: "
                + context.get("role_instruction", "")
                + "\n공개 과제: "
                + context.get("brief", ""),
            }
        ]
        for event in context.get("messages", []):
            payload = event["payload"]
            messages.append(
                {
                    "role": "user" if payload["actor"] == "candidate" else "assistant",
                    "content": f"[{payload.get('actor_name') or payload.get('actor', '')}] "
                    + payload["content"],
                }
            )
        return {
            "messages": messages,
            "calls": 0,
            "pending_tools": [],
            "citations": [],
            "trace": [],
        }

    async def model(state):
        result = await provider.complete(
            state["messages"],
            list(tools.definitions.values()),
            allow_tools=state["calls"] < 3,
        )
        calls = result.get("tool_calls", [])
        if calls and state["calls"] >= 3:
            return {
                "pending_tools": [],
                "output": "도구 호출 한도에 도달했습니다. 확인된 자료 범위에서 요청을 나누어 진행해 주세요.",
            }
        return {
            "messages": state["messages"] + [result],
            "pending_tools": calls,
            "output": result.get("content") or "",
        }

    async def execute(state):
        messages, citations, trace = (
            list(state["messages"]),
            list(state["citations"]),
            list(state["trace"]),
        )
        count = state["calls"]
        for call in state["pending_tools"]:
            if count >= 3:
                result = {"error": "TOOL_LIMIT"}
            else:
                try:
                    args = json.loads(call["function"]["arguments"])
                    result = await tools.call(
                        call["function"]["name"], args, f"{state['input_id']}:{count}"
                    )
                except (ValueError, KeyError, TypeError):
                    result = {"error": "INVALID_ARGUMENTS"}
                count += 1
            trace.append(
                {
                    "tool": call["function"]["name"],
                    "call_number": count,
                    "error": result.get("error"),
                }
            )
            for doc in result.get("results", []):
                if not any(c["chunk_id"] == doc["chunk_id"] for c in citations):
                    citations.append({k: doc[k] for k in ("document_id", "chunk_id", "title")})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False)[:24000],
                }
            )
        return {
            "messages": messages,
            "calls": count,
            "citations": citations,
            "trace": trace,
            "pending_tools": [],
        }

    async def validate(state):
        return {"output": guard(state.get("output"))}

    graph = StateGraph(ConversationState)
    graph.add_node("prepare_context", prepare)
    graph.add_node("model", model)
    graph.add_node("execute_tools", execute)
    graph.add_node("guard_output", validate)
    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "model")
    graph.add_conditional_edges(
        "model", lambda s: "execute_tools" if s.get("pending_tools") else "guard_output"
    )
    graph.add_edge("execute_tools", "model")
    graph.add_edge("guard_output", END)
    return graph.compile(checkpointer=checkpoint)
