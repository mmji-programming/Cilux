from core.plugin import Plugin, Value


class StringValue(Value):
    def __init__(self, data):
        super().__init__(data, "string")

    def __repr__(self):
        return self.data


class StringPlugin(Plugin):
    name = "str"
    deps = []
    grammar = r"""
        string_literal: STRING | CHAR
        CHAR: /'[^']'/
        STRING: /"[^"]*"/
    """
    contributes = {}

    def exec_string_literal(kernel, node):
        val = node.children[0].value
        return StringValue(val[1:-1])

    exec_handlers = {"string_literal": exec_string_literal}
