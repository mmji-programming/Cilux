from core.plugin import Plugin, Value
from lang.types.builtin import (
    BuiltinFunction,
)
from lang.funcs.synth.types import (
    SynthResult,
)
from lang.funcs.synth.analysis import (
    render_blif,
)
from lang.engines.vl2vhd_engine import (
    VerilogParser,
    VHDLGen,
)
from .yosys_base import (
    yosys_export_verilog,
    YosysPlugin,
    YosysResult,
)
from .blif import (
    BlifResult,
)

from pathlib import Path


class VhdlError(Exception):
    __module__ = Exception.__module__


class VhdlResult(Value):
    def __init__(self, data: str):
        self.data = data
        self.vtype = "vhdl"

    def __repr__(self):
        return self.data

    def __export__(self, path: Path) -> str:
        return self.data


class VhdlPlugin(Plugin):
    name = "vhdl"
    deps = ["circuit", "gate"]

    def on_load(self, kernel):
        kernel.context.declare(
            "vhdl",
            BuiltinFunction("vhdl", VhdlPlugin.exec_vhdl),
            readonly=True,
        )

    @staticmethod
    def _extract_target(kernel, node):
        for child in node.children[1:]:
            if hasattr(child, "data") and child.data == "arg":
                inner = child.children[0]
                if not (hasattr(inner, "data") and inner.data == "named_arg"):
                    return kernel.execute(inner)
        return None

    @staticmethod
    def _to_verilog_string(target) -> str:

        if isinstance(target, YosysResult):
            if target.fmt != "verilog":
                raise VhdlError(
                    f"vhdl() requires a verilog YosysResult, but got "
                    f"fmt='{target.fmt}'. Use verilog(...) instead of "
                    f"json(...) before converting to VHDL."
                )
            return target.data

        if isinstance(target, BlifResult):
            blif_text = target.data
            return VhdlPlugin._run_yosys_verilog(blif_text)

        if isinstance(target, SynthResult):
            db = target.data
            blif_text = render_blif(db, db.module_name, sub_dbs=db.sub_dbs)
            return VhdlPlugin._run_yosys_verilog(blif_text)

        raise TypeError(
            f"vhdl() expected a SynthResult, BlifResult, or verilog "
            f"YosysResult, got '{type(target).__name__}'. "
            f"Use synth(), blif(), or verilog() first."
        )

    @staticmethod
    def _run_yosys_verilog(blif_text: str) -> str:

        return YosysPlugin._run_yosys(blif_text, yosys_export_verilog)

    def exec_vhdl(kernel, node):
        target = VhdlPlugin._extract_target(kernel, node)

        if target is None:
            raise TypeError("vhdl() requires a SynthResult, BlifResult, or verilog YosysResult as the first argument.")

        verilog_text = VhdlPlugin._to_verilog_string(target)

        try:
            modules = VerilogParser(verilog_text).parse()
        except SyntaxError as e:
            raise VhdlError(f"Failed to parse intermediate Verilog: {e}")

        if not modules:
            raise VhdlError(
                "No modules found in the generated Verilog. This usually means the synthesized netlist was empty."
            )

        try:
            vhdl_text = VHDLGen(modules).generate()
        except Exception as e:
            raise VhdlError(f"Failed to generate VHDL: {e}")

        return VhdlResult(vhdl_text)
