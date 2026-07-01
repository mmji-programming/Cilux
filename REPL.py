from version import __version__, __description__
from registry import create_kernel

import os
import sys
import io
import signal
import datetime

if os.name == "nt":
    try:
        import curses
    except ImportError:
        print("error: run  pip install windows-curses.")
        sys.exit(1)
else:
    import curses


class OutputCapture:
    def __init__(self):
        self._buf = io.StringIO()

    def __enter__(self):
        self._old_out = sys.stdout
        self._old_err = sys.stderr
        sys.stdout = self._buf
        sys.stderr = self._buf
        return self

    def __exit__(self, *_):
        sys.stdout = self._old_out
        sys.stderr = self._old_err

    def getvalue(self) -> str:
        return self._buf.getvalue()


def _wrap(text: str, width: int) -> list[str]:
    result = []
    for line in text.splitlines():
        if not line:
            result.append("")
            continue
        while len(line) >= width:
            result.append(line[: width - 1])
            line = line[width - 1 :]
        result.append(line)
    return result


class History:
    def __init__(self):
        self._entries: list[str] = []
        self._cursor = -1
        self._draft = ""

    def push(self, code: str):
        code = code.strip()
        if not code:
            return
        if self._entries and self._entries[0] == code:
            return
        self._entries.insert(0, code)
        self._cursor = -1
        self._draft = ""

    def start_nav(self, current_text: str):
        if self._cursor == -1:
            self._draft = current_text

    def prev(self, current_text: str) -> str | None:
        if not self._entries:
            return None
        self.start_nav(current_text)
        next_cursor = self._cursor + 1
        if next_cursor >= len(self._entries):
            return None
        self._cursor = next_cursor
        return self._entries[self._cursor]

    def next(self, current_text: str) -> str | None:
        if self._cursor == -1:
            return None
        self._cursor -= 1
        if self._cursor == -1:
            return self._draft
        return self._entries[self._cursor]

    def reset_cursor(self):
        self._cursor = -1
        self._draft = ""


class SaveDialog:
    def __init__(self, stdscr):
        self.stdscr = stdscr

    def ask(self, prompt: str = "Save to: ") -> str | None:
        scr = self.stdscr
        h, w = scr.getmaxyx()
        y = h - 2
        buf = ""

        curses.curs_set(1)
        while True:
            try:
                scr.move(y, 0)
                scr.clrtoeol()
                display = (prompt + buf)[: w - 1]

                scr.attron(curses.A_BOLD)
                scr.addstr(y, 0, display)
                scr.attroff(curses.A_BOLD)

                scr.move(y, min(len(prompt) + len(buf), w - 1))
            except curses.error:
                pass
            scr.refresh()

            key = scr.get_wch()

            if key == "\x1b":
                return None
            if key in ("\n", "\r", curses.KEY_ENTER):
                return buf.strip()
            if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                buf = buf[:-1]
                continue
            if isinstance(key, str) and key.isprintable():
                buf += key
            elif isinstance(key, int) and 32 <= key < 127:
                buf += chr(key)


class MultilineEditor:
    SUBMIT = object()
    CANCEL = object()
    EXIT = object()
    SAVE = object()
    CLEAR = object()

    TAB_SIZE = 4

    def __init__(self, stdscr, history: History):
        self.stdscr = stdscr
        self.history = history
        self.lines: list[str] = [""]
        self.row = 0
        self.col = 0
        self.scroll_off = 0
        self._last_render_key: tuple | None = None

        curses.cbreak()
        curses.noecho()
        stdscr.keypad(True)
        stdscr.nodelay(False)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

    def _is_block_mode(self) -> bool:
        return len(self.lines) > 1

    def reset(self):
        self.lines = [""]
        self.row = 0
        self.col = 0
        self.scroll_off = 0
        self._last_render_key = None
        self.history.reset_cursor()

    def _clamp_col(self):
        self.col = max(0, min(self.col, len(self.lines[self.row])))

    def _cur(self) -> str:
        return self.lines[self.row]

    def _full_text(self) -> str:
        return "\n".join(self.lines)

    def _set(self, text: str):
        self.lines[self.row] = text

    def _render(self, prompt: str, cont: str, output_lines: list[str]):
        scr = self.stdscr
        h, w = scr.getmaxyx()

        content_start_y = 1
        content_end_y = h - 2
        available_rows = content_end_y - content_start_y

        first_prompt = cont[:-2] + "* " if self._is_block_mode() else prompt
        editor_rows = len(self.lines)

        output_slots = max(0, available_rows - editor_rows)

        total_out = len(output_lines)
        max_scroll = max(0, total_out - output_slots)
        self.scroll_off = max(0, min(self.scroll_off, max_scroll))

        view_end = total_out - self.scroll_off
        view_start = max(0, view_end - output_slots)
        visible_out = output_lines[view_start:view_end]

        cursor_scr_y = content_start_y + len(visible_out) + self.row
        cursor_scr_x = len(first_prompt if self.row == 0 else cont) + self.col
        render_key = (
            h,
            w,
            tuple(visible_out),
            tuple(self.lines),
            self.row,
            self.col,
            self.scroll_off,
        )
        if render_key == self._last_render_key:
            return
        self._last_render_key = render_key

        scr.attrset(0)
        scr.bkgdset(" ", curses.A_NORMAL)
        scr.erase()

        header_text = f" Cilux v{__version__} "
        try:
            scr.attron(curses.A_REVERSE)
            scr.addstr(0, 0, header_text.ljust(w)[:w])
            scr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

        scroll_hint = f"  ^ {self.scroll_off} lines" if self.scroll_off > 0 else ""
        footer_base = " [↑↓]: History | [PgUp/PgDn / Scroll]: Scroll | [Enter×2]: Run | [Ctrl+S]: Save | [Ctrl+D / Ctrl+C / exit]: Exit"
        footer_text = (footer_base + scroll_hint).ljust(w)[:w]
        try:
            scr.attron(curses.A_REVERSE)
            scr.addstr(h - 1, 0, footer_text)
            scr.attroff(curses.A_REVERSE)
        except curses.error:
            pass

        row_idx = content_start_y
        for line in visible_out:
            if row_idx >= content_end_y:
                break
            try:
                scr.attrset(0)
                scr.addstr(row_idx, 0, line[: w - 1])
                scr.attrset(0)
                scr.clrtoeol()
            except curses.error:
                pass
            row_idx += 1

        editor_start = row_idx
        for i, line in enumerate(self.lines):
            y = editor_start + i
            if y > content_end_y:
                break
            prefix = first_prompt if i == 0 else cont
            display = prefix + line
            try:
                scr.attrset(0)
                scr.addstr(y, 0, display[: w - 1])
                scr.attrset(0)
                scr.clrtoeol()
            except curses.error:
                pass

        if cursor_scr_y <= content_end_y and cursor_scr_x < w:
            try:
                scr.move(cursor_scr_y, cursor_scr_x)
            except curses.error:
                pass

        scr.refresh()

    def _history_prev(self):
        text = self._full_text()
        entry = self.history.prev(text)
        if entry is not None:
            self.lines = entry.splitlines() or [""]
            self.row = len(self.lines) - 1
            self.col = len(self.lines[self.row])

    def _history_next(self):
        text = self._full_text()
        entry = self.history.next(text)
        if entry is not None:
            self.lines = entry.splitlines() or [""]
            self.row = len(self.lines) - 1
            self.col = len(self.lines[self.row])

    def edit(self, prompt: str, cont: str, output_lines: list[str]) -> object | str:
        self.reset()

        while True:
            self._render(prompt, cont, output_lines)

            if _exit_requested:
                return self.EXIT

            self.stdscr.nodelay(True)
            key = None
            while key is None:
                if _exit_requested:
                    self.stdscr.nodelay(False)
                    return self.EXIT
                try:
                    key = self.stdscr.get_wch()
                except curses.error:
                    curses.napms(20)
                except KeyboardInterrupt:
                    self.stdscr.nodelay(False)
                    return self.EXIT
            self.stdscr.nodelay(False)

            if key == "\x03":
                return self.EXIT
            if key == "\x04":
                return self.EXIT
            if key == "\x13":
                return self.SAVE
            if key == "\x01":
                self.col = 0
                continue
            if key == "\x05":
                self.col = len(self._cur())
                continue

            if key == curses.KEY_MOUSE:
                try:
                    _, _mx, _my, _mz, bstate = curses.getmouse()
                    if bstate & curses.BUTTON4_PRESSED:
                        self.scroll_off += 3
                    elif bstate & curses.BUTTON5_PRESSED:
                        self.scroll_off = max(0, self.scroll_off - 3)
                except curses.error:
                    pass
                continue

            if key == curses.KEY_PPAGE:
                self.scroll_off += 5
                continue
            if key == curses.KEY_NPAGE:
                self.scroll_off = max(0, self.scroll_off - 5)
                continue

            if key in ("\n", "\r", curses.KEY_ENTER):
                cur = self._cur()
                is_last = self.row == len(self.lines) - 1

                if is_last and cur == "" and len(self.lines) > 1:
                    code = "\n".join(self.lines).rstrip("\n")
                    if code.strip():
                        stripped = code.strip()
                        if stripped in ("exit", "quit"):
                            return self.EXIT
                        if stripped in ("clear", "cls"):
                            return self.CLEAR
                        return code
                    else:
                        self.reset()
                        continue

                if is_last and self.row == 0:
                    stripped = cur.strip()
                    if stripped in ("exit", "quit"):
                        return self.EXIT
                    if stripped in ("clear", "cls"):
                        return self.CLEAR

                before = cur[: self.col]
                after = cur[self.col :]
                self._set(before)
                self.lines.insert(self.row + 1, after)
                self.row += 1
                self.col = 0
                continue

            if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                if self.col > 0:
                    line = self._cur()
                    self._set(line[: self.col - 1] + line[self.col :])
                    self.col -= 1
                elif self.row > 0:
                    prev = self.lines[self.row - 1]
                    self.col = len(prev)
                    self.lines[self.row - 1] = prev + self._cur()
                    del self.lines[self.row]
                    self.row -= 1
                continue

            if key == curses.KEY_DC:
                line = self._cur()
                if self.col < len(line):
                    self._set(line[: self.col] + line[self.col + 1 :])
                elif self.row < len(self.lines) - 1:
                    self._set(line + self.lines[self.row + 1])
                    del self.lines[self.row + 1]
                continue

            if key == curses.KEY_UP:
                if self.row > 0:
                    self.row -= 1
                    self._clamp_col()
                else:
                    self._history_prev()
                continue

            if key == curses.KEY_DOWN:
                if self.row < len(self.lines) - 1:
                    self.row += 1
                    self._clamp_col()
                else:
                    self._history_next()
                continue

            if key == curses.KEY_LEFT:
                if self.col > 0:
                    self.col -= 1
                elif self.row > 0:
                    self.row -= 1
                    self.col = len(self._cur())
                continue

            if key == curses.KEY_RIGHT:
                if self.col < len(self._cur()):
                    self.col += 1
                elif self.row < len(self.lines) - 1:
                    self.row += 1
                    self.col = 0
                continue

            if key == curses.KEY_HOME:
                self.col = 0
                continue
            if key == curses.KEY_END:
                self.col = len(self._cur())
                continue

            if key == "\t":
                sp = " " * self.TAB_SIZE
                line = self._cur()
                self._set(line[: self.col] + sp + line[self.col :])
                self.col += self.TAB_SIZE
                continue

            if isinstance(key, str) and key.isprintable():
                line = self._cur()
                self._set(line[: self.col] + key + line[self.col :])
                self.col += 1
                continue

            if isinstance(key, int) and 32 <= key < 127:
                ch = chr(key)
                line = self._cur()
                self._set(line[: self.col] + ch + line[self.col :])
                self.col += 1
                continue


def _do_save(stdscr, history_entries: list[str], dialog: SaveDialog) -> list[str]:
    h, w = stdscr.getmaxyx()
    path = dialog.ask("Save Session To (Empty = Current Dir): ")

    if path is None:
        return []

    if not path:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"session_{ts}.clx"
    elif not path.endswith(".clx"):
        path += ".clx"

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(history_entries))
        return _wrap(f"[*] Saved successfully to: {os.path.abspath(path)}", w)
    except OSError as e:
        return _wrap(f"[!] Save Error: {e}", w)


_exit_requested = False


def _run_repl(stdscr, kernel):
    global _exit_requested
    _exit_requested = False

    def _sigint_handler(sig, frame):
        global _exit_requested
        _exit_requested = True

    signal.signal(signal.SIGINT, _sigint_handler)

    stdscr.attrset(0)
    curses.curs_set(1)

    h, w = stdscr.getmaxyx()
    hist = History()
    editor = MultilineEditor(stdscr, hist)
    dialog = SaveDialog(stdscr)
    exec_history: list[str] = []
    output_lines: list[str] = _wrap(f"", w)

    while True:
        if _exit_requested:
            break
        result = editor.edit(">>> ", "... ", output_lines)
        h, w = stdscr.getmaxyx()

        if result is MultilineEditor.EXIT:
            curses.endwin()
            break

        if result is MultilineEditor.CANCEL:
            continue

        if result is MultilineEditor.CLEAR:
            output_lines = []
            continue

        if result is MultilineEditor.SAVE:
            msg = _do_save(stdscr, exec_history, dialog)
            if msg:
                output_lines.extend(msg)
            continue

        if isinstance(result, str) and result.strip():
            hist.push(result)
            exec_history.append(result)

            with OutputCapture() as cap:
                kernel.run(result)
            out = cap.getvalue()

            new_lines: list[str] = []
            for i, line in enumerate(result.splitlines()):
                new_lines.append((">>> " if i == 0 else "... ") + line)
            if out.strip():
                new_lines += _wrap(out.rstrip(), w)

            MAX_HISTORY = 2000
            output_lines = (output_lines + new_lines)[-MAX_HISTORY:]


def repl():
    try:
        kernel = create_kernel()
        curses.wrapper(_run_repl, kernel)
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)
