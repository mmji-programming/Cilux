import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class Port:
    name: str
    direction: str  # input | output | inout
    width: int = 1
    msb: int = 0
    lsb: int = 0


@dataclass
class Wire:
    name: str
    width: int = 1
    msb: int = 0
    lsb: int = 0


@dataclass
class Assignment:
    lhs: str
    rhs: str


@dataclass
class Instance:
    module_name: str
    instance_name: str
    params: Dict[str, str]
    connections: Dict[str, str]


@dataclass
class StmtAssign:
    lhs: str
    rhs: str
    blocking: bool = False


@dataclass
class StmtIf:
    condition: str
    then_body: list
    elsif_branches: list
    else_body: list


@dataclass
class StmtCase:
    expr: str
    branches: list


@dataclass
class AlwaysBlock:
    sensitivity: str
    body: list


@dataclass
class VerilogModule:
    name: str
    ports: List[Port] = field(default_factory=list)
    wires: List[Wire] = field(default_factory=list)
    assignments: List[Assignment] = field(default_factory=list)
    instances: List[Instance] = field(default_factory=list)
    always_blocks: List[AlwaysBlock] = field(default_factory=list)
    parameters: Dict[str, str] = field(default_factory=dict)


TOKEN_RE = re.compile(
    r"<=|>=|==|!=|===|!==|\^~|~\^|<<|>>|\*\*"
    r"|[(){}\[\];,:.@#'`]"
    r"|\d+'[bBoOdDhHsS][0-9a-fA-F_xXzZ]*"
    r"|[A-Za-z_\$][A-Za-z0-9_\$]*"
    r"|\d+"
    r"|[^ \t\n]"
)


class Tok:
    def __init__(self, src: str):
        self.tokens: List[str] = TOKEN_RE.findall(src)
        self.pos: int = 0

    def peek(self, offset: int = 0) -> Optional[str]:
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else None

    def consume(self) -> str:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, v: str):
        t = self.consume()
        if t != v:
            ctx = self.tokens[max(0, self.pos - 3) : self.pos + 3]
            raise SyntaxError(f"Expected '{v}', got '{t}' near {ctx}")

    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)


class AlwaysParser:
    def __init__(self, tok: Tok):
        self.t = tok

    def parse_body(self) -> list:
        if self.t.peek() == "begin":
            self.t.consume()
            stmts = []
            while self.t.peek() not in ("end", None):
                stmts.append(self.parse_stmt())
            self.t.expect("end")
            return stmts
        return [self.parse_stmt()]

    def parse_stmt(self):
        p = self.t.peek()
        if p == "if":
            return self.parse_if()
        if p in ("case", "casez", "casex"):
            return self.parse_case()
        return self.parse_assign()

    def parse_if(self) -> StmtIf:
        self.t.expect("if")
        self.t.expect("(")
        cond = self._collect_to(")")
        then = self.parse_body()
        elsifs: list = []
        else_: list = []
        while self.t.peek() == "else":
            self.t.consume()
            if self.t.peek() == "if":
                self.t.consume()
                self.t.expect("(")
                ec = self._collect_to(")")
                elsifs.append((ec, self.parse_body()))
            else:
                else_ = self.parse_body()
                break
        return StmtIf(cond, then, elsifs, else_)

    def parse_case(self) -> StmtCase:
        self.t.consume()  # consume case/casez/casex
        self.t.expect("(")
        expr = self._collect_to(")")
        branches = []
        while self.t.peek() not in ("endcase", None):
            val_toks: List[str] = []
            while self.t.peek() not in (":", None):
                val_toks.append(self.t.consume())
            self.t.expect(":")
            branches.append((" ".join(val_toks), self.parse_body()))
        self.t.expect("endcase")
        return StmtCase(expr, branches)

    def parse_assign(self) -> StmtAssign:
        lhs_toks: List[str] = []
        while self.t.peek() not in ("<=", "=", ";", None):
            lhs_toks.append(self.t.consume())
        blocking = False
        if self.t.peek() == "=":
            self.t.consume()
            blocking = True
        elif self.t.peek() == "<=":
            self.t.consume()
        rhs_toks: List[str] = []
        while self.t.peek() not in (";", None):
            rhs_toks.append(self.t.consume())
        if self.t.peek() == ";":
            self.t.consume()
        return StmtAssign(" ".join(lhs_toks), " ".join(rhs_toks), blocking)

    def _collect_to(self, closing: str) -> str:
        """Collect tokens until we hit `closing` at depth 0."""
        depth = 0
        toks: List[str] = []
        openers = {"(": ")", "[": "]", "{": "}"}
        closers = set(openers.values())
        while True:
            t = self.t.peek()
            if t is None:
                break
            if t in openers:
                depth += 1
            elif t in closers:
                if depth == 0 and t == closing:
                    self.t.consume()
                    break
                depth -= 1
            toks.append(self.t.consume())
        return " ".join(toks)


# All VHDL reserved words that must never appear as identifiers
VHDL_RESERVED = {
    "abs",
    "access",
    "after",
    "alias",
    "all",
    "and",
    "architecture",
    "array",
    "assert",
    "attribute",
    "begin",
    "block",
    "body",
    "buffer",
    "bus",
    "case",
    "component",
    "configuration",
    "constant",
    "disconnect",
    "downto",
    "else",
    "elsif",
    "end",
    "entity",
    "exit",
    "file",
    "for",
    "function",
    "generate",
    "generic",
    "group",
    "guarded",
    "if",
    "impure",
    "in",
    "inertial",
    "inout",
    "is",
    "label",
    "library",
    "linkage",
    "literal",
    "loop",
    "map",
    "mod",
    "nand",
    "new",
    "next",
    "nor",
    "not",
    "null",
    "of",
    "on",
    "open",
    "or",
    "others",
    "out",
    "package",
    "port",
    "postponed",
    "procedure",
    "process",
    "pure",
    "range",
    "record",
    "register",
    "reject",
    "rem",
    "report",
    "return",
    "rol",
    "ror",
    "select",
    "severity",
    "signal",
    "shared",
    "sla",
    "sll",
    "sra",
    "srl",
    "subtype",
    "then",
    "to",
    "transport",
    "type",
    "unaffected",
    "units",
    "until",
    "use",
    "variable",
    "wait",
    "when",
    "while",
    "with",
    "xnor",
    "xor",
}

# Verilog keywords - never treat as module/instance names
VERILOG_KWS = {
    "input",
    "output",
    "inout",
    "wire",
    "reg",
    "logic",
    "assign",
    "always",
    "begin",
    "end",
    "if",
    "else",
    "case",
    "casez",
    "casex",
    "endcase",
    "module",
    "endmodule",
    "parameter",
    "localparam",
    "integer",
    "signed",
    "unsigned",
    "initial",
    "generate",
    "endgenerate",
    "for",
    "while",
    "posedge",
    "negedge",
    "or",
    "and",
    "not",
}


def _safe_ident(name: str) -> str:
    """Make a Verilog identifier safe for use in VHDL-93.

    VHDL-93 identifier rules:
      - Must start with a letter
      - Must not end with underscore
      - No double underscores allowed
      - Cannot be a reserved word

    prefix with 'n' and keep alphanumeric chars so that
    uniqueness is preserved.  e.g. '_000_' -> 'n000', '_001_' -> 'n001'.
    """

    # Replace any character that is not alphanumeric or underscore
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
    # Collapse consecutive underscores
    safe = re.sub(r"__+", "_", safe)
    # Strip leading underscores (keep digits for uniqueness)
    safe = safe.lstrip("_")
    if not safe:
        safe = "sig"
    # If starts with digit, prefix with 'n' (preserves uniqueness: '000'->'n000')
    if safe[0].isdigit():
        safe = "n" + safe
    # Strip trailing underscores
    safe = safe.rstrip("_")
    if not safe:
        safe = "sig"
    # Final double-underscore pass
    safe = re.sub(r"__+", "_", safe)
    if safe.lower() in VHDL_RESERVED:
        safe = safe + "_v"
    return safe


class VerilogParser:
    def __init__(self, src: str):
        self.src = self._preprocess(src)

    def _preprocess(self, src: str) -> str:
        # Strip line & block comments
        src = re.sub(r"//[^\n]*", "", src)
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)

        # Strip Yosys attribute annotations  (* ... *)
        src = re.sub(r"\(\*.*?\*\)", "", src, flags=re.DOTALL)

        # Escaped identifiers  \foo[3]  -> sanitised name
        def _esc(m: re.Match) -> str:
            raw = m.group(0)
            # remove leading backslash, trailing whitespace is the delimiter
            name = raw.lstrip("\\").rstrip()
            return _safe_ident(name) + " "

        src = re.sub(r"\\[^\s]+", _esc, src)

        # Yosys $-prefixed cell names (already handled via _safe_ident,
        src = src.replace("$", "s_")

        # Words that overlap Verilog keywords are left alone entirely.
        VERILOG_ALSO_KWS = {
            "and",
            "or",
            "not",
            "if",
            "else",
            "for",
            "begin",
            "end",
            "case",
            "while",
            "generate",
            "function",
            "module",
            "input",
            "output",
            "inout",
            "wire",
            "reg",
            "return",
            "signal",
            "in",
            "out",
            "buffer",
            "label",
            "file",
            "record",
            "type",
            "array",
            "null",
            "exit",
            "next",
            "wait",
            "report",
            "assert",
            "access",
            "alias",
            "group",
            "new",
            "use",
            "map",
            "open",
            "of",
            "to",
            "is",
            "on",
        }
        # Rename VHDL reserved words used as signal identifiers only inside
        # port/wire/reg declaration lists.
        # We match: (input|output|inout|wire|reg)  [optional_width]  KW  [,;)]
        # Variable-width lookbehind not supported by Python re, so we capture the prefix.
        only_in_decl = VHDL_RESERVED - VERILOG_ALSO_KWS
        for kw in only_in_decl:
            src = re.sub(
                rf"((?:input|output|inout|wire|reg)(?:\s+(?:signed|unsigned))?\s+(?:\[[^\]]*\]\s*)?)\b({re.escape(kw)})\b",
                lambda m, k=kw: m.group(1) + k + "_v",
                src,
                flags=re.IGNORECASE,
            )

        return src

    # ------------------------------------------------------------------
    def _parse_width(self, ws: Optional[str]) -> Tuple[int, int, int]:
        if not ws:
            return 1, 0, 0
        m = re.match(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", ws.strip())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return abs(a - b) + 1, a, b
        return 1, 0, 0

    # ------------------------------------------------------------------
    def parse(self) -> List[VerilogModule]:
        mods: List[VerilogModule] = []
        pattern = re.compile(
            r"module\s+([A-Za-z0-9_]+)\s*"
            r"(?:#\s*\((?:[^()]*|\([^()]*\))*\)\s*)?"  # optional #(params)
            r"\(([^)]*)\)\s*;"  # port list
            r"(.*?)"  # body
            r"endmodule",
            re.DOTALL,
        )
        for m in pattern.finditer(self.src):
            mod = VerilogModule(name=m.group(1))
            port_header = m.group(2)
            body = m.group(3)
            self._parse_ansi_ports(mod, port_header)
            self._body(mod, body)
            mods.append(mod)
        return mods

    # ------------------------------------------------------------------
    def _parse_ansi_ports(self, mod: VerilogModule, port_list: str):
        """
        Parse ANSI-style port declarations from the module(...) header.
        Handles:
            input CLK,
            input [7:0] data,
            output reg Q,
        """
        for m in re.finditer(
            r"(input|output|inout)\s+"
            r"(?:wire\s+|reg\s+|logic\s+|signed\s+|unsigned\s+)*"
            r"(\[\s*\d+\s*:\s*\d+\s*\]\s*)?"
            r"([A-Za-z0-9_]+)",
            port_list,
        ):
            direction = m.group(1)
            w, msb, lsb = self._parse_width(m.group(2))
            name = m.group(3).strip()
            if name and not any(p.name == name for p in mod.ports):
                mod.ports.append(Port(name, direction, w, msb, lsb))

    def _body(self, mod: VerilogModule, body: str):
        for m in re.finditer(
            r"(?:parameter|localparam)\s+(?:\w+\s+)?([A-Za-z0-9_]+)\s*=\s*([^;]+);",
            body,
        ):
            mod.parameters[m.group(1).strip()] = m.group(2).strip()

        # Handle both  "input [7:0] foo;" and  "input foo, bar;"
        for m in re.finditer(
            r"(input|output|inout)\s+"
            r"(?:wire\s+|reg\s+|logic\s+)?"
            r"(\[\s*\d+\s*:\s*\d+\s*\]\s*)?"
            r"([A-Za-z0-9_,\s]+);",
            body,
        ):
            direction = m.group(1)
            width_str = m.group(2)
            w, msb, lsb = self._parse_width(width_str)
            for name in re.split(r"[,\s]+", m.group(3).strip()):
                if name:
                    mod.ports.append(Port(name, direction, w, msb, lsb))

        pnames = {p.name for p in mod.ports}

        for kw in ("wire", "reg"):
            for m in re.finditer(
                rf"{kw}\s+(?:signed\s+)?(\[\s*\d+\s*:\s*\d+\s*\]\s*)?"
                rf"([A-Za-z0-9_]+)\s*;",
                body,
            ):
                raw_name = m.group(2)
                # Sanitise names that violate VHDL-93 identifier rules
                name = _safe_ident(raw_name) if re.match(r"^_|_$", raw_name) else raw_name
                if name in pnames:
                    continue
                w, msb, lsb = self._parse_width(m.group(1))
                if not any(s.name == name for s in mod.wires):
                    mod.wires.append(Wire(name, w, msb, lsb))

        for m in re.finditer(r"assign\s+([^=;]+?)\s*=\s*([^;]+);", body):
            mod.assignments.append(Assignment(m.group(1).strip(), m.group(2).strip()))

        #   always @(posedge clk)   always @*   always @(*)
        for m in re.finditer(
            r"always\s*@\s*"
            r"(?:\(\s*\*\s*\)|\*|"  # @(*) or @* -> combinational
            r"\(([^)]*)\))",  # @(sensitivity list)
            body,
        ):
            raw_sens = m.group(1) if m.group(1) is not None else "*"
            rest = body[m.end() :]
            mod.always_blocks.append(AlwaysBlock(raw_sens.strip(), AlwaysParser(Tok(rest)).parse_body()))

        for m in re.finditer(
            r"([A-Za-z0-9_]+)\s+"
            r"(?:#\s*\(([^)]*)\)\s+)?"
            r"([A-Za-z0-9_]+)\s*"
            r"\(((?:[^()]*|\([^()]*\))*)\)\s*;",
            body,
            re.DOTALL,
        ):
            mname = m.group(1)
            if mname in VERILOG_KWS:
                continue
            mod.instances.append(
                Instance(
                    mname,
                    m.group(3),
                    self._parse_conns(m.group(2) or ""),
                    self._parse_conns(m.group(4)),
                )
            )

    def _parse_conns(self, s: str) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for m in re.finditer(r"\.\s*([A-Za-z0-9_]+)\s*\(\s*((?:[^()]*|\([^()]*\))*)\s*\)", s):
            result[m.group(1)] = m.group(2).strip()
        return result


# Yosys stdlib primitive -> VHDL direct-expansion
# These appear after the $ -> s_ substitution
YOSYS_PRIMITIVES = {
    "s__NOT_": ("NOT_gate", ["A"], ["Y"], lambda c: f"{c['Y']} <= not {c['A']};"),
    "s__BUF_": ("BUF_gate", ["A"], ["Y"], lambda c: f"{c['Y']} <= {c['A']};"),
    "s__AND_": (
        "AND_gate",
        ["A", "B"],
        ["Y"],
        lambda c: f"{c['Y']} <= {c['A']} and {c['B']};",
    ),
    "s__NAND_": (
        "NAND_gate",
        ["A", "B"],
        ["Y"],
        lambda c: f"{c['Y']} <= not ({c['A']} and {c['B']});",
    ),
    "s__OR_": (
        "OR_gate",
        ["A", "B"],
        ["Y"],
        lambda c: f"{c['Y']} <= {c['A']} or {c['B']};",
    ),
    "s__NOR_": (
        "NOR_gate",
        ["A", "B"],
        ["Y"],
        lambda c: f"{c['Y']} <= not ({c['A']} or {c['B']});",
    ),
    "s__XOR_": (
        "XOR_gate",
        ["A", "B"],
        ["Y"],
        lambda c: f"{c['Y']} <= {c['A']} xor {c['B']};",
    ),
    "s__XNOR_": (
        "XNOR_gate",
        ["A", "B"],
        ["Y"],
        lambda c: f"{c['Y']} <= {c['A']} xnor {c['B']};",
    ),
    "s__MUX_": (
        "MUX_gate",
        ["A", "B", "S"],
        ["Y"],
        lambda c: f"{c['Y']} <= {c['B']} when ({c['S']} = '1') else {c['A']};",
    ),
    "s__NMUX_": (
        "NMUX_gate",
        ["A", "B", "S"],
        ["Y"],
        lambda c: f"{c['Y']} <= not ({c['B']} when ({c['S']} = '1') else {c['A']});",
    ),
    # D flip-flops - handled specially in gen_inst
}

DFF_PRIMITIVES = {
    "s__DFF_P_",
    "s__DFF_N_",
    "s__DFFE_PP_",
    "s__DFFE_PN_",
    "s__DFFE_NP_",
    "s__DFFE_NN_",
    "s__SDFF_PP0_",
    "s__SDFF_PP1_",
    "s__SDFF_PN0_",
    "s__SDFF_PN1_",
    "s__SDFF_NP0_",
    "s__SDFF_NP1_",
    "s__SDFF_NN0_",
    "s__SDFF_NN1_",
}


def _split_top_level(s: str, sep: str = ",") -> List[str]:
    """Split string on `sep` but only at depth 0 (respects brackets)."""
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    openers = set("([{")
    closers = set(")]}")
    for ch in s:
        if ch in openers:
            depth += 1
            cur.append(ch)
        elif ch in closers:
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


class VHDLGen:
    def __init__(self, mods: List[VerilogModule]):
        self.mods = mods
        # Stack of dicts used to shield already-finalised VHDL snippets
        # (produced while decoding Yosys LUT shifts) from later text-level
        # regex passes in expr() that would otherwise re-interpret VHDL
        # concatenation '&' as bitwise/logical 'and'. See expr().
        self._protect_stack: List[dict] = []

    def _wrap_cond(self, c: str) -> str:
        """
        Wrap a bare signal name so it becomes a std_logic boolean condition.
        e.g.  RST         -> RST = '1'
              (RST)       -> RST = '1'
              (and0_out)  -> and0_out = '1'
        Leaves complex expressions (already containing operators) unchanged.
        """
        c = c.strip()
        while c.startswith("(") and c.endswith(")"):
            inner = c[1:-1].strip()
            depth = 0
            balanced = True
            for ch in inner:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth < 0:
                        balanced = False
                        break
            if balanced and depth == 0:
                c = inner
            else:
                break
        # Already has a relational operator or boolean keyword -> leave as-is
        if re.search(
            r"[=<>/]|'\s*[01xzXZ]'|\"|\band\b|\bor\b|\bnot\b|\bxor\b|\bxnor\b|\bnand\b|\bnor\b",
            c,
        ):
            return c
        # Bare identifier (possibly indexed like sig(3))
        if re.match(r"^[A-Za-z][A-Za-z0-9_]*(?:\([^)]+\))?$", c):
            return f"{c} = '1'"
        return c

    def expr(self, e: str) -> str:
        e = e.strip()
        if not e:
            return e

        # 0. Sanitise VHDL-illegal identifiers (e.g. _000_, _001_)
        # Replace any identifier starting or ending with underscore
        def _fix_ident(m: re.Match) -> str:
            name = m.group(0)
            if name.startswith("_") or name.endswith("_"):
                return _safe_ident(name)
            return name

        e = re.sub(r"\b[A-Za-z0-9_]+\b", _fix_ident, e)

        # 1. Yosys LUT: constant >> {sel_bits}
        # Pattern: WIDTH'[bBhHdD]VALUE >> { ... }
        # or:      WIDTH'[bBhHdD]VALUE >> single_sig
        lut_pat = re.compile(r"(\d+)'([bBhHdD])([0-9a-fA-F_]+)\s*>>\s*(\{[^}]*\}|[A-Za-z0-9_]+)")

        protect_map: dict = {}
        self._protect_stack.append(protect_map)

        def _lut_shift_protected(m: re.Match) -> str:
            finished = self._lut_shift(m)
            token = f"\x00LUT{len(protect_map)}\x00"
            protect_map[token] = finished
            return token

        e = lut_pat.sub(_lut_shift_protected, e)

        # 2. Numeric literals
        def _literal(m: re.Match) -> str:
            w = int(m.group(1))
            base = m.group(2).lower()
            vs = m.group(3).replace("_", "")
            # X/Z literals
            if any(c in vs.lower() for c in ("x", "z")):
                if w == 1:
                    ch = vs[0].lower()
                    return f"'{'X' if ch == 'x' else 'Z'}'"
                return f'"{vs.upper().zfill(w)}"'
            try:
                if base in ("b", "s"):
                    val = int(vs, 2)
                elif base == "o":
                    val = int(vs, 8)
                elif base in ("h",):
                    val = int(vs, 16)
                else:  # 'd'
                    val = int(vs, 10)
            except ValueError:
                return m.group(0)  # leave as-is on parse error
            if w == 1:
                return f"'{val & 1}'"
            return f'"{val:0{w}b}"'

        e = re.sub(r"(\d+)'([bBoOdDhHsS])([0-9a-fA-F_xXzZ]+)", _literal, e)

        # 3. Bus slicing / indexing
        # [MSB:LSB] -> (MSB downto LSB)
        e = re.sub(r"([A-Za-z0-9_]+)\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]", r"\1(\2 downto \3)", e)
        # [idx] -> (idx)
        e = re.sub(r"([A-Za-z0-9_]+)\s*\[\s*([A-Za-z0-9_]+)\s*\]", r"\1(\2)", e)

        # 4. Concatenations { a, b, c } -> a & b & c
        MAX_ITER = 20
        for _ in range(MAX_ITER):
            m = re.search(r"\{([^{}]+)\}", e)
            if not m:
                break
            inner = m.group(1)
            parts = [self.expr(p.strip()) for p in _split_top_level(inner)]
            e = e[: m.start()] + " & ".join(parts) + e[m.end() :]

        #  5. Ternary  cond ? a : b
        # We need to find the outermost ?/:  respecting brackets
        toks = self._split_ternary(e)
        if toks:
            c_expr = self.expr(toks[0])
            a_expr = self.expr(toks[1])
            b_expr = self.expr(toks[2])
            cond = self._wrap_cond(c_expr)
            return f"{a_expr} when ({cond}) else {b_expr}"

        #  6. Logic / bitwise operators
        e = re.sub(r"&&", " and ", e)
        e = re.sub(r"\|\|", " or ", e)
        e = re.sub(r"\^~|~\^", " xnor ", e)
        e = re.sub(r"\^", " xor ", e)
        # Single | -> bitwise OR (std_logic or std_logic_vector: or)
        e = re.sub(r"(?<!\|)\|(?!\|)", " or ", e)
        e = re.sub(r'(?<![&"\'a-zA-Z0-9_\)])&(?![&"\'a-zA-Z0-9_\(])', " and ", e)
        e = re.sub(r"===", " = ", e)
        e = re.sub(r"!==", " /= ", e)
        e = re.sub(r"==", " = ", e)
        e = re.sub(r"!=", " /= ", e)
        # bitwise NOT ~x -> not x (but not >= or <= or ~^)
        e = re.sub(r"~(?![=^])", "not ", e)
        # logical NOT  !x  ->  not x
        e = re.sub(r"(?<![=!<>])!(?!=)", "not ", e)

        e = re.sub(r" {2,}", " ", e).strip()

        # Restore the finished LUT/NOT/BUF snippets that were shielded above.
        my_map = self._protect_stack.pop()
        for token, finished in my_map.items():
            e = e.replace(token, finished)

        return e

    def _lut_shift(self, m: re.Match) -> str:
        """Decode a Yosys LUT encoded as  WIDTH'BASE_VALUE >> selector."""
        width = int(m.group(1))
        base = m.group(2).lower()
        val_str = m.group(3).replace("_", "")
        sel_raw = m.group(4).strip()

        try:
            if base in ("b", "s"):
                val_int = int(val_str, 2)
            elif base == "o":
                val_int = int(val_str, 8)
            elif base == "h":
                val_int = int(val_str, 16)
            else:
                val_int = int(val_str, 10)
        except ValueError:
            val_int = 0

        bin_str = f'"{val_int:0{width}b}"'

        if width == 2:
            sel_vhdl = self.expr(sel_raw)
            if val_int == 0b01:  # NOT
                return f"not ({sel_vhdl})"
            if val_int == 0b10:  # BUF
                return sel_vhdl
            if val_int == 0b00:  # constant 0
                return "'0'"
            if val_int == 0b11:  # constant 1
                return "'1'"

        # General N-bit LUT: use lut_mux helper
        idx_expr = self._lut_sel_expr(sel_raw)
        return f"lut_mux({bin_str}, {idx_expr})"

    def _lut_sel_expr(self, sel_raw: str) -> str:
        """
        Convert a LUT selector (possibly a concatenation {a,b,...} or single
        std_logic signal) to a std_logic_vector expression suitable for lut_mux.
        """
        if sel_raw.startswith("{"):
            inner = sel_raw[1:-1]
            parts = [self.expr(p.strip()) for p in _split_top_level(inner)]
            # Each part is std_logic; concatenation gives std_logic_vector
            return " & ".join(parts)
        sig = self.expr(sel_raw)
        # Single std_logic -> wrap via helper that returns std_logic_vector(0 downto 0)
        return f"sl_to_slv({sig})"

    def _split_ternary(self, e: str) -> Optional[Tuple[str, str, str]]:
        """
        Find outermost  COND ? THEN : ELSE  at bracket depth 0.
        Returns (cond, then, else) or None.
        """
        depth = 0
        q_pos = -1
        openers = set("([{")
        closers = set(")]}")
        for i, ch in enumerate(e):
            if ch in openers:
                depth += 1
            elif ch in closers:
                depth -= 1
            elif ch == "?" and depth == 0:
                q_pos = i
                break
        if q_pos < 0:
            return None
        # Find matching ':'
        depth = 0
        for i in range(q_pos + 1, len(e)):
            ch = e[i]
            if ch in openers:
                depth += 1
            elif ch in closers:
                depth -= 1
            elif ch == ":" and depth == 0:
                return e[:q_pos].strip(), e[q_pos + 1 : i].strip(), e[i + 1 :].strip()
        return None

    # Sensitivity list collector

    def _collect_sens(self, stmts: list) -> set:
        """
        Walk statement AST and collect all identifiers that appear on the
        RHS of assignments or in conditions — these form the combinational
        sensitivity list (VHDL-93 compatible).
        """
        sigs: set = set()
        IDENT_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\b")
        SKIP = {
            "and",
            "or",
            "not",
            "xor",
            "xnor",
            "nand",
            "nor",
            "when",
            "else",
            "downto",
            "to",
            "others",
            "std_logic",
            "std_logic_vector",
            "unsigned",
            "signed",
            "integer",
            "natural",
            "boolean",
            "true",
            "false",
            "lut_mux",
            "sl_to_slv",
            "to_integer",
        }

        def _scan(expr_str: str):
            for m in IDENT_RE.finditer(expr_str):
                name = m.group(1)
                if name.lower() not in SKIP:
                    sigs.add(name)

        def _walk(stmt_list: list):
            for s in stmt_list:
                if isinstance(s, StmtAssign):
                    _scan(s.rhs)
                elif isinstance(s, StmtIf):
                    _scan(s.condition)
                    _walk(s.then_body)
                    for _, eb in s.elsif_branches:
                        _walk(eb)
                    _walk(s.else_body)
                elif isinstance(s, StmtCase):
                    _scan(s.expr)
                    for _, body in s.branches:
                        _walk(body)

        _walk(stmts)
        return sigs

    # Statement generator

    def gen_stmts(self, stmts: list, indent: str) -> List[str]:
        lines: List[str] = []
        for s in stmts:
            lines.extend(self.gen_stmt(s, indent))
        return lines

    def gen_stmt(self, s, indent: str) -> List[str]:
        L: List[str] = []
        if isinstance(s, StmtAssign):
            L.append(f"{indent}{self.expr(s.lhs)} <= {self.expr(s.rhs)};")

        elif isinstance(s, StmtIf):
            cond = self._wrap_cond(self.expr(s.condition))
            L.append(f"{indent}if ({cond}) then")
            L.extend(self.gen_stmts(s.then_body, indent + "  "))
            for ec, eb in s.elsif_branches:
                ec2 = self._wrap_cond(self.expr(ec))
                L.append(f"{indent}elsif ({ec2}) then")
                L.extend(self.gen_stmts(eb, indent + "  "))
            if s.else_body:
                L.append(f"{indent}else")
                L.extend(self.gen_stmts(s.else_body, indent + "  "))
            L.append(f"{indent}end if;")

        elif isinstance(s, StmtCase):
            L.append(f"{indent}case {self.expr(s.expr)} is")
            for val, body in s.branches:
                v = val.strip()
                if v == "default":
                    label = "when others =>"
                else:
                    label = f"when {self.expr(v)} =>"
                L.append(f"{indent}  {label}")
                L.extend(self.gen_stmts(body, indent + "    "))
            L.append(f"{indent}end case;")
        return L

    # Always block generator

    def gen_always(self, ab: AlwaysBlock) -> List[str]:
        sens = ab.sensitivity.strip()
        L: List[str] = []

        # Combinational: @* or @(*)
        if sens == "*":
            sigs = self._collect_sens(ab.body)
            sens_str = ", ".join(sorted(sigs)) if sigs else "CLK"
            L.append(f"  process({sens_str})")
            L.append("  begin")
            L.extend(self.gen_stmts(ab.body, "    "))
            L.append("  end process;")
            return L

        pos_m = re.search(r"posedge\s+([A-Za-z0-9_]+)", sens)
        neg_m = re.search(r"negedge\s+([A-Za-z0-9_]+)", sens)

        if pos_m or neg_m:
            clk = (pos_m or neg_m).group(1)
            edge_fn = "rising_edge" if pos_m else "falling_edge"

            # Build clean sensitivity list: just signal names, no keywords
            all_edges = re.findall(r"(?:posedge|negedge)\s+([A-Za-z0-9_]+)", sens)
            clean_sens = ", ".join(dict.fromkeys(all_edges))  # deduplicated

            async_rst: Optional[str] = None
            async_edge: Optional[str] = None
            for edge, sig in re.findall(r"(posedge|negedge)\s+([A-Za-z0-9_]+)", sens):
                if sig != clk:
                    async_rst = sig
                    async_edge = edge

            L.append(f"  process({clean_sens})")
            L.append("  begin")

            if async_rst:
                rst_val = "'1'" if async_edge == "posedge" else "'0'"
                L.append(f"    if ({async_rst} = {rst_val}) then")
                # In Yosys-generated code the first if-branch is the async reset body
                if ab.body and isinstance(ab.body[0], StmtIf):
                    inner_if = ab.body[0]
                    L.extend(self.gen_stmts(inner_if.then_body, "      "))
                    L.append(f"    elsif {edge_fn}({clk}) then")
                    L.extend(self.gen_stmts(inner_if.else_body, "      "))
                    for extra in ab.body[1:]:
                        L.extend(self.gen_stmt(extra, "      "))
                L.append("    end if;")
            else:
                L.append(f"    if {edge_fn}({clk}) then")
                L.extend(self.gen_stmts(ab.body, "      "))
                L.append("    end if;")
            L.append("  end process;")
        else:
            # Clean: remove 'or' separators
            sig_list = re.sub(r"\bor\b", ",", sens, flags=re.IGNORECASE)
            sig_list = re.sub(r"\s+", " ", sig_list).strip().strip(",")
            L.append(f"  process({sig_list})")
            L.append("  begin")
            L.extend(self.gen_stmts(ab.body, "    "))
            L.append("  end process;")
        return L

    # Instance generator

    def gen_inst(self, inst: Instance) -> List[str]:
        """
        Handle Yosys primitive cells by expanding them inline,
        and emit regular port-map for user-defined modules.
        """
        mname = inst.module_name

        # Inline primitives
        if mname in YOSYS_PRIMITIVES:
            _, _ins, _outs, expander = YOSYS_PRIMITIVES[mname]
            conn = inst.connections
            try:
                stmt = expander(conn)
                return [f"  -- {inst.instance_name} ({mname})", f"  {stmt}"]
            except KeyError:
                pass  # fall through to port-map if conn incomplete

        #  DFF primitives
        if mname in DFF_PRIMITIVES:
            return self._gen_dff(inst)

        # General instance
        L = [f"  {inst.instance_name} : {inst.module_name}"]
        if inst.params:
            L.append("    generic map (")
            items = list(inst.params.items())
            for i, (p, v) in enumerate(items):
                sep = "" if i == len(items) - 1 else ","
                L.append(f"      {p} => {self.expr(v)}{sep}")
            L.append("    )")
        if inst.connections:
            L.append("    port map (")
            items = list(inst.connections.items())
            for i, (p, v) in enumerate(items):
                sep = "" if i == len(items) - 1 else ","
                sig = self.expr(v) if v.strip() else "open"
                L.append(f"      {p} => {sig}{sep}")
            L.append("    );")
        else:
            L[-1] += ";"
        return L

    def _gen_dff(self, inst: Instance) -> List[str]:
        """Expand a Yosys DFF primitive into a VHDL clocked process."""
        mname = inst.module_name
        conn = inst.connections
        clk = conn.get("C", conn.get("CLK", "'0'"))
        d = conn.get("D", "'0'")
        q = conn.get("Q", "open")

        # Polarity
        edge_fn = "rising_edge" if "_P_" in mname or mname.endswith("_P") else "falling_edge"

        L = [f"  -- {inst.instance_name} : {mname}"]

        # Synchronous reset variants
        rst = conn.get("R", conn.get("RESET", ""))
        rst_val_bit = "1" if "_PP" in mname or "_NP" in mname else "0"

        en = conn.get("E", "")

        L.append(f"  process({clk})")
        L.append("  begin")
        L.append(f"    if {edge_fn}({clk}) then")

        body_indent = "      "
        if rst:
            L.append(f"      if ({rst} = '{rst_val_bit}') then")
            L.append(f"        {self.expr(q)} <= '0';")
            L.append("      else")
            body_indent = "        "

        if en:
            L.append(f"{body_indent}if ({en} = '1') then")
            L.append(f"{body_indent}  {self.expr(q)} <= {self.expr(d)};")
            L.append(f"{body_indent}end if;")
        else:
            L.append(f"{body_indent}{self.expr(q)} <= {self.expr(d)};")

        if rst:
            L.append("      end if;")

        L.append("    end if;")
        L.append("  end process;")
        return L

    # Module generator

    def gen_mod(self, mod: VerilogModule) -> str:
        L: List[str] = [
            "library IEEE;",
            "use IEEE.STD_LOGIC_1164.ALL;",
            "use IEEE.NUMERIC_STD.ALL;",
            "",
        ]

        # Entity
        L.append(f"entity {mod.name} is")
        if mod.parameters:
            L.append("  generic (")
            items = list(mod.parameters.items())
            for i, (n, v) in enumerate(items):
                sep = "" if i == len(items) - 1 else ";"
                L.append(f"    {n} : integer := {v}{sep}")
            L.append("  );")
        if mod.ports:
            L.append("  port (")
            for i, p in enumerate(mod.ports):
                vt = "std_logic" if p.width == 1 else f"std_logic_vector({p.msb} downto {p.lsb})"
                d = {"input": "in", "output": "out", "inout": "inout"}[p.direction]
                sep = "" if i == len(mod.ports) - 1 else ";"
                L.append(f"    {p.name} : {d} {vt}{sep}")
            L.append("  );")
        L.append(f"end entity {mod.name};")
        L.append("")

        # Architecture
        L.append(f"architecture rtl of {mod.name} is")
        L.append("")

        L += [
            "  -- Helper: index a LUT truth-table std_logic_vector by an address",
            "  function lut_mux(lut_val : std_logic_vector;",
            "                   sel     : std_logic_vector)",
            "    return std_logic is",
            "    variable idx : integer;",
            "  begin",
            "    idx := to_integer(unsigned(sel));",
            "    -- Yosys LUT truth-tables: bit 0 of address indexes the MSB of the vector",
            "    idx := lut_val'high - idx;",
            "    if idx >= 0 and idx <= lut_val'high then",
            "      return lut_val(idx);",
            "    else",
            "      return 'X';",
            "    end if;",
            "  end function lut_mux;",
            "",
            "  -- Helper: convert a single std_logic bit to std_logic_vector(0 downto 0)",
            "  -- Used when passing a 1-bit signal as LUT selector.",
            "  function sl_to_slv(b : std_logic) return std_logic_vector is",
            "    variable v : std_logic_vector(0 downto 0);",
            "  begin",
            "    v(0) := b;",
            "    return v;",
            "  end function sl_to_slv;",
            "",
        ]

        # Detect Yosys constant wires (names like _000_, _001_ etc.)
        const_map: Dict[str, str] = {}  # raw_name -> '0' or '1'
        non_const_assignments: List[Assignment] = []
        for a in mod.assignments:
            raw_lhs = a.lhs.strip()
            rhs_e = a.rhs.strip()
            # Yosys constant wire: lhs looks like _XXX_ (leading+trailing)
            if re.match(r"^_[0-9_]+_$", raw_lhs):
                # Figure out constant value
                if re.match(r"^1'[bBdD][01]$", rhs_e):
                    bit = rhs_e[-1]
                    const_map[_safe_ident(raw_lhs)] = bit
                else:
                    non_const_assignments.append(a)
            else:
                non_const_assignments.append(a)

        # Signal declarations (skip wires that are now constants)
        const_names = set(const_map.keys())
        for w in mod.wires:
            safe_name = _safe_ident(w.name) if re.match(r"^_", w.name) else w.name
            if safe_name in const_names:
                continue  # will be emitted as constant below
            vt = "std_logic" if w.width == 1 else f"std_logic_vector({w.msb} downto {w.lsb})"
            L.append(f"  signal {w.name} : {vt};")
        if mod.wires:
            L.append("")

        # Constant declarations for Yosys GND/VCC nets
        for cname, cval in const_map.items():
            L.append(f"  constant {cname} : std_logic := '{cval}';")
        if const_map:
            L.append("")

        L.append("begin")
        L.append("")

        # Continuous assignments (constant wires already handled above)
        for a in non_const_assignments:
            L.append(f"  {self.expr(a.lhs)} <= {self.expr(a.rhs)};")
        if non_const_assignments:
            L.append("")

        # Always blocks
        for ab in mod.always_blocks:
            L.extend(self.gen_always(ab))
            L.append("")

        # Instances
        for inst in mod.instances:
            L.extend(self.gen_inst(inst))
            L.append("")

        L.append(f"end architecture rtl;")
        L.append("")
        return "\n".join(L)

    def generate(self) -> str:
        return "\n".join(self.gen_mod(m) for m in self.mods)
