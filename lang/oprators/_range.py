from core.plugin import Plugin


class RangePlugin(Plugin):
    name = "range"
    deps = ["int"]
    grammar = r"""
        range_def: NAME "[" int_literal ":" int_literal "]"
    """
    contributes = {}

    @staticmethod
    def resolve(name, high, low):
        if high >= low:
            return [f"{name}{i}" for i in range(high, low - 1, -1)]
        else:
            return [f"{name}{i}" for i in range(high, low + 1)]

    @staticmethod
    def exec_range_def(kernel, node):
        name = node.children[0].value
        high = kernel.execute(node.children[1]).data
        low = kernel.execute(node.children[2]).data
        return RangePlugin.resolve(name, high, low)

    exec_handlers = {"range_def": exec_range_def}
