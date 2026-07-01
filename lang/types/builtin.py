from core.plugin import Value


class BuiltinFunction(Value):
    def __init__(self, name, func):
        super().__init__(name, "builtin_function")
        self.func = func

    def __call__(self, *args):
        return self.func(*args)

    def __repr__(self):
        return f"<built-in function {self.data}>"


class Callable:
    def __call__(self, kernel, **kwargs):
        raise NotImplementedError

    def get_params(self):
        raise NotImplemented
