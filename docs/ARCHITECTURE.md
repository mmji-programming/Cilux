# Architecture

Cilux's implementation is organized around a small, deliberate idea: **the entire language is a set of
independent "Plugin" objects**, each of which contributes a fragment of grammar and a set of execution
handlers to a shared `Kernel`. There is no monolithic parser or interpreter file — `AND`, `if`, `circuit`,
`import`, `synth()`, and every other language feature are all separate, self-contained plugin modules that
register themselves with the kernel at startup.

This document explains how that system fits together, from source text to executed program.

## 1. The Core Package (`core/`)

### 1.1 `Plugin` and `Value` (`core/plugin.py`)

Every language feature is a subclass of `Plugin`:

```python
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
```

- **`name`** — the plugin's unique identifier (also often the built-in function name it registers, e.g.
  `"print"`, `"synth"`, `"AND"`).
- **`deps`** — a list of other plugin names that must already be registered before this one loads. The
  kernel enforces this at registration time (see §1.2).
- **`grammar`** — a raw Lark grammar fragment (rules/terminals) this plugin contributes to the composed
  language grammar.
- **`contributes["statement"]`** — a list of grammar rule names that should be OR'd into the top-level
  `statement` rule, letting a plugin add a new kind of statement (e.g. `circuit_def`, `import_stmt`,
  `wire_stmt`) without touching any other plugin's code.
- **`exec_handlers`** — a `{grammar_rule_name: callable}` mapping. When the tree-walking interpreter
  (`Kernel.execute`) encounters a parse-tree node whose `.data` matches one of these keys, it calls the
  associated handler with `(kernel, node)`.
- **`on_load(kernel)`** — called once, immediately upon registration. This is where plugins that expose
  callable built-ins (like `AND`, `print`, `synth`) declare themselves as read-only global variables in the
  kernel's root `Context` (see §1.3), rather than through grammar-driven dispatch.

All runtime values in Cilux — integers, bits, strings, gates, synthesized netlists, etc. — are instances
of `Value` or one of its many subclasses (`IntValue`, `BitValue`, `StringValue`, `BoolValue`, `ListValue`,
`DictValue`, `Gate`, `Circuit`, `ClockValue`, `SynthResult`, `BlifResult`, `YosysResult`, `VhdlResult`,
`SchematicResult`, `WaveResult`, `TruthTable`, `ModuleType`, ...). `Value` wraps a raw Python `data` payload
with a `vtype` string tag (`"int"`, `"bit"`, `"gate"`, `"netlist"`, ...) used by `typeof()` and by internal
type checks throughout the codebase.

### 1.2 `Kernel` (`core/engine.py`)

The `Kernel` is the central object tying everything together:

- **`kernel.plugins`** — `dict[str, Plugin]` of every registered plugin, keyed by name.
- **`kernel.context`** — the root lexical `Context` (see §1.3).
- **`kernel.register(plugin)`** — validates `plugin.deps` are already present, stores the plugin, and calls
  `plugin.on_load(kernel)`.
- **`kernel.build_grammar()`** — concatenates every plugin's `grammar` fragment and every
  `contributes["statement"]` entry into one composed Lark grammar string, with a shared `start`, `statement`,
  comment syntax (`// ...`), and common terminal imports (`WS`, `NEWLINE`, `CNAME` as `NAME`, `INT`).
- **`kernel.build_transformer()`** — returns a bare `lark.Transformer()` (transformation is otherwise done
  by hand in `execute`, not via Lark's `@v_args`-style transformer callbacks).
- **`kernel.parse(code)`** — delegates to `core/parser_cache.build_or_load_parser(kernel)`, which returns a
  Lark `LALR` parser (transparently cached — see §1.4), and parses `code` into a tree.
- **`kernel.execute(node)`** — the tree-walking interpreter. Dispatch works as follows, in priority order:
  1. **Tokens** (leaf nodes without `.data`) resolve `NAME` tokens via `context.lookup()`, and otherwise
     return the token's raw value.
  2. **`statement` / `expr_stmt` / `expr`** nodes unwrap to their single child and recurse.
  3. **`call`** nodes (function/gate/circuit invocation syntax `name(args...)`) resolve the callee (by name,
     by `dot_access`, or by `access_stmt`, i.e. `module::name`) and then:
     - If it resolves to a `BuiltinFunction` — either call its bound Python `.func` directly, or (if it's a
       plugin-registered placeholder) look up the owning plugin's `exec_handlers[name]` and call that.
     - If it resolves to an object with `.get_params()` (a `Gate` or `Circuit`) — collect positional/keyword
       arguments, map them onto the callee's declared input ports, and invoke `obj(kernel, **kwargs)`.
     - If it resolves to a `dot_access` target (e.g. `some_result.method(...)`) — call the bound Python
       method directly.
  4. **Any other node type** — the kernel scans every registered plugin's `exec_handlers` for one matching
     `node.data` and dispatches to it. This is how `circuit_def`, `wire_stmt`, `var_stmt`, `when_stmt`,
     `condition_stmt`, `import_stmt`, etc. all get executed, without `Kernel` needing to know they exist.
- **`kernel.run(code, filename=...)`** — parses and executes every top-level statement, catching parser
  errors (`UnexpectedToken`, `UnexpectedCharacters`) and a curated set of runtime exceptions
  (`NameError`, `TypeError`, `ValueError`, `ImportError`, `SyntaxError`, `PermissionError`,
  `FileNotFoundError`, `RecursionError`, plus a catch-all "Internal" bucket), and prints a
  colorized (ANSI, when the terminal supports it), source-line-annotated diagnostic to `stderr` rather than
  raising — this is what gives Cilux's CLI/REPL its `file:line:col: kind error: message` style output with a
  caret (`^`) under the offending column.

### 1.3 `Context` (`core/context.py`)

`Context` is a simple, parent-linked lexical scope:

- `declare(name, value, vtype=None, readonly=False)` — introduce a new binding in *this* scope.
- `assign(name, value, vtype=None)` — mutate an existing binding, walking up to the nearest scope that
  already owns the name (unless that scope `is_global`, in which case assignment from a child scope is
  rejected — this is what prevents a `gate`/`circuit` body from silently creating/mutating globals by
  accident, forcing the language's port/output binding conventions instead).
- `lookup(name)` — resolve a name by walking up the parent chain; raises `NameError` if unbound anywhere.
- `protect(name)` / `is_protected(name)` — marks a name (e.g. the internal `__import__` registry, or
  `__current_scope__` scope-tracking dict used during `gate_def`/`circuit_def` parsing) as inaccessible to
  user code via `lookup`/`assign`, even though it's still a real dict entry the interpreter itself can read.
- `readonly` — a set of names that reject `assign()` even from the owning scope; used for built-in functions
  (`AND`, `print`, `synth`, ...) so user code cannot shadow/reassign them.
- `typeof(name)` — walks the parallel `types` dict up the parent chain (used by the `typeof()` builtin, and
  internally wherever a plugin needs a variable's declared `vtype` without evaluating it).

Every `gate`/`circuit` call creates a **child context** (`context.create_child()`) for its body, so
instantiating the same component twice never leaks state between invocations — each call gets an
independent scope chained to the caller's scope (so it can still see outer names, including sibling
module exports injected via `_module_exports`, described in §3).

### 1.4 Parser Caching (`core/parser_cache.py`)

Building a Lark LALR parser from the fully composed grammar is not free, especially with ~30 plugins each
contributing grammar fragments. Since the grammar is **fully determined by which plugins are registered**
(and the language/Lark/Cilux versions), `parser_cache` avoids rebuilding it on every run:

1. Compute a stable hash (`blake2b`) over: the sorted list of registered plugin names, the composed grammar
   text, the running Python version, the Lark version, the parser algorithm (`"lalr"`), and Cilux's own
   `__version__`.
2. Look for a cache directory containing a `grammar.hash` file and a compressed serialized parser
   (`parser.lark.zst`, using `zstandard` if available, else falling back to `parser.lark.gz` with `gzip`).
3. If the stored hash matches, deserialize the cached Lark parser (`Lark.load`) instead of re-parsing the
   grammar text from scratch.
4. Otherwise, build a fresh `Lark(grammar, parser="lalr")`, serialize it (`parser.save`), compress it, and
   atomically write it (write-to-temp-file-then-rename) alongside the new hash, so a crash mid-write can't
   corrupt the cache.
5. The **cache directory** itself is chosen with graceful fallbacks: prefer a `__parser_cache__` folder next
   to the running executable if writable (useful for a portable/frozen `.exe` build), otherwise use the
   OS-appropriate user cache directory via `platformdirs`, otherwise fall back to a folder next to
   `parser_cache.py` itself.

`clear_cache()` is provided to wipe the cache directory manually if needed.

## 2. Grammar Composition in Practice

Because grammar fragments are assembled from many independent plugins, `Kernel.build_grammar()` produces
something conceptually like:

```lark
start: statement*
statement: expr_stmt | var_stmt | condition_stmt | wire_stmt | gate_def | circuit_def
         | clock_stmt | when_stmt | seq_assign | import_stmt
COMMENT: /\/\/[^\n]*/
%import common.WS
%import common.NEWLINE
%import common.CNAME -> NAME
%import common.INT
%ignore WS
%ignore NEWLINE
%ignore COMMENT

<...every plugin's raw `grammar` string, concatenated...>
```

Registration **order in `registry.py` matters** — not for correctness of the final grammar text (Lark
doesn't care about rule definition order), but because `Kernel.register()` enforces `deps` at load time, and
because a few plugins (like `dot` / `access`) intentionally declare no `deps` to sidestep circular grammar
dependencies while still relying on being loaded early. `registry.py` documents this explicitly with
comments grouping plugins into dependency tiers (types → operators → expression → collection types → IO →
base gates → higher operators → structs → functions → bridges).

## 3. Module System Internals (Import/Export)

`ImportPlugin` (`lang/io/_import.py`) implements `import a::b::c;`, `import a::b::*;`, and
`import a::b::{c, d as e};` by:

- Resolving dotted/`::`-separated module paths against a small **module resolution order (MRO)**: the
  current working directory first, then the bundled `lib/builtin` directory — with support for `.`-prefixed
  relative-parent paths (`import .::sibling;`, `import ..::other::thing;`).
- A module path can point either at a single `.clx` file or at a **directory**, in which case every
  `.clx` file and sub-directory inside it (skipping anything starting with `_` or `.`) becomes a named
  export of an auto-constructed namespace — this is exactly how `std::adder`, `std::mux`, etc. work: `std`
  is a directory-module whose exports are its sibling `.clx` files.
- **Executing a module file** runs it through the *same* kernel/context machinery as top-level code, but in
  an isolated way: it snapshots the set of variable names defined before execution, runs the file's
  statements, and treats every *new* top-level name as an export, then removes those names from the calling
  context so they don't leak into the importer's scope automatically (namespace imports must go through
  `module::name` or an explicit `import` clause).
- **Circular imports** are detected via a `_loading` set of in-progress module keys and raise
  `ImportError` with the full cycle chain.
- **File/module results are cached** (`_file_cache`) so re-importing the same module within one run doesn't
  re-parse or re-execute it.
- When a symbol (a `Gate`, `Circuit`, or `BuiltinFunction`-like object) is imported individually, the
  plugin attaches a `_module_exports` back-reference to it. This is consumed later inside `Gate.__call__` /
  `Circuit.__call__` (`core`-adjacent, in `lang/structs/gate.py` and `lang/structs/circuit.py`): when a
  circuit's body references a sibling component from the same module that wasn't itself explicitly
  imported, the child execution context is seeded with the module's other exports so intra-module
  references resolve without every helper circuit needing its own `import` line.

`ExportPlugin` (`lang/io/export.py`) is the generic file-writer used by `export(value, to="path.ext")`. It
is intentionally format-agnostic: any `Value` subtype that implements `__export__(self, path, **kwargs)` is
exportable. `export()`:

1. Validates `to` has a file extension and its parent directory exists (`lang/io/path_op.py`).
2. Introspects `target.__export__`'s Python signature to map any extra *positional* arguments passed to
   `export()` onto that method's named parameters (so, e.g., `export(schematic(...), "out.png", 300)` can
   position-match the `dpi` parameter of `SchematicResult.__export__`).
3. Calls `target.__export__(path, **kwargs)`. The return value determines what gets written: `None` means
   the method wrote the file itself; a `str`/`bytes` return value is written directly to `path`; a
   file-like object is `.read()` and written according to its `mode`.

## 4. Sequential Logic Execution Model

Clocks (`lang/structs/clock.py`), `when` blocks (`lang/structs/when.py`), and the scheduler
(`lang/structs/scheduler.py`) implement a small discrete-event simulator:

- A `clock NAME;` statement (optionally configured with `[freq=..., period=..., duty=..., sync|async]`
  inside `[...]` before the `clock` keyword) creates a `Clock` object and registers it with a
  per-kernel singleton `ClockScheduler` (`get_scheduler(kernel)`).
- `Clock.tick()` fires all registered `posedge` handlers, sleeps for the high-phase duration, fires all
  `negedge` handlers, then sleeps for the low-phase duration — real wall-clock `time.sleep()` calls scaled
  by the configured frequency/period, so very fast simulated clocks tick with negligible real delay while
  still preserving relative timing between multiple clocks.
- `when NAME @posedge { ... }` / `@negedge { ... }` registers a `WhenBlock` as an edge-callback on the named
  clock. A `WhenBlock` implements the classic **evaluate-then-commit** pattern needed for correct register
  semantics: every `seq_assign` (`target <= expr;`) inside the block is evaluated against the *pre-tick*
  context and buffered (not applied immediately), and only after every handler on that edge has *evaluated*
  does the scheduler call `commit()` on all of them — meaning two registers that swap values on the same
  clock edge (`a <= b; b <= a;`, split across handlers) behave correctly, exactly like real synchronous
  hardware.
- `ClockScheduler.run(cycles=N)` / `run(time=T)` (invoked by the `simulate()` builtin) advances simulated
  time by repeatedly finding whichever registered clock's next tick is soonest (correctly interleaving
  independent `sync` and `async` clocks) and ticking it, until the requested number of cycles (measured
  against the first registered sync clock) or amount of simulated time has elapsed.

## 5. Where Compiled/Frozen Execution Diverges

`main.py` checks `getattr(sys, "frozen", False)` (true when running inside a Nuitka `--onefile`/`--standalone`
build) purely to embed a build signature/watermark; it does not otherwise change control flow.
`lang/io/utils.base_dir()` similarly branches on `sys.frozen` to resolve the executable's own directory
(for locating `share/fonts` and `lib/builtin`) versus `sys.argv[0]`'s directory in development mode, and
`core/parser_cache._get_cache_dir()` prefers a writable folder *next to the frozen executable* before
falling back to the OS user-cache directory — both are deliberate accommodations for the project's Nuitka
packaging pipeline.

### Packaging notes

Two third-party dependencies load native binaries in a way static import analysis (and therefore Nuitka's
automatic dependency inclusion) cannot see:

- **`wasmtime`** loads its native library (`_wasmtime.dll` / `.so`) via `ctypes` at runtime, not via a
  regular Python `import`, so it must be explicitly bundled with
  `--include-data-files=<path-to-file>=wasmtime/win32-x86_64/_wasmtime.dll` (a plain `--include-data-dir`
  is *not* sufficient, since Nuitka's directory-data inclusion filters out `.dll`/`.so`/`.pyd` files by
  design).
- **`yowasp_yosys`** (the bundled WebAssembly build of Yosys used by `lang/bridges/yosys_base.py` for
  `verilog()`/`json()`, and transitively by `vhdl()`) needs `--include-package-data=yowasp_yosys` so its
  bundled WASM binary ships with the executable.

If interactive schematic rendering (`show_schematic()` in `lang/engines/render_schemdraw_engine.py`, which
tries `QtAgg` → `Qt5Agg` → `TkAgg` → `MacOSX` matplotlib backends in that order) needs to work in a compiled
build, at least one interactive backend must not be excluded via Nuitka's `--nofollow-import-to`, and its
corresponding plugin (e.g. `--enable-plugin=tk-inter` for the lightweight, cross-platform `TkAgg` option)
must be enabled.
