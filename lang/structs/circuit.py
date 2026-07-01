from core.plugin import Plugin, Value
from lang.oprators._range import (
    RangePlugin,
)
from lang.structs.gate import (
    Gate,
)


class Circuit(Gate):
    def __init__(self, name, inputs, outputs, instances, body):
        super().__init__(name, inputs, outputs, body)
        self.instances = instances

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

        root_ctx = old_ctx
        while root_ctx.parent is not None:
            root_ctx = root_ctx.parent

        for out in self.output:
            found = False
            for var_name, var_val in root_ctx.vars.items():
                if isinstance(var_val, LiveResult) and out.name in var_val._keys:
                    try:
                        old_val = var_val._ctx.lookup(out.name)
                        child_ctx.declare(
                            out.name,
                            old_val.data if isinstance(old_val, Value) else old_val,
                        )
                        found = True
                        break
                    except NameError:
                        pass
            if not found:
                child_ctx.declare(out.name, 0)

        for stmt in self.body:
            if stmt.data == "statement" and stmt.children[0].data == "circuit_def":
                kernel.execute(stmt.children[0])

        for inst_name, circuit_ref in self.instances:
            if isinstance(circuit_ref, str):
                try:
                    circuit = child_ctx.lookup(circuit_ref)
                except NameError:
                    module_exports = getattr(self, "_module_exports", None)
                    if module_exports and circuit_ref in module_exports:
                        circuit = module_exports[circuit_ref]
                    else:
                        raise ImportError(
                            f"'{circuit_ref}' is not defined. If it belongs to "
                            f"another module, import it explicitly, e.g. "
                            f"import <module>::{circuit_ref};"
                        )
            else:
                circuit = circuit_ref
            child_ctx.declare(inst_name, circuit)

        for stmt in self.body:
            if stmt.data == "statement" and stmt.children[0].data != "circuit_def":
                kernel.execute(stmt.children[0])

        output_keys = {out.name for out in self.output}

        SEQUENTIAL_NODE_TYPES = {
            "when_stmt",
            "circuit_def",
            "clock_stmt",
            "seq_assign",
            "gate_def",
        }

        def _is_sequential(stmt):
            if not hasattr(stmt, "data"):
                return False
            # direct match
            if stmt.data in SEQUENTIAL_NODE_TYPES:
                return True
            # wrapped in "statement" or "circuit_stmt" / "gate_stmt"
            if stmt.data in ("statement", "circuit_stmt", "gate_stmt"):
                children = getattr(stmt, "children", [])
                if children and hasattr(children[0], "data"):
                    return children[0].data in SEQUENTIAL_NODE_TYPES
            return False

        comb_body = [stmt for stmt in self.body if not _is_sequential(stmt)]

        kernel.context = old_ctx
        return LiveResult(output_keys, child_ctx, comb_body=comb_body, kernel=kernel)


class LiveResult(Value):
    def __init__(self, keys, context, comb_body=None, kernel=None):
        super().__init__(keys, "circuit_output")
        self._ctx = context
        self._keys = keys
        self._comb_body = comb_body or []
        self._kernel = kernel

    def _refresh(self):
        if not self._comb_body or self._kernel is None:
            return
        old_ctx = self._kernel.context
        self._kernel.context = self._ctx
        try:
            for stmt in self._comb_body:
                self._kernel.execute(stmt)
        finally:
            self._kernel.context = old_ctx

    def __dot__(self, prop):
        if prop in self._keys:
            self._refresh()
            val = self._ctx.lookup(prop)
            return val.data if isinstance(val, Value) else val
        raise AttributeError(f"'{prop}' not found")

    def __getitem__(self, key):
        if key in self._keys:
            self._refresh()
            val = self._ctx.lookup(key)
            return val.data if isinstance(val, Value) else val
        raise KeyError(key)

    def __repr__(self):
        self._refresh()
        result = {}
        for key in self._keys:
            val = self._ctx.lookup(key)
            result[key] = val.data if isinstance(val, Value) else val
        return str(result)


class CircuitPlugin(Plugin):
    name = "circuit"
    deps = ["gate", "wire", "access"]
    grammar = r"""
        circuit_def: "circuit" NAME "{" circuit_body "}"
        circuit_body: (input_stmt | output_stmt | instance_stmt | circuit_stmt)*
        instance_stmt: (NAME | access_stmt) (NAME | range_def) ("," (NAME | range_def))* ";"
        circuit_stmt: statement
    """
    contributes = {"statement": ["circuit_def"]}

    def exec_circuit_def(kernel, node):
        name = node.children[0].value

        def check_nested(n):
            if hasattr(n, "data"):
                if n.data == "gate_def":
                    cur = kernel.context.vars.get("__current_scope__")
                    if cur and cur["type"] == "gate":
                        raise SyntaxError("Nested gates are not allowed")
                    return
                for child in n.children:
                    check_nested(child)

        body_node = node.children[1]
        check_nested(body_node)

        cur = kernel.context.vars.get("__current_scope__")
        if cur and cur["type"] == "gate":
            raise SyntaxError("Circuits cannot be defined inside gates")

        inputs, outputs, instances, body = [], [], [], []
        prev_scope = kernel.context.vars.get("__current_scope__")
        kernel.context.vars["__current_scope__"] = {
            "name": name,
            "type": "circuit",
            "io": set(),
        }

        for child in body_node.children:
            if not hasattr(child, "data"):
                continue
            inner = child.children[0] if child.data in ("circuit_stmt", "gate_stmt") else child

            if inner.data == "input_stmt":
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

            elif inner.data == "instance_stmt":
                first = inner.children[0]
                if hasattr(first, "data") and first.data == "access_stmt":
                    circuit_ref = kernel.execute(first)
                else:
                    circuit_ref = first.value

                for child in inner.children[1:]:
                    if hasattr(child, "data") and child.data == "range_def":
                        expanded = RangePlugin.exec_range_def(kernel, child)
                        for inst_name in expanded:
                            instances.append((inst_name, circuit_ref))
                    elif child.type == "NAME":
                        instances.append((child.value, circuit_ref))

            elif child.data in ("circuit_stmt", "gate_stmt"):
                body.append(inner)

        if prev_scope is not None:
            kernel.context.vars["__current_scope__"] = prev_scope
        else:
            kernel.context.vars.pop("__current_scope__", None)

        circuit = Circuit(name, inputs, outputs, instances, body)
        kernel.context.declare(name, circuit)

    exec_handlers = {"circuit_def": exec_circuit_def}
