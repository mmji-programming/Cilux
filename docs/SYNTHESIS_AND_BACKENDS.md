# Synthesis Pipeline & Export Backends

This document covers everything that happens after a Cilux `gate`/`circuit` has been *defined* and someone
calls `synth()` on it: elaboration into a flat netlist, optimization passes, and every downstream export
format (BLIF, Verilog/JSON via Yosys, VHDL, and the two schematic rendering engines). All of this lives
under `lang/funcs/synth/`, `lang/bridges/`, and `lang/engines/`.

## The `synth()` Pipeline

`synth(target, resolution_level=None, export=false)` (`lang/funcs/synth/plugin.py`) runs, in order:

```
target (Gate/Circuit)
   │
   ▼
Elaborator(kernel, target, resolution_level).elaborate()   →  NetlistDB (raw, unoptimized)
   │
   ▼
CompilerPasses.detect_combinational_loops(db)                →  raises RuntimeError on real loops
   │
   ▼
NetlistOptimizer.optimize(db)                                  →  constant folding, mux reduction,
   │                                                                dead-code elimination (iterated to
   │                                                                a fixpoint, up to 50 passes)
   ▼
CompilerPasses.remove_phantom_signals(db)                        →  drop unreachable driver-less signals
   │
   ▼
CompilerPasses.validate_graph(db)                                   →  emits `warnings` for floating
   │                                                                    inputs, undriven outputs, and any
   │                                                                    remaining loops (best-effort;
   │                                                                    non-fatal)
   ▼
SynthResult(db)   ← returned to Cilux code
```

`Elaborator` is instantiated fresh for the target (and, recursively, for any black-boxed sub-module — see
below); `CompilerPasses` and `NetlistOptimizer` are stateless classes of `@staticmethod`/`@classmethod`
passes operating directly on the mutable `NetlistDB`.

### Core data model (`lang/funcs/synth/types.py`)

- **`Signal`** — `id`, `width` (currently always effectively `1` — see the limitation note below),
  `driver: Optional[Endpoint]`, `readers: List[Endpoint]`, and `is_constant`/`const_value` for the two
  singleton constant signals (`CONST_0`, `CONST_1`) every `NetlistDB` starts with.
- **`Endpoint`** — `(inst_id, port_name, direction)`; `inst_id == "TOP"` refers to a top-level
  input/output rather than a sub-instance pin. `.to_string()` renders as `"port"` for top-level or
  `"inst.port"` otherwise — this is what you see in `SynthResult`'s pretty-printed summary and in
  combinational-loop error messages.
- **`Instance`** — one instantiated primitive gate, DFF, MUX, or black-boxed sub-circuit: `id`,
  `module_name`, `inputs`/`outputs` (`Dict[str, Signal]`), `is_blackbox`, `is_sequential`,
  `port_roles` (marks which input is the `clock`/`reset`/`set`/`preset` control pin, so passes and
  renderers can treat it specially instead of as ordinary data), and `trigger_edge`.
- **`NetlistDB`** — the whole flattened design: `signals`, `instances`, `top_inputs`, `top_outputs`,
  `sub_dbs` (populated only when black-boxing is used — a `NetlistDB` per referenced sub-module),
  `module_name`, plus `create_signal()` / `get_const(0|1)` helpers.
- **`SymbolTable`** (`symbol_table.py`) — a small parent-linked table (distinct from the language-level
  `core.context.Context`) used purely during elaboration to map Cilux variable names to `Signal` objects
  and to resolve local circuit/gate definitions while walking a body.

### What `resolution_level` actually controls

`resolution_level` is a **hierarchy-flattening depth budget**, threaded through `_walk`/`_instantiate` as
`res_left`, decremented by one on every nested circuit/gate instantiation:

- `resolution_level=0` (the default) fully **inlines/flattens everything** — every instantiated
  sub-circuit's internal gates become directly part of the parent's `NetlistDB`, all the way down to base
  primitives (`AND`/`OR`/`XOR`/`NOT`/`NAND`/`NOR`/`XNOR`/`DFF_P`/`DFF_N`/`MUX2`).
- A **positive** `resolution_level` stops flattening once that many levels of hierarchy have been descended,
  and instead emits a **black-box `Instance`** (`is_blackbox=True`) referencing a *separately elaborated*
  sub-`NetlistDB` for that module (built by `Elaborator._ensure_sub_db`, stored in `self.sub_dbs` and
  shared across the whole elaboration run so the same sub-module is only elaborated once even if
  instantiated many times). This is what lets BLIF/Verilog/VHDL output preserve some of the design's
  original module hierarchy (`.subckt` references) instead of always producing one giant flat netlist.
- `export=true` calls `max_resolution_level(target, kernel)` (`analysis.py`) first, which walks the
  instance tree (cycle-safely, via a `visited` `id()` set) to compute the deepest instantiation chain, and
  uses that as the resolution level automatically — i.e. "preserve the design's full hierarchy" rather than
  either fully flattening or arbitrarily picking a shallow cutoff. `export=true` and an explicit
  `resolution_level` together are rejected with a `TypeError`, since they're contradictory instructions.
- `_ensure_sub_db` detects and rejects **circular module instantiation** (a circuit black-boxing itself,
  directly or indirectly) with a `ValueError`, since that can never terminate.

### Elaboration Walk (`Elaborator._walk`, `elaborator.py`)

The elaborator is a hand-written recursive-descent walker over the *already-parsed* Lark tree of a
circuit/gate body (not a second parse — it reuses the same `Lark` tree `Kernel.execute` would otherwise
interpret). It performs, per statement kind:

- **`wire_stmt` / `var_stmt`** — resolves the right-hand side to a `Signal` (creating primitive gate
  `Instance`s for `AND`/`OR`/etc. calls, or recursively instantiating sub-circuits/gates for user-defined
  component calls) and binds it in the local `SymbolTable`, wiring driver/reader `Endpoint`s appropriately.
- **`condition_stmt`** — combinational `if`/`elif`/`else` is elaborated into a chain of `MUX2` primitive
  instances via `_process_condition_stmt`/`_process_conditional_logic`, selecting between each branch's
  computed signal based on the branch's evaluated condition signal.
- **`when_stmt` / `seq_assign`** — registers on the active clock edge, elaborated into `DFF_P`/`DFF_N`
  instances (see blueprints below), tracking `self.active_clk_sig` / `self.active_edge` /
  `self.async_reset_sig` for the duration of the block so nested register logic picks up the correct clock
  and reset wiring automatically.
- **Feedback signals** (a register whose own output feeds back into its own next-state logic, e.g.
  `Q <= Q + 1;` style counters) are pre-declared ahead of the main walk (`_pre_declare_feedback_signals`,
  `_scan_when_targets`, `_scan_seq_assigns`) specifically so that forward references inside `when` bodies
  resolve to the correct pre-existing `Signal` object rather than accidentally creating a second, disconnected
  one.
- **`_instantiate`** (and its cache-aware wrapper `_instantiate_and_get_id`) memoizes repeated calls with
  identical arguments (`_make_inst_cache_key`) so that, e.g., calling the same helper gate twice with the
  same literal inputs inside a loop-like structure doesn't produce duplicate hardware.
- **Primitive blueprints** (`PRIMITIVE_BLUEPRINTS`) — `MUX2`, `DFF_P`, `DFF_N` are *not* Cilux-level
  built-ins the way `AND`/`OR` are; they only exist inside the elaborator as synthesis-time primitives
  produced by conditional logic and clocked assignment respectively. When `res_left > 0` they can
  themselves be further expanded into their constituent base gates via `_blueprint_mux2`,
  `_blueprint_dff_p`, `_blueprint_dff_n` (e.g. a positive-edge DFF is built structurally from latches and
  gates rather than being a synthesis-level black box) — this is explicitly designed to be extensible: the
  comment above `PRIMITIVE_BLUEPRINTS` documents the three-step process for adding a new expandable
  primitive.
- A **call-depth guard** (`self.call_depth`, `MAX_DEPTH = 100`) prevents runaway recursive elaboration of
  pathological/self-referential designs from exhausting the Python call stack ungracefully.

## Optimization & Validation Passes (`lang/funcs/synth/passes.py`)

### `NetlistOptimizer.optimize(db)`

Runs to a fixpoint (or a hard cap of 50 iterations) over three rewrite passes, re-running all three each
iteration as long as *any* of them changed the graph:

1. **`_propagate_constants_and_fold`** — folds any primitive gate whose inputs are all constant into a
   direct constant driver, and simplifies gates with a mix of constant/non-constant inputs using standard
   Boolean identities (e.g. `AND(x, 0) → 0`, `OR(x, 1) → 1`, `AND(x, 1) → x`).
2. **`_reduce_muxes`** — simplifies `MUX2` instances whose select line is constant, or whose two data inputs
   are proven identical, replacing the mux with a direct wire to the selected/common input
   (`_replace_instance_with_sig`).
3. **`_eliminate_dead_code`** — traces backward from every top-level output (and from any signal actually
   read by a black-box sub-instance) via `trace_back`, and removes any instance/signal that provably cannot
   reach a real output — classic mark-and-sweep dead-code elimination for hardware.

After the fixpoint loop, `_scrub_phantom_references` cleans up any dangling references left behind by the
rewrites (e.g. an instance input still pointing at a `Signal` object that was just deleted from `db.signals`).

### `CompilerPasses`

- **`detect_combinational_loops(db)`** — builds a directed graph of *combinational-only* instance-to-instance
  dependencies (edges through sequential/`DFF_*` instances are deliberately not added, since a loop through
  a register is a normal, valid feedback path, not a combinational race) and runs a classic iterative
  white/gray/black DFS cycle search. If a cycle is found, it raises `RuntimeError` with a step-by-step
  description of the offending path (`inst --[signal]--> inst --[signal]--> ...`), since an unbroken
  combinational loop is not synthesizable/simulatable hardware.
- **`remove_phantom_signals(db)`** — removes signals that are referenced nowhere (not a top-level
  input/output, not any instance's input or output, undriven, non-constant, yet still have readers) — an
  artifact-cleanup pass distinct from full dead-code elimination.
- **`validate_graph(db)`** — a best-effort, **non-raising** sanity pass that emits Python `warnings` (not
  exceptions) for floating inputs, undriven top-level outputs, and any residual combinational loop it can
  detect via a second, independent DFS (kept intentionally separate from
  `detect_combinational_loops`, which is the authoritative, raising check run earlier in the pipeline).

## Rendering the Netlist: `SynthResult.__repr__`

Printing a `SynthResult` directly (without exporting) shows a bounded, human-scannable summary: header
stats (instance count split into combinational/sequential, signal count, inputs/outputs), a table of up to
`MAX_INST=12` instances with their tag (`comb`/`seq`/`box`) and resolved connections, and a table of up to
`MAX_SIG=8` non-constant signals with driver/reader endpoints — with `··· +N more ...` truncation notices
when the design exceeds those caps, so inspecting even a large synthesized design in a REPL stays readable.

## Export Formats

### BLIF (`blif()`, `lang/bridges/blif.py` → `lang/funcs/synth/analysis.py: render_blif`)

`render_blif(db, model_name, sub_dbs)` walks a `NetlistDB` and emits standard **BLIF** text:

- `.model` / `.inputs` / `.outputs` / `.end` structure, one `.model` block per sub-module referenced via a
  black-box `Instance` (recursively, from `sub_dbs`), plus the top model.
- Names are sanitized for BLIF compatibility (`_sanitize_blif_name` — `.` → `__`, `[`/`]` → `_`).
- Combinational primitives are emitted as `.names` truth-table lines: `_emit_2input_gate` for direct
  2-input primitives and `_emit_tree_gate` for reducing >2-input `AND`/`OR`/`XOR`/etc. calls into a balanced
  tree of 2-input `.names` entries (BLIF's native truth-table format is most naturally binary/2-input).
- Sequential instances become `.latch` lines, with clock/reset/set/preset control pins identified via each
  `Instance.port_roles` (`_collect_dff_control_ports`) rather than positionally, so DFF variants with
  different control-pin layouts are handled uniformly.
- Black-box instances are emitted as `.subckt` references into their corresponding sub-model.
- `BlifResult.__export__` writes the raw text as-is to any file extension.

### Verilog / JSON (`verilog()` / `json()`, `lang/bridges/yosys_base.py`)

Both are implemented as thin wrappers that:

1. Convert the input (`BlifResult` or `SynthResult`) to BLIF text (`render_blif`, reusing the same function
   as `blif()`).
2. Write it to a temporary `.blif` file.
3. Invoke a **bundled WebAssembly build of Yosys** (`yowasp_yosys.run_yosys`, i.e. `yowasp-yosys` — Yosys
   compiled to WASM and run via the `wasmtime` runtime, requiring no separate native Yosys install) with a
   fixed optimization/technology-mapping script:

   ```
   read_blif <file>; proc; opt -full; wreduce; peepopt; opt_expr; opt_clean -purge;
   share -aggressive; opt -full; techmap; opt -full; opt_clean -purge;
   abc -g simple; opt -full; opt_clean -purge;   (repeated 3×)
   write_verilog -noattr -nodec <out>   (or write_json <out>)
   ```

4. Reads the resulting file back into memory as the returned `YosysResult(data, fmt="verilog"|"json")`.

Yosys's own stdout/stderr are suppressed (redirected to `os.devnull` at the file-descriptor level, and its
Python-level streams intercepted too) via `_silent_run_yosys`, with `SystemExit` (how Yosys reports fatal
errors) caught and converted into a normal Python `RuntimeError` carrying the captured `ERROR:` line.
Temporary files are always cleaned up in a `finally` block.

### VHDL (`vhdl()`, `lang/bridges/vhdl.py` + `lang/engines/vl2vhd_engine.py`)

`vhdl()` accepts a `SynthResult`, `BlifResult`, **or** an existing verilog-flavored `YosysResult`, normalizes
all three down to a Verilog string (running the same Yosys BLIF→Verilog pipeline as `verilog()` if starting
from BLIF/synth), and then converts that Verilog text to VHDL with a **hand-written** parser and generator
(no external tool):

- **`VerilogParser`** (`vl2vhd_engine.py`) tokenizes/parses the structural-Verilog output of the Yosys step
  into small dataclasses: `Port`, `Wire`, `Assignment`, `Instance` (module instantiation with parameter/port
  connections), `StmtAssign`, `StmtIf`, and similar, per module.
- **`VHDLGen`** walks those parsed module structures and emits equivalent VHDL entity/architecture text.

Because the Yosys step is specifically asked to normalize (`-noattr -nodec`) the intermediate Verilog into a
simple, predictable structural subset, `VerilogParser` only needs to understand that constrained subset
rather than general Verilog-2001/SystemVerilog — it is not, and is not intended to be, a general-purpose
Verilog front end.

## Schematic Rendering

`schematic(synth_result, mode="text"|"gui")` (`lang/funcs/schematic.py`) dispatches to one of two
completely independent rendering engines, both operating directly on a `NetlistDB`:

### `mode="text"` — `lang/engines/ascii_draw_engine.py` (`ASCIIBackend` in `renderers.py`)

A from-scratch ASCII schematic layout engine: builds a directed graph of instances/signals, finds back-edges
(`find_back_edges`, a DFS-based cycle-breaking step needed before layered layout can proceed), assigns
instances to horizontal layers (`assign_layers`), and lays out box-drawing-character (`─│┌┐└┘├┤┬┴┼`) gate
symbols and wire routing entirely in a 2D character grid. Exportable to `.txt` (raw text), or rendered to
`.png`/`.svg` via the bundled monospace font utilities (`lang/funcs/utils.py: to_png`/`to_svg`).

### `mode="gui"` — `lang/engines/render_schemdraw_engine.py`

A richer, proportionally-laid-out schematic using the third-party `schemdraw` + `matplotlib` libraries:
topologically sorts instances (`_topo_sort`) and assigns them to columns (`_assign_columns`), builds
proper `schemdraw` gate/IC symbols (via `schemdraw.logic` for base gates and `elm.Ic(...)` with computed pin
layouts for larger/custom components — `_build_gate`, `_build_ic`), and routes wires between exact pin
anchor positions with junction-dot placement for multi-fanout signals and multi-segment routing for
long/looping connections (`_route_lane`). This engine actually measures each schemdraw element's real anchor
geometry (`_measure_pin_offsets`, using a scratch, non-shown `schemdraw.Drawing`) rather than guessing pin
positions from a fixed template.

`show_schematic()` opens this rendering interactively (`plt.show()`), trying matplotlib GUI backends in
order `QtAgg` → `Qt5Agg` → `TkAgg` → `MacOSX` and raising a clear `RuntimeError` if none is available; a
non-interactive `Agg`-backed export path (`draw_schematic()` / `SchematicResult.__export__`) writes directly
to `.png`/`.svg`/`.pdf` without requiring any GUI toolkit at all.

## Known Limitation: No Native Multi-Bit Signals

Every `Signal` in the synthesis layer is, in practice, exactly **one bit**. `input a[3:0]` and similar range
syntax (§5 of [`LANGUAGE_REFERENCE.md`](./LANGUAGE_REFERENCE.md#5-ranges-hilo--bus-sugar)) is purely
*language-level* name-expansion into `a3, a2, a1, a0` — four completely independent scalar signals — not a
true multi-bit bus tracked as a single object through elaboration. `NetlistDB.get_const(val)` explicitly
raises `ValueError` for any literal other than `0`/`1`, with an in-source design note (`types.py`) laying out
exactly what would need to change to add real bus support: propagating `Signal.width`, making `get_const`
width-aware (returning either a list of per-bit `Signal`s or a new `SignalBus` dataclass), updating the two
literal-evaluation sites in `_eval_expr`, and extending BLIF emission to emit one `.names`/`.latch` line per
bit of a bus. This is tracked as future work, not a bug — see the project's own `TODO` file.
