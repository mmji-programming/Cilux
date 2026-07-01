from core.plugin import Plugin, Value


class BoolValue(Value):
    def __init__(self, data):
        super().__init__(data, "bool")

    def __repr__(self):
        return "true" if self.data else "false"


class BoolPlugin(Plugin):
    name = "bool"
    deps = []
    grammar = r"""
        bool_literal: BOOL_TRUE | BOOL_FALSE
        BOOL_TRUE: "true"
        BOOL_FALSE: "false"
    """
    contributes = {}

    def exec_bool_literal(kernel, node):
        return BoolValue(node.children[0].type == "BOOL_TRUE")

    exec_handlers = {"bool_literal": exec_bool_literal}
