import heapq
from collections import defaultdict, deque


def find_back_edges(G):
    color = {n: 0 for n in G}
    back = set()
    forward_seen = set()

    def dfs(u):
        color[u] = 1
        for v in sorted(G[u]["to"]):
            if v not in G:
                continue
            if v == u:
                back.add((u, v))
            elif color[v] == 1:
                back.add((u, v))
            elif color[v] == 0:
                forward_seen.add((u, v))
                dfs(v)
            else:
                forward_seen.add((u, v))
        color[u] = 2

    import sys

    sys.setrecursionlimit(10_000)
    for n in G:
        if color[n] == 0:
            dfs(n)

    for u, v in list(forward_seen):
        if (v, u) in forward_seen and (v, u) not in back and (u, v) not in back:
            back.add((v, u))

    return back


def assign_layers(G, back_edges):
    dag_out = {}
    for n in G:
        dag_out[n] = {v for v in G[n]["to"] if (n, v) not in back_edges and v != n and v in G}

    dag_in = defaultdict(set)
    for u, vs in dag_out.items():
        for v in vs:
            dag_in[v].add(u)

    layer = {n: 0 for n in G}
    in_count = {n: len(dag_in[n]) for n in G}
    queue = deque(n for n in G if in_count[n] == 0)
    visited = set()

    while queue:
        u = queue.popleft()
        if u in visited:
            continue
        visited.add(u)
        for v in dag_out[u]:
            layer[v] = max(layer[v], layer[u] + 1)
            in_count[v] -= 1
            if in_count[v] == 0:
                queue.append(v)

    for n in G:
        if n not in visited:
            layer[n] = layer.get(n, 0)

    return layer, dag_out


def place_nodes(G, layer, dag_out, back_edges):
    back_set = set(back_edges)

    real_in = defaultdict(set)
    real_out = dag_out

    for u, vs in real_out.items():
        for v in vs:
            real_in[v].add(u)

    back_src = defaultdict(set)
    back_dst = defaultdict(set)
    self_loop_nodes = set()
    for u, v in back_set:
        if u == v:
            self_loop_nodes.add(u)
        else:
            back_src[u].add(v)
            back_dst[v].add(u)

    def in_deg(n):
        extra = 1 if n in self_loop_nodes else 0
        return len(real_in[n]) + len(back_dst[n]) + extra

    def out_deg(n):
        return len(real_out[n]) + len(back_src[n])

    def node_width(n):
        ic, oc = in_deg(n), out_deg(n)
        w = max(5, ic * 2 + 3, oc * 2 + 3, len(G[n]["lable"]) + 4)
        return w + (w % 2 == 0)

    def make_ports(count, bx, w):
        if count == 0:
            return []
        if count == 1:
            return [bx + w // 2]
        step = (w - 2) / (count - 1)
        return [bx + 1 + round(i * step) for i in range(count)]

    max_layer = max(layer.values()) if layer else 0
    nodes_by_layer = defaultdict(list)
    for n, d in layer.items():
        nodes_by_layer[d].append(n)

    max_layer_out_edges = 0
    if nodes_by_layer:
        max_layer_out_edges = max(sum(out_deg(n) for n in l_nodes) for l_nodes in nodes_by_layer.values())

    DYNAMIC_LAYER_H = int(max(7, 7 + (max_layer_out_edges * 0.5)))
    DYNAMIC_NODE_GAP = int(max(5, 5 + (max_layer_out_edges * 0.3)))

    x_pos = {}
    for d in range(max_layer + 1):
        nodes = sorted(nodes_by_layer[d])
        for i, n in enumerate(nodes):
            x_pos[n] = i * (node_width(n) + DYNAMIC_NODE_GAP)

    def barycenter_pass(order):
        for d in order:
            nodes = sorted(nodes_by_layer[d], key=lambda n: x_pos[n])
            new_pos = {}
            for n in nodes:
                refs = []
                refs += [x_pos[v] for v in real_out[n] if v in x_pos]
                refs += [x_pos[p] for p in real_in[n] if p in x_pos]
                refs += [x_pos[v] for v in back_src[n] if v in x_pos]
                refs += [x_pos[v] for v in back_dst[n] if v in x_pos]
                new_pos[n] = sum(refs) / len(refs) if refs else x_pos[n]

            ordered = sorted(nodes, key=lambda n: new_pos[n])
            cx = max(0, int(new_pos[ordered[0]]) - node_width(ordered[0]) // 2)
            for n in ordered:
                w = node_width(n)
                bx = max(cx, int(new_pos[n]) - w // 2)
                x_pos[n] = bx + w / 2
                cx = bx + w + DYNAMIC_NODE_GAP

    for _ in range(4):
        barycenter_pass(range(max_layer + 1))
        barycenter_pass(range(max_layer, -1, -1))

    nodes_info = {}
    for n in G:
        if n not in layer:
            continue
        d = layer[n]
        w = node_width(n)
        bx = int(x_pos[n] - w / 2)
        by = d * DYNAMIC_LAYER_H

        ic = in_deg(n)
        oc = out_deg(n)

        nodes_info[n] = {
            "bx": bx,
            "by": by,
            "w": w + 1,
            "in_ports": make_ports(ic, bx, w),
            "out_ports": make_ports(oc, bx, w),
            "in_ports_q": list(make_ports(ic, bx, w)),
            "out_ports_q": list(make_ports(oc, bx, w)),
        }

    return nodes_info, real_out, real_in, back_src, back_dst


def route_wire(sx, sy, ex, ey, blocked, board, airspace, bounds):
    SMINX, SMAXX, SMINY, SMAXY = bounds
    start = (sx, sy + 2)
    goal = (ex, ey - 2)

    def get_c(x, y):
        return board.get((x, y), " ")

    pq = []
    heapq.heappush(pq, (0, 0, start[0], start[1], 0, 1))
    best = {}
    came_from = {}

    start_state = (start[0], start[1], 0, 1)
    came_from[start_state] = None
    best[start_state] = 0

    found = None

    while pq:
        f, cost, cx, cy, ddx, ddy = heapq.heappop(pq)

        if (cx, cy) == goal:
            found = (cx, cy, ddx, ddy)
            break

        state = (cx, cy, ddx, ddy)
        if best.get(state, float("inf")) < cost:
            continue

        for ndx, ndy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
            nx, ny = cx + ndx, cy + ndy
            if not (SMINX <= nx <= SMAXX and SMINY <= ny <= SMAXY):
                continue
            if (nx, ny) in blocked:
                continue
            if ndx == -ddx and ndy == -ddy and (ddx != 0 or ddy != 0):
                continue

            mv = 1
            if (ddx != 0 or ddy != 0) and (ndx != ddx or ndy != ddy):
                mv += 8
            if ndy == -1:
                mv += 20
            if (nx, ny) in airspace and ndx != 0:
                mv += 200

            cell = get_c(nx, ny)
            if cell != " ":
                if cell in "┌┐└┘┬┴↓" or cell.isalnum():
                    mv += 5000
                elif cell in ("∩", "┼"):
                    mv += 3000
                elif (ndx != 0 and cell == "│") or (ndy != 0 and cell == "─"):
                    mv += 80
                elif (ndx != 0 and cell == "─") or (ndy != 0 and cell == "│"):
                    mv += 1000
                else:
                    mv += 300

            nc = cost + mv
            h = abs(nx - goal[0]) + abs(ny - goal[1])
            nstate = (nx, ny, ndx, ndy)
            if nc < best.get(nstate, float("inf")):
                best[nstate] = nc
                came_from[nstate] = state
                heapq.heappush(pq, (nc + h, nc, nx, ny, ndx, ndy))

    if found is None:
        return None

    path = []
    cur = found
    while cur is not None:
        path.append((cur[0], cur[1]))
        cur = came_from.get(cur)
    path.reverse()

    return [(sx, sy), (sx, sy + 1)] + path + [(ex, ey - 1), (ex, ey)]


def path_to_chars(full_path):
    result = []
    n = len(full_path)
    for i in range(1, n - 1):
        px, py = full_path[i - 1]
        cx, cy = full_path[i]
        nx, ny = full_path[i + 1]
        d1 = (cx - px, cy - py)
        d2 = (nx - cx, ny - cy)

        if i == n - 2:
            result.append((cx, cy, "↓"))
            continue

        if d1 == d2:
            char = "│" if d1[0] == 0 else "─"
        else:
            corners = {
                ((0, 1), (1, 0)): "└",
                ((-1, 0), (0, -1)): "└",
                ((0, 1), (-1, 0)): "┘",
                ((1, 0), (0, -1)): "┘",
                ((0, -1), (1, 0)): "┌",
                ((-1, 0), (0, 1)): "┌",
                ((0, -1), (-1, 0)): "┐",
                ((1, 0), (0, 1)): "┐",
            }
            char = corners.get((d1, d2), "┼")
        result.append((cx, cy, char))
    return result


_CONN = {
    "─": {"R", "L"},
    "│": {"U", "D"},
    "┌": {"R", "D"},
    "┐": {"L", "D"},
    "└": {"R", "U"},
    "┘": {"L", "U"},
    "├": {"R", "U", "D"},
    "┤": {"L", "U", "D"},
    "┬": {"R", "L", "D"},
    "┴": {"R", "L", "U"},
    "┼": {"R", "L", "U", "D"},
    "↓": {"D"},
}
_FROM_CONN = {frozenset(v): k for k, v in _CONN.items()}


def _char_dirs(c):
    return set(_CONN.get(c, set()))


def merge_char(existing, new_char):
    if existing == " ":
        return new_char
    if existing == new_char:
        return existing
    if existing == "↓":
        return "↓"
    if new_char == "↓":
        return "↓"
    if (existing == "─" and new_char == "│") or (existing == "│" and new_char == "─"):
        return "∩"
    if existing == "∩":
        if new_char == "│":
            return "∩"
        if new_char == "─":
            return "∩"
        merged = {"R", "L", "U", "D"}
        merged |= _char_dirs(new_char)
        result = _FROM_CONN.get(frozenset(merged))
        return result if result else "∩"
    if new_char == "∩":
        if existing == "│":
            return "∩"
        if existing == "─":
            return "∩"
        merged = _char_dirs(existing) | {"R", "L", "U", "D"}
        result = _FROM_CONN.get(frozenset(merged))
        return result if result else "∩"
    merged = _char_dirs(existing) | _char_dirs(new_char)
    result = _FROM_CONN.get(frozenset(merged))
    if result:
        return result
    return "┼"


def draw_self_loop(node, info, board, blocked):
    bx, by, w = info["bx"], info["by"], info["w"]

    def set_c(x, y, c):
        board[(x, y)] = merge_char(board.get((x, y), " "), c)

    out_x = bx + w - 1
    out_y = by + 1

    if info["in_ports_q"]:
        in_x = info["in_ports_q"].pop(0)
    else:
        in_x = bx + w // 2
    in_y = by

    loop_right = bx + w + 2
    loop_top = by - 1

    set_c(out_x, out_y, "├")
    for xx in range(out_x + 1, loop_right):
        set_c(xx, out_y, "─")
    set_c(loop_right, out_y, "┘")
    for yy in range(loop_top + 1, out_y):
        set_c(loop_right, yy, "│")
    set_c(loop_right, loop_top, "┐")
    for xx in range(in_x + 1, loop_right):
        set_c(xx, loop_top, "─")
    set_c(in_x, loop_top, "┌")
    for yy in range(loop_top + 1, in_y - 1):
        set_c(in_x, yy, "│")
    set_c(in_x, in_y - 1, "↓")
    if in_x != bx and in_x != bx + w - 1:
        board[(in_x, in_y)] = "┴"


def route_back_wire(sx, sy, ex, ey, blocked, board, airspace, bounds):
    SMINX, SMAXX, SMINY, SMAXY = bounds

    def get_c(x, y):
        return board.get((x, y), " ")

    start = (sx, sy + 2)
    goal = (ex, ey - 2)

    pq = []
    heapq.heappush(pq, (0, 0, start[0], start[1], 0, 1))
    best = {}
    came_from = {}

    start_state = (start[0], start[1], 0, 1)
    came_from[start_state] = None
    best[start_state] = 0

    found = None

    while pq:
        f, cost, cx, cy, ddx, ddy = heapq.heappop(pq)

        if (cx, cy) == goal:
            found = (cx, cy, ddx, ddy)
            break

        state = (cx, cy, ddx, ddy)
        if best.get(state, float("inf")) < cost:
            continue

        for ndx, ndy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
            nx, ny = cx + ndx, cy + ndy
            if not (SMINX <= nx <= SMAXX and SMINY <= ny <= SMAXY):
                continue
            if (nx, ny) in blocked:
                continue
            if ndx == -ddx and ndy == -ddy and (ddx != 0 or ddy != 0):
                continue

            mv = 1
            if (ddx != 0 or ddy != 0) and (ndx != ddx or ndy != ddy):
                mv += 3
            if (nx, ny) in airspace and ndx != 0:
                mv += 150

            cell = get_c(nx, ny)
            if cell != " ":
                if cell in "┌┐└┘┬┴↓" or cell.isalnum():
                    mv += 3000
                elif cell in ("∩", "┼"):
                    mv += 2000
                elif (ndx != 0 and cell == "│") or (ndy != 0 and cell == "─"):
                    mv += 80
                elif (ndx != 0 and cell == "─") or (ndy != 0 and cell == "│"):
                    mv += 1000
                else:
                    mv += 200

            nc = cost + mv
            h = abs(nx - goal[0]) + abs(ny - goal[1])
            nstate = (nx, ny, ndx, ndy)
            if nc < best.get(nstate, float("inf")):
                best[nstate] = nc
                came_from[nstate] = state
                heapq.heappush(pq, (nc + h, nc, nx, ny, ndx, ndy))

    if found is None:
        return None

    path = []
    cur = found
    while cur is not None:
        path.append((cur[0], cur[1]))
        cur = came_from.get(cur)
    path.reverse()

    return [(sx, sy), (sx, sy + 1)] + path + [(ex, ey - 1), (ex, ey)]


def draw_circuit(nx_G):

    G = {}
    for node in nx_G.nodes():
        lbl = nx_G.nodes[node].get("lable", nx_G.nodes[node].get("label", str(node)))
        G[node] = {"lable": lbl, "to": set(nx_G.successors(node))}

    back_edges = find_back_edges(G)
    layer, dag_out = assign_layers(G, back_edges)
    nodes_info, real_out, real_in, back_src, back_dst = place_nodes(G, layer, dag_out, back_edges)

    board = {}
    blocked = set()
    airspace = set()

    def set_c(x, y, c):
        board[(x, y)] = merge_char(board.get((x, y), " "), c)

    def get_c(x, y):
        return board.get((x, y), " ")

    node_heights = {}

    for node, info in nodes_info.items():
        bx, by, w = info["bx"], info["by"], info["w"]
        label = G[node]["lable"]
        lines = label.split("\n")
        h_box = len(lines) + 2
        node_heights[node] = h_box

        ip = info["in_ports"]
        op = info["out_ports"]

        for dx in range(-2, w + 3):
            for dy in range(-1, h_box + 2):
                airspace.add((bx + dx, by + dy))

        set_c(bx, by, "┌")
        for i in range(1, w - 1):
            set_c(bx + i, by, "─")
        set_c(bx + w - 1, by, "┐")
        for p in ip:
            if p != bx and p != bx + w - 1:
                set_c(p, by, "┴")

        for i, line in enumerate(lines):
            set_c(bx, by + 1 + i, "│")
            lx = bx + (w - len(line)) // 2
            for j, ch in enumerate(line):
                set_c(lx + j, by + 1 + i, ch)
            set_c(bx + w - 1, by + 1 + i, "│")

        by_bottom = by + h_box - 1
        set_c(bx, by_bottom, "└")
        for i in range(1, w - 1):
            set_c(bx + i, by_bottom, "─")
        set_c(bx + w - 1, by_bottom, "┘")
        for p in op:
            if p != bx and p != bx + w - 1:
                set_c(p, by_bottom, "┬")

        for y in range(by, by + h_box):
            for x in range(bx, bx + w):
                blocked.add((x, y))

    all_bx = [info["bx"] for info in nodes_info.values()]
    all_by = [info["by"] for info in nodes_info.values()]
    bounds = (
        min(all_bx) - 30,
        max(info["bx"] + info["w"] for info in nodes_info.values()) + 30,
        min(all_by) - 30,
        max(all_by) + 30,
    )

    edges = []
    self_loops = []
    back_edges_set = set(back_edges)
    for u in G:
        if u not in nodes_info:
            continue
        for v in sorted(real_out[u]):
            if v in nodes_info:
                edges.append((u, v, "forward"))
        for v in sorted(back_src[u]):
            if v in nodes_info and u != v:
                edges.append((u, v, "back"))
        if (u, u) in back_edges_set:
            self_loops.append(u)

    edges.sort(key=lambda e: (-layer.get(e[0], 0), nodes_info[e[0]]["bx"]))

    for u, v, etype in edges:
        iu, iv = nodes_info[u], nodes_info[v]
        if not iu["out_ports_q"] or not iv["in_ports_q"]:
            continue

        sx = iu["out_ports_q"].pop(0)
        sy = iu["by"] + node_heights[u] - 1

        ex = iv["in_ports_q"].pop(0)
        ey = iv["by"]

        if etype == "back":
            path = route_back_wire(sx, sy, ex, ey, blocked, board, airspace, bounds)
        else:
            path = route_wire(sx, sy, ex, ey, blocked, board, airspace, bounds)

        if path:
            for cx, cy, char in path_to_chars(path):
                board[(cx, cy)] = merge_char(get_c(cx, cy), char)

    for u in self_loops:
        draw_self_loop(u, nodes_info[u], board, blocked)

    if not board:
        return
    xs, ys = [x for x, _ in board], [y for _, y in board]
    result = ""
    for y in range(min(ys) - 1, max(ys) + 2):
        result += ("".join(get_c(x, y) for x in range(min(xs) - 1, max(xs) + 2))) + "\n"
    return result.rstrip()
