from core.plugin import Plugin, Value
from functools import reduce
from lang.types.bit import BitValue
from lang.types.builtin import BuiltinFunction


class XorPlugin(Plugin):
    name = "XOR"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):

        kernel.context.declare(
            "XOR",
            BuiltinFunction("XOR", XorPlugin.exec_xor),
            "builtin_function",
            readonly=True,
        )

    def exec_xor(kernel, node):
        vals = []
        for child in node.children:
            if hasattr(child, "data"):
                if child.data == "arg":
                    vals.append(kernel.execute(child.children[0]))
                elif child.data == "expr":
                    vals.append(kernel.execute(child))

        bits = [v.data if isinstance(v, Value) else v for v in vals]
        return BitValue(reduce(lambda x, y: x ^ y, bits) if bits else 0)
