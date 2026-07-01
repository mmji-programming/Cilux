from core.plugin import Plugin
from lang.io.utils import (
    base_dir,
)
from lang.types.module import (
    ModuleType,
)


from pathlib import Path
from typing import Dict, Any

BASE_DIR = base_dir()
LIBS = "lib"
MODULE_EXTENSION = ".clx"
MRO = [
    Path.cwd(),
    BASE_DIR / LIBS / "builtin",
]


class ImportPlugin(Plugin):
    name = "import"
    deps = ["access"]
    grammar = r"""
        import_stmt: "import" import_expr ";"

        import_expr: (BACK "::")? NAME ("::" NAME)* ("::" (STAR | "{" selective_import "}"))?

        selective_import: import_item ("," import_item)* ","?
        import_item: NAME ("as" NAME)?
        
        BACK: "."+
        STAR: "*"
    """

    contributes = {"statement": ["import_stmt"]}

    def __init__(self):
        self._file_cache: Dict[str, Dict[str, Any]] = {}
        self._loading: set = set()

    def on_load(self, kernel):
        kernel.context.declare("__import__", {})
        kernel.context.protect("__import__")

    @staticmethod
    def exec_import_stmt(kernel, node):
        plugin = kernel.plugins.get("import")
        if plugin is None:
            raise RuntimeError("ImportPlugin not registered")
        return plugin._execute_import(kernel, node)

    def _execute_import(self, kernel, node):

        expr = node.children[0]
        parts, wildcard, selective = self._parse_expr(expr)

        if wildcard:
            self._import_wildcard(kernel, parts)
            return

        if selective:
            self._import_selective(kernel, parts, selective)
            return

        try:
            module_path = self._find_module(parts)

        except ImportError:
            if len(parts) > 1:
                module_parts = parts[:-1]
                symbol_name_raw = parts[-1]
                symbol_name = symbol_name_raw[1] if isinstance(symbol_name_raw, tuple) else symbol_name_raw

                module = self._load_exports(kernel, module_parts)
                exports = module._exports if hasattr(module, "_exports") else module
                if symbol_name not in exports:
                    raise ImportError(
                        f"'{symbol_name}' not found in "
                        f"'{'::'.join(p[1] if isinstance(p, tuple) else p for p in module_parts)}'"
                    )

                symbol = exports[symbol_name]

                if hasattr(symbol, "data") or hasattr(symbol, "__call__"):
                    try:
                        existing = getattr(symbol, "_module_exports", None)
                        if existing is None:
                            (
                                object.__setattr__(symbol, "_module_exports", exports)
                                if hasattr(symbol, "__dict__")
                                else None
                            )
                            if hasattr(symbol, "_module_exports") is False:
                                symbol._module_exports = exports
                    except (AttributeError, TypeError):
                        pass

                kernel.context.declare(symbol_name, symbol)
                return
            else:
                raise

        self._import_namespace(kernel, parts)

    def _parse_expr(self, node):
        parts = []
        wildcard = False
        selective = None

        def walk(n):
            nonlocal wildcard, selective

            if hasattr(n, "type"):
                if n.type == "NAME":
                    parts.append(n.value)
                elif n.type == "BACK":
                    parts.append(("BACK", n.value))
                elif n.type == "STAR" or n.value == "*":
                    wildcard = True
                return

            if hasattr(n, "data"):
                if n.data == "selective_import":
                    selective = self._parse_selective(n)
                    return
                if n.data == "import_item":
                    return

            if hasattr(n, "children"):
                for child in n.children:
                    walk(child)

        walk(node)
        return parts, wildcard, selective

    def _parse_selective(self, node):
        items = {}
        for child in node.children:
            if hasattr(child, "data") and child.data == "import_item":
                orig = child.children[0].value
                alias = child.children[1].value if len(child.children) > 1 else orig
                items[orig] = alias
        return items

    def _find_module(self, parts):
        clean_parts = []
        back_depth = 0

        for part in parts:
            if isinstance(part, tuple) and part[0] == "BACK":
                back_depth = len(part[1]) - 1
            else:
                clean_parts.append(part)

        relative = Path(*clean_parts) if clean_parts else Path(".")

        for base in MRO:
            actual_base = base
            for _ in range(back_depth):
                actual_base = actual_base.parent

            file_path = (actual_base / relative).with_suffix(MODULE_EXTENSION)
            if file_path.exists() and file_path.is_file():
                return file_path

            dir_path = actual_base / relative
            if dir_path.exists() and dir_path.is_dir():
                return dir_path

        display = "::".join(p[1] if isinstance(p, tuple) else p for p in parts)
        raise ImportError(f"Module '{display}' not found")

    def _load_exports(self, kernel, parts):
        module_key = "::".join(p[1] if isinstance(p, tuple) else p for p in parts)

        if module_key in self._loading:
            chain = " -> ".join(list(self._loading) + [module_key])
            raise ImportError(f"Circular import detected: {chain}")

        self._loading.add(module_key)

        try:
            path = self._find_module(parts)
            file_key = str(path.resolve()) if not path.is_dir() else str(path)

            if file_key in self._file_cache:
                return self._file_cache[file_key]

            if path.is_dir():
                exports = self._load_directory(kernel, path, parts)
            else:
                exports = self._execute_file(kernel, path)

            name = parts[-1] if not isinstance(parts[-1], tuple) else parts[-1][1]
            is_builtin = "builtin" in str(path.resolve())
            module = ModuleType(name, path, exports, is_builtin)

            self._file_cache[file_key] = module
            self._file_cache[module_key] = module
            return module

        finally:
            self._loading.discard(module_key)

    def _load_directory(self, kernel, dirpath, parts):
        result = {}

        for item in sorted(dirpath.iterdir()):
            if item.name.startswith("_") or item.name.startswith("."):
                continue

            if item.is_dir():
                result[item.name] = self._load_exports(kernel, parts + [item.name])

            elif item.suffix == MODULE_EXTENSION:
                result[item.stem] = self._load_exports(kernel, parts + [item.stem])

        return result

    def _execute_file(self, kernel, filepath):
        filepath = Path(filepath).resolve()
        cache_key = str(filepath)

        if cache_key in self._file_cache:
            cached = self._file_cache[cache_key]
            if hasattr(cached, "_exports"):
                return cached._exports
            return cached

        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()

        old_mro = list(MRO)
        MRO.insert(0, filepath.parent)

        try:
            tree = kernel.parse(source)
            before = set(kernel.context.vars.keys())

            for stmt in tree.children:
                kernel.execute(stmt)

            after = kernel.context.vars
            new_names = []
            exports = {}

            for name in after:
                if name not in before:
                    obj = after[name]
                    if obj is not None:
                        exports[name] = obj
                        new_names.append(name)

            for name in new_names:
                del kernel.context.vars[name]

            self._file_cache[cache_key] = exports
            return exports

        finally:
            MRO[:] = old_mro

    def _import_namespace(self, kernel, parts):

        module = self._load_exports(kernel, parts)

        name = module.data

        import_registry = kernel.context.vars.get("__import__", {})
        import_registry[name] = module
        kernel.context.vars["__import__"] = import_registry

        kernel.context.declare(name, module)

    def _import_wildcard(self, kernel, parts):
        module = self._load_exports(kernel, parts)
        for name, obj in module._exports.items():
            kernel.context.declare(name, obj)

    def _import_selective(self, kernel, parts, items):
        module = self._load_exports(kernel, parts)
        exports = module._exports if hasattr(module, "_exports") else module
        for original, alias in items.items():
            if original not in exports:
                raise ImportError(f"'{original}' not found in module")
            symbol = exports[original]
            try:
                if not hasattr(symbol, "_module_exports"):
                    symbol._module_exports = exports
            except (AttributeError, TypeError):
                pass
            kernel.context.declare(alias, symbol)

    exec_handlers = {"import_stmt": exec_import_stmt}
