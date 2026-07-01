from core.plugin import Plugin, Value
from functools import reduce
from lang.types.bit import BitValue
from lang.types.builtin import BuiltinFunction


class NandPlugin(Plugin):
    name = "NAND"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):

        kernel.context.declare(
            "NAND",
            BuiltinFunction("NAND", NandPlugin.exec_nand),
            "builtin_function",
            readonly=True,
        )

    def exec_nand(kernel, node):
        vals = []
        for child in node.children:
            if hasattr(child, "data"):
                if child.data == "arg":
                    vals.append(kernel.execute(child.children[0]))
                elif child.data == "expr":
                    vals.append(kernel.execute(child))

        bits = [v.data if isinstance(v, Value) else v for v in vals]
        _and = reduce(lambda x, y: x & y, bits) if bits else 0
        nand = _and ^ 1
        return BitValue(nand)
