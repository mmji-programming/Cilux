from core.plugin import Plugin, Value
from lang.types.builtin import (
    BuiltinFunction,
)
from lang.io.path_op import (
    path_check,
    PathOp,
    PathCheckError,
)
from lang.types.unwrap import (
    unwrap,
)

import inspect
from pathlib import Path


class ExportError(Exception):
    __module__ = Exception.__module__


class ExportResult(Value):
    def __init__(self, output_path: Path):
        super().__init__(str(output_path), "export_result")


class ExportPlugin(Plugin):
    name = "export"
    deps = []
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "export",
            BuiltinFunction("export", ExportPlugin.exec_export),
            "builtin_function",
            readonly=True,
        )

    def exec_export(kernel, node):

        target = None
        to = None
        positional = []
        named_args = {}

        for child in node.children[1:]:
            if not (hasattr(child, "data") and child.data == "arg"):
                continue

            inner = child.children[0]

            if hasattr(inner, "data") and inner.data == "named_arg":
                key = inner.children[0].value
                val = kernel.execute(inner.children[1])
                named_args[key] = val.data if isinstance(val, Value) else val
            else:
                val = kernel.execute(inner)
                if target is None:
                    target = val
                else:
                    positional.append(val.data if isinstance(val, Value) else val)

        to = named_args.pop("to", None)

        if target is None:
            raise TypeError("export() requires a value to export as the first argument.")

        if not hasattr(target, "__export__"):
            raise TypeError(
                f"'{type(target).__name__}' does not support export. "
                f"The plugin must implement __export__(path, ...) on its result type."
            )

        if to is None:
            if positional:
                to = positional.pop(0)
            else:
                raise TypeError("export() requires a 'to' argument.")

        try:
            path = path_check(to, PathOp.HAS_EXTENSION)
            path_check(path, PathOp.PARENT_EXISTS)
        except PathCheckError as e:
            raise ExportError(str(e))

        if positional:
            try:
                sig = inspect.signature(target.__export__)
                params = [
                    p
                    for p in sig.parameters.values()
                    if p.name not in ("self", "path")
                    and p.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                for i, val in enumerate(positional):
                    if i < len(params):
                        pname = params[i].name
                        if pname not in named_args:
                            named_args[pname] = val
            except (ValueError, TypeError):
                pass

        named_args = {k: unwrap(v) for k, v in named_args.items()}

        result = target.__export__(path, **named_args)

        if result is None:
            pass

        elif isinstance(result, bytes):
            path.write_bytes(result)

        elif isinstance(result, str):
            path.write_text(result, encoding="utf-8")

        elif hasattr(result, "read"):
            mode = getattr(result, "mode", "b")
            if "b" in mode:
                path.write_bytes(result.read())
            else:
                path.write_text(result.read(), encoding="utf-8")

        else:
            raise ExportError(
                f"__export__() returned an unsupported type '{type(result).__name__}'. "
                f"Expected: None (wrote itself), str, bytes, or file-like object."
            )

        return ExportResult(path)
