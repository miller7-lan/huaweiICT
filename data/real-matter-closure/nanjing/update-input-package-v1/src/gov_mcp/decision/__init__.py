"""Versioned synthetic domain snapshots and deterministic government-rule decisions.

The foundation has no remote model calls. The separate workflow/server modules
add persistent synthetic tasks, professional review, versioning and a local UI/MCP.
"""

from .logic import Truth, evaluate
from .models import KnowledgeSnapshot
from .service import DecisionService

__all__ = ["Truth", "evaluate", "KnowledgeSnapshot", "DecisionService"]
