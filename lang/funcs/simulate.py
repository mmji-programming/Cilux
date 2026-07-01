from core.plugin import Plugin
from lang.types.builtin import (
    BuiltinFunction,
)
from lang.structs.clock import (
    ClockValue,
)
from lang.structs.scheduler import (
    get_scheduler,
)


class SimulatePlugin(Plugin):
    name = "simulate"
    deps = ["clock", "when"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "simulate",
            BuiltinFunction("simulate", SimulatePlugin.exec_simulate),
            "builtin_function",
            readonly=True,
        )

    def exec_simulate(kernel, node):
        kwargs = {}

        def _extract_named_args(children):
            for child in children:
                if not hasattr(child, "data"):
                    continue
                if child.data == "named_arg":
                    key = child.children[0].value
                    val = kernel.execute(child.children[1])
                    # Unwrap Value wrappers uniformly
                    if isinstance(val, ClockValue):
                        kwargs[key] = val.data
                    elif hasattr(val, "data"):
                        kwargs[key] = val.data
                    else:
                        kwargs[key] = val
                elif child.data == "arg" and child.children:
                    # Unwrap the arg wrapper and recurse
                    _extract_named_args(child.children)

        _extract_named_args(node.children)

        cycles = kwargs.get("cycles")
        sim_time = kwargs.get("time")

        if cycles is not None:
            cycles = int(cycles)
        if sim_time is not None:
            sim_time = float(sim_time)

        get_scheduler(kernel).run(cycles=cycles, time=sim_time)
