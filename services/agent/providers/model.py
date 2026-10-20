import asyncio
import os

import httpx


class ModelNotConfigured(RuntimeError):
    pass


class OpenAIModel:
    async def complete(self, messages, tools, *, allow_tools=True):
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ModelNotConfigured("OPENAI_API_KEY가 설정되지 않았습니다.")
        payload = {
            "model": "gpt-4o-mini",
            "messages": messages,
            "max_completion_tokens": 1400,
        }
        if tools:
            payload.update(
                tools=tools,
                tool_choice="auto" if allow_tools else "none",
                parallel_tool_calls=False,
            )
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=40) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": "Bearer " + key},
                        json=payload,
                    )
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(attempt + 1)
                    continue
                response.raise_for_status()
                return response.json()["choices"][0]["message"]
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise
                await asyncio.sleep(attempt + 1)
        raise RuntimeError("Model response unavailable")
