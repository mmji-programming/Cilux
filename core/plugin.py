class Plugin:
    name: str = ""
    version: str = "1.0"
    deps: list = list()
    grammar: str = ""
    contributes: dict = dict()
    exec_handlers: dict = dict()

    def on_load(self, kernel: "Kernel"):
        pass

    def on_unload(self, kernel: "Kernel"):
        pass


class Value:
    def __init__(self, data, vtype):
        self.data = data
        self.vtype = vtype

    def __bool__(self):
        return bool(self.data)

    def __eq__(self, other):
        if isinstance(other, Value):
            return self.data == other.data
        return self.data == other

    def __repr__(self):
        return repr(self.data)
