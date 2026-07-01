from core.plugin import Plugin, Value
from lang.types.port import (
    Port,
    Wire,
)
from lang.types._dict import (
    DictValue,
)
from lang.types.bit import (
    BitValue,
)
from lang.oprators._range import (
    RangePlugin,
)
from lang.oprators.wire import (
    WirePlugin,
)


class Gate(Value):
    def __init__(self, name, inputs, outputs, body):
        super().__init__(name, "gate")
        self.name = name
        self.input = [Port(i, "input") for i in inputs]
        self.output = [Port(o, "output") for o in outputs]
        self.body = body
        self.wire_map = []

    def __dot__(self, prop):
        for p in self.input:
            if p.name == prop:
                return p
        for p in self.output:
            if p.name == prop:
                return p
        if prop == "input":
            return [p.name for p in self.input]
        if prop == "output":
            return [p.name for p in self.output]

        raise AttributeError(f"'{prop}' not found")

    @property
    def input_names(self):
        return [p.name for p in self.input]

    @property
    def output_names(self):
        return [p.name for p in self.output]

    def get_params(self):
        return [p.name for p in self.input]

    def add_wire(self, source_str, target_str):
        w = Wire()
        w.source = source_str
        w.target = target_str
        self.wire_map.append(w)
        return w

    def __call__(self, kernel, **kwargs):
        child_ctx = kernel.context.create_child()
        old_ctx = kernel.context
        kernel.context = child_ctx

        for inp in self.input:
            val = kwargs.get(inp.name, 0)
            if hasattr(val, "data"):
                val = val.data
            if isinstance(val, Value):
                val = val.data
            inp.write(val)
            child_ctx.declare(inp.name, val)

        module_exports = getattr(self, "_module_exports", None)
        if module_exports:
            for sib_name, sib_obj in module_exports.items():
                if sib_name not in child_ctx.vars:
                    child_ctx.declare(sib_name, sib_obj)

        for stmt in self.body:
            kernel.execute(stmt)

        result = {}
        for out in self.output:
            if out.name in child_ctx.vars:
                val = child_ctx.lookup(out.name)
                result[out.name] = BitValue(val.data if isinstance(val, Value) else val)

        kernel.context = old_ctx
        return DictValue(result)

    def __repr__(self):
        return f"<gate {self.name}>"


class GatePlugin(Plugin):
    name = "gate"
    deps = ["expr", "wire"]
    grammar = r"""
        gate_def: "gate" NAME "{" gate_body "}"
        gate_body: (input_stmt | output_stmt | gate_stmt)*
        input_stmt: "input" (NAME | range_def) ("," (NAME | range_def))* ";"
        output_stmt: "output" (NAME | range_def) ("," (NAME | range_def))* ";"
        gate_stmt: wire_stmt | var_stmt | expr_stmt | gate_def
    """
    contributes = {"statement": ["gate_def"]}

    def exec_gate_def(kernel, node):
        name = node.children[0].value

        current = kernel.context.vars.get("__current_scope__")
        if current and current["type"] == "gate":
            raise SyntaxError("Nested gates are not allowed")

        body_node = node.children[1]

        def check_nested(n):
            if hasattr(n, "data"):
                if n.data == "gate_def":
                    raise SyntaxError("Nested gates are not allowed")
                for child in n.children:
                    check_nested(child)

        check_nested(body_node)

        inputs, outputs, body = [], [], []
        wires = []
        prev_scope = kernel.context.vars.get("__current_scope__")
        kernel.context.vars["__current_scope__"] = {
            "name": name,
            "type": "gate",
            "io": set(),
        }

        for child in body_node.children:
            if not hasattr(child, "data"):
                continue

            inner = child.children[0] if child.data == "gate_stmt" else child

            if inner.data == "wire_stmt":
                result = WirePlugin.extract_wire_map(inner)
                if result:
                    wires.append(result)
                body.append(inner)
            elif inner.data == "input_stmt":
                for c in inner.children:
                    if hasattr(c, "data") and c.data == "range_def":
                        inputs.extend(RangePlugin.exec_range_def(kernel, c))
                    elif c.type == "NAME":
                        inputs.append(c.value)
            elif inner.data == "output_stmt":
                for c in inner.children:
                    if hasattr(c, "data") and c.data == "range_def":
                        outputs.extend(RangePlugin.exec_range_def(kernel, c))
                    elif c.type == "NAME":
                        outputs.append(c.value)
            elif child.data == "gate_stmt":
                body.append(inner)

        if prev_scope is not None:
            kernel.context.vars["__current_scope__"] = prev_scope
        else:
            kernel.context.vars.pop("__current_scope__", None)

        gate = Gate(name, inputs, outputs, body)

        for wire_dict in wires:
            src = wire_dict["src"]
            dst = wire_dict["dst"]
            gate.add_wire(src, dst)

        kernel.context.declare(name, gate)

    exec_handlers = {"gate_def": exec_gate_def}
