from core.engine import Kernel

#  Types (no deps)
from lang.types._int import IntPlugin
from lang.types._bool import BoolPlugin
from lang.types._str import StringPlugin

#  Operators (no deps)
from lang.oprators._range import RangePlugin
from lang.oprators.access import AccessPlugin

#  Expression (deps: none / minimal)
from lang.expr.expression import ExprPlugin, ExprStmtPlugin

#  Types (deps: expr)
from lang.types._list import ListType
from lang.types._dict import DictType

#  IO (deps: access)
from lang.io._import import ImportPlugin
from lang.io.export import ExportPlugin

#  Base Gates (deps: expr)
from lang.structs.base_gates.and_gate import AndPlugin
from lang.structs.base_gates.or_gate import OrPlugin
from lang.structs.base_gates.xor_gate import XorPlugin
from lang.structs.base_gates.not_gate import NotPlugin
from lang.structs.base_gates.xnor_gate import XnorPlugin
from lang.structs.base_gates.nor_gate import NorPlugin
from lang.structs.base_gates.nand_gate import NandPlugin

#  Operators (deps: expr, gates, access)
from lang.oprators.dot import DotPlugin
from lang.oprators.assign import AssignPlugin
from lang.oprators.wire import WirePlugin
from lang.oprators.condition import ConditionPlugin

#  Structs: Gate / Circuit (deps: gate, wire, access)
from lang.structs.gate import GatePlugin
from lang.structs.circuit import CircuitPlugin
from lang.structs.clock import ClockPlugin
from lang.structs.when import WhenPlugin

#  Functions (deps: circuit, gate, expr, str, ...)
from lang.funcs._print import PrintPlugin
from lang.funcs.typeof import TypeofPlugin
from lang.funcs.table import TablePlugin
from lang.funcs.wave import WavePlugin
from lang.funcs.simulate import SimulatePlugin
from lang.funcs.synth.plugin import SynthPlugin
from lang.funcs.schematic import SchematicPlugin

#  Bridges (deps: circuit, gate, synth)
from lang.bridges.blif import BlifPlugin
from lang.bridges.yosys_base import YosysPlugin
from lang.bridges.vhdl import VhdlPlugin


PLUGINS = [
    # Types (no deps)
    IntPlugin(),
    BoolPlugin(),
    StringPlugin(),
    # Operators (no deps)
    RangePlugin(),
    AccessPlugin(),
    # Expression
    ExprPlugin(),
    ExprStmtPlugin(),
    # Types (deps: expr)
    ListType(),
    DictType(),
    # IO (deps: access)
    ImportPlugin(),
    ExportPlugin(),
    # Base Gates (deps: expr)
    AndPlugin(),
    OrPlugin(),
    XorPlugin(),
    NotPlugin(),
    XnorPlugin(),
    NorPlugin(),
    NandPlugin(),
    # Operators (deps: expr, gates, access)
    DotPlugin(),
    AssignPlugin(),
    WirePlugin(),
    ConditionPlugin(),
    # Structs (deps: gate, wire, access)
    GatePlugin(),
    CircuitPlugin(),
    ClockPlugin(),
    WhenPlugin(),
    # Functions (deps: circuit, gate, expr, str, ...)
    PrintPlugin(),
    TypeofPlugin(),
    TablePlugin(),
    WavePlugin(),
    SimulatePlugin(),
    SynthPlugin(),
    SchematicPlugin(),
    # Bridges (deps: circuit, gate, synth)
    BlifPlugin(),
    YosysPlugin(),
    VhdlPlugin(),
]


def create_kernel():
    kernel = Kernel()
    for plugin in PLUGINS:
        kernel.register(plugin)
    return kernel
