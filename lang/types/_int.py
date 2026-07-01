from core.plugin import Plugin, Value
from .bit import (
    BitValue,
)


class IntValue(Value):
    def __init__(self, data):
        self.data = data
        self.vtype = "int"

    def __and__(self, other):
        if isinstance(other, IntValue):
            return IntValue(self.data & other.data)
        elif isinstance(other, int):
            return IntValue(self.data & other)
        return NotImplemented

    def __rand__(self, other):
        if isinstance(other, int):
            return IntValue(other & self.data)
        return NotImplemented

    def __or__(self, other):
        if isinstance(other, IntValue):
            return IntValue(self.data | other.data)
        elif isinstance(other, int):
            return IntValue(self.data | other)
        return NotImplemented

    def __ror__(self, other):
        if isinstance(other, int):
            return IntValue(other | self.data)
        return NotImplemented

    def __xor__(self, other):
        if isinstance(other, IntValue):
            return IntValue(self.data ^ other.data)
        elif isinstance(other, int):
            return IntValue(self.data ^ other)
        return NotImplemented

    def __rxor__(self, other):
        if isinstance(other, int):
            return IntValue(other ^ self.data)
        return NotImplemented

    def __invert__(self):
        return IntValue(~self.data)

    def __add__(self, other):
        if isinstance(other, IntValue):
            return IntValue(self.data + other.data)
        elif isinstance(other, int):
            return IntValue(self.data + other)
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, int):
            return IntValue(other + self.data)
        return NotImplemented


class IntPlugin(Plugin):
    name = "int"
    deps = []
    grammar = r"""
        int_literal: INT
    """
    contributes = {}

    def exec_int_literal(self, node):
        value = int(node.children[0].value)

        if value in [0, 1]:
            return BitValue(value)

        return IntValue(value)

    exec_handlers = {"int_literal": exec_int_literal}
