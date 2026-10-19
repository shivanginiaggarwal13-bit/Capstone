"""
Utility functions and classes for the runtime.
"""
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class StreamingCallbackHandler(BaseCallbackHandler):
    """Callback handler to stream LLM tokens to stdout."""

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        print(token, end="", flush=True)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        print()  # New line after completion
