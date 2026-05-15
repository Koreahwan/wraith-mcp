"""Browser Use chat model backed by MCP client sampling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, overload
from urllib.parse import urlsplit, urlunsplit

from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import (
    AssistantMessage,
    BaseMessage,
    ContentPartImageParam,
    ContentPartRefusalParam,
    ContentPartTextParam,
    SystemMessage,
    UserMessage,
)
from browser_use.llm.views import ChatInvokeCompletion
from mcp import types as mcp_types
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class SamplingSession(Protocol):
    @property
    def client_params(self) -> Any: ...

    async def create_message(self, **kwargs: Any) -> mcp_types.CreateMessageResult: ...


class SamplingRequestContext(Protocol):
    @property
    def request_id(self) -> Any: ...


class SamplingContext(Protocol):
    @property
    def session(self) -> SamplingSession: ...

    @property
    def request_context(self) -> SamplingRequestContext: ...


def client_supports_sampling(ctx: SamplingContext | None) -> bool:
    if ctx is None:
        return False
    client_params = ctx.session.client_params
    capabilities = client_params.capabilities if client_params else None
    return bool(capabilities and capabilities.sampling)


@dataclass
class McpSamplingChatModel(BaseChatModel):
    """Use the MCP client's model session through sampling/createMessage."""

    ctx: SamplingContext
    model: str = "mcp-client"
    max_tokens: int = 4096
    temperature: float | None = None
    _verified_api_keys: bool = True

    @property
    def provider(self) -> str:
        return "mcp-sampling"

    @property
    def name(self) -> str:
        return self.model

    @overload
    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[str]: ...

    @overload
    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T],
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T]: ...

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        sampling_messages, system_prompt = browser_messages_to_sampling(messages)
        if output_format is not None:
            system_prompt = with_schema_instruction(system_prompt, output_format)

        max_tokens = int(kwargs.get("max_tokens") or self.max_tokens)
        result = await self.ctx.session.create_message(
            messages=sampling_messages,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            include_context="none",
            temperature=self.temperature,
            model_preferences=mcp_types.ModelPreferences(
                hints=[mcp_types.ModelHint(name=self.model)],
            ),
            related_request_id=self.ctx.request_context.request_id,
        )
        text = sampling_content_to_text(result.content)
        if output_format is None:
            return ChatInvokeCompletion(
                completion=text,
                usage=None,
                stop_reason=result.stopReason,
            )

        try:
            completion = output_format.model_validate_json(text)
        except ValueError as exc:
            raise RuntimeError(
                f"MCP sampling response did not match {output_format.__name__}: {exc}"
            ) from exc
        return ChatInvokeCompletion(
            completion=completion,
            usage=None,
            stop_reason=result.stopReason,
        )


def browser_messages_to_sampling(
    messages: list[BaseMessage],
) -> tuple[list[mcp_types.SamplingMessage], str | None]:
    system_parts: list[str] = []
    sampling_messages: list[mcp_types.SamplingMessage] = []

    for message in messages:
        if isinstance(message, SystemMessage):
            system_parts.append(browser_message_text(message))
            continue

        role = "assistant" if isinstance(message, AssistantMessage) else "user"
        sampling_messages.append(
            mcp_types.SamplingMessage(
                role=role,
                content=browser_message_content(message),
            )
        )

    return sampling_messages, "\n\n".join(system_parts) or None


def browser_message_text(message: BaseMessage) -> str:
    content = message.content
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(content_part_text(part) for part in content)


def browser_message_content(
    message: BaseMessage,
) -> mcp_types.SamplingMessageContentBlock | list[mcp_types.SamplingMessageContentBlock]:
    content = message.content
    if content is None:
        return mcp_types.TextContent(type="text", text="")
    if isinstance(content, str):
        return mcp_types.TextContent(type="text", text=content)

    parts: list[mcp_types.SamplingMessageContentBlock] = [
        content_part_to_sampling(part) for part in content
    ]
    if len(parts) == 1:
        return parts[0]
    return parts


def content_part_to_sampling(
    part: ContentPartTextParam | ContentPartImageParam | ContentPartRefusalParam,
) -> mcp_types.SamplingMessageContentBlock:
    if isinstance(part, ContentPartImageParam):
        return image_part_to_sampling(part)
    return mcp_types.TextContent(type="text", text=content_part_text(part))


def image_part_to_sampling(part: ContentPartImageParam) -> mcp_types.ImageContent | mcp_types.TextContent:
    url = part.image_url.url
    mime_type = part.image_url.media_type
    if url.startswith("data:") and "," in url:
        header, data = url.split(",", 1)
        media = header.removeprefix("data:").split(";", 1)[0]
        return mcp_types.ImageContent(
            type="image",
            data=data,
            mimeType=media or mime_type,
        )
    return mcp_types.TextContent(type="text", text=f"[Image URL: {redact_url(url)}]")


def content_part_text(
    part: ContentPartTextParam | ContentPartImageParam | ContentPartRefusalParam,
) -> str:
    if isinstance(part, ContentPartTextParam):
        return part.text
    if isinstance(part, ContentPartRefusalParam):
        return part.refusal
    return f"[Image URL: {redact_url(part.image_url.url)}]"


def redact_url(url: str) -> str:
    parsed = urlsplit(url)
    redacted = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    hidden_parts = []
    if parsed.query:
        hidden_parts.append("query")
    if parsed.fragment:
        hidden_parts.append("fragment")
    if hidden_parts:
        return f"{redacted} [{' and '.join(hidden_parts)} redacted]"
    return redacted


def sampling_content_to_text(
    content: mcp_types.SamplingMessageContentBlock | list[mcp_types.SamplingMessageContentBlock],
) -> str:
    if isinstance(content, list):
        return "\n".join(sampling_content_to_text(part) for part in content)
    if isinstance(content, mcp_types.TextContent):
        return content.text
    if isinstance(content, mcp_types.ImageContent):
        return "[image content]"
    if isinstance(content, mcp_types.AudioContent):
        return "[audio content]"
    return str(content)


def with_schema_instruction(system_prompt: str | None, output_format: type[BaseModel]) -> str:
    schema = json.dumps(output_format.model_json_schema(), ensure_ascii=False)
    instruction = (
        "Return only valid JSON matching this schema. Do not wrap it in markdown.\n"
        f"{schema}"
    )
    return f"{system_prompt}\n\n{instruction}" if system_prompt else instruction
