"""Command line: python3 -m tally.cli FILE prints the total per name, then the grand total."""
import argparse

from tally.parse import parse_lines
from tally.stats import by_name, total


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tally")
    ap.add_argument("file")
    a = ap.parse_args(argv)
    with open(a.file, encoding="utf-8") as f:
        records = parse_lines(f.read().splitlines())
    for name, amount in by_name(records).items():
        print(f"{name}: {amount:g}")
    print(f"TOTAL: {total(records):g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
