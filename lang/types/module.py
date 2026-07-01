from core.plugin import Value

from pathlib import Path


class ModuleType(Value):
    def __init__(self, name: str, path: Path, exports: dict = None, is_builtin: bool = False):
        super().__init__(name, "module")
        self.path = path
        self.is_builtin = is_builtin
        self._exports = exports or {}

    def __setitem__(self, key, value):
        raise PermissionError(f"Cannot modify module '{self.data}'.")

    def __getitem__(self, key):
        return self._exports[key]

    def __contains__(self, key):
        return key in self._exports

    def get(self, key, default=None):
        return self._exports.get(key, default)

    def items(self):
        return self._exports.items()

    def keys(self):
        return self._exports.keys()

    def __repr__(self):
        if self.is_builtin:
            return f"<module '{self.data}' (builtin)>"
        return f"<module '{self.data}' ({self.path})>"

    def __str__(self):
        return self.__repr__()
