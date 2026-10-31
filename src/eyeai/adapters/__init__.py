"""Source adapters: turn camera / sensor / business-system signals into Events.

Adapters never decode video and never actuate anything. They only emit
`eyeai.schema.Event` objects through the `emit` callback supplied by the caller.
"""
from .base import ThreadedAdapter, resolve_source_ts

__all__ = ["ThreadedAdapter", "resolve_source_ts"]
