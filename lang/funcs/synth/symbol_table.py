from .types import (
    Signal,
)

from typing import Dict, Optional, Any


class SymbolTable:
    def __init__(self, parent=None):
        self.parent = parent
        self.symbols: Dict[str, Signal] = {}
        self.definitions: Dict[str, Any] = {}

    def bind_signal(self, name: str, sig: Signal):
        self.symbols[name] = sig

    def resolve_signal(self, name: str) -> Optional[Signal]:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent:
            return self.parent.resolve_signal(name)
        return None

    def bind_def(self, name: str, target: Any):
        self.definitions[name] = target

    def resolve_def(self, name: str) -> Any:
        if name in self.definitions:
            return self.definitions[name]
        if self.parent:
            return self.parent.resolve_def(name)
        return None
