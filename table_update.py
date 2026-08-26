#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update data_tables.json with a single cell write."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_tables(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_tables(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_cell(data: dict, table: str, row: int, col: int, value: str) -> None:
    if not table:
        return
    if row <= 0 or col <= 0:
        return
    rows = data.get(table)
    if not isinstance(rows, list):
        rows = []
    while len(rows) < row:
        rows.append([])
    row_values = rows[row - 1]
    if not isinstance(row_values, list):
        row_values = []
    while len(row_values) < col:
        row_values.append("")
    row_values[col - 1] = value
    rows[row - 1] = row_values
    data[table] = rows


def append_cell(data: dict, table: str, value: str) -> None:
    if not table:
        return
    rows = data.get(table)
    if not isinstance(rows, list):
        rows = []
    rows.append([value])
    data[table] = rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--table", default="")
    parser.add_argument("--table-file", default="")
    parser.add_argument("--row", type=int, default=0)
    parser.add_argument("--col", type=int, default=0)
    parser.add_argument("--value", default="")
    parser.add_argument("--value-file", default="")
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()

    table_name = args.table
    if not table_name and args.table_file:
        try:
            table_name = Path(args.table_file).read_text(encoding="utf-8-sig").strip()
        except Exception:
            table_name = ""

    value = args.value
    if args.value_file:
        try:
            value = Path(args.value_file).read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            value = ""

    path = Path(args.file)
    data = load_tables(path)
    if args.append or args.row <= 0 or args.col <= 0:
        append_cell(data, table_name, value)
    else:
        set_cell(data, table_name, args.row, args.col, value)
    save_tables(path, data)


if __name__ == "__main__":
    main()
