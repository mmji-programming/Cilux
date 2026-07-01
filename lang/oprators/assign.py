from core.plugin import Plugin
from .access import (
    AccessPlugin,
)


class AssignPlugin(Plugin):
    name = "var"
    deps = ["expr", "access"]
    grammar = r"""
        var_stmt: expr "=" expr ";"
    """
    contributes = {"statement": ["var_stmt"]}

    def exec_var_stmt(kernel, node):

        target_node = node.children[0]
        value = kernel.execute(node.children[1])

        if hasattr(target_node, "data") and target_node.data == "expr":
            target = target_node.children[0]
        else:
            target = target_node

        vtype = value.vtype if hasattr(value, "vtype") else None

        if hasattr(target, "type") and target.type == "NAME":
            name = target.value
            if name in kernel.context.vars:
                kernel.context.assign(name, value, vtype)
            else:
                kernel.context.declare(name, value, vtype)
            return

        if hasattr(target, "data") and target.data == "access_stmt":
            parts = [c.value for c in target.children if hasattr(c, "value")]
            obj = AccessPlugin._resolve(kernel, parts[:-1])
            var_name = parts[-1]
            obj[var_name] = value
            return

        raise SyntaxError(f"Cannot assign to {target}")

    exec_handlers = {"var_stmt": exec_var_stmt}
