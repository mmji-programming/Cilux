from core.plugin import Plugin, Value


class ListValue(Value):
    def __init__(self, data):
        super().__init__(data, "list")

    def __getitem__(self, key):
        return self.data[key]

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        return iter(self.data)


class ListType(Plugin):
    name = "list"
    deps = ["expr"]
    grammar = r"""
        list_type: "[" (expr ("," expr)*)? ","? "]"
    """
    contributes = {}

    def exec_list_type(kernel, node):
        return ListValue([kernel.execute(c) for c in node.children])

    exec_handlers = {"list_type": exec_list_type}
