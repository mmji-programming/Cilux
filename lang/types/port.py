class Port:
    def __init__(self, name, direction):
        self.name = name
        self.direction = direction  # I | O
        self.wire = None
        self.value = 0

    def connect(self, wire):
        self.wire = wire

    def read(self):
        if self.wire:
            return self.wire.value
        return self.value

    def write(self, value):
        if self.wire:
            self.wire.value = value
        else:
            self.value = value

    def __repr__(self):
        return f"<{self.direction} {self.name}>"


class Wire:
    def __init__(self):
        self.value = 0
        self.source = None
        self.target = None

    def __repr__(self):
        src = self.source
        tgt = self.target

        if isinstance(src, dict):
            if src["type"] == "gate":
                src = f"{src['name']}({','.join(self._flatten(i) for i in src.get('inputs', []))})"
            elif src["type"] == "port":
                inst = src["instance"]
                if isinstance(inst, dict):
                    inst = inst.get("name", str(inst))
                src = f"{inst}.{src['port']}"
            elif src["type"] == "wire":
                src = src["name"]

        if isinstance(tgt, dict):
            tgt = tgt.get("name", str(tgt))

        return f"{src} -> {tgt}"

    def _flatten(self, node):
        if isinstance(node, dict):
            if node["type"] == "wire":
                return node["name"]
            elif node["type"] == "gate":
                inputs = ",".join(self._flatten(i) for i in node.get("inputs", []))
                return f"{node['name']}({inputs})"
            elif node["type"] == "port":
                inst = node["instance"]
                if isinstance(inst, dict):
                    inst = inst.get("name", str(inst))
                return f"{inst}.{node['port']}"
        return str(node)
