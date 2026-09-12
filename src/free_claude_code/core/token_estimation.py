"""Process-wide best-effort plain-text token estimation."""

from typing import Protocol

from loguru import logger

from free_claude_code.native import estimate_tokens_fast

_DISALLOWED_SPECIAL: tuple[str, ...] = ()

# Fast path threshold: use ultra approx for short stream chunks; tiktoken for long
_FAST_PATH_MAX_CHARS = 512


class _TokenEncoder(Protocol):
    def encode(
        self, text: str, *, disallowed_special: tuple[str, ...]
    ) -> list[int]: ...


def _load_encoder() -> _TokenEncoder | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        logger.warning(
            "cl100k_base token encoder unavailable ({}); using approximate token estimates",
            type(exc).__name__,
        )
        return None


_ENCODER = _load_encoder()


def estimate_text_tokens(text: str) -> int:
    """Estimate tokens for plain text.

    Short strings (stream chunks) use the native ultra approx for microsecond
    latency. Longer strings prefer tiktoken cl100k_base when available.
    """
    if not text:
        return 0
    if len(text) <= _FAST_PATH_MAX_CHARS:
        return estimate_tokens_fast(text)
    if _ENCODER is not None:
        return len(_ENCODER.encode(text, disallowed_special=_DISALLOWED_SPECIAL))
    return estimate_tokens_fast(text)
