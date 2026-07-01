from core.plugin import Plugin, Value
from .types import (
    SynthResult,
)
from .elaborator import (
    Elaborator,
)
from lang.types.builtin import (
    BuiltinFunction,
)
from .passes import (
    CompilerPasses,
    NetlistOptimizer,
)
from .analysis import (
    max_resolution_level,
)


class SynthPlugin(Plugin):
    name = "synth"
    deps = ["circuit", "expr", "str", "dict", "range"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "synth",
            BuiltinFunction("synth", SynthPlugin.exec_synth),
            "builtin_function",
            readonly=True,
        )

    def exec_synth(kernel, node):
        target_obj = None
        resolution_level = None
        export_mode = False

        for i, child in enumerate(node.children):
            if i == 0:
                continue
            if hasattr(child, "data") and child.data == "arg":
                inner = child.children[0]
                if hasattr(inner, "data") and inner.data == "named_arg":
                    key = inner.children[0].value
                    if key == "resolution_level":
                        val = kernel.execute(inner.children[1])
                        resolution_level = val.data if isinstance(val, Value) else val
                    elif key == "export":
                        val = kernel.execute(inner.children[1])
                        export_mode = val.data if isinstance(val, Value) else val
                else:
                    val = kernel.execute(inner)
                    if hasattr(val, "vtype") and val.vtype in ("gate", "circuit"):
                        target_obj = val
                    elif isinstance(val, Value):
                        try:
                            target_obj = kernel.context.lookup(val.data)
                        except NameError:
                            target_obj = val
                    else:
                        try:
                            target_obj = kernel.context.lookup(str(val))
                        except NameError:
                            target_obj = val

        if target_obj is None:
            raise TypeError("No valid circuit or gate provided")

        if export_mode and resolution_level is not None:
            raise TypeError(
                "synth() received both 'resolution_level' and 'export=true'. "
                "'export=true' automatically computes the maximum resolution_level, "
                "so specifying 'resolution_level' explicitly is not allowed alongside it. "
                "Use one or the other."
            )

        if resolution_level is None:
            resolution_level = max_resolution_level(target_obj, kernel) if export_mode else 0

        try:
            elaborator = Elaborator(kernel, target_obj, int(resolution_level))
            db = elaborator.elaborate()

            CompilerPasses.detect_combinational_loops(db)

            db = NetlistOptimizer.optimize(db)
            CompilerPasses.remove_phantom_signals(db)
            CompilerPasses.validate_graph(db)

            # Set attrs
            db.module_name = target_obj.data
            db.sub_dbs = elaborator.sub_dbs

            return SynthResult(db)

        except RecursionError as re:
            raise RecursionError(f"Compilation Error: {re}")
        except ValueError as e:
            raise RuntimeError(f"Cilux Design Error: {e}")
        except Exception as e:
            import traceback

            raise RuntimeError(f"Synthesis Internal Error: {e}\n{traceback.format_exc()}")
