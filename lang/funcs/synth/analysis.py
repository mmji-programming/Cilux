from .types import (
    NetlistDB,
    Signal,
)


from typing import Optional, List, Dict


def max_resolution_level(obj, kernel=None, visited=None) -> int:

    if visited is None:
        visited = set()

    obj_id = id(obj)
    if obj_id in visited:
        return 0

    visited.add(obj_id)

    instances = getattr(obj, "instances", None)
    if not instances:
        return 0

    max_depth = 0
    exports = getattr(obj, "_module_exports", {}) or {}

    for _, circ_ref in instances:
        if isinstance(circ_ref, str):
            resolved = exports.get(circ_ref)
            if resolved is None and kernel:
                try:
                    resolved = kernel.context.lookup(circ_ref)
                except NameError:
                    pass
            if resolved is None:
                continue

            if not getattr(resolved, "_module_exports", None) and exports:
                try:
                    resolved._module_exports = exports
                except AttributeError:
                    pass

            circ_ref = resolved

        if not hasattr(circ_ref, "instances"):
            continue

        d = max_resolution_level(circ_ref, kernel, visited)
        max_depth = max(max_depth, d + 1)

    return max_depth


def render_blif(db: NetlistDB, model_name: str = "top", sub_dbs: dict = None) -> str:

    _UNSAFE_BLIF_CHARS = str.maketrans(
        {
            ".": "__",
            "[": "_",
            "]": "_",
        }
    )

    def _sanitize_blif_name(name: str) -> str:
        if name is None:
            return name
        if name in ("$true", "$false"):
            return name
        return name.translate(_UNSAFE_BLIF_CHARS)

    model_name = _sanitize_blif_name(model_name)

    def _sig_name(sig) -> "Optional[str]":
        if sig is None:
            return None
        if sig.is_constant:
            return "$true" if sig.const_value else "$false"
        return _sanitize_blif_name(sig.id)

    def _require_sig_name(sig, context: str) -> str:
        name = _sig_name(sig)
        if name is None:
            raise ValueError(f"render_blif: required signal is None in context '{context}'")
        return name

    def _is_seq(inst) -> bool:
        return getattr(inst, "is_sequential", False)

    def _collect_dff_control_ports(inst):
        ar_sig, set_sig = None, None
        ctrl_ports = []

        for pname, psig in inst.inputs.items():
            role = inst.port_roles.get(pname)
            if role == "reset":
                ar_sig = psig
            elif role == "set":
                set_sig = psig
            elif role not in ("clock", "data") and pname not in {
                "CLK",
                "clk",
                "D",
                "d",
            }:
                ctrl_ports.append((pname, psig))

        return ar_sig, set_sig, ctrl_ports

    def _emit_subckt(inst, lines: list, defined: set):
        port_map = []
        for pname, psig in inst.inputs.items():
            n = _sig_name(psig)
            if n:
                port_map.append(f"{pname}={n}")
        for pname, psig in inst.outputs.items():
            n = _sig_name(psig)
            if n:
                port_map.append(f"{pname}={n}")
                defined.add(n)
        safe_mod_name = _sanitize_blif_name(inst.module_name)
        lines.append(f".subckt {safe_mod_name} {' '.join(port_map)}" if port_map else f".subckt {safe_mod_name}")

    def _emit_2input_gate(mod: str, a_name: str, b_name: str, out_name: str, lines: list):
        lines.append(f".names {a_name} {b_name} {out_name}")
        if mod == "AND":
            lines.append("11 1")
        elif mod == "OR":
            lines.extend(["1- 1", "-1 1"])
        elif mod == "XOR":
            lines.extend(["01 1", "10 1"])
        elif mod == "NAND":
            lines.extend(["00 1", "01 1", "10 1"])
        elif mod == "NOR":
            lines.append("00 1")
        elif mod == "XNOR":
            lines.extend(["00 1", "11 1"])

    _decomp_counter = [0]

    def _emit_tree_gate(mod: str, in_names: list, out_name: str, lines: list, defined: set):
        if len(in_names) == 1:
            lines.extend([f".names {in_names[0]} {out_name}", "1 1"])
            defined.add(out_name)
            return
        if len(in_names) == 2:
            _emit_2input_gate(mod, in_names[0], in_names[1], out_name, lines)
            defined.add(out_name)
            return
        mid = len(in_names) // 2
        left_names, right_names = in_names[:mid], in_names[mid:]
        idx = _decomp_counter[0]
        _decomp_counter[0] += 1
        left_wire, right_wire = (
            f"_decomp_{mod.lower()}_{idx}_L",
            f"_decomp_{mod.lower()}_{idx}_R",
        )
        _emit_tree_gate(mod, left_names, left_wire, lines, defined)
        _emit_tree_gate(mod, right_names, right_wire, lines, defined)
        _emit_2input_gate(mod, left_wire, right_wire, out_name, lines)
        defined.add(out_name)

    blif_lines: "List[str]" = []
    blif_lines.append(f".model {model_name}")

    input_names = list(db.top_inputs.keys())
    output_names = list(db.top_outputs.keys())
    blif_lines.append(".inputs" + (" " + " ".join(input_names) if input_names else ""))
    blif_lines.append(".outputs" + (" " + " ".join(output_names) if output_names else ""))

    used_consts: set = set()
    for inst in db.instances.values():
        for sig in inst.inputs.values():
            if sig and sig.is_constant:
                used_consts.add(sig.const_value)
    for out_sig in db.top_outputs.values():
        if out_sig and out_sig.is_constant:
            used_consts.add(out_sig.const_value)
    for sig in db.signals.values():
        if sig.is_constant and sig.readers:
            used_consts.add(sig.const_value)

    if 0 in used_consts:
        blif_lines.append(".names $false")
    if 1 in used_consts:
        blif_lines.extend([".names $true", "1"])

    defined_wires: set = set(input_names)
    if 0 in used_consts:
        defined_wires.add("$false")
    if 1 in used_consts:
        defined_wires.add("$true")

    COMB_PRIMITIVES = {"AND", "OR", "XOR", "NOT", "NAND", "NOR", "XNOR"}
    _DECOMPOSE_THRESHOLD = 8

    # ---- multi-driver pre-pass -----------------------------------------
    # Detect any signal that is claimed as an OUTPUT by more than one
    # instance.  Without this check, a bug anywhere upstream (Elaborator,
    # NetlistOptimizer) that leaves two instances both pointing their
    # output port at the same Signal object would cause render_blif to
    # silently emit two separate .names/.latch definitions for the exact
    # same wire name -- invalid BLIF that downstream tools (ABC/Yosys)
    # would either reject or silently misinterpret.  Fail loudly instead.
    _output_owner: Dict[str, str] = {}
    for _iid, _inst in db.instances.items():
        for _pname, _psig in _inst.outputs.items():
            if _psig is None:
                continue
            _wname = _sig_name(_psig)
            if _wname is None or _psig.is_constant:
                continue
            if _wname in _output_owner and _output_owner[_wname] != _iid:
                raise ValueError(
                    f"render_blif: signal '{_wname}' is driven by multiple "
                    f"instances ('{_output_owner[_wname]}' and '{_iid}'). "
                    f"A wire may only have a single driver; this indicates a "
                    f"multi-driver bug upstream in elaboration/optimization."
                )
            _output_owner[_wname] = _iid

    for inst_id, inst in db.instances.items():
        mod_orig = inst.module_name
        mod = mod_orig.upper()

        if _is_seq(inst):
            d_sig = None
            clk_sig = None

            for pname, psig in inst.inputs.items():
                role = inst.port_roles.get(pname)
                if role == "data":
                    d_sig = psig
                elif role == "clock":
                    clk_sig = psig

            q_sig = None
            for psig in inst.outputs.values():
                q_sig = psig
                break

            if mod_orig == "LATCH":
                if clk_sig is None or _sig_name(clk_sig) is None:
                    _emit_subckt(inst, blif_lines, defined_wires)
                    continue
                latch_type, clk_name = "as", _sig_name(clk_sig)
            else:
                is_neg = getattr(inst, "trigger_edge", "posedge") == "negedge"
                latch_type = "fe" if is_neg else "re"
                clk_name = _sig_name(clk_sig) if clk_sig else "clk"

            if d_sig is None or q_sig is None:
                _emit_subckt(inst, blif_lines, defined_wires)
                continue

            ar_sig, set_sig, ctrl_ports = _collect_dff_control_ports(inst)

            d_name = _require_sig_name(d_sig, f"{inst_id}.D")
            q_name = _require_sig_name(q_sig, f"{inst_id}.Q")
            init_val = getattr(inst, "init_val", 3)

            if (
                ctrl_ports
                or (ar_sig and set_sig)
                or (ar_sig and clk_sig is not None)
                or (set_sig and clk_sig is not None)
            ):
                _emit_subckt(inst, blif_lines, defined_wires)
            elif ar_sig:
                ar_name = _require_sig_name(ar_sig, f"{inst_id}.AR")
                blif_lines.append(f".latch {d_name} {q_name} ah {ar_name} {init_val}")
                defined_wires.add(q_name)
            elif set_sig:
                set_name = _require_sig_name(set_sig, f"{inst_id}.SET")
                blif_lines.append(f".latch {d_name} {q_name} al {set_name} {init_val}")
                defined_wires.add(q_name)
            else:
                blif_lines.append(f".latch {d_name} {q_name} {latch_type} {clk_name} {init_val}")
                defined_wires.add(q_name)

        elif mod in COMB_PRIMITIVES:
            in_sigs = sorted(
                [(pname, sig) for pname, sig in inst.inputs.items() if sig is not None],
                key=lambda kv: kv[0],
            )
            out_sig = inst.outputs.get("o0") or (list(inst.outputs.values())[0] if inst.outputs else None)
            if out_sig is None:
                continue

            in_names = [_require_sig_name(s, f"{inst_id}.{p}") for p, s in in_sigs]
            out_name = _require_sig_name(out_sig, f"{inst_id}.o0")
            n_in = len(in_names)

            if mod == "NOT":
                blif_lines.extend(
                    [
                        f".names {in_names[0] if in_names else '$false'} {out_name}",
                        "0 1",
                    ]
                )
                defined_wires.add(out_name)
            elif mod == "AND":
                if n_in == 0:
                    blif_lines.extend([f".names {out_name}", "1"])
                else:
                    blif_lines.extend([f".names {' '.join(in_names)} {out_name}", "1" * n_in + " 1"])
                defined_wires.add(out_name)
            elif mod == "OR":
                if n_in == 0:
                    defined_wires.add(out_name)
                else:
                    blif_lines.append(f".names {' '.join(in_names)} {out_name}")
                    for i in range(n_in):
                        blif_lines.append(f"{'-' * i}1{'-' * (n_in - i - 1)} 1")
                defined_wires.add(out_name)
            elif mod == "NOR":
                if n_in == 0:
                    blif_lines.extend([f".names {out_name}", "1"])
                else:
                    blif_lines.extend([f".names {' '.join(in_names)} {out_name}", "0" * n_in + " 1"])
                defined_wires.add(out_name)
            elif mod in ("XOR", "NAND", "XNOR"):
                if n_in <= 2:
                    if n_in == 0:
                        blif_lines.append(f".names {out_name}")
                        if mod in ("XNOR", "NAND"):
                            blif_lines.append("1")
                    elif n_in == 1:
                        blif_lines.append(f".names {in_names[0]} {out_name}")
                        blif_lines.append("1 1" if mod == "XOR" else "0 1")
                    else:
                        blif_lines.append(f".names {' '.join(in_names)} {out_name}")
                        if mod == "XOR":
                            blif_lines.extend(["01 1", "10 1"])
                        elif mod == "NAND":
                            blif_lines.extend(["00 1", "01 1", "10 1"])
                        elif mod == "XNOR":
                            blif_lines.extend(["00 1", "11 1"])
                    defined_wires.add(out_name)
                elif n_in <= _DECOMPOSE_THRESHOLD:
                    blif_lines.append(f".names {' '.join(in_names)} {out_name}")
                    for mask in range(1 << n_in):
                        bits = [(mask >> (n_in - 1 - j)) & 1 for j in range(n_in)]
                        if mod == "XOR" and sum(bits) % 2 == 1:
                            blif_lines.append("".join(map(str, bits)) + " 1")
                        elif mod == "NAND" and not all(bits):
                            blif_lines.append("".join(map(str, bits)) + " 1")
                        elif mod == "XNOR" and sum(bits) % 2 == 0:
                            blif_lines.append("".join(map(str, bits)) + " 1")
                    defined_wires.add(out_name)
                else:
                    if mod == "XNOR":
                        xor_wire = f"_xor_tree_{out_name}"
                        _emit_tree_gate("XOR", in_names, xor_wire, blif_lines, defined_wires)
                        blif_lines.extend([f".names {xor_wire} {out_name}", "0 1"])
                    elif mod == "NAND":
                        and_wire = f"_and_tree_{out_name}"
                        _emit_tree_gate("AND", in_names, and_wire, blif_lines, defined_wires)
                        blif_lines.extend([f".names {and_wire} {out_name}", "0 1"])
                    else:
                        _emit_tree_gate("XOR", in_names, out_name, blif_lines, defined_wires)
                    defined_wires.add(out_name)

        elif mod in ("MUX2", "MUX"):
            i0_sig, i1_sig, sel_sig = (
                inst.inputs.get("i0"),
                inst.inputs.get("i1"),
                inst.inputs.get("sel"),
            )
            out_sig = (
                inst.outputs.get("o0")
                or inst.outputs.get("out")
                or (list(inst.outputs.values())[0] if inst.outputs else None)
            )
            if out_sig is None:
                continue

            i0_name, i1_name, sel_name = (
                _require_sig_name(i0_sig, f"{inst_id}.i0"),
                _require_sig_name(i1_sig, f"{inst_id}.i1"),
                _require_sig_name(sel_sig, f"{inst_id}.sel"),
            )
            out_name = _require_sig_name(out_sig, f"{inst_id}.o0")
            blif_lines.extend([f".names {i0_name} {i1_name} {sel_name} {out_name}", "1-0 1", "-11 1"])
            defined_wires.add(out_name)
        else:
            _emit_subckt(inst, blif_lines, defined_wires)

    def _find_driving_wire(out_sig: "Signal") -> "Optional[str]":
        if out_sig is None:
            return None
        if out_sig.is_constant:
            return "$true" if out_sig.const_value else "$false"
        if out_sig.driver is None:
            return None
        ep = out_sig.driver
        if ep.inst_id == "TOP":
            return ep.port_name
        if out_sig.driver.inst_id == ep.inst_id and out_sig.driver.port_name == ep.port_name:
            inst = db.instances.get(ep.inst_id)
            if inst:
                q_sig = inst.outputs.get(ep.port_name)
                if q_sig is not None and q_sig is not out_sig:
                    return q_sig.id
            return out_sig.id
        for sig in db.signals.values():
            if sig.driver and sig.driver.inst_id == ep.inst_id and sig.driver.port_name == ep.port_name:
                return sig.id
        return out_sig.id

    for out_port, out_sig in db.top_outputs.items():
        if out_sig is None:
            continue
        if out_sig.is_constant:
            const_name = "$true" if out_sig.const_value else "$false"
            if out_port not in defined_wires:
                blif_lines.extend([f".names {const_name} {out_port}", "1 1"])
                defined_wires.add(out_port)
            continue

        driver_wire = _find_driving_wire(out_sig)
        if driver_wire is None:
            blif_lines.append(f"# WARNING: output '{out_port}' has no driver")
            if 0 not in used_consts:
                blif_lines.append(".names $false")
                used_consts.add(0)
                defined_wires.add("$false")
            if out_port not in defined_wires:
                blif_lines.extend([f".names $false {out_port}", "1 1"])
                defined_wires.add(out_port)
            continue

        if driver_wire == out_port:
            if out_port not in defined_wires:
                blif_lines.append(f"# WARNING: output '{out_port}' has no driver")
                if 0 not in used_consts:
                    blif_lines.append(".names $false")
                    used_consts.add(0)
                    defined_wires.add("$false")
                blif_lines.extend([f".names $false {out_port}", "1 1"])
                defined_wires.add(out_port)
        else:
            if out_port not in defined_wires:
                blif_lines.extend([f".names {driver_wire} {out_port}", "1 1"])
                defined_wires.add(out_port)

    blif_lines.append(".end")
    blif_str = "\n".join(blif_lines)

    if sub_dbs:
        for sub_name, sub_db in sub_dbs.items():
            blif_str += "\n\n" + render_blif(sub_db, model_name=sub_name, sub_dbs=None)

    return blif_str
