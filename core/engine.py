from .plugin import Plugin
from .context import (
    Context,
)
from .parser_cache import (
    build_or_load_parser,
)

from lark import Transformer, UnexpectedToken, UnexpectedCharacters
from lang.types.builtin import BuiltinFunction, Value


class Kernel:
    def __init__(self):

        self.plugins: dict[str, Plugin] = dict()
        self.context = Context()

    def register(self, plugin: Plugin):

        for dep in plugin.deps:
            if dep not in self.plugins:
                raise Exception(f"Plugin '{plugin.name}' needs '{dep}'")

        self.plugins[plugin.name] = plugin
        plugin.on_load(self)

    def build_grammar(self):

        stmts = list()
        fragments = list()

        for p in self.plugins.values():
            if p.grammar:
                fragments.append(p.grammar)

            for rules in p.contributes.get("statement", list()):
                stmts.append(rules)

        stmt_rule = "statement: " + " | ".join(stmts) if stmts else "statement: /.+/"
        frag_text = "\n\n".join(fragments)

        return (
            f"start: statement*\n"
            f"{stmt_rule}\n"
            f"COMMENT: /\\/\\/[^\\n]*/\n"
            f"%import common.WS\n"
            f"%import common.NEWLINE\n"
            f"%import common.CNAME -> NAME\n"
            f"%import common.INT\n"
            f"%ignore WS\n"
            f"%ignore NEWLINE\n"
            f"%ignore COMMENT\n"
            f"{frag_text}"
        )

    def build_transformer(self):
        return Transformer()

    def parse(self, code: str):
        lark = build_or_load_parser(self)
        return lark.parse(code)

    def execute(self, node):
        # Token
        if not hasattr(node, "data"):
            if hasattr(node, "type") and node.type == "NAME":
                try:
                    return self.context.lookup(node.value)
                except NameError:
                    raise NameError(f"'{node.value}' is not defined")
            return node.value if hasattr(node, "value") else str(node)

        # Statement
        if node.data == "statement":
            return self.execute(node.children[0])

        # Expression Statement
        if node.data == "expr_stmt":
            return self.execute(node.children[0])

        # Expression
        if node.data == "expr":
            return self.execute(node.children[0])

        # Call
        if node.data == "call":
            name_node = node.children[0]

            if hasattr(name_node, "data") and name_node.data == "access_stmt":
                obj = self.execute(name_node)
                name = str(obj)

            elif hasattr(name_node, "data") and name_node.data == "dot_access":
                obj = self.execute(name_node.children[0])
                method_name = name_node.children[1].value

                if isinstance(obj, Value):
                    method = getattr(obj.data, method_name)
                else:
                    method = getattr(obj, method_name)

                args = []
                kwargs = {}
                for child in node.children[1:]:
                    if hasattr(child, "data") and child.data == "arg":
                        inner = child.children[0]
                        if hasattr(inner, "data") and inner.data == "named_arg":
                            key = inner.children[0].value
                            val = self.execute(inner.children[1])
                            kwargs[key] = val.data if isinstance(val, Value) else val
                        else:
                            val = self.execute(inner)
                            args.append(val.data if isinstance(val, Value) else val)

                return method(*args, **kwargs)

            else:
                name = name_node.value
                try:
                    obj = self.context.lookup(name)
                except NameError:
                    raise NameError(f"'{name}' is not defined")

            # BuiltinFunction (AND, OR, simulate, ...)
            if isinstance(obj, BuiltinFunction):
                name = obj.data
                if getattr(obj, "func", None) is not None:
                    return obj(self, node)
                for p in self.plugins.values():
                    if p.name == name and name in p.exec_handlers:
                        return p.exec_handlers[name](self, node)
                raise TypeError(f"'{name}' is not callable")

            # Gate / Circuit
            if hasattr(obj, "get_params"):
                args = []
                kwargs = {}

                for child in node.children[1:]:
                    if hasattr(child, "data") and child.data == "arg":
                        inner = child.children[0]
                        if hasattr(inner, "data") and inner.data == "named_arg":
                            key = inner.children[0].value
                            val = self.execute(inner.children[1])
                            kwargs[key] = val.data if isinstance(val, Value) else val
                        else:
                            if kwargs:
                                raise SyntaxError("Positional argument after keyword argument")
                            val = self.execute(inner)
                            args.append(val.data if isinstance(val, Value) else val)

                final = {}
                params = obj.get_params()
                for i, inp in enumerate(params):
                    if i < len(args):
                        final[inp] = args[i]
                    elif inp in kwargs:
                        final[inp] = kwargs[inp]
                    else:
                        raise TypeError(f"Missing argument: '{inp}'")

                if len(args) > len(params) or any(k not in params for k in kwargs):
                    raise TypeError(f"Too many arguments for '{name}'")

                return obj(self, **final)

            raise TypeError(f"'{name}' is not callable")

        # Plugin Dispatch (non-call)
        node_type = node.data
        for p in self.plugins.values():
            if node_type in p.exec_handlers:
                return p.exec_handlers[node_type](self, node)

        return None

    def run(self, code, filename="<input>"):

        try:
            tree = self.parse(code)
            for stmt in tree.children:
                self.execute(stmt)

        except UnexpectedToken as e:
            msg = self._shorten_lark_error(str(e))
            self._error(filename, e.line, e.column, "Syntax", msg, code)

        except UnexpectedCharacters as e:
            msg = f"unexpected character {e.char!r}"
            self._error(filename, e.line, e.column, "Syntax", msg, code)

        except (
            NameError,
            TypeError,
            ValueError,
            ImportError,
            SyntaxError,
            PermissionError,
            FileNotFoundError,
            RecursionError,
        ) as e:
            self._error(
                filename,
                None,
                None,
                type(e).__name__.replace("Error", ""),
                str(e),
                code,
            )

        except Exception as e:
            self._error(filename, None, None, "Internal", str(e), code)

    _RESET = "\033[0m"
    _BOLD = "\033[1m"
    _RED = "\033[31m"
    _YELLOW = "\033[33m"
    _CYAN = "\033[36m"
    _WHITE = "\033[97m"
    _DIM = "\033[2m"

    _KIND_COLOR = {
        "syntax": "\033[31m",
        "name": "\033[33m",
        "type": "\033[33m",
        "value": "\033[33m",
        "import": "\033[36m",
        "permission": "\033[36m",
        "filenotfound": "\033[36m",
        "recursion": "\033[35m",
        "internal": "\033[35m",
    }

    @staticmethod
    def _supports_color() -> bool:
        import sys, os

        if not hasattr(sys.stderr, "isatty") or not sys.stderr.isatty():
            return False
        if os.name == "nt":
            return os.environ.get("ANSICON") is not None or "WT_SESSION" in os.environ

        return True

    def _color(self, code_str: str, text: str) -> str:
        if self._supports_color():
            return f"{code_str}{text}{self._RESET}"
        return text

    def _shorten_lark_error(self, msg: str) -> str:
        if "Expected one of:" in msg:
            msg = msg.split("Expected one of:")[0].strip()
        if "Previous tokens:" in msg:
            msg = msg.split("Previous tokens:")[0].strip()
        return msg

    def _error(self, filename: str, line, col, kind: str, message: str, code: str = "") -> None:
        import sys

        color = self._KIND_COLOR.get(kind.lower(), self._RED)

        if line and col:
            location = self._color(self._DIM, f"{filename}:{line}:{col}")
        else:
            location = self._color(self._DIM, filename)

        kind_label = self._color(color + self._BOLD, f"{kind.lower()} error")
        msg_text = self._color(self._WHITE, message)

        print(f"{location}: {kind_label}: {msg_text}", file=sys.stderr)

        if line and col and code:
            lines = code.split("\n")
            if 1 <= line <= len(lines):
                src_line = lines[line - 1]
                line_no = self._color(self._DIM, f"{line:>4} │ ")
                print(f"{line_no}{src_line}", file=sys.stderr)

                pad = " " * (len(f"{line:>4} │ ") - len(self._DIM) - len(self._RESET) + (col - 1))
                caret = self._color(color + self._BOLD, "^")
                print(f"{pad}{caret}", file=sys.stderr)

        print(file=sys.stderr)
