from core.plugin import Plugin, Value
from lang.types.builtin import BuiltinFunction
from lang.types.bit import BitValue


class NotPlugin(Plugin):
    name = "NOT"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "NOT",
            BuiltinFunction("NOT", NotPlugin.exec_not_expr),
            "builtin_function",
            readonly=True,
        )

    def exec_not_expr(kernel, node):

        if hasattr(node.children[1], "data") and node.children[1].data == "arg":
            if len(node.children) > 2:
                raise TypeError("NOT() takes exactly 1 argument")
            inner = node.children[1].children[0]
        else:
            inner = node.children[1]

        val = kernel.execute(inner)
        v = val.data if isinstance(val, Value) else val
        return BitValue(0 if v else 1)
