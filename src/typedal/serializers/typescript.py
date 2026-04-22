"""
Helpers for generating TypedDict/TypeScript shapes.
"""

from __future__ import annotations

import typing as t
import warnings

from configuraptor import Singleton

try:  # optional dependency
    import typtyp
except ImportError:  # pragma: no cover
    typtyp = None  # type: ignore


def is_supported() -> bool:
    """Check if typescript support is enabled."""
    return typtyp is not None


class TypedDictRegistry(Singleton):
    """
    Global registry for model -> TypedDict mappings used by TypeScript serialization.
    """

    def __init__(self) -> None:
        """Initialize the singleton registry and optional shared typtyp world."""
        self._types: dict[type, type[dict[str, t.Any]]] = {}
        self._world = typtyp.World() if typtyp else None
        self._names: set[str] = set()

    @property
    def world(self) -> "typtyp.World | None":
        """Return the shared typtyp world instance, if typtyp is installed."""
        return self._world

    def get(self, model: type) -> type[dict[str, t.Any]] | None:
        """Return the registered TypedDict for a model, or None if absent."""
        return self._types.get(model)

    def create(self, model: type, fields: dict[str, t.Any] = None, name: str = "") -> type[dict[str, t.Any]]:
        """
        Create/register a TypedDict for a model and add it to the shared world.

        If the world is unavailable (typtyp not installed), registration is local only.
        """
        name = name or model.__name__
        raw_typed_dict = t.TypedDict(name, fields or {})
        typed_dict = t.cast(type[dict[str, t.Any]], raw_typed_dict)
        self._types[model] = typed_dict
        self._add_to_world(typed_dict, name=name)
        return typed_dict

    def _add_to_world(self, typ: type[dict[str, t.Any]], *, name: str) -> None:
        """Add a type to the world once per name."""
        world = self.world
        if world is None or name in self._names:
            return
        world.add(typ, name=name)
        self._names.add(name)

    def get_typescript(self, caller_name: str = "get_typescript") -> str:
        """
        Return TypeScript output from the shared world.

        If typtyp is not installed, emit a warning and return an empty string.
        """
        world = self.world
        if world is None:  # pragma: ignore
            warnings.warn(
                f"`{caller_name}` can not be used without the typescript extra. Please install `typedal[typescript]`",
            )
            return ""

        return world.get_typescript()
