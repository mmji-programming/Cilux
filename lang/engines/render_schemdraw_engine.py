from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple, Any

import matplotlib

matplotlib.use("Agg")

import schemdraw
import schemdraw.elements as elm
from schemdraw import logic
from schemdraw.segments import SegmentPoly

Signal = Any
Instance = Any
DB = Any


_GATE_MAP: Set[str] = {"AND", "NAND", "OR", "NOR", "XOR", "XNOR", "NOT", "BUF"}
_MUX_TYPES: Set[str] = {"MUX2", "MUX"}

# Aesthetics & Scale Configuration
_PORT_COLOR = "#1a73e8"
_WIRE_COLOR = "#2c3e50"
_CONST_COLOR = "#7f8c8d"
_LABEL_COLOR = "#27ae60"
_UNDRIVEN_COLOR = "#c0392b"
LW_WIRE = 1.5


def _topo_sort(db: DB) -> List[str]:
    dep: Dict[str, Set[str]] = {iid: set() for iid in db.instances}
    for iid, inst in db.instances.items():
        roles = getattr(inst, "port_roles", {})
        for port, sig in inst.inputs.items():
            if roles.get(port) in ("clock", "reset", "set", "preset"):
                continue
            if sig and sig.driver and sig.driver.inst_id != "TOP":
                d = sig.driver.inst_id
                if d in db.instances and d != iid:
                    dep[iid].add(d)
    order: List[str] = []
    visited: Set[str] = set()
    temp: Set[str] = set()

    def visit(n: str) -> None:
        if n in visited or n in temp:
            return
        temp.add(n)
        for d in dep[n]:
            visit(d)
        temp.discard(n)
        visited.add(n)
        order.append(n)

    for iid in db.instances:
        visit(iid)
    return order


def _assign_columns(order: List[str], db: DB) -> Dict[str, int]:
    col: Dict[str, int] = {}
    for iid in order:
        inst = db.instances[iid]
        roles = getattr(inst, "port_roles", {})
        mx = -1
        for port, sig in inst.inputs.items():
            if roles.get(port) in ("clock", "reset", "set", "preset"):
                continue
            if sig and sig.driver and sig.driver.inst_id != "TOP":
                d = sig.driver.inst_id
                if d in col:
                    mx = max(mx, col[d])
        col[iid] = mx + 1
    return col


def _count_data_inputs(inst: Instance) -> int:
    roles = getattr(inst, "port_roles", {})
    return max(
        2,
        sum(1 for p in inst.inputs if roles.get(p) not in ("clock", "reset", "set", "preset")),
    )


def _measure_pin_offsets(
    el_factory, amap: Dict[str, str]
) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Tuple[float, float]]]:
    """Return (port_offsets, extra_anchors).

    port_offsets maps port-name -> (x, y) in element-local coords.
    extra_anchors maps raw anchor-name -> (x, y) for additional anchors
    we need (e.g. "end" for the true wire-tip past a NOT/NAND bubble).
    """
    scratch = schemdraw.Drawing(show=False)
    placed = scratch.add(el_factory().at((0, 0)))

    offsets: Dict[str, Tuple[float, float]] = {}
    extras: Dict[str, Tuple[float, float]] = {}

    def _read(anchor_name: str) -> Optional[Tuple[float, float]]:
        try:
            ab = placed.absanchors
            if anchor_name in ab:
                p = ab[anchor_name]
                return (float(p[0]), float(p[1]))
        except Exception:
            pass
        try:
            an = placed.anchors
            if anchor_name in an:
                p = an[anchor_name]
                return (float(p[0]), float(p[1]))
        except Exception:
            pass
        return None

    for port_name, anchor_name in amap.items():
        pos = _read(anchor_name)
        if pos is not None:
            offsets[port_name] = pos

    for extra_name in ("end", "start"):
        pos = _read(extra_name)
        if pos is not None:
            extras[extra_name] = pos

    return offsets, extras


def _build_gate(mod: str, inst: Instance):
    kind = mod.upper()
    n = _count_data_inputs(inst)

    def factory():
        if kind == "NOT":
            return logic.Not()
        if kind == "BUF":
            return logic.Buf()
        if kind == "AND":
            return logic.And(inputs=n)
        if kind == "NAND":
            return logic.Nand(inputs=n)
        if kind == "OR":
            return logic.Or(inputs=n)
        if kind == "NOR":
            return logic.Nor(inputs=n)
        if kind == "XOR":
            return logic.Xor(inputs=n)
        if kind == "XNOR":
            return logic.Xnor(inputs=n)
        return logic.And(inputs=n)

    if kind in ("NOT", "BUF"):
        in_ports = list(inst.inputs.keys())
        out_ports = list(inst.outputs.keys())
        amap: Dict[str, str] = {}
        if in_ports:
            amap[in_ports[0]] = "in"
        if out_ports:
            amap[out_ports[0]] = "out"
    else:
        roles = getattr(inst, "port_roles", {})
        in_ports = sorted(p for p in inst.inputs if roles.get(p) not in ("clock", "reset", "set", "preset"))
        amap = {p: f"in{i + 1}" for i, p in enumerate(in_ports)}
        out_ports = list(inst.outputs.keys())
        if out_ports:
            amap[out_ports[0]] = "out"

    offsets, extras = _measure_pin_offsets(factory, amap)

    _BUBBLE_GATES = {"NOT", "NAND", "NOR", "XNOR"}
    if out_ports and out_ports[0] in offsets:
        ox, _oy = offsets[out_ports[0]]
        if kind in _BUBBLE_GATES:
            if "end" in extras:
                # "end" is the true outer tip of the output wire/bubble.
                offsets[out_ports[0]] = (extras["end"][0], 0.0)
            else:
                # Measure the bounding box to find the rightmost edge.
                try:
                    sc_bb = schemdraw.Drawing(show=False)
                    p_bb = sc_bb.add(factory().at((0, 0)))
                    bb = p_bb.get_bbox(transform=True)
                    # bb.xmax is the outer tip of the bubble.
                    offsets[out_ports[0]] = (bb.xmax, 0.0)
                except Exception:
                    offsets[out_ports[0]] = (ox + 0.16, 0.0)
        else:
            offsets[out_ports[0]] = (ox, 0.0)

    single_input = kind in ("NOT", "BUF")
    for ip in in_ports:
        if ip in offsets:
            ix, iy = offsets[ip]
            if single_input:
                offsets[ip] = (ix, 0.0)  # centred input — safe to clamp
            else:
                offsets[ip] = (ix, round(iy, 3))  # multi-input — keep real Y

    return factory, amap, offsets, "gate"


def _build_ic(inst: Instance, label: str):
    left_pins: List[elm.IcPin] = []
    right_pins: List[elm.IcPin] = []
    amap: Dict[str, str] = {}
    existing: Set[str] = set()

    def _san(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", name)

    def _uniq(base: str) -> str:
        a, n = base, 0
        while a in existing:
            n += 1
            a = f"{base}_{n}"
        existing.add(a)
        return a

    max_lbl = len(label)
    for p in inst.inputs:
        anc = _uniq(_san(p))
        amap[p] = anc
        left_pins.append(elm.IcPin(name=p, side="left", anchorname=anc))
        max_lbl = max(max_lbl, len(p))
    for p in inst.outputs:
        anc = _uniq(_san(p))
        amap[p] = anc
        right_pins.append(elm.IcPin(name=p, side="right", anchorname=anc))
        max_lbl = max(max_lbl, len(p))

    n_pins = max(len(left_pins), len(right_pins), 1)
    edgepadW = max(1.0, max_lbl * 0.09 + 0.5)
    spacing = max(0.85, min(1.3, 8.0 / n_pins))
    pins_arg = left_pins + right_pins

    def factory():
        return elm.Ic(
            pins=pins_arg,
            edgepadW=edgepadW,
            edgepadH=0.35,
            pinspacing=spacing,
        ).label(label, "center", fontsize=7)

    offsets, _extras = _measure_pin_offsets(factory, amap)
    return factory, amap, offsets, "ic"


def _build_element_for(iid: str, db: DB):
    inst = db.instances[iid]
    mod = inst.module_name
    upper = mod.upper()

    if upper in _GATE_MAP:
        return _build_gate(mod, inst)
    if getattr(inst, "is_sequential", False):
        return _build_ic(inst, upper.replace("_", "\n"))
    if upper in _MUX_TYPES:
        return _build_ic(inst, "MUX")
    return _build_ic(inst, upper)


class _InputFlag(elm.Element):
    """Filled pentagon pointing right (->). Wire exits from the tip."""

    def __init__(self, *, color=_PORT_COLOR, w=0.55, h=0.42, **kw):
        super().__init__(**kw)
        pts = [(0, -h / 2), (w * 0.6, -h / 2), (w, 0), (w * 0.6, h / 2), (0, h / 2)]
        self.segments.append(SegmentPoly(pts, fill=color, color=color, lw=0.5))
        self.anchors["tip"] = (w, 0)


class _OutputFlag(elm.Element):
    """Filled pentagon pointing left (←) - 180 deg flipped. Wire enters from the tip."""

    def __init__(self, *, color=_PORT_COLOR, w=0.55, h=0.42, **kw):
        super().__init__(**kw)
        pts = [(w, -h / 2), (w * 0.4, -h / 2), (0, 0), (w * 0.4, h / 2), (w, h / 2)]
        self.segments.append(SegmentPoly(pts, fill=color, color=color, lw=0.5))
        self.anchors["tip"] = (0, 0)


_ROW_GAP = 1.8
_COL_GAP = 3.8
_PORT_SW = 2.0
_PORT_STEP = 2.2

_BUS_BASE = 1.5
_BUS_STEP = 0.55
_BUS_MAX = 60


def _sig_label(sig: Signal) -> str:
    if sig is None:
        return "?"
    if sig.is_constant:
        return str(sig.const_value)
    return sig.id


def _is_anon(label: str) -> bool:
    return bool(re.match(r"^(sig_|CONST_)", label))


def render_schemdraw(
    db: DB,
    model_name: str = "top",
    output: Optional[str] = None,
    dpi: int = 150,
    show: bool = False,
    fontsize: int = 9,
) -> schemdraw.Drawing:

    order = _topo_sort(db)
    col_map = _assign_columns(order, db)
    n_cols = (max(col_map.values()) + 1) if col_map else 1

    sig_driver: Dict[str, Tuple[str, str]] = {}
    for iid, inst in db.instances.items():
        for port, sig in inst.outputs.items():
            if sig:
                sig_driver[sig.id] = (iid, port)

    # Calculate global fanout to determine which signals need Branch Junction Dots
    fanout_map: Dict[str, int] = {}
    for iid, inst in db.instances.items():
        for port, sig in inst.inputs.items():
            if sig:
                fanout_map[sig.id] = fanout_map.get(sig.id, 0) + 1
    for port, sig in db.top_outputs.items():
        if sig:
            fanout_map[sig.id] = fanout_map.get(sig.id, 0) + 1

    cell_info: Dict[str, Any] = {}
    for iid in order:
        factory, amap, offsets, kind = _build_element_for(iid, db)
        sc = schemdraw.Drawing(show=False)
        p = sc.add(factory().at((0, 0)))
        bb = p.get_bbox(transform=True)
        cell_info[iid] = (
            factory,
            amap,
            offsets,
            kind,
            bb.xmax - bb.xmin,
            bb.ymax - bb.ymin,
            bb.ymin,
            bb.ymax,
        )

    max_cell_width = max((v[4] for v in cell_info.values()), default=2.0)
    col_width = max_cell_width + _COL_GAP

    cell_x: Dict[str, float] = {}
    cell_y: Dict[str, float] = {}
    col_cursor: Dict[int, float] = {}

    for iid in order:
        col = col_map.get(iid, 0)
        _, _, _, _, w, h, ymin_l, ymax_l = cell_info[iid]
        top = col_cursor.get(col, 0.0)
        at_y = top - ymax_l
        cell_x[iid] = col * col_width
        cell_y[iid] = at_y
        col_cursor[col] = at_y + ymin_l - _ROW_GAP

    in_port_x = -(col_width * 0.4 + _PORT_SW + 1.0)
    out_port_x = n_cols * col_width + 1.5

    d = schemdraw.Drawing(fontsize=fontsize, show=show)

    placed_el: Dict[str, Any] = {}
    placed_offs: Dict[str, Dict[str, Tuple[float, float]]] = {}

    for iid in order:
        factory, amap, offsets, kind, *_ = cell_info[iid]
        x, y = cell_x[iid], cell_y[iid]
        el = d.add(factory().at((x, y)))
        placed_el[iid] = el

        live_offs: Dict[str, Tuple[float, float]] = {}
        try:
            ab = el.absanchors
            for port_name, anchor_name in amap.items():
                if anchor_name in ab:
                    p = ab[anchor_name]
                    live_offs[port_name] = (float(p[0]) - x, float(p[1]) - y)
        except Exception:
            pass

        # Fall back to scratch offsets for any port not found in absanchors,
        # and for the output pin of bubble gates where we already computed the
        # corrected tip position in _build_gate.
        _BUBBLE_GATES = {"NOT", "NAND", "NOR", "XNOR"}
        inst_mod = db.instances[iid].module_name.upper()
        out_ports_iid = list(db.instances[iid].outputs.keys())

        for port_name, off in offsets.items():
            if port_name not in live_offs:
                live_offs[port_name] = off
            elif port_name in out_ports_iid and inst_mod in _BUBBLE_GATES:
                # For bubble-gate outputs, trust the corrected scratch offset

                live_offs[port_name] = off

        placed_offs[iid] = live_offs

    _bus_n = {"i": 0}

    # Input port stubs
    port_anchor: Dict[str, Tuple[float, float]] = {}
    for i, (pname, sig) in enumerate(db.top_inputs.items()):
        y = -i * _PORT_STEP
        flag_x = in_port_x + _PORT_SW
        d.add(elm.Line().at((in_port_x, y)).right(_PORT_SW).color(_PORT_COLOR).linewidth(LW_WIRE))
        d.add(_InputFlag(color=_PORT_COLOR).at((flag_x, y)))
        d.add(elm.Label().at((in_port_x - 0.15, y)).label(pname, loc="left", fontsize=8, color=_PORT_COLOR))
        if sig:
            port_anchor[sig.id] = (flag_x + 0.55, y)

    # Output port stubs
    out_conn: Dict[str, Tuple[float, float]] = {}
    for i, (pname, sig) in enumerate(db.top_outputs.items()):
        y = -i * _PORT_STEP
        d.add(_OutputFlag(color=_PORT_COLOR).at((out_port_x, y)))

        _OUTPUT_FLAG_W = 0.55  # must match _OutputFlag default w
        d.add(
            elm.Label()
            .at((out_port_x + _OUTPUT_FLAG_W + 0.12, y))
            .label(pname, loc="right", fontsize=8, color=_PORT_COLOR)
        )
        if sig:
            out_conn[pname] = (out_port_x, y)

    def _pin_pos(iid: str, port: str) -> Optional[Tuple[float, float]]:
        offs = placed_offs.get(iid, {})
        cx, cy = cell_x.get(iid, 0.0), cell_y.get(iid, 0.0)
        if port in offs:
            dx, dy = offs[port]
            return (cx + dx, cy + dy)

        info = cell_info.get(iid)
        w = info[4] if info else 1.5
        inst = db.instances.get(iid)
        if inst and port in getattr(inst, "outputs", {}):
            return (cx + w, cy)
        return (cx, cy)

    drawn_wires: Set[str] = set()
    labeled_sigs: Set[str] = set()
    # Track junction-dot centres already placed to avoid duplicates.
    # Key is a rounded (x, y) tuple so floating-point near-misses merge.
    junction_dots: Set[Tuple[float, float]] = set()

    # For multi-fanout nets: cache the first vx/vx_src chosen so all
    # branches of the same signal share the exact same vertical trunk line.
    # Key: sig_id  ->  {"vx_A": float, "vx_src_B": float, "lane_B": float,
    #                  "trunk_drawn": bool}
    _trunk_cache: Dict[str, Dict[str, Any]] = {}

    # Tracks which trunk vertical segments have already been drawn to
    # avoid overdrawing: key = (x, y_top, y_bot) rounded to 2dp.
    _drawn_segs: Set[Tuple[float, float, float]] = set()

    #  Obstacle-Aware Channel Router Setup
    #
    # Build a list of component bounding boxes (with margin) so the router
    # can pick vertical-trunk X positions that fall in the clear inter-column
    # channels, never clipping through a gate or IC body.
    #
    # _OBS_MARGIN: extra clearance added on every side of a component bbox.
    _OBS_MARGIN = 0.18

    # Collect all component bboxes as (x_lo, y_lo, x_hi, y_hi).
    _obstacles: List[Tuple[float, float, float, float]] = []
    for _iid in order:
        if _iid not in placed_el:
            continue
        _el = placed_el[_iid]
        try:
            _bb = _el.get_bbox(transform=True)
            _obstacles.append(
                (
                    _bb.xmin - _OBS_MARGIN,
                    _bb.ymin - _OBS_MARGIN,
                    _bb.xmax + _OBS_MARGIN,
                    _bb.ymax + _OBS_MARGIN,
                )
            )
        except Exception:
            _cx = cell_x.get(_iid, 0.0)
            _cy = cell_y.get(_iid, 0.0)
            _info = cell_info.get(_iid)
            _cw = _info[4] if _info else max_cell_width
            _ch = _info[5] if _info else 1.5
            _obstacles.append(
                (
                    _cx - _OBS_MARGIN,
                    _cy - _ch - _OBS_MARGIN,
                    _cx + _cw + _OBS_MARGIN,
                    _cy + _OBS_MARGIN,
                )
            )

    def _x_clear(x: float, y_lo: float, y_hi: float) -> bool:
        """Return True if the vertical line x in [y_lo, y_hi] avoids all obstacles."""
        y_lo_c, y_hi_c = (y_lo, y_hi) if y_lo <= y_hi else (y_hi, y_lo)
        for ox0, oy0, ox1, oy1 in _obstacles:
            if ox0 <= x <= ox1:
                if not (y_hi_c < oy0 or y_lo_c > oy1):
                    return False
        return True

    def _find_clear_vx(
        x_lo: float,
        x_hi: float,
        y_lo: float,
        y_hi: float,
        preferred: float,
        step: float = 0.10,
    ) -> float:
        """
        Find the X in [x_lo, x_hi] closest to preferred where a vertical
        segment spanning [y_lo, y_hi] does not intersect any obstacle bbox.
        """
        if _x_clear(preferred, y_lo, y_hi):
            return preferred
        lo, hi = preferred, preferred
        max_steps = max(2, int((x_hi - x_lo) / step) + 2)
        for _ in range(max_steps):
            lo = max(x_lo, lo - step)
            hi = min(x_hi, hi + step)
            if _x_clear(lo, y_lo, y_hi):
                return lo
            if _x_clear(hi, y_lo, y_hi):
                return hi
            if lo <= x_lo and hi >= x_hi:
                break
        return (x_lo + x_hi) / 2.0

    def _chan_bounds(col: int) -> Tuple[float, float]:
        """Return (x_lo, x_hi) of the inter-column channel to the RIGHT of col."""
        if col == -1:
            x_lo = in_port_x + _PORT_SW + _OBS_MARGIN
            x_hi = 0.0 - _OBS_MARGIN
        else:
            x_lo = col * col_width + max_cell_width + _OBS_MARGIN
            x_hi = (col + 1) * col_width - _OBS_MARGIN
        if x_hi < x_lo + 0.05:
            mid = (x_lo + x_hi) / 2.0
            x_lo, x_hi = mid - 0.025, mid + 0.025
        return x_lo, x_hi

    _chan_track: Dict[int, int] = {}

    def _alloc_track(col: int) -> int:
        t = _chan_track.get(col, 0)
        _chan_track[col] = t + 1
        return t

    def _seg_key(x: float, y0: float, y1: float) -> Tuple[float, float, float]:
        lo, hi = (y0, y1) if y0 <= y1 else (y1, y0)
        return (round(x, 2), round(lo, 2), round(hi, 2))

    def _vseg(x: float, y0: float, y1: float) -> None:
        """Draw a vertical segment only if not already drawn (dedup by key)."""
        if abs(y0 - y1) < 0.005:
            return
        k = _seg_key(x, y0, y1)
        if k not in _drawn_segs:
            _drawn_segs.add(k)
            d.add(elm.Line().at((x, y0)).to((x, y1)).color(_WIRE_COLOR).linewidth(LW_WIRE))

    def _dot_px(x: float, y: float) -> Tuple[float, float]:
        """Canonical grid key for junction-dot deduplication (2-dp precision)."""
        return (round(x, 2), round(y, 2))

    def _add_junction(x: float, y: float, radius: float = 0.07) -> None:
        key = _dot_px(x, y)
        if key not in junction_dots:
            junction_dots.add(key)
            d.add(elm.Dot(radius=radius).at((x, y)).color(_WIRE_COLOR))

    def _seg(x0, y0, x1, y1, lw=LW_WIRE):
        d.add(elm.Line().at((x0, y0)).to((x1, y1)).color(_WIRE_COLOR).linewidth(lw))

    def _lbl(x, y, text):
        d.add(elm.Label().at((x, y + 0.15)).label(text, fontsize=5, color=_LABEL_COLOR))

    def _route_lane(
        sig_id: str,
        sx: float,
        sy: float,
        dx: float,
        dy: float,
        src_col: int,
        dst_col: int,
        label: str,
        anon: bool,
    ):
        _bus_n["i"] += 1
        idx = _bus_n["i"]
        is_multi = fanout_map.get(sig_id, 0) > 1

        # A: adjacent column, small vertical gap
        if dst_col == src_col + 1 and abs(sy - dy) < _ROW_GAP * 3.0:
            cache = _trunk_cache.setdefault(sig_id, {})
            if "vx_A" in cache:
                vx = cache["vx_A"]
            else:
                x_lo_chan, x_hi_chan = _chan_bounds(src_col)
                track = _alloc_track(src_col)
                chan_w = x_hi_chan - x_lo_chan
                preferred = x_lo_chan + chan_w * (0.40 + 0.08 * (track % 6))
                preferred = min(preferred, x_hi_chan - 0.05)
                vx = _find_clear_vx(
                    x_lo_chan,
                    x_hi_chan,
                    min(sy, dy),
                    max(sy, dy),
                    preferred,
                )
                cache["vx_A"] = vx

            # Straight wire (same Y): just connect directly, no jog needed.
            if abs(sy - dy) < 0.01:
                _seg(sx, sy, dx, dy)
                if is_multi:
                    _add_junction(sx, sy)
                return

            # Three-segment L-route: horizontal -> vertical -> horizontal
            _seg(sx, sy, vx, sy)
            _vseg(vx, sy, dy)
            _seg(vx, dy, dx, dy)

            # Endpoint dots.
            for ex, ey in [(sx, sy), (dx, dy)]:
                key = _dot_px(ex, ey)
                if key not in junction_dots:
                    d.add(elm.Dot(radius=0.04).at((ex, ey)).color(_WIRE_COLOR))

            # Junction dots for multi-fanout.
            if is_multi:
                _add_junction(sx, sy)
                _add_junction(vx, sy)
                _add_junction(vx, dy)

            if not anon:
                _lbl((sx + vx) / 2, sy, label)

        # B: long route / loop / skipping columns
        else:
            cache = _trunk_cache.setdefault(sig_id, {})
            if "vx_src_B" in cache:
                vx_src = cache["vx_src_B"]
                lane = cache["lane_B"]
            else:
                if _obstacles:
                    _obs_y_floor = min(oy0 for _, oy0, _, _ in _obstacles)
                else:
                    _obs_y_floor = _BUS_BASE
                lane_index = idx % _BUS_MAX
                lane = _obs_y_floor - _BUS_STEP * (1 + lane_index)

                x_lo_src, x_hi_src = _chan_bounds(src_col)
                track_src = _alloc_track(src_col)
                chan_w_src = x_hi_src - x_lo_src
                pref_src = x_lo_src + chan_w_src * (0.40 + 0.08 * (track_src % 6))
                pref_src = min(pref_src, x_hi_src - 0.05)
                vx_src = _find_clear_vx(
                    x_lo_src,
                    x_hi_src,
                    lane,
                    sy,
                    pref_src,
                )
                cache["vx_src_B"] = vx_src
                cache["lane_B"] = lane

            if dst_col == n_cols:
                x_lo_dst = n_cols * col_width - _OBS_MARGIN
                x_hi_dst = out_port_x - _OBS_MARGIN
            else:
                x_lo_dst, x_hi_dst = _chan_bounds(dst_col - 1)

            track_dst = _alloc_track(dst_col - 1 if dst_col > 0 else 0)
            chan_w_dst = max(x_hi_dst - x_lo_dst, 0.1)
            pref_dst = x_lo_dst + chan_w_dst * (0.40 + 0.08 * (track_dst % 6))
            pref_dst = min(pref_dst, x_hi_dst - 0.05)
            vx_dst = _find_clear_vx(
                x_lo_dst,
                x_hi_dst,
                lane,
                dy,
                pref_dst,
            )

            # Five-segment route: src -> trunk-vertical -> bus lane -> dst-vertical -> dst
            _seg(sx, sy, vx_src, sy)
            _vseg(vx_src, sy, lane)
            _seg(vx_src, lane, vx_dst, lane)
            _vseg(vx_dst, lane, dy)
            _seg(vx_dst, dy, dx, dy)

            # Endpoint dots at source and destination.
            for ex, ey in [(sx, sy), (dx, dy)]:
                key = _dot_px(ex, ey)
                if key not in junction_dots:
                    d.add(elm.Dot(radius=0.04).at((ex, ey)).color(_WIRE_COLOR))

            # Junction dots: source origin, trunk corner, and destination branch.
            if is_multi:
                _add_junction(sx, sy)
                _add_junction(vx_src, sy)
                _add_junction(vx_dst, dy)

            if not anon:
                _lbl((vx_src + vx_dst) / 2, lane, label)

    def _draw_const(dx, dy, val):
        _seg(dx - 0.5, dy, dx, dy, lw=LW_WIRE)
        d.add(elm.Dot(radius=0.05).at((dx, dy)).color(_WIRE_COLOR))
        d.add(elm.Label().at((dx - 0.55, dy + 0.18)).label(f"={val}", fontsize=6, color=_CONST_COLOR))

    def _draw_undriven(dx, dy, label):
        d.add(elm.Line().at((dx - 0.5, dy)).to((dx, dy)).color(_UNDRIVEN_COLOR).linewidth(LW_WIRE))
        d.add(elm.Dot(radius=0.05).at((dx, dy)).color(_UNDRIVEN_COLOR))
        d.add(elm.Label().at((dx - 0.55, dy + 0.22)).label(f"⚠ {label} UNDRIVEN", fontsize=6, color=_UNDRIVEN_COLOR))

    def _route_sig(sig: Signal, dst_iid: str, dst_port: str) -> None:
        if sig is None:
            return
        key = f"{sig.id}->{dst_iid}.{dst_port}"
        if key in drawn_wires:
            return
        drawn_wires.add(key)

        dst = _pin_pos(dst_iid, dst_port)
        if dst is None:
            return
        dx, dy = dst

        label = _sig_label(sig)
        anon = _is_anon(label)
        if not anon:
            if sig.id in labeled_sigs:
                anon = True
            else:
                labeled_sigs.add(sig.id)

        if sig.is_constant:
            _draw_const(dx, dy, sig.const_value)
            return

        src_pos: Optional[Tuple[float, float]] = None
        src_col = -1
        if sig.id in port_anchor:
            src_pos = port_anchor[sig.id]
            src_col = -1
        elif sig.id in sig_driver:
            src_iid, src_port = sig_driver[sig.id]
            src_pos = _pin_pos(src_iid, src_port)
            src_col = col_map.get(src_iid, 0)

        if src_pos is None:
            _draw_undriven(dx, dy, label)
            return

        sx, sy = src_pos
        if abs(sx - dx) < 0.02 and abs(sy - dy) < 0.02:
            d.add(elm.Dot(radius=0.05).at((dx, dy)).color(_WIRE_COLOR))
            return

        dst_col = col_map.get(dst_iid, 0)
        _route_lane(sig.id, sx, sy, dx, dy, src_col, dst_col, label, anon)

    for iid, inst in db.instances.items():
        if iid not in placed_el:
            continue
        for port, sig in inst.inputs.items():
            _route_sig(sig, iid, port)

    for pname, sig in db.top_outputs.items():
        if sig is None:
            continue
        conn = out_conn.get(pname)
        if conn is None:
            continue
        ox, oy = conn

        if sig.is_constant:
            _draw_const(ox, oy, sig.const_value)
            continue

        src_pos: Optional[Tuple[float, float]] = None
        src_col = -1
        if sig.id in port_anchor:
            src_pos = port_anchor[sig.id]
            src_col = -1
        elif sig.id in sig_driver:
            src_iid, src_port = sig_driver[sig.id]
            src_pos = _pin_pos(src_iid, src_port)
            src_col = col_map.get(src_iid, 0)

        if src_pos is None:
            _draw_undriven(ox, oy, pname)
            continue

        sx, sy = src_pos
        _route_lane(sig.id, sx, sy, ox, oy, src_col, n_cols, pname, False)

    try:
        bb = d.get_bbox()
        title_y = bb.ymax + 0.8
    except Exception:
        title_y = 2.0

    d.add(
        elm.Label()
        .label(f"Module: {model_name}  ({len(db.instances)} cells)", loc="top")
        .at((col_width * n_cols / 2, title_y))
    )

    if output:
        d.save(output, dpi=dpi)

    return d


def draw_schematic(
    db: DB,
    model_name: str = "top",
    output: Optional[str] = None,
    dpi: int = 150,
) -> str:
    render_schemdraw(db, model_name=model_name, output=output, dpi=dpi)
    return output or ""


def show_schematic(
    db: DB,
    model_name: str = "top",
    fontsize: int = 9,
) -> None:

    import matplotlib.pyplot as plt

    prev_backend = matplotlib.get_backend()
    gui_backend = None
    for candidate in ("QtAgg", "Qt5Agg", "TkAgg", "MacOSX"):
        try:
            matplotlib.use(candidate, force=True)
            gui_backend = candidate
            break
        except Exception:
            continue

    if gui_backend is None:
        matplotlib.use(prev_backend, force=True)
        raise RuntimeError(
            "No interactive matplotlib backend available "
            "(tried QtAgg/Qt5Agg/TkAgg/MacOSX) — cannot open a GUI window. "
            "Install PyQt5/PySide6 or python3-tk, or run where a display is available."
        )

    try:
        d = render_schemdraw(db, model_name=model_name, output=None, show=True, fontsize=fontsize)
        d.draw()
        plt.show()
    finally:
        matplotlib.use(prev_backend, force=True)
