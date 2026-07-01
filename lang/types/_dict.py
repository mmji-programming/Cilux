from core.plugin import Plugin, Value


class DictValue(Value):
    def __init__(self, data):
        super().__init__(data, "dict")

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, val):
        self.data[key] = val

    def __dot__(self, prop):
        return self.data.get(prop)


class DictType(Plugin):
    name = "dict"
    deps = ["expr"]
    grammar = r"""
        dict_type: "{" (dict_key ":" expr ("," dict_key ":" expr)*)? ","? "}"
        dict_key: NAME
                | string_literal
    """
    contributes = {}

    def exec_dict_type(kernel, node):

        _dict = dict()

        dict_keys = node.children
        for i in range(0, len(dict_keys), 2):
            key = dict_keys[i].children[0]

            if hasattr(key, "data") and key.data == "string_literal":
                key = kernel.execute(key)
                if isinstance(key, Value):
                    key = key.data
            else:
                key = key.value

            value = dict_keys[i + 1]
            if hasattr(value, "data") and value.data == "expr":
                value = kernel.execute(value)

            _dict[key] = value

        return DictValue(_dict)

    exec_handlers = {"dict_type": exec_dict_type}
