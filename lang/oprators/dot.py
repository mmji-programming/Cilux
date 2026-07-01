from core.plugin import Plugin


class DotPlugin(Plugin):
    name = "dot"
    deps = []  # Due to the circular dependency problem between expr and dot_access, we leave the dot_access deps blank but set its loading priority higher than expr.
    grammar = r"""
        dot_access: expr "." NAME
    """
    contributes = {}

    def exec_dot(kernel, node):
        obj = kernel.execute(node.children[0])
        prop = node.children[1].value

        if hasattr(obj, "__dot__"):
            return obj.__dot__(prop)

        if hasattr(obj, prop):
            return getattr(obj, prop)

        raise AttributeError(f"'{type(obj).__name__}' has no attribute '{prop}'")

    exec_handlers = {"dot_access": exec_dot}
