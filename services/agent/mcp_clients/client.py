import json
import os
import time
from contextlib import AsyncExitStack
from copy import deepcopy

import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def token_for(claims, audience, scope):
    return jwt.encode(
        {
            **claims,
            "iss": "dayzero-gateway",
            "aud": audience,
            "scope": scope,
            "exp": int(time.time()) + 120,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


class ToolConnections:
    def __init__(self, claims, role):
        self.claims, self.role = claims, role
        self.stack = AsyncExitStack()
        self.sessions, self.definitions = {}, {}

    async def __aenter__(self):
        try:
            servers = [
                (
                    "rag",
                    "rag-mcp",
                    os.getenv("RAG_MCP_URL", "http://127.0.0.1:9102/mcp"),
                )
            ]
            if self.role != "assistant" and self.role != "manager":
                servers.append(
                    (
                        "business",
                        "business-mcp",
                        os.getenv("BUSINESS_MCP_URL", "http://127.0.0.1:9101/mcp"),
                    )
                )
            for prefix, audience, url in servers:
                scope = "documents:manage" if self.role == "manager" else prefix
                http_client = await self.stack.enter_async_context(
                    httpx.AsyncClient(
                        headers={
                            "Authorization": "Bearer " + token_for(self.claims, audience, scope)
                        },
                        timeout=httpx.Timeout(15, read=60),
                    )
                )
                transport = await self.stack.enter_async_context(
                    streamable_http_client(url, http_client=http_client)
                )
                session = await self.stack.enter_async_context(
                    ClientSession(transport[0], transport[1])
                )
                await session.initialize()
                self.sessions[prefix] = session
                for tool in (await session.list_tools()).tools:
                    management = tool.name in (
                        "stage_company_document",
                        "index_approved_document",
                    )
                    forbidden = tool.name in (
                        "apply_task_assignment",
                        "record_observation",
                    )
                    if forbidden or management != (self.role == "manager"):
                        continue
                    if self.role == "leader" and tool.name not in (
                        "get_session_context",
                        "list_tasks",
                        "search_company_documents",
                        "get_company_document",
                    ):
                        continue
                    schema = deepcopy(tool.inputSchema)
                    schema.get("properties", {}).pop("command_id", None)
                    schema["required"] = [
                        k for k in schema.get("required", []) if k != "command_id"
                    ]
                    name = prefix + "__" + tool.name
                    self.definitions[name] = {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool.description,
                            "parameters": schema,
                        },
                    }
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *exc):
        return await self.stack.__aexit__(*exc)

    async def call(self, name, arguments, command_id):
        if name not in self.definitions:
            return {"error": "ACCESS_DENIED"}
        prefix, actual = name.split("__", 1)
        arguments = deepcopy(arguments)
        if actual in (
            "propose_task_assignment",
            "update_task_status",
            "publish_colleague_draft",
            "stage_company_document",
            "index_approved_document",
        ):
            arguments["command_id"] = command_id
        result = await self.sessions[prefix].call_tool(actual, arguments)
        if result.isError:
            return {
                "error": "TOOL_ERROR",
                "detail": " ".join(c.text for c in result.content if c.type == "text")[:2000],
            }
        if result.structuredContent is not None:
            return result.structuredContent
        text = " ".join(c.text for c in result.content if c.type == "text")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text[:8000]}
