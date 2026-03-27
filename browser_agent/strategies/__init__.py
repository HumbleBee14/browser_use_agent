"""Task strategy registry.

Maps YAML strategy names to strategy classes.
Adding a new strategy = add a class + one line here.
"""

from __future__ import annotations

from strategies.base import BaseTaskStrategy
from strategies.form_fill import FormFillStrategy
from strategies.graph_traversal import GraphTraversalStrategy
from strategies.single_page import SinglePageStrategy

STRATEGY_REGISTRY: dict[str, type[BaseTaskStrategy]] = {
    "single_page": SinglePageStrategy,
    "graph_traversal": GraphTraversalStrategy,
    "form_fill": FormFillStrategy,
}


def resolve_strategy(name: str) -> type[BaseTaskStrategy]:
    """Look up a strategy class by name. Raises KeyError if unknown."""
    if name not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys())
        raise KeyError(
            f"Unknown strategy '{name}'. Available: {available}"
        )
    return STRATEGY_REGISTRY[name]


__all__ = [
    "BaseTaskStrategy",
    "FormFillStrategy",
    "GraphTraversalStrategy",
    "SinglePageStrategy",
    "STRATEGY_REGISTRY",
    "resolve_strategy",
]
