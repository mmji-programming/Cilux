from core.plugin import Value

from dataclasses import dataclass, field
from typing import Dict, List, Optional


class SynthResult(Value):
    MAX_INST = 12
    MAX_SIG = 8
    MAX_READER = 3

    def __init__(self, db: "NetlistDB"):
        super().__init__(db, "netlist")

    def __repr__(self) -> str:
        db = self.data
        n_inst = len(db.instances)
        n_sig = len([s for s in db.signals.values() if not s.is_constant])
        n_in = len(db.top_inputs)
        n_out = len(db.top_outputs)
        W = 64

        seq_count = sum(1 for i in db.instances.values() if i.is_sequential)
        comb_count = n_inst - seq_count

        def hr(c="─"):
            return c * W

        lines = []

        lines += [
            hr("═"),
            f"  Netlist  {db.module_name}",
            f"  {'in':<8} {' '.join(db.top_inputs.keys()) or '—'}",
            f"  {'out':<8} {' '.join(db.top_outputs.keys()) or '—'}",
            f"  {'stats':<8} {n_inst} inst  ({comb_count} comb · {seq_count} seq)  ·  {n_sig} signals",
            hr("═"),
        ]

        lines += [
            f"  {'INSTANCE':<16} {'MODULE':<14} {'TYPE':<5}  CONNECTIONS",
            hr("·"),
        ]

        inst_items = list(db.instances.items())
        for inst_id, inst in inst_items[: self.MAX_INST]:
            if inst.is_sequential:
                tag = "seq"
            elif inst.is_blackbox:
                tag = "box"
            else:
                tag = "comb"

            in_parts = []
            for p, s in inst.inputs.items():
                if s.is_constant:
                    drv = str(s.const_value)
                elif s.driver:
                    drv = s.driver.to_string()
                else:
                    drv = "?"
                in_parts.append(f"{p}={drv}")

            out_parts = list(inst.outputs.keys())
            conn = f"{', '.join(in_parts)}  ->  {', '.join(out_parts)}"
            lines.append(f"  {inst_id:<16} {inst.module_name:<14} {tag:<5}  {conn}")

        if n_inst > self.MAX_INST:
            lines.append(f"  ··· +{n_inst - self.MAX_INST} more instances")

        lines += [
            hr("─"),
            f"  {'SIGNAL':<20} {'DRIVER':<20} READERS",
            hr("·"),
        ]

        sig_items = [(sid, s) for sid, s in db.signals.items() if not s.is_constant]
        for sig_id, sig in sig_items[: self.MAX_SIG]:
            drv = sig.driver.to_string() if sig.driver else "—"
            readers = [r.to_string() for r in sig.readers[: self.MAX_READER]]
            if len(sig.readers) > self.MAX_READER:
                readers.append(f"+{len(sig.readers) - self.MAX_READER}")
            lines.append(f"  {sig_id:<20} {drv:<20} {', '.join(readers) or '—'}")

        if len(sig_items) > self.MAX_SIG:
            lines.append(f"  ··· +{len(sig_items) - self.MAX_SIG} more signals")

        lines.append(hr("═"))
        return "\n".join(lines)

    def __export__(self, path, **kwargs) -> str:
        raise NotImplementedError("Use verilog(), json(), or blif() to export a netlist.")


class DummyPort:
    def __init__(self, name: str):
        self.name = name


@dataclass
class Endpoint:
    inst_id: str
    port_name: str
    direction: str

    def to_string(self):
        if self.inst_id == "TOP":
            return self.port_name
        return f"{self.inst_id}.{self.port_name}"


@dataclass
class Signal:
    id: str
    width: int = 1
    driver: Optional[Endpoint] = None
    readers: List[Endpoint] = field(default_factory=list)
    is_constant: bool = False
    const_value: int = 0


@dataclass
class Instance:
    id: str
    module_name: str
    inputs: Dict[str, Signal] = field(default_factory=dict)
    outputs: Dict[str, Signal] = field(default_factory=dict)
    is_blackbox: bool = False

    is_sequential: bool = False
    port_roles: Dict[str, str] = field(default_factory=dict)
    trigger_edge: str = "posedge"


class NetlistDB:
    def __init__(self):
        self.module_name: str = "top"
        self.sub_dbs: Dict[str, "NetlistDB"] = {}
        self.signals: Dict[str, Signal] = {}
        self.instances: Dict[str, Instance] = {}
        self.top_inputs: Dict[str, Signal] = {}
        self.top_outputs: Dict[str, Signal] = {}

        self.const_signals = {
            0: Signal(id="CONST_0", is_constant=True, const_value=0),
            1: Signal(id="CONST_1", is_constant=True, const_value=1),
        }
        self.signals["CONST_0"] = self.const_signals[0]
        self.signals["CONST_1"] = self.const_signals[1]

        self.sig_counter = 0

    def create_signal(self, prefix="sig", exact_id=None) -> Signal:
        if exact_id:
            sig_id = exact_id
        else:
            sig_id = f"{prefix}_{self.sig_counter}"
            self.sig_counter += 1

        if sig_id not in self.signals:
            sig = Signal(id=sig_id)
            self.signals[sig_id] = sig
            return sig
        return self.signals[sig_id]

    def get_const(self, val: int) -> Signal:
        if val not in (0, 1):
            raise ValueError(
                f"Cilux synthesis error: integer literal '{val}' is not a "
                f"valid 1-bit value (only 0 and 1 are supported). Signals in "
                f"this version of the compiler are strictly 1-bit; multi-bit "
                f"/ bus literals (e.g. 'STATE <= 2;' for a >1-bit register) "
                f"are not yet supported and would otherwise be silently "
                f"misinterpreted as 0, producing incorrect hardware."
            )
        return self.const_signals[val]

        # ============================================================
        # FUTURE WORK -- multi-bit / bus signal support
        # ============================================================
        # If multi-bit signals are added to the language in the future,
        # this is the central place to extend:
        #
        #   1. Signal.width (already present as a field, currently unused
        #      everywhere except as a default of 1) must actually be
        #      propagated: every create_signal() call site that declares a
        #      register/wire of width N must set sig.width = N, and every
        #      consumer of a Signal must respect it instead of assuming 1.
        #
        #   2. get_const(val, width=1) should accept a `width` parameter and
        #      either:
        #        (a) return/construct a *bus* of `width` single-bit constant
        #            Signals (e.g. a List[Signal], one per bit, MSB-first or
        #            LSB-first -- pick one convention and use it everywhere),
        #            or
        #        (b) introduce a new SignalBus dataclass wrapping width-many
        #            1-bit Signal objects, and make get_const construct/cache
        #            one CONST_<N>_<width> Signal-bus per distinct (value,
        #            width) pair actually used in the design (mirroring the
        #            existing const_signals cache pattern for 0/1).
        #
        #   3. _eval_expr's two int_literal / INT-token handling sites
        #      (search for "node.type == \"INT\"" and
        #      'node.data in ("int_literal", "bool_literal")') must be
        #      updated to call the new width-aware get_const and propagate
        #      the resulting bus type through seq_assign / var_stmt / wire_stmt
        #      instead of assuming every expression evaluates to one Signal.
        #
        #   4. render_blif's comb-primitive and DFF-emission logic
        #      (the COMB_PRIMITIVES loop and the `_is_seq` branch) must be
        #      extended to emit one .names/.latch line PER BIT of a bus
        #      signal, using a consistent per-bit wire naming scheme (e.g.
        #      "{sig.id}[{bit_index}]") so each bit becomes its own BLIF wire.
        #
        #   5. Every place that currently does `int(node.value)` and feeds it
        #      straight into get_const (the two call sites mentioned in #3)
        #      needs a companion "what is the bit-width of the LHS target
        #      this literal is being assigned to?" lookup -- the width must
        #      come from the target signal's declared width (from `range_def`
        #      in the grammar, e.g. `STATE[3:0]`), not be inferred from the
        #      literal's magnitude.
        # ============================================================
