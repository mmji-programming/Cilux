from core.plugin import Plugin, Value
from lang.types.builtin import BuiltinFunction
from lang.funcs.synth.types import SynthResult
from lang.funcs.synth.analysis import render_blif
from .blif import BlifResult


from pathlib import Path
import sys
import io
import os
import tempfile
from yowasp_yosys import run_yosys


def _silent_run_yosys(command: str, mode: str):

    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    old_stdout_fd = os.dup(1)
    old_stderr_fd = os.dup(2)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        run_yosys(["-p", command])
        return True, f"Yosys [{mode}] export completed successfully."
    except SystemExit:
        captured = sys.stdout.getvalue() + sys.stderr.getvalue()
        error_line = next((l for l in captured.splitlines() if "ERROR:" in l), "Unknown error")
        return False, error_line.replace("ERROR:", "").strip()
    finally:
        os.dup2(old_stdout_fd, 1)
        os.dup2(old_stderr_fd, 2)
        os.close(old_stdout_fd)
        os.close(old_stderr_fd)
        os.close(devnull_fd)
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _run_yosys_to_string(input_blif: str, write_command: str, suffix: str, mode: str) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    tmp_path_safe = tmp_path.replace("\\", "/")
    input_blif_safe = input_blif.replace("\\", "/")

    try:
        command = (
            f"read_blif {input_blif_safe}; "
            f"proc; "
            f"opt -full; "
            f"wreduce; "
            f"peepopt; "
            f"opt_expr; opt_clean -purge; "
            f"share -aggressive; "
            f"opt -full; "
            f"techmap; "
            f"opt -full; opt_clean -purge; "
            f"abc -g simple; "
            f"opt -full; opt_clean -purge; "
            f"abc -g simple; "
            f"opt -full; opt_clean -purge; "
            f"abc -g simple; "
            f"opt -full; opt_clean -purge; "
            f"{write_command} {tmp_path_safe}"
        )

        ok, msg = _silent_run_yosys(command, mode)

        if not ok:
            raise RuntimeError(f"Yosys [{mode}] export failed: {msg}")

        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read()

    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def yosys_export_verilog(input_blif: str) -> str:
    return _run_yosys_to_string(
        input_blif,
        write_command="write_verilog -noattr -nodec",
        suffix=".v",
        mode="verilog",
    )


def yosys_export_json(input_blif: str) -> str:
    return _run_yosys_to_string(
        input_blif,
        write_command="write_json",
        suffix=".json",
        mode="json",
    )


class YosysResult(Value):
    def __init__(self, data: str, fmt: str):
        self.data = data
        self.vtype = f"yosys-{fmt}"
        self.fmt = fmt

    def __repr__(self):
        return self.data

    def __export__(self, _: Path) -> str:
        return self.data


class YosysPlugin(Plugin):
    name = "yosys"
    deps = ["circuit", "gate"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "verilog",
            BuiltinFunction("verilog", YosysPlugin.exec_verilog),
            readonly=True,
        )
        kernel.context.declare(
            "json",
            BuiltinFunction("json", YosysPlugin.exec_json),
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
    def _to_blif_string(target) -> str:

        if isinstance(target, BlifResult):
            return target.data

        if isinstance(target, SynthResult):
            db = target.data
            return render_blif(db, db.module_name, sub_dbs=db.sub_dbs)

        raise TypeError(
            f"Expected a BlifResult or SynthResult, got '{type(target).__name__}'. Use blif(...) or synth(...) first."
        )

    @staticmethod
    def _run_yosys(blif_text: str, exporter) -> str:

        fd, tmp_path = tempfile.mkstemp(suffix=".blif")
        os.close(fd)
        tmp_path_safe = tmp_path.replace("\\", "/")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(blif_text)

            return exporter(tmp_path_safe)

        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def exec_verilog(kernel, node):
        target = YosysPlugin._extract_target(kernel, node)

        if target is None:
            raise TypeError("verilog() requires a BlifResult or SynthResult as the first argument.")

        blif_text = YosysPlugin._to_blif_string(target)
        verilog = YosysPlugin._run_yosys(blif_text, yosys_export_verilog)

        return YosysResult(verilog, fmt="verilog")

    def exec_json(kernel, node):
        target = YosysPlugin._extract_target(kernel, node)

        if target is None:
            raise TypeError("json() requires a BlifResult or SynthResult as the first argument.")

        blif_text = YosysPlugin._to_blif_string(target)
        json_text = YosysPlugin._run_yosys(blif_text, yosys_export_json)

        return YosysResult(json_text, fmt="json")
