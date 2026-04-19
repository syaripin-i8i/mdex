from __future__ import annotations

import math
from functools import lru_cache


@lru_cache(maxsize=1)
def _tiktoken_encoder():
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None

    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def estimate_tokens(text: str) -> int:
    clean_text = text or ""
    encoder = _tiktoken_encoder()
    if encoder is not None:
        try:
            return max(1, len(encoder.encode(clean_text)))
        except Exception:
            pass
    return max(1, math.ceil(len(clean_text) / 4))
