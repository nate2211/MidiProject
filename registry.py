# registry.py
from __future__ import annotations
from typing import Any, Callable, Dict, List, Type


class BlockRegistry:
    def __init__(self):
        self._by_name: Dict[str, Type[Any]] = {}

    def register(self, name: str) -> Callable[[Type[Any]], Type[Any]]:
        name = str(name).strip().lower()

        def deco(cls: Type[Any]) -> Type[Any]:
            self._by_name[name] = cls
            cls.NAME = name
            return cls

        return deco

    def names(self) -> List[str]:
        return sorted(self._by_name.keys())

    def cls(self, name: str) -> Type[Any]:
        name = str(name).strip().lower()
        if name not in self._by_name:
            raise KeyError(f"Unknown block: {name}")
        return self._by_name[name]

    def kind(self, name: str) -> str:
        cls = self.cls(name)
        return str(getattr(cls, "KIND", "fx"))

    def params_schema(self, name: str) -> Dict[str, Dict[str, Any]]:
        cls = self.cls(name)
        return getattr(cls, "PARAMS", {}) or {}

    def default_params(self, name: str) -> Dict[str, Any]:
        schema = self.params_schema(name)
        return {k: v.get("default") for k, v in schema.items()}


BLOCKS = BlockRegistry()
