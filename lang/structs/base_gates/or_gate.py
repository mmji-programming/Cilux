from core.plugin import Plugin, Value
from functools import reduce
from lang.types.bit import BitValue
from lang.types.builtin import BuiltinFunction


class OrPlugin(Plugin):
    name = "OR"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):

        kernel.context.declare(
            "OR",
            BuiltinFunction("OR", OrPlugin.exec_or),
            "builtin_function",
            readonly=True,
        )

    def exec_or(kernel, node):
        vals = []
        for child in node.children:
            if hasattr(child, "data"):
                if child.data == "arg":
                    vals.append(kernel.execute(child.children[0]))
                elif child.data == "expr":
                    vals.append(kernel.execute(child))

        bits = [v.data if isinstance(v, Value) else v for v in vals]
        return BitValue(reduce(lambda x, y: x | y, bits) if bits else 0)
