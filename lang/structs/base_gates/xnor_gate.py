from core.plugin import Plugin, Value
from functools import reduce
from lang.types.bit import BitValue
from lang.types.builtin import BuiltinFunction


class XnorPlugin(Plugin):
    name = "XNOR"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):

        kernel.context.declare(
            "XNOR",
            BuiltinFunction("XNOR", XnorPlugin.exec_xnor),
            "builtin_function",
            readonly=True,
        )

    def exec_xnor(kernel, node):
        vals = []
        for child in node.children:
            if hasattr(child, "data"):
                if child.data == "arg":
                    vals.append(kernel.execute(child.children[0]))
                elif child.data == "expr":
                    vals.append(kernel.execute(child))

        bits = [v.data if isinstance(v, Value) else v for v in vals]
        xor = reduce(lambda x, y: x ^ y, bits) if bits else 0
        xnor = xor ^ 1
        return BitValue(xnor)
