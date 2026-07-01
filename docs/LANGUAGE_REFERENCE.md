# Cilux Language Reference

This document describes the complete syntax and semantics of the Cilux language as implemented by the
plugins in `lang/`. Every construct below is backed by a specific grammar-contributing `Plugin`; where
useful, the responsible source file is noted in *(parentheses)*.

File extension: **`.clx`**. Statements are terminated with `;` where shown; blocks use `{ }`.

## 1. Comments

```cilux
// a single-line comment — everything to end of line is ignored
```

There is no block-comment syntax.

## 2. Literals & Types

| Literal / Type | Syntax | Notes |
|---|---|---|
| Bit | `0`, `1` | Integer literals `0` and `1` are automatically wrapped as `BitValue` (1-bit), not `IntValue` — this matters for synthesis, which is strictly 1-bit. *(`lang/types/_int.py`)* |
| Int | any other integer, e.g. `42` | `IntValue`; supports `& \| ^ ~ +`. |
| Bool | `true`, `false` | `BoolValue`. *(`lang/types/_bool.py`)* |
| String | `"double quoted"` or `'x'` (single char) | `StringValue`. *(`lang/types/_str.py`)* |
| List | `[expr, expr, ...]` | Trailing comma allowed; `ListValue`. *(`lang/types/_list.py`)* |
| Dict | `{ key: expr, "key2": expr, ... }` | Keys are bare `NAME`s or string literals; `DictValue`, supports `.prop` access. *(`lang/types/_dict.py`)* |

`bit` values support bitwise `& \| ^` and `~` (invert), plus `+` which is **addition mod 2** (XOR-like carry
truncation) — reflecting that a `bit` is a single hardware wire, not a general integer.

## 3. Variables & Assignment

```cilux
x = 5;
y = "hello";
z = [1, 2, 3];
```

`var_stmt: expr "=" expr ";"` *(`lang/oprators/assign.py`)*. If the name already exists in the current
scope, this **assigns**; otherwise it **declares** a new binding in the current scope. Assignment can also
target a namespaced path, e.g. `some_module::x = 5;`, which resolves the module object and sets the key
directly (subject to that object's own mutability — `ModuleType`, for instance, raises `PermissionError` on
any attempted mutation).

Names are ordinary identifiers (`NAME` = Lark's `CNAME`).

## 4. Expressions & Calls

```cilux
expr: list_type | dict_type | call | dot_access | NAME | string_literal
    | int_literal | bool_literal | access_stmt | "(" expr ")"

call: NAME "(" (arg ("," arg)* ","?)? ")"
    | access_stmt "(" (arg ("," arg)* ","?)? ")"
    | dot_access "(" (arg ("," arg)* ","?)? ")"

arg: named_arg | expr
named_arg: NAME "=" expr
```

Function/gate/circuit calls support both **positional** and **named** arguments (named arguments must come
after all positional ones — mixing them the other way raises a `SyntaxError`/`TypeError`).

### 4.1 Dot access

```cilux
result.sum          // read a field of a call result / netlist / clock / dict
some_dict.key
```

`dot_access: expr "." NAME` *(`lang/oprators/dot.py`)*. Resolution order: if the target implements a Python
`__dot__(self, prop)` method, that is used (this is how `Gate`/`Circuit` results, `ClockValue`, `DictValue`,
`SynthResult`-adjacent types, etc. expose custom fields); otherwise falls back to a plain Python
`getattr`.

### 4.2 Namespace access (`::`)

```cilux
std::adder::half_adder    // reach into an imported module's exports without a selective import
```

`access_stmt: NAME "::" NAME ("::" NAME)*` *(`lang/oprators/access.py`)*. Resolution always starts from the
kernel's internal `__import__` registry (populated by whole-module `import a::b;` statements) and walks the
given path segment by segment. `*` and leading `.` segments are rejected outside of `import` statements.

## 5. Ranges (`[hi:lo]`) — Bus Sugar

```cilux
input a[3:0];        // expands to a3, a2, a1, a0 (input names)
output sum[3:0];
full_adder fa[3:0];  // expands to instance names fa3, fa2, fa1, fa0
```

`range_def: NAME "[" int_literal ":" int_literal "]"` *(`lang/oprators/_range.py`)*. `RangePlugin.resolve`
expands `name[hi:lo]` into the list of scalar names `name{hi}, name{hi-1}, ..., name{lo}` (descending if
`hi >= lo`, otherwise ascending). This is purely a **name-expansion / language-level convenience** — every
expanded name is still an independent 1-bit scalar signal at the synthesis level (see
[`SYNTHESIS_AND_BACKENDS.md`](./SYNTHESIS_AND_BACKENDS.md#known-limitation-no-native-multi-bit-signals)
for why true multi-bit buses aren't yet supported end-to-end).

## 6. Built-in Logic Gates

`AND`, `OR`, `XOR`, `NOT`, `NAND`, `NOR`, `XNOR` are pre-registered **built-in functions** (not grammar
keywords) available in every scope, taking any number of bit-valued arguments and reducing left-to-right
(e.g. `AND(a, b, c)` computes `a & b & c`) — except `NOT`, which is strictly unary.

```cilux
XOR(a, b) -> sum;
AND(a, b) -> cout;
NOT(sel) 
```

*(`lang/structs/base_gates/*.py`)*

## 7. Wiring (`->`)

```cilux
expr "->" NAME ";"
```

*(`lang/oprators/wire.py`)* — `wire_stmt`. Connects the left-hand expression's result to a named target in
the current scope:

- If the source is a plain value, `-> NAME` just declares `NAME` bound to that value in the current scope
  (a lightweight alias/signal name).
- If the source is a `Port` (an `input`/`output` terminal of the enclosing `gate`), the wire is additionally
  recorded structurally as a `Wire` object on the enclosing `Gate`'s `wire_map` for structural
  introspection/rendering.
- Inside a `gate` body specifically, wiring to a **declared `output` name** is how you drive that output —
  the gate's `__call__` reads back whatever value ends up bound to each output name after the body runs.

## 8. Gates

```cilux
gate my_gate {
    input a, b;
    output y;

    AND(a, b) -> y;
}
```

*(`lang/structs/gate.py`)*, grammar:

```lark
gate_def: "gate" NAME "{" gate_body "}"
gate_body: (input_stmt | output_stmt | gate_stmt)*
input_stmt: "input" (NAME | range_def) ("," (NAME | range_def))* ";"
output_stmt: "output" (NAME | range_def) ("," (NAME | range_def))* ";"
gate_stmt: wire_stmt | var_stmt | expr_stmt | gate_def
```

- A `gate` is a low-level, purely-combinational primitive-style component. **Gates cannot be nested** inside
  another gate body (checked recursively at definition time and raises `SyntaxError`).
- Calling a gate (`my_gate(1, 0)` or `my_gate(a=1, b=0)`) creates a fresh child scope, binds each input port
  to the given argument (positional args map onto ports in declared order; keyword args by name; missing
  required inputs raise `TypeError`), executes the body, and returns a `DictValue` mapping each declared
  output name to its final `BitValue`.
- Gate instances/results support `.field` access for reading a specific output (e.g. `r = g(1,0); r.y`).

## 9. Circuits

```cilux
circuit full_adder {
    input a, b, cin;
    output sum, cout;

    half_adder ha1, ha2;

    s1 = ha1(a, b);
    s2 = ha2(s1.sum, cin);

    s2.sum -> sum;
    OR(s1.cout, s2.cout) -> cout;
}
```

*(`lang/structs/circuit.py`)*, grammar:

```lark
circuit_def: "circuit" NAME "{" circuit_body "}"
circuit_body: (input_stmt | output_stmt | instance_stmt | circuit_stmt)*
instance_stmt: (NAME | access_stmt) (NAME | range_def) (",")* ";"
circuit_stmt: statement
```

A `Circuit` is a `Gate` subclass (same `input`/`output`/wiring model) that additionally supports:

- **`instance_stmt`** — declaring one or more named instances of another gate/circuit, optionally by
  namespaced reference (`std::adder::half_adder ha;`), and optionally range-expanded
  (`full_adder fa[3:0];` → four instances `fa3..fa0`). Instances are resolved **lazily**, at call time, so
  forward references and instances of circuits defined later in the same file work.
- **Nested `circuit_def`s** are allowed inside a circuit body (unlike gates) and are executed *first*,
  before any other body statements, so a circuit can define and immediately use a locally-scoped helper
  circuit.
- **Sequential vs. combinational body separation** — statement kinds that represent sequential/structural
  declarations (`when_stmt`, `circuit_def`, `clock_stmt`, `seq_assign`, `gate_def`) are tracked separately
  from purely combinational statements. The non-sequential subset is re-executed on every subsequent
  `.field` read of the circuit's result (see `LiveResult._refresh` below) so that combinational outputs stay
  correctly re-evaluated as their driving signals change (e.g. across clock edges), matching real
  combinational-logic behavior.
- **`LiveResult`** — calling a circuit returns a `LiveResult`, not a plain dict. Reading `result.<output>` (or
  `result["<output>"]`) transparently **re-runs the circuit's combinational-only statements** against the
  live call context before returning the value — this is what lets `result.sum` reflect the current state of
  a register-driven circuit after `simulate()` advances a clock, rather than freezing the value at
  call-time.

## 10. Clocks

```cilux
[freq=100, mhz] 
clock clk;

[period=10ns, async] clock rst;
clock default_clk;             // no config block → 100MHz, 50% duty, synchronous by default
```

*(`lang/structs/clock.py`)*, grammar:

```lark
clock_stmt: ("[" clock_config "]")? "clock" NAME ";"
clock_config: clock_param ("," clock_param)* ","?
clock_param: FREQ "=" INT unit?
           | PERIOD "=" INT unit?
           | DUTY "=" INT
           | SYNC
           | ASYNC
unit: khz | mhz | ghz | hz | ks | ms | us | ns | ps | s   (case-insensitive)
```

- Exactly one of `freq=` / `period=` should be given (both together are allowed only if mutually
  consistent — a `NameError` is raised if they conflict); the other is derived automatically.
- `duty=` (percent, default `50`) controls the high/low time split within one period.
- `sync` (default) vs `async` controls scheduling: synchronous clocks tick in lock-step against the
  scheduler's cycle-counting API; asynchronous clocks free-run against simulated wall time and additionally
  fire any handler flagged `is_reset` immediately upon registration/tick boundary crossings (used for
  asynchronous reset semantics — see §11).
- A declared clock is registered with the kernel-level `ClockScheduler` automatically; `simulate(cycles=N)`
  or `simulate(time=T)` (§14.6) is what actually advances it.
- `.name`, `.freq`, `.period`, `.duty`, `.state`, `.sync`, `.async`, `.edge_callbacks` are all readable via
  dot-access on a clock variable.

## 11. Sequential Blocks (`when`)

```cilux
when clk @posedge {
    Q <= D;
}

when rst @negedge {
    if (rst_active) {
        Q <= 0;
    }
}
```

*(`lang/structs/when.py`)*, grammar:

```lark
when_stmt: "when" NAME ON STATE "{" when_body "}"
when_body: (seq_assign | condition_stmt)*
seq_assign: NAME "<=" expr ";"
ON: "@"
STATE: "posedge" | "negedge"
```

- `when NAME @posedge|negedge { ... }` registers a handler on the named clock's rising or falling edge.
- **`seq_assign` (`<=`)** is the *only* legal assignment form inside a `when` body, and implements proper
  clocked-register semantics via a **buffer-then-commit** protocol (see
  [`ARCHITECTURE.md §4`](./ARCHITECTURE.md#4-sequential-logic-execution-model)): all right-hand sides across
  every handler on a given edge are evaluated against pre-edge state before any of them are actually
  written, so e.g. two registers can swap values in the same edge deterministically.
- `if`/`elif`/`else` (§12) is allowed inside a `when` body for conditional register updates.
- A `when` block whose clock is `async` and whose edge is `negedge` is treated as an **asynchronous reset**
  handler (`is_reset=True`) and additionally fires outside the normal synchronous scheduling order whenever
  the async clock's scheduler detects it should run.

## 12. Conditionals

```cilux
if (sel) {
    y = a;
} elif (NOT(sel)) {
    y = b;
} else {
    y = 0;
}
```

*(`lang/oprators/condition.py`)*, grammar:

```lark
condition_stmt: IF "(" condition ")" "{" if_block "}"
              (ELIF "(" condition ")" "{" if_block "}")*
              (ELSE "{" if_block "}")*
condition: expr
if_block: statement*
```

Standard first-match-wins semantics: the condition is evaluated top to bottom, and the first branch whose
condition is truthy (or the `else` branch, if present and nothing else matched) executes; all others are
skipped entirely (not merely their bodies — later conditions in the same chain are not even evaluated once
a branch has fired).

## 13. Modules — `import` / namespaces

```cilux
import std::adder::half_adder;                 // single symbol import
import std::adder::*;                            // wildcard: import every export of the module
import std::adder::{half_adder, full_adder as fa}; // selective import, with optional alias
import std::adder;                                 // namespace import — access via std::adder::half_adder later, or the bound name `adder`
import .::sibling_file;                              // relative import (one leading '.' per parent level)
```

*(`lang/io/_import.py`)*, grammar:

```lark
import_stmt: "import" import_expr ";"
import_expr: (BACK "::")? NAME ("::" NAME)* ("::" (STAR | "{" selective_import "}"))?
selective_import: import_item ("," import_item)* ","?
import_item: NAME ("as" NAME)?
BACK: "."+
STAR: "*"
```

Module resolution searches, in order: the current working directory, then the bundled
`lib/builtin` directory (see [`STANDARD_LIBRARY.md`](./STANDARD_LIBRARY.md) for what ships there). A module
path can resolve to either a single `.clx` file or a directory (whose `.clx` files and sub-directories
become its exports). See
[`ARCHITECTURE.md §3`](./ARCHITECTURE.md#3-module-system-internals-importexport) for full resolution,
caching, and circular-import behavior.

## 14. Built-in Functions

All of the following are pre-declared, read-only global callables (registered via each plugin's `on_load`).

### 14.1 `print(...)`

Prints any number of arguments, space-separated, using each value's `__repr__`. *(`lang/funcs/_print.py`)*

### 14.2 `typeof(x)`

Returns a `string` naming `x`'s runtime `vtype` (`"int"`, `"bit"`, `"bool"`, `"string"`, `"list"`,
`"dict"`, `"gate"`, `"circuit"`, `"clock"`, `"netlist"`, `"module"`, `"undefined"`, ...).
*(`lang/funcs/typeof.py`)*

### 14.3 `table(component, ..., keep_order=true, cycles=None)`

```cilux
table(half_adder, full_adder).__export__ // or just print(table(...))
print(table(half_adder, full_adder).half_adder)
```

Generates a boxed **ASCII truth table** for one or more `gate`/`circuit` values, keyed by name.

- **Combinational** components get one row per input combination (`2^n` rows for `n` inputs).
- **Sequential** components (detected by scanning the body for `when NAME @edge` referencing a declared
  `clock`) instead produce `cycles` rows (default `8`, or `2^n` if `cycles` is omitted and there are
  non-clock inputs) by repeatedly ticking the relevant clocks and re-sampling outputs — clock input columns
  are rendered with `↑`/`↓` markers and the current tick count.
- Returns a `TruthTable` value; `.export(to="file.svg"|"file.png")` renders the ASCII table as an image
  using the bundled monospace font. *(`lang/funcs/table.py`)*

### 14.4 `wave(...)`

Renders an ASCII **timing diagram** for a set of named signal sample sequences (with configurable rising
/falling-edge glyphs, gap width, and clock-signal edge alignment). Returns a `WaveResult`, exportable to
`.svg`/`.png`. *(`lang/funcs/wave.py`)*

### 14.5 `simulate(cycles=N)` / `simulate(time=T)`

Advances the kernel's `ClockScheduler` by `N` cycles of its first registered synchronous clock, or by `T`
simulated seconds, correctly interleaving any additional registered clocks (sync or async).
*(`lang/funcs/simulate.py`)*

### 14.6 `synth(target, resolution_level=None, export=false)`

Elaborates and synthesizes a `gate`/`circuit` into a flat, optimized gate-level `SynthResult` (netlist).
See [`SYNTHESIS_AND_BACKENDS.md`](./SYNTHESIS_AND_BACKENDS.md#the-synth-pipeline) for the full pipeline.
`export=true` automatically computes the maximum meaningful `resolution_level`
(mutually exclusive with an explicit `resolution_level`). *(`lang/funcs/synth/plugin.py`)*

### 14.7 `schematic(synth_result, mode="text"|"gui")`

Produces a schematic view of a synthesized netlist:

- `mode="text"` (default) — ASCII-art schematic (`lang/engines/ascii_draw_engine.py`); exportable to
  `.txt`/`.png`/`.svg`.
- `mode="gui"` — a `matplotlib` + `schemdraw`-rendered schematic (`lang/engines/render_schemdraw_engine.py`);
  printing it opens an interactive window (tries `QtAgg`/`Qt5Agg`/`TkAgg`/`MacOSX` backends in turn), while
  exporting writes `.png`/`.svg`/`.pdf` directly via the non-interactive `Agg` backend. *(`lang/funcs/schematic.py`)*

### 14.8 `blif(synth_result)`

Renders a `SynthResult` to **BLIF** (Berkeley Logic Interchange Format) text as a `BlifResult`. Exportable
to any extension (writes raw text). *(`lang/bridges/blif.py`)*

### 14.9 `verilog(blif_or_synth_result)` / `json(blif_or_synth_result)`

Pipes a BLIF (or synth-result-converted-to-BLIF) design through a bundled **WebAssembly build of Yosys**
(`yowasp-yosys`) running a fixed optimize/techmap/`abc`-mapping script, producing a `YosysResult` tagged
`fmt="verilog"` or `fmt="json"` respectively. *(`lang/bridges/yosys_base.py`)*

### 14.10 `vhdl(synth_or_blif_or_verilog_result)`

Converts a design to **VHDL**: if given a `SynthResult`/`BlifResult`, first runs it through the same
Yosys Verilog-export pipeline as `verilog()`; the resulting Verilog text is then parsed and re-emitted as
VHDL by a hand-written parser/generator (`lang/engines/vl2vhd_engine.py: VerilogParser`, `VHDLGen`).
*(`lang/bridges/vhdl.py`)*

### 14.11 `export(value, to="path.ext", ...)`

Generic exporter for any `Value` implementing `__export__`. See
[`ARCHITECTURE.md §3`](./ARCHITECTURE.md#3-module-system-internals-importexport) for the dispatch details.
*(`lang/io/export.py`)*

## 15. Error Reporting

Uncaught language-level errors (`NameError`, `TypeError`, `ValueError`, `ImportError`, `SyntaxError`,
`PermissionError`, `FileNotFoundError`, `RecursionError`) and syntax errors from the parser are never
allowed to crash the interpreter with a raw Python traceback — `Kernel.run()` (`core/engine.py`) catches
them and prints a formatted, optionally ANSI-colorized diagnostic of the form:

```
<file>:<line>:<col>: <kind> error: <message>
  12 │ AND(a, b -> y;
              ^
```

Any other, unanticipated exception is reported as a generic `Internal error` rather than propagating.
