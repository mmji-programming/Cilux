from core.plugin import Plugin, Value
from lang.types.port import (
    Port,
    Wire,
)


class WirePlugin(Plugin):
    name = "wire"
    deps = ["expr"]
    grammar = r"""
        wire_stmt: expr "->" NAME ";"
    """
    contributes = {"statement": ["wire_stmt"]}

    def exec_wire(kernel, node):

        src = kernel.execute(node.children[0])
        dst_name = node.children[1].value

        if isinstance(src, Value):
            value = src.data if hasattr(src, "data") else src
            kernel.context.declare(dst_name, value)
            return

        if isinstance(src, Port):
            current = kernel.context.vars.get("__current_scope__")
            if current:
                gate_name = current.get("name")
                gate = kernel.context.lookup(gate_name)
                if hasattr(gate, "output"):
                    for out in gate.output:
                        if out.name == dst_name:
                            w = Wire()
                            w.source = src
                            w.target = out
                            gate.wire_map.append(w)
                            break
            kernel.context.declare(dst_name, src)
            return

        if hasattr(src, "vtype") and src.vtype in ("gate", "circuit"):
            kernel.context.declare(dst_name, src)
            return

        # fallback
        kernel.context.declare(dst_name, src)

    @staticmethod
    def extract_wire_map(node):
        if not hasattr(node, "data") or node.data != "wire_stmt":
            return None

        src = WirePlugin._node_str(node.children[0])
        dst = node.children[1].value if hasattr(node.children[1], "value") else str(node.children[1])

        if isinstance(src, dict) and src["type"] == "gate":
            src["output"] = dst

        return {"src": src, "dst": dst}

    @staticmethod
    def _node_str(node):
        if hasattr(node, "data") and node.data == "call":
            name = node.children[0].value if hasattr(node.children[0], "value") else str(node.children[0])
            inputs = []
            for c in node.children[1:]:
                if hasattr(c, "data") and c.data in ("arg", "call_arg"):
                    inputs.append(WirePlugin._node_str(c.children[0]))
            return {"type": "gate", "name": name, "inputs": inputs, "output": None}
        elif hasattr(node, "data") and node.data == "dot_access":
            instance = WirePlugin._node_str(node.children[0])
            port = node.children[1].value if hasattr(node.children[1], "value") else str(node.children[1])
            return {"type": "port", "instance": instance, "port": port}
        elif hasattr(node, "data") and node.data == "access_stmt":
            parts = [c.value for c in node.children if hasattr(c, "value")]
            return {"type": "access", "path": "::".join(parts)}
        elif hasattr(node, "type") and node.type == "NAME":
            return {"type": "wire", "name": node.value}
        elif hasattr(node, "type") and node.type == "INT":
            return {"type": "literal", "value": node.value}
        elif hasattr(node, "data") and node.data == "int_literal":
            for child in node.children:
                if hasattr(child, "value"):
                    return {"type": "literal", "value": child.value}
            return {"type": "literal", "value": "0"}
        elif hasattr(node, "data") and node.data == "expr":
            return WirePlugin._node_str(node.children[0])
        else:
            return {"type": "unknown", "value": str(node)}

    exec_handlers = {"wire_stmt": exec_wire}
