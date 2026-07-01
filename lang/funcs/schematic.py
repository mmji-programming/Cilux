from core.plugin import Plugin, Value
from lang.types.builtin import (
    BuiltinFunction,
)
from .synth.types import (
    SynthResult,
)
from .synth.renderers import ASCIIBackend, NetlistDB
from .utils import (
    Path,
    to_png,
    to_svg,
)
from lang.engines.render_schemdraw_engine import (
    draw_schematic,
    show_schematic,
)

SUPPORTED_MODES = {"text", "gui"}


class SchematicError(Exception):
    __module__ = Exception.__module__


class SchematicResult(Value):
    def __init__(self, data: NetlistDB, mode: str):
        self.data = data
        self.vtype = "schematic"
        self.mode = mode

    def __repr__(self):
        if self.mode == "gui":
            show_schematic(self.data, self.data.module_name, fontsize=9)
            return "<schematic (gui)>"

        return ASCIIBackend.render(self.data, self.data.module_name)

    def __export__(self, path: Path, dpi: int = 150, font=None) -> str | None:
        suffix = path.suffix.lower()

        if self.mode == "text":
            if suffix == ".txt":
                return ASCIIBackend.render(self.data, self.data.module_name)
            if suffix in {".png", ".svg"}:
                content = ASCIIBackend.render(self.data, self.data.module_name)
                if suffix == ".png":
                    to_png(text=content, filename=path, font=font)
                else:
                    to_svg(text=content, filename=path)
                return None
            raise SchematicError(f"mode='text' supports .txt  .png  .svg — got '{suffix}'.")

        if self.mode == "gui":
            if suffix in {".png", ".svg", ".pdf"}:
                draw_schematic(
                    self.data,
                    model_name=self.data.module_name,
                    output=str(path),
                    dpi=dpi,
                )
                return None
            raise SchematicError(f"mode='gui' supports .png  .svg  .pdf — got '{suffix}'.")


class SchematicPlugin(Plugin):
    name = "schematic"
    deps = ["synth"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "schematic",
            BuiltinFunction("schematic", SchematicPlugin.exec_schematic),
            "builtin_function",
            readonly=True,
        )

    def exec_schematic(kernel, node):
        named_args = {}
        args = []
        _kwargs = False

        for child in node.children[1:]:
            if not (hasattr(child, "data") and child.data == "arg"):
                continue

            inner = child.children[0]
            if hasattr(inner, "data") and inner.data == "named_arg":
                key = inner.children[0].value
                val = kernel.execute(inner.children[1])
                named_args[key] = val.data if isinstance(val, Value) else val
                _kwargs = True
            else:
                if _kwargs:
                    raise TypeError("Positional argument after keyword argument.")
                val = kernel.execute(inner)
                args.append(val)

        if not args:
            raise TypeError("schematic() requires a SynthResult as the first argument.")

        if not isinstance(args[0], SynthResult):
            raise TypeError(
                f"schematic() expected a SynthResult as the first argument, "
                f"got '{type(args[0]).__name__}'. "
                f"Use synth() to synthesize a circuit first."
            )

        target: SynthResult = args.pop(0)

        if args and "mode" in named_args:
            raise TypeError("schematic() received 'mode' as both a positional and keyword argument.")

        mode = args[0] if args else named_args.get("mode", "text")
        mode = mode.data if isinstance(mode, Value) else mode

        if mode not in SUPPORTED_MODES:
            raise SchematicError(f"Unsupported mode '{mode}'. Expected one of: {', '.join(sorted(SUPPORTED_MODES))}.")

        return SchematicResult(target.data, mode=mode)
