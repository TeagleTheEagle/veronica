"""VERONICA's middleware system for tool-call processing.

Middleware allows for pre- and post-processing of tool calls and results,
enabling features like secret scrubbing, output summarization, and safety validation
without cluttering the core tool logic.
"""
from typing import Any, Callable, Dict, List, Optional, Sequence, TypedDict
from dataclasses import dataclass

@dataclass
class ToolContext:
    """Context passed through the middleware stack."""
    tool_name: str
    args: Dict[str, Any]
    result: Any = None
    error: Optional[Exception] = None
    metadata: Dict[str, Any] = None

class Middleware:
    """Base class for all tool middleware."""
    def pre_process(self, context: ToolContext) -> ToolContext:
        """Called before the tool is executed."""
        return context

    def post_process(self, context: ToolContext) -> ToolContext:
        """Called after the tool is executed."""
        return context

class SecretScrubbingMiddleware(Middleware):
    """Ensures sensitive data is not leaked in tool results."""
    def __init__(self, secret_markers: Sequence[str]):
        self.markers = secret_markers

    def post_process(self, context: ToolContext) -> ToolContext:
        if isinstance(context.result, str):
            for marker in self.markers:
                # Simple implementation: if a marker is found, we flag it
                # In a full version, this would use regex to mask values
                if marker.upper() in context.result.upper():
                    context.result = "[REDACTED FOR SECURITY] " + context.result
        return context

class SummarizationMiddleware(Middleware):
    """Summarizes excessively large tool outputs to save context tokens."""
    def __init__(self, max_chars: int = 10000):
        self.max_chars = max_chars

    def post_process(self, context: ToolContext) -> ToolContext:
        if isinstance(context.result, str) and len(context.result) > self.max_chars:
            # In a real version, this would call the LLM to summarize.
            # For now, it performs a smart truncation.
            truncated = context.result[:self.max_chars] + \
                        f"\n\n[... Output truncated for brevity. Original size: {len(context.result)} chars ...]"
            context.result = truncated
        return context

class MiddlewareStack:
    """Manages a sequence of middleware and executes them in order."""
    def __init__(self, middleware: Sequence[Middleware]):
        self.stack = middleware

    def execute(self, tool_fn: Callable, *args, **kwargs) -> Any:
        # Initial context
        context = ToolContext(
            tool_name=tool_fn.__name__ if hasattr(tool_fn, '__name__') else "unknown",
            args=kwargs,
            metadata={}
        )

        # 1. Pre-processing (forward order)
        for m in self.stack:
            context = m.pre_process(context)

        # 2. Tool Execution
        try:
            context.result = tool_fn(*args, **kwargs)
        except Exception as e:
            context.error = e
            raise e

        # 3. Post-processing (reverse order)
        for m in reversed(self.stack):
            context = m.post_process(context)

        return context.result
