from core.plugin import Plugin


class ExprPlugin(Plugin):
    name = "expr"
    deps = []

    grammar = r"""
        expr: list_type 
            | dict_type 
            | call 
            | dot_access 
            | NAME 
            | string_literal 
            | int_literal 
            | bool_literal 
            | access_stmt
            | "(" expr ")"
            
        call: NAME "(" (arg ("," arg)* ","?)? ")"
            | access_stmt "(" (arg ("," arg)* ","?)? ")"
            | dot_access "(" (arg ("," arg)* ","?)? ")"
            
        arg: named_arg 
            | expr
            
        named_arg: NAME "=" expr
    """
    contributes = {}

    def exec_expr(kernel, node):
        return kernel.execute(node.children)

    exec_handlers = {"expr": exec_expr}


class ExprStmtPlugin(Plugin):
    name = "expr_stmt"
    deps = ["expr"]
    grammar = r"""
        expr_stmt: expr ";"? 
    """
    contributes = {"statement": ["expr_stmt"]}

    def exec_expr_stmt(kernel, node):
        kernel.execute(node.children[0])

    exec_handlers = {"expr_stmt": exec_expr_stmt}
