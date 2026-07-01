from core.plugin import Plugin


class AccessPlugin(Plugin):
    name = "access"
    deps = []
    grammar = r"""
        access_stmt: NAME "::" NAME ("::" NAME)*
    """
    contributes = {}

    @staticmethod
    def parts(node, evaluate=True):
        return [(part.value if evaluate else part) for part in node.children]

    @staticmethod
    def _resolve(kernel, parts):
        if not parts:
            raise NameError("Empty access path.")

        # find root context
        ctx = kernel.context
        while ctx.parent is not None:
            ctx = ctx.parent

        item = ctx.vars.get("__import__")
        if item is None:
            raise NameError("No modules imported.")

        for part in parts:
            if part == "*":
                raise SyntaxError("The use of * is only allowed when importing.")

            if part.startswith("."):
                raise SyntaxError("The use of '.' is only allowed when importing.")

            try:
                item = item[part]
            except Exception as e:
                raise NameError(f"'{part}' no exist.")

        return item

    def exec_access_stmt(kernel, node):

        if hasattr(node, "data") and node.data == "access_stmt":
            parts = AccessPlugin.parts(node)
            return AccessPlugin._resolve(kernel, parts)

    exec_handlers = {"access_stmt": exec_access_stmt}
