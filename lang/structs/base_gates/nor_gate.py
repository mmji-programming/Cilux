from core.plugin import Plugin, Value
from functools import reduce
from lang.types.bit import BitValue
from lang.types.builtin import BuiltinFunction


class NorPlugin(Plugin):
    name = "NOR"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):

        kernel.context.declare(
            "NOR",
            BuiltinFunction("NOR", NorPlugin.exec_nor),
            "builtin_function",
            readonly=True,
        )

    def exec_nor(kernel, node):
        vals = []
        for child in node.children:
            if hasattr(child, "data"):
                if child.data == "arg":
                    vals.append(kernel.execute(child.children[0]))
                elif child.data == "expr":
                    vals.append(kernel.execute(child))

        bits = [v.data if isinstance(v, Value) else v for v in vals]
        _or = reduce(lambda x, y: x | y, bits) if bits else 0
        nor = _or ^ 1
        return BitValue(nor)
