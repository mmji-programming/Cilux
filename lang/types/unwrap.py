from core.plugin import Value


def unwrap(v):
    if isinstance(v, Value):
        return unwrap(v.data)
    if isinstance(v, list):
        return [unwrap(i) for i in v]
    if isinstance(v, dict):
        return {k: unwrap(val) for k, val in v.items()}
    return v
