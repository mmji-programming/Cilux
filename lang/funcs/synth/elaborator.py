from .symbol_table import (
    SymbolTable,
)
from .types import (
    DummyPort,
    Endpoint,
    Signal,
    Instance,
    NetlistDB,
)
from .passes import (
    CompilerPasses,
    NetlistOptimizer,
)

from typing import Dict, Optional
import collections


class Elaborator:
    BASE_PRIMITIVES = {
        "AND",
        "OR",
        "XOR",
        "NOT",
        "NAND",
        "NOR",
        "XNOR",
        "DFF_P",
        "DFF_N",
        "MUX2",
    }

    COMMUTATIVE_GATES = frozenset(
        {
            "AND",
            "OR",
            "XOR",
            "NAND",
            "NOR",
            "XNOR",
            "and",
            "or",
            "xor",
            "nand",
            "nor",
            "xnor",
        }
    )

    # To add a new expandable primitive later:
    #   1. Add "NEW_PRIM": "_blueprint_new_prim" here.
    #   2. Write _blueprint_new_prim(self, inst_id, **kwargs) with the right sig.
    #   3. Add a _make_new_prim() helper that calls _call_blueprint when res_left>0.
    PRIMITIVE_BLUEPRINTS: Dict[str, str] = {
        "MUX2": "_blueprint_mux2",
        "DFF_P": "_blueprint_dff_p",
        "DFF_N": "_blueprint_dff_n",
    }

    def __init__(self, kernel, target_circuit, res_level):
        self.kernel = kernel
        self.target = target_circuit
        self.res_level = res_level
        self.db = NetlistDB()
        self.inst_counters = collections.defaultdict(int)
        self.instance_map = {}
        self.call_depth = 0
        self.MAX_DEPTH = 100

        self.active_clk_sig = None
        self.active_edge = None
        self.async_reset_sig = None

        self._alias_map: Dict[int, Signal] = {}
        self._inline_call_cache: Dict[tuple, str] = {}
        self._inline_outputs: Dict[str, Dict[str, Signal]] = {}
        self._unified_inst_cache: Dict[tuple, str] = {}
        self._negedge_inv_cache: Dict[str, Signal] = {}

        self.sub_dbs: Dict[str, "NetlistDB"] = {}
        self._sub_db_in_progress: set = set()
        self._module_exports_stack: list = []

    def generate_inst_id(self, module_name: str, var_name: str = None) -> str:
        base = var_name if var_name else module_name.lower()
        idx = self.inst_counters[base]
        self.inst_counters[base] += 1
        return f"{base}{idx}"

    def _ensure_sub_db(self, module_name: str, obj) -> None:
        if module_name in self.sub_dbs:
            return
        if not hasattr(obj, "body"):
            return
        if module_name in self._sub_db_in_progress:
            raise ValueError(
                f"Cilux synthesis error: circular module instantiation "
                f"detected involving '{module_name}'. A circuit/gate cannot "
                f"(directly or indirectly) instantiate itself as a blackbox "
                f"submodule."
            )

        self._sub_db_in_progress.add(module_name)
        try:
            sub_elab = Elaborator(self.kernel, obj, self.res_level)
            sub_elab.sub_dbs = self.sub_dbs
            sub_elab._sub_db_in_progress = self._sub_db_in_progress
            sub_db = sub_elab.elaborate()
            sub_db = NetlistOptimizer.optimize(sub_db)
            CompilerPasses.remove_phantom_signals(sub_db)
            self.sub_dbs[module_name] = sub_db
        finally:
            self._sub_db_in_progress.discard(module_name)

    def elaborate(self):
        top_scope = SymbolTable()

        # Initialize Top-level inputs and outputs
        if hasattr(self.target, "input"):
            for p in self.target.input:
                sig = self.db.create_signal(exact_id=p.name)
                sig.driver = Endpoint("TOP", p.name, "IN")
                self.db.top_inputs[p.name] = sig
                top_scope.bind_signal(p.name, sig)

        if hasattr(self.target, "output"):
            for p in self.target.output:
                sig = self.db.create_signal(exact_id=p.name)
                self.db.top_outputs[p.name] = sig
                top_scope.bind_signal(p.name, sig)

        if hasattr(self.target, "instances") and self.target.instances:
            for inst_name, circ_name in self.target.instances:
                top_scope.bind_def(inst_name, circ_name)

        body_nodes = self.target.body if isinstance(self.target.body, list) else [self.target.body]

        # PASS 1: Pre-collect and register all signals in the design
        # This prevents dangling signals during wiring (Pass 2)
        for node in body_nodes:
            self._collect_all_signals(node, top_scope)

        self._pre_declare_feedback_signals(body_nodes, top_scope, prefix="")

        for node in body_nodes:
            self._register_nested_circuit_defs(node, top_scope)

        # PASS 2: Main synthesis / Wiring
        body_nodes = self._preprocess_wires(body_nodes)
        for node in body_nodes:
            self._walk(node, top_scope, prefix="", res_left=self.res_level)

        # Finalize output wiring
        for p_name, sig in self.db.top_outputs.items():
            r = Endpoint("TOP", p_name, "OUT")
            if r not in sig.readers:
                sig.readers.append(r)

        return self.db

    def _collect_all_signals(self, node, scope: SymbolTable):
        if not hasattr(node, "data"):
            return

        # Register wire targets
        if node.data == "wire_stmt":
            dst_name = self._get_pin_name(node.children[1])
            if dst_name and not scope.resolve_signal(dst_name):
                sig = self.db.create_signal(exact_id=dst_name)
                scope.bind_signal(dst_name, sig)

        # Register sequential targets
        elif node.data == "seq_assign":
            target_name = self._get_pin_name(node.children[0])
            if target_name and not scope.resolve_signal(target_name):
                sig = self.db.create_signal(exact_id=f"reg_{target_name}")
                scope.bind_signal(target_name, sig)

        # Register variable declarations
        elif node.data == "var_stmt":
            var_name = self._get_pin_name(node.children[0])
            if var_name and not scope.resolve_signal(var_name):
                sig = self.db.create_signal(exact_id=var_name)
                scope.bind_signal(var_name, sig)

        if hasattr(node, "children"):
            for child in node.children:
                self._collect_all_signals(child, scope)

    def _get_pin_name(self, node) -> Optional[str]:
        # Helper to extract identifier names from AST nodes
        if hasattr(node, "type") and node.type == "NAME":
            return node.value
        if hasattr(node, "children") and node.children:
            return self._get_pin_name(node.children[0])
        return None

    def _deep_resolve_output(self, fresh: "Signal", pre_sig: "Signal", sub_scope: "SymbolTable") -> "Signal":
        if fresh is None:
            return pre_sig

        visited = set()
        cur = fresh
        while id(cur) in self._alias_map and id(cur) not in visited:
            visited.add(id(cur))
            cur = self._alias_map[id(cur)]
        fresh = cur

        if fresh.driver is not None:
            return fresh

        if fresh is pre_sig:
            for sig in self.db.signals.values():
                if sig is fresh:
                    continue
                if sig.driver and any(r.inst_id == "TOP" for r in sig.readers):
                    continue
                for ep in sig.readers:
                    if ep.inst_id and ep.inst_id in self.db.instances:
                        inst_out = self.db.instances[ep.inst_id].outputs
                        if fresh in inst_out.values():
                            return sig

        if fresh.readers:
            return fresh

        return pre_sig if pre_sig is not None else fresh

    def _pre_declare_feedback_signals(self, body_nodes, scope, prefix):
        for node in body_nodes:
            self._scan_when_targets(node, scope, prefix)

    def _scan_when_targets(self, node, scope, prefix):
        if not hasattr(node, "data"):
            return
        if node.data == "when_stmt":
            for child in node.children:
                if hasattr(child, "data") and child.data in (
                    "when_body",
                    "circuit_body",
                ):
                    self._scan_seq_assigns(child, scope, prefix)
            return
        if hasattr(node, "children"):
            for ch in node.children:
                self._scan_when_targets(ch, scope, prefix)

    def _scan_seq_assigns(self, node, scope, prefix):
        if not hasattr(node, "data"):
            return
        if node.data == "seq_assign":
            if node.children:
                target_tok = node.children[0]
                target = target_tok.value if hasattr(target_tok, "value") else None
                if target and not scope.resolve_signal(target):
                    placeholder = self.db.create_signal(exact_id=f"{prefix}{target}")
                    scope.bind_signal(target, placeholder)
            return
        if hasattr(node, "children"):
            for ch in node.children:
                self._scan_seq_assigns(ch, scope, prefix)

    def _make_inst_cache_key(self, func_name: str, args: list, kwargs: dict) -> tuple:
        def _k(s):
            return s.id if s is not None else "__none__"

        arg_keys = [_k(a) for a in args]

        if func_name.upper() in self.COMMUTATIVE_GATES:
            arg_keys = sorted(arg_keys)

        return (func_name,) + tuple(arg_keys) + tuple((k, _k(v)) for k, v in sorted(kwargs.items()))

    def _register_nested_circuit_defs(self, node, scope: SymbolTable):
        if not hasattr(node, "data"):
            return
        if node.data in ("circuit_def", "gate_def"):
            name_tok = next(
                (c for c in node.children if hasattr(c, "type") and c.type == "NAME"),
                None,
            )
            if name_tok:
                cname = name_tok.value
                try:
                    cobj = self.kernel.context.lookup(cname)
                    scope.bind_def(cname, cobj)
                except Exception:
                    try:
                        self.kernel.execute(node)
                        cobj = self.kernel.context.lookup(cname)
                        scope.bind_def(cname, cobj)
                    except Exception:
                        pass
            return
        if hasattr(node, "children"):
            for ch in node.children:
                self._register_nested_circuit_defs(ch, scope)

    def _preprocess_wires(self, body_nodes):
        last_writer = {}
        for node in body_nodes:
            inner = node
            if hasattr(node, "data") and node.data == "statement":
                inner = node.children[0]
            if hasattr(inner, "data") and inner.data == "wire_stmt":
                src_ast = inner.children[0]
                dst_ast = inner.children[1]
                dst_name = self._get_pin_name(dst_ast)
                if dst_name:
                    last_writer[dst_name] = (src_ast, inner)

        filtered = []
        for node in body_nodes:
            inner = node
            if hasattr(node, "data") and node.data == "statement":
                inner = node.children[0]
            if hasattr(inner, "data") and inner.data == "wire_stmt":
                dst_ast = inner.children[1]
                dst_name = self._get_pin_name(dst_ast)
                if dst_name in last_writer and last_writer[dst_name][1] is inner:
                    filtered.append(node)
            else:
                filtered.append(node)
        return filtered

    def _process_conditional_logic(
        self, node, scope, prefix, res_left, fallback_state=None, is_sequential=True
    ) -> Dict[str, Signal]:
        if fallback_state is None:
            fallback_state = {}

        if not hasattr(node, "data"):
            return {}

        if node.data in (
            "if_block",
            "when_body",
            "circuit_body",
            "statement",
            "circuit_stmt",
            "gate_stmt",
            "expr_stmt",
        ):
            combined = {}
            running_fallback = dict(fallback_state)
            for child in node.children:
                res = self._process_conditional_logic(child, scope, prefix, res_left, running_fallback, is_sequential)
                for k, v in res.items():
                    combined[k] = v
                    running_fallback[k] = v
            return combined

        if node.data == "seq_assign":
            target_name = self._get_pin_name(node.children[0])
            rhs_ast = node.children[1]
            d_sig = self._eval_expr(rhs_ast, scope, prefix, res_left)
            return {target_name: d_sig}

        if node.data == "wire_stmt":
            src_sig = self._eval_expr(node.children[0], scope, prefix, res_left)
            dst_name = self._get_pin_name(node.children[1])
            return {dst_name: src_sig} if dst_name and src_sig else {}

        if node.data == "condition_stmt":
            return self._process_condition_stmt(node, scope, prefix, res_left, fallback_state, is_sequential)

        return {}

    def _process_condition_stmt(
        self,
        node,
        scope: SymbolTable,
        prefix: str,
        res_left: int,
        fallback_state=None,
        is_sequential=True,
    ) -> Dict[str, Signal]:
        if fallback_state is None:
            fallback_state = {}

        branches = []
        else_block = None
        i = 0

        while i < len(node.children):
            c = node.children[i]
            is_if = (hasattr(c, "type") and c.type == "IF") or (hasattr(c, "value") and c.value == "if")
            is_elif = (hasattr(c, "type") and c.type == "ELIF") or (hasattr(c, "value") and c.value == "elif")
            is_else = (hasattr(c, "type") and c.type == "ELSE") or (hasattr(c, "value") and c.value == "else")

            if is_if or is_elif:
                branches.append((node.children[i + 1], node.children[i + 2]))
                i += 3
            elif is_else:
                else_block = node.children[i + 1]
                i += 2
            else:
                if hasattr(c, "data") and c.data == "condition":
                    branches.append((c, node.children[i + 1]))
                    i += 2
                elif hasattr(c, "data") and c.data == "if_block":
                    else_block = c
                    i += 1
                else:
                    i += 1

        branch_assignments = []
        for cond_node, block_node in branches:
            if hasattr(cond_node, "data") and cond_node.data == "condition":
                cond_sig = self._eval_expr(cond_node.children[0], scope, prefix, res_left)
            else:
                cond_sig = self._eval_expr(cond_node, scope, prefix, res_left)

            # Pass the fallback_state down
            block_dct = self._process_conditional_logic(
                block_node, scope, prefix, res_left, fallback_state, is_sequential
            )
            branch_assignments.append((cond_sig, block_dct))

        else_dct = {}
        if else_block:
            else_dct = self._process_conditional_logic(
                else_block, scope, prefix, res_left, fallback_state, is_sequential
            )

        all_targets = set(else_dct.keys())
        for _, dct in branch_assignments:
            all_targets.update(dct.keys())

        combined_assignments = {}
        for target in all_targets:
            current_sig = else_dct.get(target)
            if not current_sig:
                # 1. Resolve from fallback first (for MUX chaining)
                if target in fallback_state:
                    current_sig = fallback_state[target]
                # 2. fall back to scope (Q-output placeholder)
                else:
                    current_sig = scope.resolve_signal(target)

                if current_sig is None or current_sig.driver is None:
                    if not is_sequential:
                        raise ValueError(
                            f"Cilux synthesis error: combinational signal "
                            f"'{target}' is assigned inside an 'if' with no "
                            f"matching 'else' (or its else-branch leaves "
                            f"'{target}' unassigned), and '{target}' has no "
                            f"prior driven value to fall back on. "
                            f"Combinational logic has no memory: an "
                            f"incompletely-specified conditional assignment "
                            f"like this would either synthesize as a "
                            f"silently-grounded (tied-to-0) wire or, if "
                            f"'{target}' is a top-level output, as an "
                            f"invalid combinational self-feedback loop. "
                            f"Fix this by adding an 'else' branch that "
                            f"assigns '{target}' in every code path, or by "
                            f"moving this assignment into a 'when' block "
                            f"(clocked register) if you intended '{target}' "
                            f"to hold its previous value when the condition "
                            f"is false."
                        )

                    if current_sig is None:
                        current_sig = self.db.create_signal(exact_id=f"reg_{target}")
                        scope.bind_signal(target, current_sig)

            for cond_sig, dct in reversed(branch_assignments):
                if target in dct:
                    true_sig = dct[target]
                    false_sig = current_sig

                    mux_id = prefix + self.generate_inst_id("MUX2", f"mux_{target}")
                    current_sig = self._make_mux2(
                        mux_id,
                        i0=false_sig,
                        i1=true_sig,
                        sel=cond_sig,
                        res_left=res_left,
                    )

            combined_assignments[target] = current_sig

        return combined_assignments

    def _walk(self, node, scope: SymbolTable, prefix: str, res_left: int):
        if not hasattr(node, "data"):
            return

        if node.data in (
            "circuit_body",
            "statement",
            "circuit_stmt",
            "gate_stmt",
            "expr_stmt",
            "instance_stmt",
        ):
            for child in node.children:
                self._walk(child, scope, prefix, res_left)
            return

        if node.data in ("circuit_def", "gate_def"):
            circ_name_tok = next(
                (c for c in node.children if hasattr(c, "type") and c.type == "NAME"),
                None,
            )
            if circ_name_tok:
                circ_name = circ_name_tok.value
                try:
                    existing = self.kernel.context.lookup(circ_name)
                except Exception:
                    existing = None

                if existing is None:
                    try:
                        self.kernel.execute(node)
                    except Exception:
                        pass

                try:
                    circ_obj = self.kernel.context.lookup(circ_name)
                    scope.bind_def(circ_name, circ_obj)
                except Exception:
                    pass
            return

        if node.data == "clock_stmt":
            name_node = next(
                (c for c in node.children if hasattr(c, "type") and c.type == "NAME"),
                None,
            )
            if name_node:
                clk_name = name_node.value
                sig = scope.resolve_signal(clk_name)
                if not sig:
                    sig = self.db.create_signal(exact_id=clk_name)
                    sig.driver = Endpoint("TOP", clk_name, "IN")
                    self.db.top_inputs[clk_name] = sig
                    scope.bind_signal(clk_name, sig)

                is_async = False
                try:
                    clk_obj_val = self.kernel.context.lookup(clk_name)
                    clk_obj = clk_obj_val.data if hasattr(clk_obj_val, "data") else clk_obj_val
                    if hasattr(clk_obj, "sync"):
                        is_async = not bool(clk_obj.sync)
                except Exception:
                    pass
                if is_async:
                    import warnings

                    warnings.warn(
                        f"Cilux: clock '{clk_name}' is declared 'async', but "
                        f"the Cilux grammar has no syntax for attaching a "
                        f"distinct async-reset signal to a clock -- 'async' "
                        f"is currently a declaration-only annotation with no "
                        f"effect on the synthesized hardware. Registers "
                        f"clocked by '{clk_name}' will be ordinary "
                        f"edge-triggered flip-flops with NO asynchronous "
                        f"reset. To get an actual async reset, condition "
                        f"the register assignment on a real reset input "
                        f"signal inside the 'when' block instead (e.g. "
                        f"'if (RST) {{ Q <= 0; }} else {{ ... }}'), which "
                        f"this compiler synthesizes as a synchronous "
                        f"reset mux feeding D.",
                        stacklevel=2,
                    )
            return

        if node.data == "when_stmt":
            children = getattr(node, "children", [])
            clk_name = children[0].value if children else None
            if not clk_name:
                return
            edge_type = "posedge"
            body_node = None
            for idx, ch in enumerate(children[1:], start=1):
                if hasattr(ch, "value") and ch.value in ("posedge", "negedge"):
                    edge_type = ch.value
                elif hasattr(ch, "data") and ch.data in ("when_body", "circuit_body"):
                    body_node = ch
            if body_node is None:
                for ch in reversed(children):
                    if hasattr(ch, "data"):
                        body_node = ch
                        break
            if body_node is None:
                return

            prev_clk = self.active_clk_sig
            prev_edge = self.active_edge

            self.active_clk_sig = scope.resolve_signal(clk_name)
            if not self.active_clk_sig:
                self.active_clk_sig = self.db.create_signal(exact_id=clk_name)
                self.active_clk_sig.driver = Endpoint("TOP", clk_name, "IN")
                self.db.top_inputs[clk_name] = self.active_clk_sig
                scope.bind_signal(clk_name, self.active_clk_sig)

            self.active_edge = edge_type
            # NOTE: We do NOT invert CLK for negedge.  The clock polarity is
            # carried in self.active_edge ("posedge"/"negedge") and encoded in
            # the DFF module_name (DFF_P vs DFF_N).  render_blif then maps:
            #   DFF_P  ->  .latch D Q re CLK 3
            #   DFF_N  ->  .latch D Q fe CLK 3
            # This is the correct, EDA-standard representation.  Inserting a
            # NOT gate and using "re" on the inverted clock is functionally
            # equivalent but breaks clock-tree recognition in every downstream
            # tool (ABC, Yosys, STA, P&R flows).

            assignments = self._process_conditional_logic(body_node, scope, prefix, res_left)

            for target_name, final_d_sig in assignments.items():
                q_sig = scope.resolve_signal(target_name)
                if not q_sig:
                    q_sig = self.db.create_signal(exact_id=f"reg_{target_name}")
                    scope.bind_signal(target_name, q_sig)

                if q_sig.driver and q_sig.driver.inst_id in self.db.instances:
                    existing_inst = self.db.instances[q_sig.driver.inst_id]
                    existing_clk = None
                    for pname, psig in existing_inst.inputs.items():
                        if existing_inst.port_roles.get(pname) == "clock":
                            existing_clk = psig
                            break

                    same_clock = (
                        existing_clk is not None
                        and self.active_clk_sig is not None
                        and existing_clk is self.active_clk_sig
                    )

                    existing_edge_is_neg = getattr(existing_inst, "trigger_edge", "posedge") == "negedge"
                    new_edge_is_neg = self.active_edge == "negedge"
                    same_edge = existing_edge_is_neg == new_edge_is_neg

                    if not (same_clock and same_edge):
                        raise ValueError(
                            f"Cilux synthesis error: register '{target_name}' is driven by "
                            f"multiple 'when' blocks with different clocks/edges "
                            f"(existing driver: instance '{existing_inst.id}' "
                            f"module '{existing_inst.module_name}'; new block clock="
                            f"'{clk_name}' edge='{self.active_edge}'). "
                            f"A register may only be clocked by a single clock signal "
                            f"and edge; merge these into one 'when' block."
                        )

                    if final_d_sig is not None:
                        old_d_sig = existing_inst.inputs.get("D")
                        if old_d_sig is not None:
                            try:
                                old_d_sig.readers.remove(Endpoint(existing_inst.id, "D", "IN"))
                            except ValueError:
                                pass
                        final_d_sig.readers.append(Endpoint(existing_inst.id, "D", "IN"))
                        existing_inst.inputs["D"] = final_d_sig

                    continue

                dff_type = "DFF_P" if self.active_edge == "posedge" else "DFF_N"
                inst_id = prefix + self.generate_inst_id(dff_type, target_name)
                inst = self._make_dff(
                    dff_type,
                    inst_id,
                    d_sig=final_d_sig,
                    clk_sig=self.active_clk_sig,
                    q_sig=q_sig,
                    res_left=res_left,
                )

                if target_name in self.db.top_outputs and prefix == "":
                    self.db.top_outputs[target_name].driver = q_sig.driver

                self.db.instances[inst_id] = inst

            self.active_clk_sig = prev_clk
            self.active_edge = prev_edge
            return

        if node.data == "condition_stmt":
            assignments = self._process_conditional_logic(node, scope, prefix, res_left, is_sequential=False)
            for target, sig in assignments.items():
                if sig is None:
                    continue
                scope.bind_signal(target, sig)
                if target in self.db.top_outputs and prefix == "":
                    top_sig = self.db.top_outputs[target]
                    if sig.driver and not top_sig.driver:
                        top_sig.driver = sig.driver
                    elif sig.driver:
                        top_sig.driver = sig.driver
                    top_reader = Endpoint("TOP", target, "OUT")
                    if top_reader not in sig.readers:
                        sig.readers.append(top_reader)
                    self.db.top_outputs[target] = sig
            return

        if node.data == "wire_stmt":
            src_ast, dst_ast = node.children[0], node.children[1]
            src_sig = self._eval_expr(src_ast, scope, prefix, res_left)
            dst_sig = self._eval_expr(dst_ast, scope, prefix, res_left)

            if src_sig and dst_sig:
                if src_sig is dst_sig:
                    return

                dst_name = self._get_pin_name(dst_ast)
                self._alias_map[id(dst_sig)] = src_sig

                # Migrate any readers already attached to dst_sig onto src_sig
                for reader in dst_sig.readers:
                    if reader not in src_sig.readers:
                        src_sig.readers.append(reader)
                dst_sig.readers = src_sig.readers

                if dst_sig.driver is None:
                    dst_sig.driver = src_sig.driver

                if dst_name:
                    scope.bind_signal(dst_name, src_sig)

                if dst_name in self.db.top_outputs and prefix == "":
                    top_out_sig = self.db.top_outputs[dst_name]
                    if src_sig.driver:
                        top_out_sig.driver = src_sig.driver
                    elif src_sig.is_constant:
                        top_out_sig.driver = None
                        top_out_sig.is_constant = src_sig.is_constant
                        top_out_sig.const_value = src_sig.const_value
                    if not src_sig.is_constant:
                        self.db.top_outputs[dst_name] = src_sig
                    top_reader = Endpoint("TOP", dst_name, "OUT")
                    if top_reader not in src_sig.readers:
                        src_sig.readers.append(top_reader)
                    return

                src_name = self._get_pin_name(src_ast)
                if src_name in self.db.top_inputs:
                    scope.bind_signal(dst_name if dst_name else src_name, src_sig)
            return

        if node.data == "var_stmt":
            first = node.children[0]
            var_name = None
            if hasattr(first, "type") and first.type == "NAME":
                var_name = first.value
            elif hasattr(first, "data") and first.data == "expr":
                inner = first.children[0]
                if hasattr(inner, "type") and inner.type == "NAME":
                    var_name = inner.value

            sig = self._eval_expr(node.children[1], scope, prefix, res_left, var_name)
            if sig and var_name:
                scope.bind_signal(var_name, sig)

                if var_name in self.db.top_outputs and prefix == "":
                    top_sig = self.db.top_outputs[var_name]
                    if sig.is_constant:
                        top_sig.is_constant = sig.is_constant
                        top_sig.const_value = sig.const_value
                    elif sig.driver:
                        top_sig.driver = sig.driver
                    else:
                        self.db.top_outputs[var_name] = sig
                    top_reader = Endpoint("TOP", var_name, "OUT")
                    if top_reader not in sig.readers:
                        sig.readers.append(top_reader)
            return

        if node.data == "expr":
            self._eval_expr(node, scope, prefix, res_left)
            return

    def _resolve_signal_deep(self, sig: "Signal", scope: "SymbolTable", visited=None) -> "Signal":
        if visited is None:
            visited = set()
        if sig is None:
            return sig
        if id(sig) in visited:
            return sig
        visited.add(id(sig))
        if sig.driver or sig.is_constant:
            return sig
        alias_target = self._alias_map.get(id(sig))
        if alias_target is not None:
            return self._resolve_signal_deep(alias_target, scope, visited)
        return sig

    def _resolve_name_deep(self, name: str, scope: "SymbolTable", visited_names=None) -> Optional["Signal"]:
        if visited_names is None:
            visited_names = set()
        if name in visited_names:
            return None
        visited_names.add(name)

        sig = scope.resolve_signal(name)
        if sig is None:
            return None
        return self._resolve_signal_deep(sig, scope)

    def _resolve_name_deep_by_sig(self, sig: "Signal", scope: "SymbolTable", visited=None) -> Optional["Signal"]:
        return self._resolve_signal_deep(sig, scope, visited)

    def _get_pin_name(self, node) -> Optional[str]:
        if not hasattr(node, "data"):
            return node.value if hasattr(node, "value") else str(node)
        if node.data == "expr":
            return self._get_pin_name(node.children[0])
        if node.data == "dot_access":
            obj = self._get_pin_name(node.children[0])
            prop = node.children[1].value if hasattr(node.children[1], "value") else str(node.children[1])
            return f"{obj}.{prop}"
        if len(node.children) > 0:
            return self._get_pin_name(node.children[0])
        return None

    def _eval_expr(
        self,
        node,
        scope: SymbolTable,
        prefix: str,
        res_left: int,
        target_name: str = None,
    ) -> Optional[Signal]:
        if not hasattr(node, "data"):
            if hasattr(node, "type"):
                if node.type == "NAME":
                    circ_def = scope.resolve_def(node.value)
                    if circ_def:
                        return self._instantiate(
                            circ_def,
                            node.value,
                            [],
                            {},
                            scope,
                            prefix,
                            res_left,
                            node.value,
                        )
                    sig = self._resolve_name_deep(node.value, scope)
                    return sig
                elif node.type == "INT":
                    return self.db.get_const(int(node.value))
            return None

        if node.data == "expr":
            return self._eval_expr(node.children[0], scope, prefix, res_left, target_name)

        if node.data in ("int_literal", "bool_literal"):
            val_token = node.children[0]
            val = (
                1 if val_token.type == "BOOL_TRUE" else (0 if val_token.type == "BOOL_FALSE" else int(val_token.value))
            )
            return self.db.get_const(val)

        if node.data == "dot_access":
            obj_node = node.children[0]
            port_name = node.children[1].value if hasattr(node.children[1], "value") else str(node.children[1])

            actual_obj_node = obj_node
            if hasattr(obj_node, "data") and obj_node.data == "expr" and obj_node.children:
                actual_obj_node = obj_node.children[0]

            if hasattr(actual_obj_node, "data") and actual_obj_node.data == "call":
                call_node = actual_obj_node
                func_name_tok = call_node.children[0]
                func_name_val = func_name_tok.value if hasattr(func_name_tok, "value") else str(func_name_tok)

                call_args, call_kwargs = [], {}
                for arg_node in call_node.children[1:]:
                    if hasattr(arg_node, "data") and arg_node.data == "arg":
                        inner = arg_node.children[0]
                        if hasattr(inner, "data") and inner.data == "named_arg":
                            k = inner.children[0].value
                            v = self._eval_expr(inner.children[1], scope, prefix, res_left)
                            call_kwargs[k] = v
                        else:
                            v = self._eval_expr(inner, scope, prefix, res_left)
                            call_args.append(v)
                    else:
                        v = self._eval_expr(arg_node, scope, prefix, res_left)
                        call_args.append(v)

                def _sig_key(s):
                    return s.id if s is not None else "__none__"

                cache_key = (
                    (func_name_val,)
                    + tuple(_sig_key(a) for a in call_args)
                    + tuple((k, _sig_key(v)) for k, v in sorted(call_kwargs.items()))
                )
                existing_inst_id = self._inline_call_cache.get(cache_key)

                if existing_inst_id and (
                    existing_inst_id in self.db.instances or existing_inst_id in getattr(self, "_inline_outputs", {})
                ):
                    inline_outs = getattr(self, "_inline_outputs", {})
                    if existing_inst_id in inline_outs:
                        port_sigs = inline_outs[existing_inst_id]
                        if port_name in port_sigs:
                            return port_sigs[port_name]
                        return None
                    inst = self.db.instances[existing_inst_id]
                    if port_name in inst.outputs:
                        return inst.outputs[port_name]
                    out_sig = self.db.create_signal(exact_id=f"{existing_inst_id}_{port_name}")
                    out_sig.driver = Endpoint(existing_inst_id, port_name, "OUT")
                    inst.outputs[port_name] = out_sig
                    return out_sig
                else:
                    inst_id = self._instantiate_and_get_id(
                        func_name_val, call_args, call_kwargs, scope, prefix, res_left
                    )
                    if inst_id:
                        self._inline_call_cache[cache_key] = inst_id
                        inline_outs = getattr(self, "_inline_outputs", {})
                        if inst_id in inline_outs:
                            port_sigs = inline_outs[inst_id]
                            if port_name in port_sigs:
                                return port_sigs[port_name]
                            return None
                        inst = self.db.instances.get(inst_id)
                        if inst:
                            if port_name in inst.outputs:
                                return inst.outputs[port_name]
                            out_sig = self.db.create_signal(exact_id=f"{inst_id}_{port_name}")
                            out_sig.driver = Endpoint(inst_id, port_name, "OUT")
                            inst.outputs[port_name] = out_sig
                            return out_sig
                    return None

            obj_name = self._get_pin_name(obj_node)
            full_name = f"{obj_name}.{port_name}"

            sig = scope.resolve_signal(full_name)
            if sig:
                return sig

            actual_inst_id = None
            var_sig = scope.resolve_signal(obj_name)
            if var_sig is not None:
                if var_sig.driver and var_sig.driver.inst_id in self.db.instances:
                    actual_inst_id = var_sig.driver.inst_id
                if actual_inst_id is None:
                    for iid, inst_obj in self.db.instances.items():
                        if var_sig in inst_obj.outputs.values():
                            actual_inst_id = iid
                            break
                if actual_inst_id is None:
                    for iid, port_map in self._inline_outputs.items():
                        if var_sig in port_map.values():
                            actual_inst_id = iid
                            break

            if actual_inst_id is None:
                lookup_key = f"{prefix}{obj_name}"
                actual_inst_id = self.instance_map.get(lookup_key)

            if actual_inst_id is None:
                actual_inst_id = f"{prefix}{obj_name}"

            if actual_inst_id in self._inline_outputs:
                port_map = self._inline_outputs[actual_inst_id]
                if port_name in port_map:
                    sig = port_map[port_name]
                    scope.bind_signal(full_name, sig)
                    return sig
                return None

            if actual_inst_id in self.db.instances:
                inst = self.db.instances[actual_inst_id]

                if port_name in inst.outputs:
                    sig = inst.outputs[port_name]
                    scope.bind_signal(full_name, sig)
                    return sig
                if port_name in inst.inputs:
                    sig = inst.inputs[port_name]
                    scope.bind_signal(full_name, sig)
                    return sig

                circ_def = None
                try:
                    circ_def = scope.resolve_def(inst.module_name) or self.kernel.context.lookup(inst.module_name)
                except Exception:
                    pass

                sig = self.db.create_signal(exact_id=f"{actual_inst_id}_{port_name}")
                if circ_def:
                    out_names = {p.name for p in getattr(circ_def, "output", [])} or set(
                        getattr(circ_def, "output_names", [])
                    )
                    in_names = {p.name for p in getattr(circ_def, "input", [])} or set(
                        getattr(circ_def, "input_names", [])
                    )
                    if port_name in out_names:
                        sig.driver = Endpoint(actual_inst_id, port_name, "OUT")
                        inst.outputs[port_name] = sig
                    elif port_name in in_names:
                        sig.readers.append(Endpoint(actual_inst_id, port_name, "IN"))
                        inst.inputs[port_name] = sig
                    else:
                        sig.driver = Endpoint(actual_inst_id, port_name, "OUT")
                        inst.outputs[port_name] = sig
                else:
                    sig.driver = Endpoint(actual_inst_id, port_name, "OUT")
                    inst.outputs[port_name] = sig

                scope.bind_signal(full_name, sig)
                return sig

            sig = self.db.create_signal(exact_id=f"sig_{obj_name}_{port_name}")
            scope.bind_signal(full_name, sig)
            return sig

        if node.data == "call":
            func_name = node.children[0].value
            args, kwargs = [], {}

            for arg_node in node.children[1:]:
                if hasattr(arg_node, "data") and arg_node.data == "arg":
                    inner = arg_node.children[0]
                    if hasattr(inner, "data") and inner.data == "named_arg":
                        key = inner.children[0].value
                        val_sig = self._eval_expr(inner.children[1], scope, prefix, res_left)
                        kwargs[key] = val_sig
                    else:
                        val_sig = self._eval_expr(inner, scope, prefix, res_left)
                        args.append(val_sig)
                else:
                    val_sig = self._eval_expr(arg_node, scope, prefix, res_left)
                    args.append(val_sig)

            if func_name in self.BASE_PRIMITIVES:
                _pkey = self._make_inst_cache_key(func_name, args, kwargs)
                if _pkey in self._unified_inst_cache:
                    _cached_pid = self._unified_inst_cache[_pkey]
                    if target_name:
                        self.instance_map[f"{prefix}{target_name}"] = _cached_pid
                    inst_obj = self.db.instances.get(_cached_pid)
                    return inst_obj.outputs.get("o0") if inst_obj else None

                inst_id = prefix + self.generate_inst_id(func_name, target_name)
                if target_name:
                    self.instance_map[f"{prefix}{target_name}"] = inst_id

                if func_name.upper() == "MUX2":
                    i0_sig = args[0] if len(args) > 0 else None
                    i1_sig = args[1] if len(args) > 1 else None
                    sel_sig = args[2] if len(args) > 2 else None
                    out_sig = self._make_mux2(inst_id, i0=i0_sig, i1=i1_sig, sel=sel_sig, res_left=res_left)
                    self._unified_inst_cache[_pkey] = inst_id
                    return out_sig

                inst = Instance(id=inst_id, module_name=func_name, is_blackbox=True)
                self.db.instances[inst_id] = inst

                for i, arg_sig in enumerate(args):
                    if arg_sig:
                        arg_sig.readers.append(Endpoint(inst_id, f"i{i}", "IN"))
                        inst.inputs[f"i{i}"] = arg_sig

                out_sig = self.db.create_signal(exact_id=f"{inst_id}_out")
                out_sig.driver = Endpoint(inst_id, "o0", "OUT")
                inst.outputs["o0"] = out_sig

                self._unified_inst_cache[_pkey] = inst_id
                return out_sig

            circ_name = func_name
            c_def = scope.resolve_def(func_name)
            if c_def:
                circ_name = c_def
            return self._instantiate(circ_name, func_name, args, kwargs, scope, prefix, res_left, target_name)

        if node.data == "access_stmt":
            obj = self.kernel.execute(node)
            if hasattr(obj, "data"):
                val = obj.data
                return self.db.get_const(val) if isinstance(val, int) else None
            return None
        return None

    def _instantiate_and_get_id(self, func_name: str, args, kwargs, scope, prefix, res_left) -> Optional[str]:
        if func_name in self.BASE_PRIMITIVES:
            _pkey2 = self._make_inst_cache_key(func_name, args, kwargs)
            if _pkey2 in self._unified_inst_cache:
                return self._unified_inst_cache[_pkey2]

            inst_id = prefix + self.generate_inst_id(func_name, None)

            if func_name.upper() == "MUX2":
                i0_sig = args[0] if len(args) > 0 else None
                i1_sig = args[1] if len(args) > 1 else None
                sel_sig = args[2] if len(args) > 2 else None
                self._make_mux2(inst_id, i0=i0_sig, i1=i1_sig, sel=sel_sig, res_left=res_left)
            else:
                inst = Instance(id=inst_id, module_name=func_name, is_blackbox=True)
                self.db.instances[inst_id] = inst
                for i, arg_sig in enumerate(args):
                    if arg_sig:
                        arg_sig.readers.append(Endpoint(inst_id, f"i{i}", "IN"))
                        inst.inputs[f"i{i}"] = arg_sig
                out_sig = self.db.create_signal(exact_id=f"{inst_id}_out")
                out_sig.driver = Endpoint(inst_id, "o0", "OUT")
                inst.outputs["o0"] = out_sig

            self._unified_inst_cache[_pkey2] = inst_id
            return inst_id

        try:
            obj = self.kernel.context.lookup(func_name)
        except Exception:
            return None

        _ukey2 = self._make_inst_cache_key(func_name, args, kwargs)
        if _ukey2 in self._unified_inst_cache:
            return self._unified_inst_cache[_ukey2]

        inst_id = prefix + self.generate_inst_id(func_name, func_name)

        if res_left > 0 and hasattr(obj, "body"):
            sub_scope = SymbolTable(parent=scope)

            for bn in obj.body if isinstance(obj.body, list) else [obj.body]:
                self._register_nested_circuit_defs(bn, sub_scope)

            if hasattr(obj, "instances") and obj.instances:
                for inst_n, c_n in obj.instances:
                    sub_scope.bind_def(inst_n, c_n)

            for i, in_port in enumerate(getattr(obj, "input", [])):
                arg_sig = args[i] if i < len(args) else kwargs.get(in_port.name)
                if not arg_sig:
                    raise ValueError(
                        f"Cilux synthesis error: input port '{in_port.name}' of "
                        f"instance '{inst_id}' (module '{getattr(obj, 'name', '?')}') "
                        f"is unconnected. Floating module inputs are not allowed: "
                        f"on real hardware they act as antennas, causing "
                        f"unpredictable toggling and shoot-through current. "
                        f"Explicitly connect '{in_port.name}' to a signal, or "
                        f"tie it to a constant (0 or 1) if it is intentionally unused."
                    )
                sub_scope.bind_signal(in_port.name, arg_sig)

            out_port_signals: Dict[str, Signal] = {}
            for out_port in getattr(obj, "output", []):
                sig = self.db.create_signal(exact_id=f"{inst_id}_{out_port.name}")
                sub_scope.bind_signal(out_port.name, sig)
                out_port_signals[out_port.name] = sig

            body_nodes = obj.body if isinstance(obj.body, list) else [obj.body]
            for bn in body_nodes:
                self._walk(bn, sub_scope, f"{inst_id}_", res_left - 1)

            final_outputs: Dict[str, Signal] = {}
            for port_name_out, old_sig in out_port_signals.items():
                fresh = sub_scope.resolve_signal(port_name_out)
                if fresh is not None:
                    if not fresh.driver and old_sig.driver:
                        final_outputs[port_name_out] = old_sig
                    else:
                        final_outputs[port_name_out] = fresh
                else:
                    final_outputs[port_name_out] = old_sig

            self._inline_outputs[inst_id] = final_outputs
            self._unified_inst_cache[_ukey2] = inst_id

            return inst_id

        else:
            inst = Instance(id=inst_id, module_name=func_name, is_blackbox=True)
            self.db.instances[inst_id] = inst

            self._ensure_sub_db(func_name, obj)

            inputs_list = getattr(obj, "input", []) or [DummyPort(n) for n in getattr(obj, "input_names", [])]
            outputs_list = getattr(obj, "output", []) or [DummyPort(n) for n in getattr(obj, "output_names", [])]

            for i, in_port in enumerate(inputs_list):
                arg_sig = args[i] if i < len(args) else kwargs.get(in_port.name)
                if not arg_sig:
                    raise ValueError(
                        f"Cilux synthesis error: input port '{in_port.name}' of "
                        f"instance '{inst_id}' (module '{func_name}') is "
                        f"unconnected. Floating module inputs are not allowed: "
                        f"on real hardware they act as antennas, causing "
                        f"unpredictable toggling and shoot-through current. "
                        f"Explicitly connect '{in_port.name}' to a signal, or "
                        f"tie it to a constant (0 or 1) if it is intentionally unused."
                    )
                if Endpoint(inst_id, in_port.name, "IN") not in arg_sig.readers:
                    arg_sig.readers.append(Endpoint(inst_id, in_port.name, "IN"))
                inst.inputs[in_port.name] = arg_sig

            for out_port in outputs_list:
                out_sig = self.db.create_signal(exact_id=f"{inst_id}_{out_port.name}")
                out_sig.driver = Endpoint(inst_id, out_port.name, "OUT")
                inst.outputs[out_port.name] = out_sig

            if not inst.outputs:
                out_sig = self.db.create_signal(exact_id=f"{inst_id}_o0")
                out_sig.driver = Endpoint(inst_id, "o0", "OUT")
                inst.outputs["o0"] = out_sig

            self._unified_inst_cache[_ukey2] = inst_id

            return inst_id

    def _instantiate(self, circ_name, func_name, args, kwargs, scope, prefix, res_left, target_name) -> Signal:
        self.call_depth += 1
        if self.call_depth > self.MAX_DEPTH:
            raise RecursionError(f"Infinite elaboration loop detected at {circ_name}")

        try:
            if isinstance(circ_name, str):
                try:
                    obj = self.kernel.context.lookup(circ_name)
                except NameError:
                    obj = None
                    # 1. search in _module_exports stack (set by _import.py on the parent)
                    for _exports in reversed(self._module_exports_stack):
                        if circ_name in _exports:
                            obj = _exports[circ_name]
                            break
                    # 2. search in ImportPlugin._file_cache (always complete)
                    if obj is None:
                        try:
                            ip = self.kernel.plugins.get("import")
                            if ip:
                                for _cached in ip._file_cache.values():
                                    _exp = getattr(
                                        _cached,
                                        "_exports",
                                        _cached if isinstance(_cached, dict) else None,
                                    )
                                    if _exp and circ_name in _exp:
                                        obj = _exp[circ_name]
                                        break
                        except Exception:
                            pass
                    if obj is None:
                        raise NameError(f"'{circ_name}' not found in current scope")

            elif hasattr(circ_name, "body") or hasattr(circ_name, "input"):
                obj = circ_name
                circ_name = getattr(circ_name, "name", getattr(circ_name, "data", str(circ_name)))
            else:
                obj = self.kernel.context.lookup(str(circ_name))

            _ukey = self._make_inst_cache_key(str(circ_name), args, kwargs)
            if _ukey in self._unified_inst_cache:
                _cached_id = self._unified_inst_cache[_ukey]
                if target_name:
                    self.instance_map[f"{prefix}{target_name}"] = _cached_id
                if _cached_id in self._inline_outputs:
                    _out_map = self._inline_outputs[_cached_id]
                    if target_name:
                        for _pname, _psig in _out_map.items():
                            scope.bind_signal(f"{target_name}.{_pname}", _psig)
                    return next(iter(_out_map.values()), None) if _out_map else None
                elif _cached_id in self.db.instances:
                    _inst = self.db.instances[_cached_id]
                    if target_name:
                        for _pname, _psig in _inst.outputs.items():
                            scope.bind_signal(f"{target_name}.{_pname}", _psig)
                    return next(iter(_inst.outputs.values()), None) if _inst.outputs else None

            inst_id = prefix + self.generate_inst_id(circ_name, target_name if target_name else func_name)
            if target_name:
                self.instance_map[f"{prefix}{target_name}"] = inst_id

            if res_left > 0 and hasattr(obj, "body"):
                sub_scope = SymbolTable(parent=scope)
                out_sig_primary = None

                # Push this module's exports onto the stack so nested _instantiate
                # calls can find siblings (e.g. half_adder when elaborating full_adder)
                _mod_exports = getattr(obj, "_module_exports", None) or {}
                self._module_exports_stack.append(_mod_exports)
                try:
                    for bn in obj.body if isinstance(obj.body, list) else [obj.body]:
                        self._register_nested_circuit_defs(bn, sub_scope)

                    if hasattr(obj, "instances") and obj.instances:
                        for inst_n, c_n in obj.instances:
                            sub_scope.bind_def(inst_n, c_n)

                    for i, in_port in enumerate(getattr(obj, "input", [])):
                        arg_sig = args[i] if i < len(args) else kwargs.get(in_port.name)
                        if not arg_sig:
                            raise ValueError(
                                f"Cilux synthesis error: input port '{in_port.name}' of "
                                f"instance '{inst_id}' (module '{circ_name}') is "
                                f"unconnected. Floating module inputs are not allowed: "
                                f"on real hardware they act as antennas, causing "
                                f"unpredictable toggling and shoot-through current. "
                                f"Explicitly connect '{in_port.name}' to a signal, or "
                                f"tie it to a constant (0 or 1) if it is intentionally unused."
                            )
                        sub_scope.bind_signal(in_port.name, arg_sig)
                        if target_name:
                            scope.bind_signal(f"{target_name}.{in_port.name}", arg_sig)

                    out_port_pre: Dict[str, Signal] = {}
                    for out_port in getattr(obj, "output", []):
                        sig = self.db.create_signal(exact_id=f"{inst_id}_{out_port.name}")
                        sub_scope.bind_signal(out_port.name, sig)
                        out_port_pre[out_port.name] = sig
                        if not out_sig_primary:
                            out_sig_primary = sig

                    body_nodes = obj.body if isinstance(obj.body, list) else [obj.body]
                    for bn in body_nodes:
                        self._walk(bn, sub_scope, f"{inst_id}_", res_left - 1)

                    final_out_map: Dict[str, Signal] = {}
                    for pname, pre_sig in out_port_pre.items():
                        fresh = sub_scope.resolve_signal(pname)
                        resolved = self._deep_resolve_output(fresh, pre_sig, sub_scope)
                        final_out_map[pname] = resolved
                        if target_name:
                            scope.bind_signal(f"{target_name}.{pname}", resolved)

                    self._inline_outputs[inst_id] = final_out_map
                    self._unified_inst_cache[_ukey] = inst_id

                    if target_name:
                        self.instance_map[f"{prefix}{target_name}"] = inst_id

                    if out_port_pre:
                        first_port = next(iter(out_port_pre))
                        out_sig_primary = final_out_map.get(first_port, out_sig_primary)

                    return out_sig_primary

                finally:
                    self._module_exports_stack.pop()

            else:
                inst = Instance(id=inst_id, module_name=circ_name, is_blackbox=True)
                self.db.instances[inst_id] = inst

                self._ensure_sub_db(circ_name, obj)

                inputs_list = obj.input if hasattr(obj, "input") else []
                outputs_list = obj.output if hasattr(obj, "output") else []

                if not inputs_list and hasattr(obj, "input_names"):
                    inputs_list = [DummyPort(n) for n in obj.input_names]
                if not outputs_list and hasattr(obj, "output_names"):
                    outputs_list = [DummyPort(n) for n in obj.output_names]

                for i, in_port in enumerate(inputs_list):
                    arg_sig = args[i] if i < len(args) else kwargs.get(in_port.name)
                    if not arg_sig:
                        raise ValueError(
                            f"Cilux synthesis error: input port '{in_port.name}' of "
                            f"instance '{inst_id}' (module '{circ_name}') is "
                            f"unconnected. Floating module inputs are not allowed: "
                            f"on real hardware they act as antennas, causing "
                            f"unpredictable toggling and shoot-through current. "
                            f"Explicitly connect '{in_port.name}' to a signal, or "
                            f"tie it to a constant (0 or 1) if it is intentionally unused."
                        )
                    if Endpoint(inst_id, in_port.name, "IN") not in arg_sig.readers:
                        arg_sig.readers.append(Endpoint(inst_id, in_port.name, "IN"))
                    inst.inputs[in_port.name] = arg_sig
                    if target_name:
                        scope.bind_signal(f"{target_name}.{in_port.name}", arg_sig)

                out_sig_primary = None
                for out_port in outputs_list:
                    out_sig = self.db.create_signal(exact_id=f"{inst_id}_{out_port.name}")
                    out_sig.driver = Endpoint(inst_id, out_port.name, "OUT")
                    inst.outputs[out_port.name] = out_sig
                    if target_name:
                        scope.bind_signal(f"{target_name}.{out_port.name}", out_sig)
                    if not out_sig_primary:
                        out_sig_primary = out_sig

                if not out_sig_primary:
                    out_sig = self.db.create_signal(exact_id=f"{inst_id}_o0")
                    out_sig.driver = Endpoint(inst_id, "o0", "OUT")
                    inst.outputs["o0"] = out_sig
                    if target_name:
                        scope.bind_signal(f"{target_name}.o0", out_sig)
                    out_sig_primary = out_sig

                self._unified_inst_cache[_ukey] = inst_id

                return out_sig_primary

        except Exception as _elab_err:
            import warnings

            warnings.warn(
                f"Elaboration of '{circ_name}' failed ({type(_elab_err).__name__}: {_elab_err}); "
                f"substituting a blackbox instance.",
                stacklevel=2,
            )
            inst_id = prefix + self.generate_inst_id(circ_name, target_name if target_name else func_name)
            if target_name:
                self.instance_map[f"{prefix}{target_name}"] = inst_id

            inst = Instance(id=inst_id, module_name=circ_name, is_blackbox=True)
            self.db.instances[inst_id] = inst

            for i, arg_sig in enumerate(args):
                if arg_sig:
                    arg_sig.readers.append(Endpoint(inst_id, f"i{i}", "IN"))
                    inst.inputs[f"i{i}"] = arg_sig

            out_sig = self.db.create_signal(exact_id=f"{inst_id}_out")
            out_sig.driver = Endpoint(inst_id, "o0", "OUT")
            inst.outputs["o0"] = out_sig
            if target_name:
                scope.bind_signal(f"{target_name}.o0", out_sig)
            return out_sig
        finally:
            self.call_depth -= 1

    def _call_blueprint(self, prim_name: str, inst_id: str, **kwargs):
        method_name = self.PRIMITIVE_BLUEPRINTS[prim_name]
        method = getattr(self, method_name)
        return method(inst_id, **kwargs)

    def _make_dff(
        self,
        dff_type: str,
        inst_id: str,
        d_sig: Optional[Signal],
        clk_sig: Optional[Signal],
        q_sig: Signal,
        res_left: int,
    ) -> Instance:
        """Create a DFF_P or DFF_N instance, wiring it to the already-declared
        q_sig.  The existing q_sig is always reused so that all prior scope
        bindings remain valid -- no signal migration is needed.

        When res_left > 0 the DFF is expanded via PRIMITIVE_BLUEPRINTS into a
        master-slave latch pair. The CLK used is always the real signal object
        passed by the caller -- never a hardcoded name.

        When res_left == 0 the behaviour is identical to the original code.
        """
        edge_type = "posedge" if dff_type.startswith("DFF_P") else "negedge"
        if res_left > 0:
            # Dispatch through the registry -- no hardcoded method name here.
            self._call_blueprint(
                dff_type,
                inst_id,
                d_sig=d_sig,
                clk_sig=clk_sig,
                q_sig=q_sig,
            )

            # Return a wrapper Instance so callers (.outputs["Q"], etc.) work.
            wrapper = Instance(id=inst_id, module_name=dff_type, is_blackbox=False)
            wrapper.is_sequential = True
            wrapper.trigger_edge = edge_type
            if d_sig:
                wrapper.inputs["D"] = d_sig
            if clk_sig:
                wrapper.inputs["CLK"] = clk_sig
            wrapper.outputs["Q"] = q_sig
            return wrapper

        # res_left == 0: plain blackbox, identical to original code
        inst = Instance(id=inst_id, module_name=dff_type, is_blackbox=True)
        inst.is_sequential = True
        inst.trigger_edge = edge_type
        inst.port_roles["D"] = "data"
        inst.port_roles["CLK"] = "clock"
        if d_sig:
            d_sig.readers.append(Endpoint(inst_id, "D", "IN"))
            inst.inputs["D"] = d_sig
        if clk_sig:
            clk_sig.readers.append(Endpoint(inst_id, "CLK", "IN"))
            inst.inputs["CLK"] = clk_sig
        q_sig.driver = Endpoint(inst_id, "Q", "OUT")
        inst.outputs["Q"] = q_sig
        return inst

    def _make_mux2(
        self,
        inst_id: str,
        i0: Optional[Signal],
        i1: Optional[Signal],
        sel: Optional[Signal],
        res_left: int,
    ) -> Signal:
        """Create a MUX2 and return its output signal.

        When res_left == 0: plain blackbox, identical to the original code.
        When res_left > 0:  dispatches through PRIMITIVE_BLUEPRINTS registry
            to expand to AND/OR/NOT gate primitives: o = (i1 & sel) | (i0 & ~sel).
        The sel / i0 / i1 signal objects are passed directly by the caller --
        no names are assumed, so the expansion is always in sync with the
        actual circuit signals.
        """
        if res_left > 0:
            return self._call_blueprint("MUX2", inst_id, i0=i0, i1=i1, sel=sel)

        # res_left == 0: plain blackbox, identical to original code
        inst = Instance(id=inst_id, module_name="MUX2", is_blackbox=True)
        if i1:
            i1.readers.append(Endpoint(inst_id, "i1", "IN"))
            inst.inputs["i1"] = i1
        if i0:
            i0.readers.append(Endpoint(inst_id, "i0", "IN"))
            inst.inputs["i0"] = i0
        if sel:
            sel.readers.append(Endpoint(inst_id, "sel", "IN"))
            inst.inputs["sel"] = sel
        mux_out = self.db.create_signal(exact_id=f"{inst_id}_out")
        mux_out.driver = Endpoint(inst_id, "o0", "OUT")
        inst.outputs["o0"] = mux_out
        self.db.instances[inst_id] = inst
        return mux_out

    def _blueprint_mux2(
        self,
        inst_id: str,
        i0: Optional[Signal],
        i1: Optional[Signal],
        sel: Optional[Signal],
    ) -> Signal:
        """Expand MUX2 to AND/OR/NOT.  o = (i1 & sel) | (i0 & ~sel).
        Returns the final output signal (equivalent to mux_out in the blackbox path).
        To add a deeper expansion in the future, only this method needs changing.
        """

        def _gate(gid, mname, a, b=None):
            g = Instance(id=gid, module_name=mname, is_blackbox=True)
            if a:
                a.readers.append(Endpoint(gid, "i0", "IN"))
                g.inputs["i0"] = a
            if b:
                b.readers.append(Endpoint(gid, "i1", "IN"))
                g.inputs["i1"] = b
            out = self.db.create_signal(exact_id=f"{gid}_out")
            out.driver = Endpoint(gid, "o0", "OUT")
            g.outputs["o0"] = out
            self.db.instances[gid] = g
            return out

        not_sel = _gate(f"{inst_id}_not_sel", "NOT", sel)
        and1_out = _gate(f"{inst_id}_and1", "AND", i1, sel)
        and0_out = _gate(f"{inst_id}_and0", "AND", i0, not_sel)
        or_out = _gate(f"{inst_id}_or", "OR", and1_out, and0_out)

        # Register a wrapper so callers that look up inst_id in db.instances
        # (e.g. the unified_inst_cache path) find something consistent.
        wrapper = Instance(id=inst_id, module_name="MUX2", is_blackbox=False)
        if i0:
            wrapper.inputs["i0"] = i0
        if i1:
            wrapper.inputs["i1"] = i1
        if sel:
            wrapper.inputs["sel"] = sel
        wrapper.outputs["o0"] = or_out
        self.db.instances[inst_id] = wrapper

        return or_out

    # Blueprint: DFF_P  (positive-edge D flip-flop)
    # Internal structure: master-slave latch pair.
    #
    #   Master latch (LATCH_M) -- transparent when CLK == 0 (active-low enable)
    #     EN_m = ~CLK
    #     captures D while CLK is low
    #
    #   Slave latch (LATCH_S) -- transparent when CLK == 1 (active-high enable)
    #     EN_s = CLK
    #     captures master output while CLK is high => edge-triggered on posedge
    #
    # Signal flow:
    #   D --> [LATCH_M: D, EN=~CLK] --> qm --> [LATCH_S: D, EN=CLK] --> Q
    #
    # The CLK signal here is ALWAYS the real circuit clock passed by _make_dff;
    # it is never resolved by name.

    def _blueprint_dff_p(
        self,
        inst_id: str,
        d_sig: Optional[Signal],
        clk_sig: Optional[Signal],
        q_sig: Signal,
    ) -> None:
        """Expand DFF_P into a master-slave latch pair.
        Drives q_sig directly (same object the caller declared in scope).
        """

        def _latch(lid, d, en, q):
            """D-latch: Q follows D while EN=1, holds while EN=0."""
            lat = Instance(id=lid, module_name="LATCH", is_blackbox=True)
            lat.is_sequential = True
            lat.port_roles["D"] = "data"
            lat.port_roles["EN"] = "clock"
            if d:
                d.readers.append(Endpoint(lid, "D", "IN"))
                lat.inputs["D"] = d
            if en:
                en.readers.append(Endpoint(lid, "EN", "IN"))
                lat.inputs["EN"] = en
            q.driver = Endpoint(lid, "Q", "OUT")
            lat.outputs["Q"] = q
            self.db.instances[lid] = lat

        # ~CLK  (master enable: latch-M is transparent while CLK is LOW)
        not_clk_id = f"{inst_id}_not_clk"
        not_clk = Instance(id=not_clk_id, module_name="NOT", is_blackbox=True)
        if clk_sig:
            clk_sig.readers.append(Endpoint(not_clk_id, "i0", "IN"))
            not_clk.inputs["i0"] = clk_sig
        not_clk_out = self.db.create_signal(exact_id=f"{not_clk_id}_out")
        not_clk_out.driver = Endpoint(not_clk_id, "o0", "OUT")
        not_clk.outputs["o0"] = not_clk_out
        self.db.instances[not_clk_id] = not_clk

        # Master latch output signal
        qm = self.db.create_signal(exact_id=f"{inst_id}_qm")

        # Master latch: EN = ~CLK, captures D while CLK low
        _latch(f"{inst_id}_latch_m", d=d_sig, en=not_clk_out, q=qm)

        # Slave latch: EN = CLK, captures qm while CLK high => posedge output
        _latch(f"{inst_id}_latch_s", d=qm, en=clk_sig, q=q_sig)

    # Blueprint: DFF_N  (negative-edge D flip-flop)
    # Same master-slave structure but with inverted enable polarity:
    #
    #   Master latch -- transparent when CLK == 1 (active-high)
    #     EN_m = CLK
    #
    #   Slave latch  -- transparent when CLK == 0 (active-low)
    #     EN_s = ~CLK
    #
    # => edge-triggered on negedge of CLK
    #
    # Signal flow:
    #   D --> [LATCH_M: D, EN=CLK] --> qm --> [LATCH_S: D, EN=~CLK] --> Q

    def _blueprint_dff_n(
        self,
        inst_id: str,
        d_sig: Optional[Signal],
        clk_sig: Optional[Signal],
        q_sig: Signal,
    ) -> None:
        """Expand DFF_N into a master-slave latch pair (negedge triggered).
        Drives q_sig directly.
        """

        def _latch(lid, d, en, q):
            lat = Instance(id=lid, module_name="LATCH", is_blackbox=True)
            lat.is_sequential = True
            lat.port_roles["D"] = "data"
            lat.port_roles["EN"] = "clock"
            if d:
                d.readers.append(Endpoint(lid, "D", "IN"))
                lat.inputs["D"] = d
            if en:
                en.readers.append(Endpoint(lid, "EN", "IN"))
                lat.inputs["EN"] = en
            q.driver = Endpoint(lid, "Q", "OUT")
            lat.outputs["Q"] = q
            self.db.instances[lid] = lat

        # ~CLK  (slave enable for negedge: latch-S transparent while CLK LOW)
        not_clk_id = f"{inst_id}_not_clk"
        not_clk = Instance(id=not_clk_id, module_name="NOT", is_blackbox=True)
        if clk_sig:
            clk_sig.readers.append(Endpoint(not_clk_id, "i0", "IN"))
            not_clk.inputs["i0"] = clk_sig
        not_clk_out = self.db.create_signal(exact_id=f"{not_clk_id}_out")
        not_clk_out.driver = Endpoint(not_clk_id, "o0", "OUT")
        not_clk.outputs["o0"] = not_clk_out
        self.db.instances[not_clk_id] = not_clk

        # Master latch output signal
        qm = self.db.create_signal(exact_id=f"{inst_id}_qm")

        # Master latch: EN = CLK, captures D while CLK high
        _latch(f"{inst_id}_latch_m", d=d_sig, en=clk_sig, q=qm)

        # Slave latch: EN = ~CLK, captures qm while CLK low => negedge output
        _latch(f"{inst_id}_latch_s", d=qm, en=not_clk_out, q=q_sig)
