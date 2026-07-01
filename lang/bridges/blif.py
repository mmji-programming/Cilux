from core.plugin import Plugin, Value
from lang.types.builtin import (
    BuiltinFunction,
)
from lang.funcs.synth.types import (
    SynthResult,
)
from lang.funcs.synth.analysis import (
    render_blif,
)

from pathlib import Path


class BlifResult(Value):
    def __init__(self, data: str):
        self.data = data
        self.vtype = "blif"

    def __repr__(self):
        return self.data

    def __export__(self, path: Path) -> str:
        return self.data


class BlifPlugin(Plugin):
    name = "blif"
    deps = ["circuit", "gate"]

    def on_load(self, kernel):
        kernel.context.declare(
            "blif",
            BuiltinFunction("blif", BlifPlugin.exec_blif),
            readonly=True,
        )

    def exec_blif(kernel, node):
        target = None

        for child in node.children[1:]:
            if hasattr(child, "data") and child.data == "arg":
                inner = child.children[0]
                if not (hasattr(inner, "data") and inner.data == "named_arg"):
                    target = kernel.execute(inner)
                    break

        if target is None:
            raise TypeError("blif() requires a SynthResult as the first argument.")

        if not isinstance(target, SynthResult):
            raise TypeError(f"blif() expected a SynthResult, got '{type(target).__name__}'. Use synth() first.")

        db = target.data
        return BlifResult(render_blif(db, db.module_name, sub_dbs=db.sub_dbs))
