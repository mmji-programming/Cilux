class Context:
    def __init__(self, parent=None, is_global=False):
        self.vars = dict()
        self.types = dict()
        self.readonly = set()
        self.__protected = set()
        self.parent = parent
        self.is_global = is_global

    def create_child(self):
        return Context(self, is_global=False)

    def declare(self, name, value, vtype=None, readonly=False):
        self.vars[name] = value
        self.types[name] = vtype or (value.vtype if hasattr(value, "vtype") else None)
        if readonly:
            self.readonly.add(name)

    def protect(self, name):
        self.__protected.add(name)

    def is_protected(self, name):
        return name in self.__protected

    def assign(self, name, value, vtype=None):

        if self.is_protected(name):
            raise PermissionError(f"Cannot assign to internal variable '{name}'")

        if name in self.readonly:
            raise PermissionError(f"Cannot modify readonly '{name}'")

        if name in self.vars:
            self.vars[name] = value
            self.types[name] = vtype
            return

        if self.parent and not self.parent.is_global:
            self.parent.assign(name, value, vtype)
            return

        raise PermissionError(f"Cannot modify '{name}' from this scope")

    def lookup(self, name):
        if self.is_protected(name):
            raise NameError(f"'{name}' is internal and cannot be accessed")

        if name in self.vars:
            return self.vars[name]
        if self.parent:
            return self.parent.lookup(name)
        raise NameError(f"'{name}' not found in current scope")

    def typeof(self, name) -> str:

        if name in self.types:
            return self.types[name]

        if self.parent:
            return self.parent.typeof(name)

        return "unknown"
