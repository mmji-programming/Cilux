from .types import (
    NetlistDB,
    Instance,
    Signal,
    Endpoint,
)

import collections
from typing import Dict, List


class CompilerPasses:
    @staticmethod
    def remove_phantom_signals(db: NetlistDB):
        top_input_sigs = {id(sig) for sig in db.top_inputs.values()}
        top_output_sigs = {id(sig) for sig in db.top_outputs.values()}

        inst_output_sigs = set()
        for inst in db.instances.values():
            inst_output_sigs.update(id(sig) for sig in inst.outputs.values())

        inst_input_sigs = set()
        for inst in db.instances.values():
            inst_input_sigs.update(id(sig) for sig in inst.inputs.values() if sig)

        phantoms = [
            sig_id
            for sig_id, sig in db.signals.items()
            if (
                id(sig) not in top_input_sigs
                and id(sig) not in top_output_sigs
                and id(sig) not in inst_output_sigs
                and id(sig) not in inst_input_sigs
                and sig.driver is None
                and not sig.is_constant
                and sig.readers
            )
        ]

        for sig_id in phantoms:
            sig = db.signals.pop(sig_id, None)
            if sig:
                for inst in db.instances.values():
                    for pname, psig in list(inst.inputs.items()):
                        if psig is sig:
                            del inst.inputs[pname]

    @staticmethod
    def validate_graph(db: NetlistDB):
        import warnings

        for sig_id, sig in db.signals.items():
            if sig.is_constant:
                continue
            if sig.readers and not sig.driver and sig not in db.top_inputs.values():
                warnings.warn(
                    f"Signal '{sig_id}' has readers but no driver (floating input).",
                    stacklevel=2,
                )

        for port, sig in db.top_outputs.items():
            if not sig or not sig.driver:
                warnings.warn(
                    f"Top-level output '{port}' is undriven.",
                    stacklevel=2,
                )

        # SEQ_BOUNDARIES = {"DFF_P", "DFF_N", "LATCH", "DFF_AR", "DFF_SET"}

        visited: set = set()
        rec_stack: set = set()

        def is_seq(inst_id: str) -> bool:
            if inst_id == "TOP":
                return False
            inst = db.instances.get(inst_id)
            return inst.is_sequential if inst else False

        def _has_comb_cycle(inst_id: str) -> bool:
            visited.add(inst_id)
            rec_stack.add(inst_id)
            inst = db.instances.get(inst_id)
            if inst:
                for port, sig in inst.inputs.items():
                    if is_seq(inst_id) and inst.port_roles.get(port) == "clock":
                        continue
                    if sig and sig.driver and sig.driver.inst_id in db.instances:
                        nxt = sig.driver.inst_id
                        if is_seq(nxt):
                            continue
                        if nxt not in visited:
                            if _has_comb_cycle(nxt):
                                return True
                        elif nxt in rec_stack:
                            return True
            rec_stack.discard(inst_id)
            return False

        for iid in list(db.instances.keys()):
            if iid not in visited:
                if _has_comb_cycle(iid):
                    warnings.warn(
                        f"Combinational loop detected in the netlist (starting at '{iid}').",
                        stacklevel=2,
                    )
                    break

    @staticmethod
    def detect_combinational_loops(db: NetlistDB):

        def is_seq(inst_id: str) -> bool:
            if inst_id == "TOP":
                return False
            inst = db.instances.get(inst_id)
            return inst.is_sequential if inst else False

        adjacency: Dict[str, List[tuple]] = collections.defaultdict(list)
        all_comb_insts: set = set()

        for sig_id, sig in db.signals.items():
            if not sig.driver or sig.driver.inst_id == "TOP":
                continue
            driver_id = sig.driver.inst_id
            if driver_id not in db.instances:
                continue
            if is_seq(driver_id):
                continue
            all_comb_insts.add(driver_id)

            for reader in sig.readers:
                reader_id = reader.inst_id
                if reader_id == "TOP" or reader_id not in db.instances:
                    continue
                if is_seq(reader_id):
                    continue
                all_comb_insts.add(reader_id)
                adjacency[driver_id].append((reader_id, sig_id))

        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {n: WHITE for n in all_comb_insts}

        def _find_cycle_from(start: str):
            # (node, iterator-index-into-adjacency, path-so-far)
            path: List[str] = [start]
            edge_path: List[str] = []
            iter_stack = [iter(adjacency.get(start, []))]
            color[start] = GRAY

            while iter_stack:
                try:
                    nxt, sig_id = next(iter_stack[-1])
                except StopIteration:
                    finished = path.pop()
                    if edge_path:
                        edge_path.pop()
                    color[finished] = BLACK
                    iter_stack.pop()
                    continue

                if color.get(nxt, WHITE) == WHITE:
                    color[nxt] = GRAY
                    path.append(nxt)
                    edge_path.append(sig_id)
                    iter_stack.append(iter(adjacency.get(nxt, [])))
                elif color.get(nxt) == GRAY:
                    cycle_start_idx = path.index(nxt)
                    cycle_nodes = path[cycle_start_idx:] + [nxt]
                    cycle_sigs = edge_path[cycle_start_idx:] + [sig_id]
                    return cycle_nodes, cycle_sigs
            return None, None

        for node in list(all_comb_insts):
            if color.get(node, WHITE) != WHITE:
                continue
            cycle_nodes, cycle_sigs = _find_cycle_from(node)
            if cycle_nodes:
                steps = []
                for i in range(len(cycle_nodes) - 1):
                    steps.append(f"{cycle_nodes[i]} --[{cycle_sigs[i]}]--> {cycle_nodes[i + 1]}")
                path_desc = "\n  ".join(steps)
                raise RuntimeError(
                    f"\n[Synthesis Error]: Combinational loop detected!\n"
                    f"Loop path:\n  {path_desc}\n"
                    f"Combinational loops cause instability. Please insert a "
                    f"sequential element (DFF/LATCH) to break the cycle."
                )
        return


class NetlistOptimizer:
    @classmethod
    def optimize(cls, db: NetlistDB) -> NetlistDB:
        changed = True
        iteration = 0
        MAX_ITERATIONS = 50

        while changed and iteration < MAX_ITERATIONS:
            changed = False
            changed |= cls._propagate_constants_and_fold(db)
            changed |= cls._reduce_muxes(db)
            changed |= cls._eliminate_dead_code(db)

            cls._scrub_phantom_references(db)
            iteration += 1

        return db

    @classmethod
    def _propagate_constants_and_fold(cls, db: NetlistDB) -> bool:
        changed = False
        to_remove = []

        for inst_id, inst in list(db.instances.items()):
            mod = inst.module_name.upper()
            if mod not in ("AND", "OR", "NOT", "XOR", "NAND", "NOR", "XNOR"):
                continue

            out_sig = inst.outputs.get("o0", inst.outputs.get("q"))
            if not out_sig:
                continue

            # Collect ALL inputs (i0, i1, i2, ...) sorted by port name.
            # The Elaborator assigns port names i0..iN-1 for N-input gates.
            in_items = sorted(inst.inputs.items())  # [("i0", sig), ("i1", sig), ...]
            in_sigs = [s for _, s in in_items if s is not None]
            n_in = len(in_sigs)

            # Classify each input as constant-0, constant-1, or variable
            const_vals = [(s.const_value if s.is_constant else None) for s in in_sigs]
            has_const_0 = 0 in const_vals
            has_const_1 = 1 in const_vals
            non_const = [s for s, v in zip(in_sigs, const_vals) if v is None]

            new_driver_sig = None

            # AND
            if mod == "AND":
                if has_const_0:
                    new_driver_sig = db.get_const(0)  # AND(..., 0, ...) = 0
                elif len(non_const) == 0:
                    new_driver_sig = db.get_const(1)  # AND(1, 1, ...) = 1
                elif len(non_const) == 1:
                    new_driver_sig = non_const[0]  # AND(1, ..., x, ..., 1) = x
                elif len(set(id(s) for s in non_const)) == 1:
                    new_driver_sig = non_const[0]  # AND(x, x, ...) = x

            # OR
            elif mod == "OR":
                if has_const_1:
                    new_driver_sig = db.get_const(1)  # OR(..., 1, ...) = 1
                elif len(non_const) == 0:
                    new_driver_sig = db.get_const(0)  # OR(0, 0, ...) = 0
                elif len(non_const) == 1:
                    new_driver_sig = non_const[0]  # OR(0, ..., x, ..., 0) = x
                elif len(set(id(s) for s in non_const)) == 1:
                    new_driver_sig = non_const[0]  # OR(x, x, ...) = x

            # NOT
            elif mod == "NOT":
                i0_sig = in_sigs[0] if in_sigs else None
                i0_val = const_vals[0] if const_vals else None
                if i0_val == 0:
                    new_driver_sig = db.get_const(1)
                elif i0_val == 1:
                    new_driver_sig = db.get_const(0)
                elif i0_sig and i0_sig.driver and i0_sig.driver.inst_id in db.instances:
                    prev_inst = db.instances[i0_sig.driver.inst_id]
                    if prev_inst.module_name.upper() == "NOT":
                        new_driver_sig = prev_inst.inputs.get("i0")  # NOT(NOT(x)) = x

            # XOR
            elif mod == "XOR":
                # Remove all constant-0 inputs XOR(x, 0) = x
                n_ones = sum(1 for v in const_vals if v == 1)
                if len(non_const) == 0:
                    new_driver_sig = db.get_const(n_ones % 2)
                elif len(non_const) == 1 and n_ones == 0:
                    new_driver_sig = non_const[0]  # XOR(0, ..., x, ..., 0) = x
                elif len(non_const) == 1 and n_ones % 2 == 1:
                    new_driver_sig = cls._make_not_gate(db, non_const[0])  # XOR(1, x) = NOT(x)
                # XOR(x, x) = 0
                elif n_in == 2 and n_in == len(non_const):
                    i0_sig = in_sigs[0]
                    i1_sig = in_sigs[1]
                    if i0_sig is i1_sig:
                        new_driver_sig = db.get_const(0)

            # NAND
            elif mod == "NAND":
                if has_const_0:
                    new_driver_sig = db.get_const(1)  # NAND(..., 0, ...) = 1
                elif len(non_const) == 0:
                    new_driver_sig = db.get_const(0)  # NAND(1, 1, ...) = 0
                elif len(non_const) == 1:
                    new_driver_sig = cls._make_not_gate(db, non_const[0])  # NAND(1,...,x,...,1)=NOT(x)

            # NOR
            elif mod == "NOR":
                if has_const_1:
                    new_driver_sig = db.get_const(0)  # NOR(..., 1, ...) = 0
                elif len(non_const) == 0:
                    new_driver_sig = db.get_const(1)  # NOR(0, 0, ...) = 1
                elif len(non_const) == 1:
                    new_driver_sig = cls._make_not_gate(db, non_const[0])  # NOR(0,...,x,...,0) = NOT(x)

            # XNOR
            elif mod == "XNOR":
                n_ones = sum(1 for v in const_vals if v == 1)
                if len(non_const) == 0:
                    new_driver_sig = db.get_const((n_ones + 1) % 2)  # invert XOR result
                elif len(non_const) == 1 and n_ones % 2 == 0:
                    new_driver_sig = cls._make_not_gate(db, non_const[0])  # XNOR(0,x) = NOT(x)
                elif len(non_const) == 1 and n_ones % 2 == 1:
                    new_driver_sig = non_const[0]  # XNOR(1,x)=x

            if new_driver_sig:
                cls._replace_instance_with_sig(db, inst, out_sig, new_driver_sig)
                to_remove.append(inst_id)
                changed = True

        for inst_id in to_remove:
            if inst_id in db.instances:
                del db.instances[inst_id]

        return changed

    @classmethod
    def _reduce_muxes(cls, db: NetlistDB) -> bool:
        changed = False
        to_remove = []

        for inst_id, inst in list(db.instances.items()):
            mod = inst.module_name.upper()
            if mod in ("MUX2", "MUX"):
                i0_sig = inst.inputs.get("i0")
                i1_sig = inst.inputs.get("i1")
                sel_sig = inst.inputs.get("sel")
                out_sig = inst.outputs.get("o0", inst.outputs.get("out"))

                if not out_sig:
                    continue

                sel_val = sel_sig.const_value if sel_sig and sel_sig.is_constant else None
                new_driver_sig = None

                if sel_val == 0:
                    new_driver_sig = i0_sig
                elif sel_val == 1:
                    new_driver_sig = i1_sig
                elif i0_sig is i1_sig and i0_sig is not None:
                    new_driver_sig = i0_sig

                if new_driver_sig:
                    cls._replace_instance_with_sig(db, inst, out_sig, new_driver_sig)
                    to_remove.append(inst_id)
                    changed = True

        for inst_id in to_remove:
            if inst_id in db.instances:
                del db.instances[inst_id]

        return changed

    @classmethod
    def _replace_instance_with_sig(cls, db: NetlistDB, inst: Instance, old_out_sig: Signal, new_sig: Signal):
        for p, s in inst.inputs.items():
            if s:
                s.readers = [r for r in s.readers if r.inst_id != inst.id]

        for reader in old_out_sig.readers:
            if reader.inst_id != inst.id and reader not in new_sig.readers:
                new_sig.readers.append(reader)

            if reader.inst_id in db.instances:
                other_inst = db.instances[reader.inst_id]
                for p, s in other_inst.inputs.items():
                    if s is old_out_sig:
                        other_inst.inputs[p] = new_sig

        top_out_keys = [k for k, v in db.top_outputs.items() if v is old_out_sig]
        for k in top_out_keys:
            db.top_outputs[k] = new_sig

        for sig in db.signals.values():
            if sig.driver and sig.driver.inst_id == inst.id:
                sig.driver = new_sig.driver

        old_out_sig.readers = []
        old_out_sig.driver = None

    @classmethod
    def _eliminate_dead_code(cls, db: NetlistDB) -> bool:
        active_instances = set()

        def trace_back(endpoint: Endpoint):
            if not endpoint or endpoint.inst_id == "TOP" or endpoint.inst_id.startswith("CONST"):
                return
            if endpoint.inst_id in active_instances:
                return
            active_instances.add(endpoint.inst_id)
            inst = db.instances.get(endpoint.inst_id)
            if inst:
                for sig in inst.inputs.values():
                    if sig and sig.driver:
                        trace_back(sig.driver)

        for out_sig in db.top_outputs.values():
            if out_sig and out_sig.driver:
                trace_back(out_sig.driver)

        changed = False
        dead_insts = [iid for iid in db.instances.keys() if iid not in active_instances]

        if dead_insts:
            changed = True
            for iid in dead_insts:
                del db.instances[iid]

        inactive_outputs = []
        for p, sig in db.top_outputs.items():
            if not sig:
                inactive_outputs.append(p)
            elif sig.is_constant:
                # A constant-driven output is always valid
                pass
            elif not sig.driver:
                inactive_outputs.append(p)
            elif (
                sig.driver.inst_id not in active_instances
                and sig.driver.inst_id != "TOP"
                and not sig.driver.inst_id.startswith("CONST")
            ):
                inactive_outputs.append(p)

        if inactive_outputs:
            changed = True
            for p in inactive_outputs:
                del db.top_outputs[p]

        inactive_inputs = []
        for p, sig in db.top_inputs.items():
            if not sig:
                inactive_inputs.append(p)
                continue
            has_active_reader = False
            for r in sig.readers:
                if r.inst_id in active_instances or r.inst_id == "TOP":
                    has_active_reader = True
                    break
            if not has_active_reader:
                inactive_inputs.append(p)

        if inactive_inputs:
            changed = True
            for p in inactive_inputs:
                del db.top_inputs[p]

        return changed

    @classmethod
    def _scrub_phantom_references(cls, db: NetlistDB):
        active_ids = set(db.instances.keys())
        active_ids.add("TOP")

        for sig in db.signals.values():
            sig.readers = [r for r in sig.readers if r.inst_id in active_ids or r.inst_id.startswith("CONST")]
            if sig.driver and sig.driver.inst_id not in active_ids and not sig.driver.inst_id.startswith("CONST"):
                sig.driver = None

        inactive_sigs = []
        for sig_id, sig in db.signals.items():
            if sig.is_constant:
                continue
            if not sig.readers and not sig.driver:
                if sig not in db.top_outputs.values() and sig not in db.top_inputs.values():
                    inactive_sigs.append(sig_id)
        for sig_id in inactive_sigs:
            del db.signals[sig_id]
