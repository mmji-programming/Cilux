from version import __description__, __version__, __author__, __signature__
from REPL import repl, create_kernel

import sys
import argparse
from pathlib import Path

if getattr(sys, "frozen", False):
    __sig__ = __signature__
    __watermark__ = __author__


def main():

    parser = argparse.ArgumentParser(
        prog="cilux",
        description=__description__,
        epilog="Without arguments, enters REPL mode.",
    )

    parser.add_argument("-v", "--version", action="version", version=f"Cilux {__version__}")

    parser.add_argument("-g", "--grammar", nargs="?", const="", metavar="PATH", help=argparse.SUPPRESS)

    parser.add_argument("-c", "--code", metavar="CODE", help="Execute Cilux code directly")

    parser.add_argument("file", nargs="?", help="Execute a .clx file")

    args = parser.parse_args()
    kernel = create_kernel()

    if args.grammar is not None:
        grammar = kernel.build_grammar().strip()

        if args.grammar == "":
            print(grammar)
        else:
            path = Path(args.grammar).resolve()
            if not path.parent.exists():
                print(f"Error: directory not found: {path.parent}")
                sys.exit(1)

            with open(path, "w", encoding="utf-8") as f:
                lines = grammar.split("\n")
                cleaned = []
                prev_empty = False
                last_rule = ""

                for line in lines:
                    stripped = line.strip()

                    if stripped == "":
                        if not prev_empty:
                            cleaned.append("")
                            prev_empty = True
                        continue
                    prev_empty = False

                    if stripped.startswith("|"):
                        indent = " " * (len(last_rule.split(":")[0]))
                        cleaned.append(indent + stripped)
                    else:
                        cleaned.append(stripped)
                        if ":" in stripped:
                            last_rule = stripped

                f.write("\n".join(cleaned))
        return

    if args.code:
        try:
            kernel.run(args.code, filename="<command>")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    if args.file:
        path = Path(args.file).resolve()
        if not path.exists():
            print(f"Error: file not found: {args.file}")
            sys.exit(1)
        if not path.is_file():
            print(f"Error: not a file: {args.file}")
            sys.exit(1)

        with open(path, "r", encoding="utf-8") as f:
            code = f.read()

        try:
            kernel.run(code, filename=str(path))
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    repl()


if __name__ == "__main__":
    main()
