from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _content(message: Any) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    return [item for item in content if isinstance(item, dict)] if isinstance(content, list) else []


def _tool_result_is_error(item: dict[str, Any], event: dict[str, Any]) -> bool:
    if item.get("is_error") or event.get("is_error"):
        return True
    content = item.get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
    else:
        texts = []
    return any(text.lstrip().lower().startswith("error:") for text in texts)


@dataclass
class EventCollector:
    session_id: str
    assistant_messages: list[dict[str, Any]] = field(default_factory=list)
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)
    observed_models_by_round: dict[int, set[str]] = field(default_factory=dict)
    result: dict[str, Any] | None = None

    def consume(self, event: dict[str, Any], observed_elapsed_ms: int, round_index: int) -> None:
        event_type = event.get("type")
        message = event.get("message", {})
        model_candidates = [event.get("model")]
        if isinstance(message, dict):
            model_candidates.append(message.get("model"))
        models = self.observed_models_by_round.setdefault(round_index, set())
        models.update(
            str(model)
            for model in model_candidates
            if isinstance(model, str) and model.strip()
        )
        if event_type == "result":
            self.result = event
        if event_type == "assistant":
            self.assistant_messages.append(
                {
                    "round_index": round_index,
                    "uuid": event.get("uuid"),
                    "model": message.get("model") if isinstance(message, dict) else None,
                    "content": _content(message),
                    "timestamp": event.get("__timestamp"),
                }
            )
            for item in _content(message):
                if item.get("type") != "tool_use":
                    continue
                tool_id = str(item.get("id") or event.get("uuid") or "")
                if not tool_id or tool_id in self.operations:
                    continue
                self.operations[tool_id] = {
                    "tool_use_id": tool_id,
                    "name": item.get("name"),
                    "input": item.get("input"),
                    "round_index": round_index,
                    "started_elapsed_ms": observed_elapsed_ms,
                    "started_at": event.get("__timestamp"),
                    "status": "running",
                }
        if event_type in {"user", "tool_result"}:
            items = _content(event.get("message", event))
            if event_type == "tool_result" and not items:
                items = [event]
            for item in items:
                if item.get("type") != "tool_result" and event_type != "tool_result":
                    continue
                tool_id = str(item.get("tool_use_id") or event.get("tool_use_id") or "")
                if not tool_id:
                    continue
                operation = self.operations.setdefault(
                    tool_id,
                    {
                        "tool_use_id": tool_id,
                        "round_index": round_index,
                        "started_elapsed_ms": None,
                        "status": "running",
                    },
                )
                operation.update(
                    {
                        "ended_elapsed_ms": observed_elapsed_ms,
                        "ended_at": event.get("__timestamp"),
                        "status": (
                            "error"
                            if _tool_result_is_error(item, event)
                            else "success"
                        ),
                        "result": item.get("content"),
                    }
                )
                started = operation.get("started_elapsed_ms")
                operation["duration_ms"] = (
                    observed_elapsed_ms - started if isinstance(started, int) else None
                )

    def observed_models(self, round_index: int) -> list[str]:
        return sorted(self.observed_models_by_round.get(round_index, set()))

    def finalize_operations(
        self, elapsed_ms: int, interrupted_by: str | None = None
    ) -> list[dict[str, Any]]:
        for operation in self.operations.values():
            if operation.get("status") == "running":
                operation["status"] = (
                    "interrupted" if interrupted_by else "unknown"
                )
                if interrupted_by:
                    operation["interrupted_by"] = interrupted_by
                operation["observed_until_elapsed_ms"] = elapsed_ms
        return sorted(
            self.operations.values(),
            key=lambda item: (
                item.get("round_index", 0),
                item.get("started_elapsed_ms") or 0,
            ),
        )


def assistant_text(messages: list[dict[str, Any]], round_index: int) -> str:
    parts: list[str] = []
    for message in messages:
        if message.get("round_index") != round_index:
            continue
        for item in message.get("content", []):
            if item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
    return "\n".join(parts).strip()
