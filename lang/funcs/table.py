from core.plugin import Plugin, Value
from lang.types._str import StringValue
from lang.types.builtin import BuiltinFunction
from .utils import (
    Path,
    to_png,
    to_svg,
    SUPPORTED_EXTENSIONS,
)


class TruthTable(Value):
    def __init__(self, ttables: dict[str, str]) -> None:
        self.data = ttables
        self.vtype = "truth-table"

    def __dot__(self, prop):
        return StringValue(self.data.get(prop, f"There is no table named '{prop}'."))

    def __export__(self, path: Path, name=None) -> None:

        if name is None:
            content = "\n\n".join(self.data.values())

        elif isinstance(name, str):
            result = self.data.get(name)
            if result is None:
                raise KeyError(f"There is no truth table with the name '{name}'.")
            content = result

        elif isinstance(name, list):
            parts = []
            for n in name:
                result = self.data.get(n)
                if result is None:
                    raise KeyError(f"There is no truth table with the name '{n}'.")
                parts.append(result)
            content = "\n\n".join(parts)

        else:
            raise TypeError(f"'name' must be a str, list of str, or None. Got '{type(name).__name__}'.")

        suffix = path.suffix.lower()
        if suffix == ".svg":
            to_svg(text=content, filename=path)

        elif suffix == ".png":
            to_png(text=content, filename=path)
        else:
            raise ValueError(f"Unsupported extension '{suffix}'.Supported: {sorted(SUPPORTED_EXTENSIONS)}")


def render_truth_table(ttables: dict[str, dict]):
    """
    ttables: {
        circuit_name: {
            "input":        [ {col: val, ...}, ... ],
            "output":       [ {col: val, ...}, ... ],
            "clock_cols":   {col_name: edge_symbol},
            "input_types":  {col_name: type_label},
            "output_types": {col_name: type_label},
        }
    }
    """
    rendered_tables = dict()

    def center(text, width):
        text = str(text)
        if len(text) >= width:
            return text
        pad = width - len(text)
        left = pad // 2
        right = pad - left
        return (" " * left) + text + (" " * right)

    def make_border(left, middle, right, widths):
        return left + middle.join("─" * (w + 2) for w in widths) + right

    def make_row(values, widths):
        cols = [f" {center(str(v), w)} " for v, w in zip(values, widths)]
        return "│" + "│".join(cols) + "│"

    def calc_section_width(section_widths):
        if not section_widths:
            return 0
        return sum(section_widths) + len(section_widths) * 2 + (len(section_widths) - 1)

    for name, table in ttables.items():
        inputs = table.get("input", [])
        outputs = table.get("output", [])
        clock_cols = table.get("clock_cols", {})
        input_types = table.get("input_types", {})
        output_types = table.get("output_types", {})

        input_names = list(inputs[0].keys()) if inputs else []
        output_names = list(outputs[0].keys()) if outputs else []

        def col_width(key, rows, type_label=""):
            candidates = [len(str(key)), len(type_label)]
            candidates += [len(str(row.get(key, ""))) for row in rows]
            return max(max(candidates), 3)

        input_widths = [col_width(k, inputs, input_types.get(k, "")) for k in input_names]
        output_widths = [col_width(k, outputs, output_types.get(k, "")) for k in output_names]

        input_section_width = max(calc_section_width(input_widths), len("INPUTS"))
        output_section_width = max(calc_section_width(output_widths), len("OUTPUTS"))

        real_iw = calc_section_width(input_widths)
        if input_widths and input_section_width > real_iw:
            input_widths[-1] += input_section_width - real_iw

        real_ow = calc_section_width(output_widths)
        if output_widths and output_section_width > real_ow:
            output_widths[-1] += output_section_width - real_ow

        widths = input_widths + output_widths
        total_inner_width = input_section_width + output_section_width + 1

        lines = []
        title = f" {name} "
        remaining = max(0, total_inner_width - len(title))
        lp, rp = remaining // 2, remaining - remaining // 2
        lines.append("┌" + "─" * lp + title + "─" * rp + "┐")

        if input_names and output_names:
            lines.append("├" + "─" * input_section_width + "┬" + "─" * output_section_width + "┤")
            lines.append(
                "│" + center("INPUTS", input_section_width) + "│" + center("OUTPUTS", output_section_width) + "│"
            )
            sep_parts = []
            all_widths = input_widths + output_widths
            boundary = len(input_names) - 1
            for idx, w in enumerate(all_widths):
                piece = "─" * (w + 2)
                if idx == len(all_widths) - 1:
                    sep_parts.append(piece)
                elif idx == boundary:
                    sep_parts.append(piece + "┼")
                else:
                    sep_parts.append(piece + "┬")
            lines.append("├" + "".join(sep_parts) + "┤")
        else:
            lines.append(make_border("├", "┼", "┤", widths))

        headers = input_names + output_names
        lines.append(make_row(headers, widths))

        type_row = [input_types.get(k, "") for k in input_names] + [output_types.get(k, "") for k in output_names]
        if any(t for t in type_row):
            lines.append(make_border("├", "┼", "┤", widths))
            lines.append(make_row(type_row, widths))

        lines.append(make_border("├", "┼", "┤", widths))

        row_count = max(len(inputs), len(outputs))
        for idx in range(row_count):
            in_row = inputs[idx] if idx < len(inputs) else {}
            out_row = outputs[idx] if idx < len(outputs) else {}
            values = [in_row.get(k, "") for k in input_names] + [out_row.get(k, "") for k in output_names]
            lines.append(make_row(values, widths))
            if idx != row_count - 1:
                lines.append(make_border("├", "┼", "┤", widths))

        lines.append(make_border("└", "┴", "┘", widths))
        rendered_tables[name] = "\n".join(lines)

    return rendered_tables


def truth_table(kernel, components: dict, keep_order: bool = True, cycles=None):
    results = {}

    for name, obj in components.items():
        if name in results:
            continue

        used_clocks = {}

        def find_clocks(n):
            if not hasattr(n, "data"):
                return
            if n.data == "when_stmt":
                children = getattr(n, "children", [])
                clk_token = children[0] if children else None
                edge_token = None
                for ch in children[1:]:
                    if hasattr(ch, "value") and ch.value in (
                        "posedge",
                        "negedge",
                        "rising",
                        "falling",
                    ):
                        edge_token = ch
                        break
                if clk_token and hasattr(clk_token, "value") and edge_token:
                    raw = edge_token.value
                    edge = "posedge" if raw in ("posedge", "rising") else "negedge"
                    used_clocks.setdefault(clk_token.value, set()).add(edge)
            if hasattr(n, "children"):
                for c in n.children:
                    find_clocks(c)

        if hasattr(obj, "body"):
            bodies = obj.body if isinstance(obj.body, list) else [obj.body]
            for stmt in bodies:
                find_clocks(stmt)

        clock_objs = {}
        for c_name in used_clocks:
            try:
                c_val = kernel.context.lookup(c_name)
                if hasattr(c_val, "vtype") and c_val.vtype == "clock":
                    clock_objs[c_name] = c_val.data
            except Exception:
                pass

        is_seq = len(clock_objs) > 0
        input_names = obj.input_names if keep_order else sorted(obj.input_names)
        output_names = obj.output_names if keep_order else sorted(obj.output_names)
        input_size = len(input_names)

        num_rows = (
            (cycles if cycles is not None else (1 << input_size if input_size > 0 else 8))
            if is_seq
            else (1 << input_size if input_size > 0 else 1)
        )

        _init_inputs = {inp: 0 for inp in input_names}
        _persistent_result = obj(kernel, **_init_inputs)
        _live_ctx = getattr(_persistent_result, "_ctx", None)

        def _collect_when_blocks(ctx):
            blocks = []
            if ctx is None:
                return blocks
            ctx_vars = getattr(ctx, "vars", {})
            for var_val in ctx_vars.values():
                actual = var_val.data if hasattr(var_val, "data") else var_val
                if hasattr(actual, "evaluate") and hasattr(actual, "commit"):
                    blocks.append(actual)
            if hasattr(ctx, "when_blocks"):
                blocks.extend(ctx.when_blocks)
            return blocks

        live_when_blocks = _collect_when_blocks(_live_ctx)

        input_types = {}
        output_types = {}
        for k in input_names:
            if k in clock_objs:
                edges = used_clocks.get(k, set())
                parts = sorted("↑posedge" if e == "posedge" else "↓negedge" for e in edges)
                input_types[k] = "clk " + "/".join(parts)
            else:
                input_types[k] = "in"
        for k in output_names:
            output_types[k] = "reg" if is_seq else "out"

        ttable_inputs = []
        ttable_outputs = []

        for i in range(num_rows):
            map_input = {}
            if input_size > 0:
                bin_val = i % (1 << input_size)
                bits = [(bin_val >> j) & 1 for j in reversed(range(input_size))]
                map_input = {inp: bits[idx] for idx, inp in enumerate(input_names)}

            if not map_input and not is_seq:
                raise ValueError(f"Truth table '{name}' has no inputs.")

            if is_seq and _live_ctx is not None:
                for inp_name, inp_val in map_input.items():
                    try:
                        _live_ctx.assign(inp_name, inp_val)
                    except Exception:
                        _live_ctx.declare(inp_name, inp_val)
            elif not is_seq:
                result = obj(kernel, **map_input)
                _live_ctx = getattr(result, "_ctx", None)
                _persistent_result = result

            if is_seq:
                for c_obj in clock_objs.values():
                    c_obj.tick()

            map_output = {}
            for out in output_names:
                val = 0
                try:
                    raw = _persistent_result.__dot__(out)
                    val = raw.data if hasattr(raw, "data") else raw
                except Exception:
                    if _live_ctx is not None:
                        try:
                            v = _live_ctx.lookup(out)
                            val = v.data if hasattr(v, "data") else v
                        except Exception:
                            val = 0
                map_output[out] = val

            display_input = dict(map_input)
            for c_name, c_obj in clock_objs.items():
                state = getattr(c_obj, "state", None)
                tick = getattr(c_obj, "tick_count", "?")
                trigger_edges = used_clocks.get(c_name, set())
                if "posedge" in trigger_edges:
                    sym = "↑"
                elif "negedge" in trigger_edges:
                    sym = "↓"
                else:
                    sym = "↑" if state else "↓"
                display_input[c_name] = f"{sym} (t={tick})"

            ttable_inputs.append(display_input)
            ttable_outputs.append(map_output)

        clock_col_names = list(clock_objs.keys())
        plain_input_names = [k for k in input_names if k not in clock_objs]
        ordered_input_names = plain_input_names + clock_col_names
        ttable_inputs_ordered = [{k: row[k] for k in ordered_input_names if k in row} for row in ttable_inputs]

        clock_cols = {c: ("↑" if "posedge" in used_clocks.get(c, set()) else "↓") for c in clock_col_names}

        for c_name in clock_col_names:
            edges = used_clocks.get(c_name, set())
            parts = sorted("↑posedge" if e == "posedge" else "↓negedge" for e in edges)
            input_types[c_name] = "clk " + "/".join(parts)

        results[name] = {
            "input": ttable_inputs_ordered,
            "output": ttable_outputs,
            "clock_cols": clock_cols,
            "input_types": {k: input_types.get(k, "in") for k in ordered_input_names},
            "output_types": output_types,
        }

    return results


class TablePlugin(Plugin):
    name = "table"
    deps = ["gate", "circuit", "expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "table",
            BuiltinFunction("table", TablePlugin.exec_table),
            "builtin_function",
            readonly=True,
        )

    def exec_table(kernel, node):

        components = dict()
        params = {}

        for child in node.children[1:]:
            if not hasattr(child, "data"):
                continue

            if child.data == "arg":
                inner = child.children[0]
                val = kernel.execute(inner)

                if hasattr(val, "vtype") and val.vtype in ("gate", "circuit"):
                    name = val.name if hasattr(val, "name") else str(val)
                    components[name] = val
                elif isinstance(val, Value):
                    components[val.data] = val
                else:
                    components[str(val)] = val

            elif child.data == "named_arg":
                name = child.children[0].value
                value_node = child.children[1]
                val = kernel.execute(value_node)

                if name in ("keep_order", "cycles"):
                    if isinstance(val, Value):
                        params[name] = val.data
                    else:
                        params[name] = val
                else:
                    raise TypeError(f"table() got an unexpected keyword argument '{name}'.")

        if not components:
            raise TypeError(f"table() missing required positional argument: 'components'")

        ttables = truth_table(kernel, components, **params)
        render = render_truth_table(ttables)

        return TruthTable(render)
