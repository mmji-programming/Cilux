from core.plugin import Plugin
from lang.types.builtin import BuiltinFunction


class PrintPlugin(Plugin):
    name = "print"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "print",
            BuiltinFunction("print", PrintPlugin.exec_print_stmt),
            "builtin_function",
            readonly=True,
        )

    def exec_print_stmt(kernel, node):
        vals = []
        for child in node.children:
            if hasattr(child, "data") and child.data in ("expr", "arg"):
                val = kernel.execute(child if child.data == "expr" else child.children[0])
                vals.append(val)
        print(*vals)
