from core.plugin import Plugin
from lang.types.builtin import (
    BuiltinFunction,
)
from lang.types._str import (
    StringValue,
)


class TypeofPlugin(Plugin):
    name = "typeof"
    deps = ["expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "typeof",
            BuiltinFunction("typeof", TypeofPlugin.exec_typeof),
            "builtin_function",
            readonly=True,
        )

    def exec_typeof(kernel, node):
        arg_node = node.children[1]
        inner = arg_node.children[0]

        if hasattr(inner, "type") and inner.type == "NAME":
            name = inner.value
            try:
                val = kernel.context.lookup(name)
                return StringValue(val.vtype if hasattr(val, "vtype") else "value")
            except NameError:
                return StringValue("undefined")

        val = kernel.execute(inner)
        return StringValue(val.vtype if hasattr(val, "vtype") else "value")
