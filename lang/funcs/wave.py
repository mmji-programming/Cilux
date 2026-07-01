from core.plugin import Plugin, Value
from lang.types._str import (
    StringValue,
)
from lang.types.builtin import (
    BuiltinFunction,
)
from .utils import Path, to_svg, to_png, SUPPORTED_EXTENSIONS


class WaveResult(Value):
    def __init__(self, output_wave: str):
        self.data = output_wave
        self.vtype = "wave-result"

    def __repr__(self):
        return self.data

    def __export__(self, path: Path) -> None:

        if not self.data:
            raise ValueError("Wave result is empty.")

        suffix = path.suffix.lower()

        if suffix == ".svg":
            to_svg(text=self.data, filename=path)

        elif suffix == ".png":
            to_png(text=self.data, filename=path)

        else:
            raise ValueError(f"Unsupported extension '{suffix}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}")


def render_waveform(
    samples: dict,
    rising_edge=None,
    falling_edge=None,
    match_edges=False,
    gap=0,
    clock_signals=None,
) -> str:

    if not samples:
        return ""

    labels = list(samples.keys())
    max_len = max(len(vals) for vals in samples.values())

    r_top, r_bot = ("┌", "┘") if rising_edge is None else rising_edge
    f_top, f_bot = ("┐", "└") if falling_edge is None else falling_edge

    max_label_len = len(max(labels, key=len)) if labels else 0
    prefix_len = max_label_len + 1

    all_transitions = set()
    if match_edges:
        for label, vals in samples.items():
            if clock_signals and label not in clock_signals:
                continue

            allowed_edges = clock_signals[label] if isinstance(clock_signals, dict) else {"posedge", "negedge"}

            for i in range(max_len):
                current_val = vals[i] if i < len(vals) else vals[-1]
                next_val = vals[i + 1] if i + 1 < len(vals) else current_val

                if current_val == 0 and next_val == 1 and "posedge" in allowed_edges:
                    all_transitions.add(i)
                elif current_val == 1 and next_val == 0 and "negedge" in allowed_edges:
                    all_transitions.add(i)

    gap_line = ""
    if gap > 0:
        gap_line = " " * prefix_len
        if match_edges:
            gap_chars = list(gap_line + " " * (max_len * 2))
            for i in all_transitions:
                col_idx = prefix_len + (i * 2) + 1
                if col_idx < len(gap_chars):
                    gap_chars[col_idx] = "┆"
            gap_line = "".join(gap_chars[: prefix_len + max_len * 2])

    lines = []
    header = " " * prefix_len
    for i in range(max_len):
        header += str(i % 10) + " "
    lines.append(header)

    for idx, label in enumerate(labels):
        if idx > 0 and gap > 0:
            for _ in range(gap):
                lines.append(gap_line)

        line_top = " " * (max_label_len - len(label)) + label + " "
        line_bottom = " " * prefix_len

        vals = samples[label]

        for i in range(max_len):
            current_val = vals[i] if i < len(vals) else vals[-1]
            next_val = vals[i + 1] if i + 1 < len(vals) else current_val

            line_top += "─" if current_val == 1 else " "
            line_bottom += "─" if current_val == 0 else " "

            if current_val == 0 and next_val == 1:
                line_top += r_top
                line_bottom += r_bot
            elif current_val == 1 and next_val == 0:
                line_top += f_top
                line_bottom += f_bot
            else:
                line_top += "─" if current_val == 1 else " "
                line_bottom += "─" if current_val == 0 else " "

        lines.append(line_top)
        lines.append(line_bottom)

    if match_edges:
        for i in sorted(all_transitions):
            col_idx = prefix_len + (i * 2) + 1

            for row in range(1, len(lines)):
                chars = list(lines[row])
                if len(chars) <= col_idx:
                    chars.extend([" "] * (col_idx - len(chars) + 1))
                if col_idx < len(chars):
                    if chars[col_idx] == "─":
                        chars[col_idx] = "┼"
                    elif chars[col_idx] == " ":
                        chars[col_idx] = "┆"
                lines[row] = "".join(chars)

    return "\n".join(lines)


class WavePlugin(Plugin):
    name = "wave"
    deps = ["str", "gate", "circuit", "dict", "expr"]
    grammar = r""""""
    contributes = {}

    def on_load(self, kernel):
        kernel.context.declare(
            "wave",
            BuiltinFunction("wave", WavePlugin.exec_wave),
            "builtin_function",
            readonly=True,
        )

    def exec_wave(kernel, node):
        return WavePlugin._exec_wave(kernel, node)

    @staticmethod
    def _exec_wave(kernel, node):
        args, kwargs = _collect_args(kernel, node)
        if not args:
            return StringValue("No circuit specified")

        obj = args[0]

        if not hasattr(obj, "input_names"):
            raise TypeError(f"Expected a circuit or gate, got {type(obj).__name__}")

        samples = kwargs.pop("samples", None)
        if samples is None:
            samples = _random_samples(obj, kwargs.pop("cycle", 10))

        if isinstance(samples, Value):
            samples = samples.data

        used_clocks = {}

        def find_clocks(n):
            if not hasattr(n, "data"):
                return
            if n.data == "when_stmt":
                children = getattr(n, "children", [])
                clk_token = children[0] if len(children) > 0 else None
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
                    clk_name = clk_token.value
                    raw_edge = edge_token.value
                    if raw_edge in ("rising", "posedge"):
                        edge_type = "posedge"
                    elif raw_edge in ("falling", "negedge"):
                        edge_type = "negedge"
                    else:
                        edge_type = raw_edge
                    used_clocks.setdefault(clk_name, set()).add(edge_type)

            if hasattr(n, "children"):
                for c in n.children:
                    find_clocks(c)

        if hasattr(obj, "body"):
            if isinstance(obj.body, list):
                for s in obj.body:
                    find_clocks(s)
            else:
                find_clocks(obj.body)

        clock_objs = {}
        active_edges_map = {}

        possible_clock_names = set(used_clocks.keys())
        if hasattr(obj, "input_names"):
            possible_clock_names.update(obj.input_names)

        for c_name in possible_clock_names:
            try:
                c_val = kernel.context.lookup(c_name)
                if hasattr(c_val, "vtype") and c_val.vtype == "clock":
                    c_obj = c_val.data
                    clock_objs[c_name] = c_obj

                    is_async = not getattr(c_obj, "sync", True)

                    if is_async:
                        if c_name in used_clocks:
                            active_edges_map[c_name] = used_clocks[c_name]
                        else:
                            active_edges_map[c_name] = {"posedge", "negedge"}
                    elif c_name in used_clocks:
                        active_edges_map[c_name] = used_clocks[c_name]
                    else:
                        active_edges_map[c_name] = {"posedge", "negedge"}
            except Exception:
                pass

        is_seq = len(clock_objs) > 0
        resolution = kwargs.pop("resolution", 5)

        cycles_len = len(next(iter(samples.values())))

        when_blocks = []
        if hasattr(obj, "when_blocks"):
            when_blocks = list(obj.when_blocks)
        elif hasattr(obj, "__dict__"):
            for attr_val in vars(obj).values():
                if isinstance(attr_val, list):
                    for item in attr_val:
                        if hasattr(item, "evaluate") and hasattr(item, "commit"):
                            when_blocks.append(item)
                elif hasattr(attr_val, "evaluate") and hasattr(attr_val, "commit"):
                    when_blocks.append(attr_val)

        _init_inputs = {k: samples[k][0] for k in obj.input_names if k in samples}
        _init_result = obj(kernel, **_init_inputs)
        _live_ctx = getattr(_init_result, "_ctx", None)

        live_when_blocks = []
        if _live_ctx is not None:
            ctx_vars = getattr(_live_ctx, "vars", {})
            for var_val in ctx_vars.values():
                actual = var_val.data if hasattr(var_val, "data") else var_val
                if hasattr(actual, "evaluate") and hasattr(actual, "commit"):
                    live_when_blocks.append(actual)
            if hasattr(_live_ctx, "when_blocks"):
                live_when_blocks.extend(_live_ctx.when_blocks)

        for i in range(cycles_len):
            inputs = {k: samples[k][i] for k in obj.input_names if k in samples}

            result = obj(kernel, **inputs)
            live_ctx = getattr(result, "_ctx", None)

            if is_seq:
                for c_obj in clock_objs.values():
                    if hasattr(c_obj, "tick"):
                        c_obj.tick()

            if live_when_blocks:
                for wb in live_when_blocks:
                    wb.evaluate()
                    wb.commit()
            elif live_ctx is not None:
                ctx_vars = getattr(live_ctx, "vars", {})
                for var_val in ctx_vars.values():
                    actual = var_val.data if hasattr(var_val, "data") else var_val
                    if hasattr(actual, "evaluate") and hasattr(actual, "commit"):
                        actual.evaluate()
                        actual.commit()

            if live_ctx is not None:
                for out in obj.output_names:
                    try:
                        v = live_ctx.lookup(out)
                        val = v.data if hasattr(v, "data") else v
                    except Exception:
                        val = 0
                    samples.setdefault(out, []).append(val)
            else:
                for out in obj.output_names:
                    samples.setdefault(out, []).append(0)

        if is_seq:
            base_period = min(c.period for c in clock_objs.values())

            stretched_samples = {}
            for k, vals in samples.items():
                stretched = []
                for v in vals:
                    stretched.extend([v] * resolution)
                stretched_samples[k] = stretched

            total_len = len(next(iter(stretched_samples.values())))

            for c_name, c_obj in clock_objs.items():
                exact_visual_p = (c_obj.period / base_period) * resolution
                visual_p = max(2, round(exact_visual_p))

                high_c = round(visual_p * (c_obj.duty / 100.0))

                if c_obj.duty > 0 and high_c == 0:
                    high_c = 1
                if c_obj.duty < 100 and high_c == visual_p:
                    high_c = visual_p - 1

                clk_wave = []
                for t in range(total_len):
                    phase = t % visual_p
                    clk_wave.append(1 if phase < high_c else 0)

                new_s = {c_name: clk_wave}
                new_s.update(stretched_samples)
                stretched_samples = new_s

            samples = stretched_samples
            if active_edges_map:
                kwargs.setdefault("match_edges", False)
            kwargs["clock_signals"] = active_edges_map

        for key in ("rising_edge", "falling_edge"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = (kwargs[key], kwargs[key])

        return WaveResult(render_waveform(samples, **kwargs))


def _collect_args(kernel, node):
    args, kwargs = [], {}

    for arg_node in node.children[1:]:
        arg_content = arg_node.children[0]

        if arg_content.data == "named_arg":
            key_token, value_node = arg_content.children
            key = key_token.value
            value_object = kernel.execute(value_node)
            kwargs[key] = _unwrap_value(value_object)
        else:
            value_object = kernel.execute(arg_content)
            args.append(value_object)

    return args, kwargs


def _unwrap_value(value_obj):
    if isinstance(value_obj, Value):
        return value_obj.data
    return value_obj


def _random_samples(obj, cycles):
    import random

    inputs = getattr(obj, "input_names", getattr(obj, "input", []))
    samples = {}

    for inp in inputs:
        name = inp.name if hasattr(inp, "name") else str(inp)
        samples[name] = [random.choice([0, 1]) for _ in range(cycles)]

    return samples
