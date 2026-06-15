"""Error types raised by the LoopForge engine.

Every failure that crosses the LoopForge boundary (template, adapter, image,
flow) surfaces as a :class:`LoopForgeError` (or one of its subtypes) so that
callers depend on a single, narrow error contract and never have to catch a
provider SDK's transport exceptions directly. No provider key is ever placed
into an error message.
"""

from __future__ import annotations


class LoopForgeError(Exception):
    """Base error for every recoverable LoopForge failure."""


class InvalidJsonError(LoopForgeError):
    """Raised when model output cannot be parsed/repaired into valid JSON."""


class AdapterError(LoopForgeError):
    """Raised when an LLM or image provider SDK call fails or returns nothing."""
