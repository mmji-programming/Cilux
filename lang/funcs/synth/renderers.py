from .types import (
    NetlistDB,
)
from lang.engines.ascii_draw_engine import (
    draw_circuit,
)


import re
import sys
import json

_GRAPH_AVAILABLE = False
try:
    import networkx as nx

    _GRAPH_AVAILABLE = True
except ImportError:
    pass


def render_json(db: NetlistDB, module_name: str = "circuit") -> str:
    # just a simple internal fallback

    netlist = {
        "module": module_name,
        "inputs": [],
        "outputs": [],
        "constants": [],
        "instances": {},
        "connections": [],
    }

    for name, sig in db.top_inputs.items():
        netlist["inputs"].append(name)
    for name, sig in db.top_outputs.items():
        netlist["outputs"].append(name)
    for sig_id, sig in db.signals.items():
        if sig.is_constant and sig.readers:
            netlist["constants"].append({"name": sig_id, "value": sig.const_value})

    top_output_sig_ids = {sig.id for sig in db.top_outputs.values() if sig is not None}

    for inst_id, inst in db.instances.items():
        inst_data = {"type": inst.module_name, "inputs": {}, "outputs": {}}
        for port_name, sig in inst.inputs.items():
            if sig:
                inst_data["inputs"][port_name] = (
                    sig.id if sig.is_constant else (sig.driver.to_string() if sig.driver else "unconnected")
                )
        for port_name, sig in inst.outputs.items():
            if sig:
                is_connected = bool(sig.readers) or sig.id in top_output_sig_ids
                inst_data["outputs"][port_name] = sig.id if is_connected else "unconnected"
        netlist["instances"][inst_id] = inst_data

    connections = set()
    for sig_id, sig in db.signals.items():
        if sig.driver and sig.readers:
            src = sig.driver.to_string()
            for reader in sig.readers:
                dst = reader.to_string()
                if src != dst:
                    connections.add((src, dst))
    netlist["connections"] = [{"from": src, "to": dst} for src, dst in sorted(connections)]
    return json.dumps(netlist, indent=2)


def _display_name(name: str) -> str:
    if not name:
        return ""

    if name == "CONST_0":
        return "0"
    if name == "CONST_1":
        return "1"

    clean_name = name
    clean_name = re.sub(r"^__fold_not_(.+)$", r"~\1", clean_name)
    clean_name = re.sub(r"_n_internal$", r"'", clean_name)
    clean_name = re.sub(r"_float$", r" (float)", clean_name)
    clean_name = re.sub(r"^sig_([0-9]+)$", r"w\1", clean_name)
    clean_name = re.sub(r"^reg_(.+)$", r"\1_reg", clean_name)
    clean_name = re.sub(r"^sig_(.+)_(.+)$", r"\1.\2", clean_name)
    clean_name = re.sub(r"(.+)_([io][0-9]+|out|in|sel|D|Q|CLK|SET|AR)$", r"\1.\2", clean_name)

    parts = clean_name.split("_")
    if len(parts) > 4:
        clean_name = parts[0] + "_..._" + "_".join(parts[-3:])

    return clean_name


class ASCIIBackend:
    @staticmethod
    def render(db: NetlistDB, module_name: str) -> str:
        if not _GRAPH_AVAILABLE:
            print(
                "[draw] asciinet/networkx not installed — falling back to JSON netlist.\n"
                "       Install with: pip install asciinet networkx",
                file=sys.stderr,
            )
            return render_json(db, module_name=module_name)
        try:
            G = nx.DiGraph()

            for p_name in db.top_inputs.keys():
                G.add_node(p_name, type="input", label=_display_name(p_name))

            for p_name in db.top_outputs.keys():
                G.add_node(p_name, type="output", label=_display_name(p_name))

            active_inst_ids = set(db.instances.keys())

            if "CONST_0" in db.signals and db.signals["CONST_0"].readers:
                if any(r.inst_id in active_inst_ids or r.inst_id == "TOP" for r in db.signals["CONST_0"].readers):
                    G.add_node("CONST_0", type="constant", label=_display_name("CONST_0"))

            if "CONST_1" in db.signals and db.signals["CONST_1"].readers:
                if any(r.inst_id in active_inst_ids or r.inst_id == "TOP" for r in db.signals["CONST_1"].readers):
                    G.add_node("CONST_1", type="constant", label=_display_name("CONST_1"))

            for inst_id, inst in db.instances.items():
                clean_inst_name = _display_name(inst_id)
                G.add_node(
                    inst_id,
                    type="instance",
                    label=f"{clean_inst_name}\n({inst.module_name})",
                )

            for sig in db.signals.values():
                if not sig.readers:
                    continue
                if sig.driver:
                    src_node = sig.driver.inst_id
                    if src_node == "TOP":
                        src_node = sig.driver.port_name
                else:
                    src_node = sig.id if sig.is_constant else None

                if not src_node:
                    continue
                for reader in sig.readers:
                    dst_node = reader.inst_id
                    if dst_node == "TOP":
                        dst_node = reader.port_name

                    label = reader.port_name if reader.inst_id != "TOP" else ""
                    G.add_edge(src_node, dst_node, label=label)

            ascii_graph = draw_circuit(G) if G.nodes() else "  (Circuit layout is empty)"

            return ascii_graph
        except Exception as _render_err:
            print(
                f"[draw] ASCII rendering failed ({type(_render_err).__name__}: {_render_err}) — falling back to JSON netlist.",
                file=sys.stderr,
            )
            return render_json(db, module_name=module_name)
