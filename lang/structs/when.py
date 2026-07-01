from core.plugin import Plugin, Value


class WhenBlock:
    def __init__(self, kernel, clock_name, edge, body_stmts, context, is_reset=False):
        self.kernel = kernel
        self.clock_name = clock_name
        self.edge = edge
        self.body_stmts = body_stmts
        self.context = context
        self.buffer = {}
        self.is_reset = is_reset

    def evaluate(self):
        self.kernel._active_when_block = self
        try:
            for stmt in self.body_stmts:
                self._eval(stmt)
        finally:
            self.kernel._active_when_block = None

    def commit(self):
        for target, value in self.buffer.items():
            if target in self.context.vars:
                self.context.assign(target, value)
            else:
                self.context.declare(target, value)
        self.buffer.clear()

    def _eval(self, node):
        if hasattr(node, "data") and node.data == "seq_assign":
            target = node.children[0].value
            source = self._eval(node.children[1])
            self.buffer[target] = source
            return source

        old_ctx = self.kernel.context
        self.kernel.context = self.context
        try:
            result = self.kernel.execute(node)
            return result.data if hasattr(result, "data") else result
        finally:
            self.kernel.context = old_ctx


class WhenPlugin(Plugin):
    name = "when"
    deps = ["condition", "circuit", "gate"]
    grammar = r"""
        when_stmt: "when" NAME ON STATE "{" when_body "}"
        when_body: (seq_assign | condition_stmt)*
        seq_assign: NAME "<=" expr ";"
                
        ON: "@"
        STATE: "posedge"
             | "negedge"
    """
    contributes = {"statement": ["when_stmt", "seq_assign"]}

    def exec_seq_assign(kernel, node):
        target = node.children[0].value

        active = getattr(kernel, "_active_when_block", None)
        if active is not None:
            source = kernel.execute(node.children[1])
            active.buffer[target] = source.data if isinstance(source, Value) else source
            return

        if target not in kernel.context.vars:
            kernel.context.declare(target, 0)

    def exec_when_stmt(kernel, node):
        clock_name = node.children[0].value
        edge = node.children[2].value
        body = node.children[3]

        if hasattr(body, "children"):
            for stmt in body.children:
                kernel.execute(stmt)
        else:
            kernel.execute(body)

        stmts = body.children if hasattr(body, "children") else [body]
        clock_obj = kernel.context.lookup(clock_name)
        clock = clock_obj.data

        is_reset = edge == "negedge" and not clock.sync

        block = WhenBlock(kernel, clock_name, edge, stmts, kernel.context, is_reset)
        clock.add_handler(block, edge)

    exec_handlers = {
        "when_stmt": exec_when_stmt,
        "seq_assign": exec_seq_assign,
    }
