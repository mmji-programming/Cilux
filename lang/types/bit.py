from core.plugin import Value


class BitValue(Value):
    def __init__(self, data):

        if isinstance(data, bool):
            if data:
                self.data = 1
            else:
                self.data = 0
        else:
            self.data = data

        self.vtype = "bit"

    def __and__(self, other):
        if isinstance(other, BitValue):
            return BitValue(self.data & other.data)
        elif isinstance(other, int):
            return BitValue(self.data & other)
        return NotImplemented

    def __rand__(self, other):
        if isinstance(other, int):
            return BitValue(other & self.data)
        return NotImplemented

    def __or__(self, other):
        if isinstance(other, BitValue):
            return BitValue(self.data | other.data)
        elif isinstance(other, int):
            return BitValue(self.data | other)
        return NotImplemented

    def __ror__(self, other):
        if isinstance(other, int):
            return BitValue(other | self.data)
        return NotImplemented

    def __xor__(self, other):
        if isinstance(other, BitValue):
            return BitValue(self.data ^ other.data)
        elif isinstance(other, int):
            return BitValue(self.data ^ other)
        return NotImplemented

    def __rxor__(self, other):
        if isinstance(other, int):
            return BitValue(other ^ self.data)
        return NotImplemented

    def __invert__(self):
        return BitValue(0 if self.data else 1)

    def __add__(self, other):
        if isinstance(other, BitValue):
            return BitValue((self.data + other.data) % 2)
        elif isinstance(other, int):
            return BitValue((self.data + other) % 2)
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, int):
            return BitValue((other + self.data) % 2)
        return NotImplemented
