from core.plugin import Plugin


class ConditionPlugin(Plugin):
    name = "condition"
    deps = ["expr"]
    grammar = r"""
        condition_stmt: IF "(" condition ")" "{" if_block "}" (ELIF "(" condition ")" "{" if_block "}")* (ELSE "{" if_block "}")*
        condition: expr
        if_block: statement*

        IF: "if"
        ELSE: "else"
        ELIF: "elif"
    """
    contributes = {"statement": ["condition_stmt"]}

    def exec_condition(kernel, node):
        if not hasattr(node, "data") or node.data != "condition_stmt":
            return

        state = None
        cond_result = None
        block_executed = False

        for child in node.children:
            if hasattr(child, "type"):
                state = child.type

                if block_executed:
                    continue

                if state == "ELSE":
                    continue

                continue

            if hasattr(child, "data") and child.data == "condition":
                cond = kernel.execute(child.children[0])
                cond_result = cond.data if hasattr(cond, "data") else cond
                continue

            if hasattr(child, "data") and child.data == "if_block":
                if state == "ELSE" and not block_executed:
                    for stmt in child.children:
                        kernel.execute(stmt)
                    block_executed = True

                elif state in ("IF", "ELIF") and cond_result and not block_executed:
                    for stmt in child.children:
                        kernel.execute(stmt)
                    block_executed = True

                cond_result = None
                continue

    exec_handlers = {"condition_stmt": exec_condition}
