#!/usr/bin/env python3
"""Generate DBML ERDs from Snowflake information_schema CSV exports."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


VIEW_COMMENT_MARKERS = (
    "summary table",
    "summary",
    "view",
    "derived",
    "aggregated",
)

FK_ALIASES = {
    "from_plan_id": "plan_data",
    "group_id": "organization_group",
    "last_billed_usage_data_id": "usage_data",
    "last_billed_usage_sms_id": "usage_sms",
    "last_order_data_id": "order_data",
    "new_pool_plan_id": "pool_plan",
    "old_pool_plan_id": "pool_plan",
    "pool_id": "data_pool",
    "start_usage_id": "usage_data",
    "tag_id": "device_tag",
    "to_plan_id": "plan_data",
    "end_usage_id": "usage_data",
}

IGNORED_ID_COLUMNS = {
    "cell_id",
    "destination_type_id",
    "external_tadig_id",
    "network_id",
    "offer_id",
    "operation_id",
    "preorder_id",
    "product_id",
    "request_id",
    "stripe_charge_id",
    "stripe_id",
    "transaction_id",
}


@dataclass
class Column:
    name: str
    ordinal_position: int
    data_type: str
    character_maximum_length: str = ""
    numeric_precision: str = ""
    numeric_scale: str = ""
    datetime_precision: str = ""
    is_nullable: str = ""
    column_comment: str = ""
    ref_table: str | None = None


@dataclass
class Table:
    catalog: str
    schema: str
    name: str
    table_type: str
    table_comment: str = ""
    columns: list[Column] = field(default_factory=list)

    @property
    def dbml_name(self) -> str:
        return normalize_name(self.name)

    @property
    def has_id(self) -> bool:
        return any(normalize_name(column.name) == "id" for column in self.columns)

    @property
    def is_likely_view(self) -> bool:
        if self.has_id:
            return False

        comment = self.table_comment.lower()
        return any(marker in comment for marker in VIEW_COMMENT_MARKERS)


def normalize_name(value: str) -> str:
    return value.strip().lower()


def clean_comment(value: str) -> str:
    return " ".join((value or "").replace("\r", "\n").split())


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}


def parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_tables(csv_path: Path) -> list[Table]:
    with csv_path.open(newline="") as csv_file:
        rows = [normalize_row(row) for row in csv.DictReader(csv_file)]

    grouped: dict[tuple[str, str, str, str], Table] = {}

    for row in rows:
        key = (
            row.get("table_catalog", ""),
            row.get("table_schema", ""),
            row.get("table_name", ""),
            row.get("table_type", ""),
        )

        if key not in grouped:
            grouped[key] = Table(
                catalog=key[0],
                schema=key[1],
                name=key[2],
                table_type=key[3],
                table_comment=clean_comment(row.get("table_comment", "")),
            )

        grouped[key].columns.append(
            Column(
                name=row.get("column_name", ""),
                ordinal_position=parse_int(row.get("ordinal_position", "")),
                data_type=row.get("data_type", ""),
                character_maximum_length=row.get("character_maximum_length", ""),
                numeric_precision=row.get("numeric_precision", ""),
                numeric_scale=row.get("numeric_scale", ""),
                datetime_precision=row.get("datetime_precision", ""),
                is_nullable=row.get("is_nullable", ""),
                column_comment=clean_comment(row.get("column_comment", "")),
            )
        )

    tables = sorted(grouped.values(), key=lambda table: table.dbml_name)
    for table in tables:
        table.columns.sort(key=lambda column: column.ordinal_position)

    return tables


def table_candidates(column_name: str) -> list[str]:
    base = column_name.removesuffix("_id")
    candidates = [base]

    if base.endswith("ies"):
        candidates.append(base[:-3] + "y")
    if base.endswith("s"):
        candidates.append(base[:-1])

    candidates.append(base + "s")
    candidates.append(base + "es")

    if "_" in base:
        candidates.append(base.split("_")[-1])

    return list(dict.fromkeys(candidates))


def infer_relationships(tables: list[Table]) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    table_names = {table.dbml_name for table in tables}
    inferred = []
    unresolved = []

    for table in tables:
        for column in table.columns:
            column_name = normalize_name(column.name)
            if not column_name.endswith("_id"):
                continue

            alias_target = FK_ALIASES.get(column_name)
            if alias_target and alias_target in table_names:
                matched_table = alias_target
            elif column_name in IGNORED_ID_COLUMNS:
                continue
            else:
                matched_table = next(
                    (candidate for candidate in table_candidates(column_name) if candidate in table_names),
                    None,
                )

            if matched_table:
                column.ref_table = matched_table
                inferred.append((table.dbml_name, column_name, matched_table))
            else:
                unresolved.append((table.dbml_name, column_name))

    return inferred, unresolved


def format_type(column: Column) -> str:
    data_type = normalize_name(column.data_type)
    precision = column.numeric_precision
    scale = column.numeric_scale

    if data_type == "number":
        if precision and scale and not (precision == "38" and scale == "0"):
            return f"number({precision},{scale})"
        return "number"

    if data_type.startswith("timestamp"):
        return data_type

    return data_type or "unknown"


def column_settings(column: Column) -> str:
    settings = []
    column_name = normalize_name(column.name)

    if column_name == "id":
        settings.append("pk")
    if column.is_nullable.upper() == "NO":
        settings.append("not null")
    if column.ref_table:
        settings.append(f"ref: > {column.ref_table}.id")

    if not settings:
        return ""

    return " [" + ", ".join(settings) + "]"


def render_dbml(tables: list[Table]) -> str:
    lines = []

    for table in tables:
        if table.is_likely_view:
            comment = table.table_comment or "no table comment provided"
            lines.append(f"// Likely view-like object: {comment}")

        lines.append(f"Table {table.dbml_name} {{")
        for column in table.columns:
            column_name = normalize_name(column.name)
            lines.append(
                f"  {column_name} {format_type(column)}{column_settings(column)}"
            )
        lines.append("}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def output_stem(csv_path: Path) -> str:
    return csv_path.name.removesuffix("-schema.csv")


def write_outputs(csv_path: Path, dbml: str) -> list[Path]:
    generated_on = date.today().isoformat()
    stem = output_stem(csv_path)
    output_dir = csv_path.parent
    dbml_header = (
        f"// Generated from {csv_path.name} on {generated_on}. Do not edit manually.\n"
        "// Run /refresh-erd to regenerate.\n\n"
    )
    fenced_header = (
        f"Generated from `{csv_path.name}` on {generated_on}. Do not edit manually.\n\n"
        "Run `/refresh-erd` to regenerate.\n\n"
    )

    outputs = {
        output_dir / f"{stem}-erd.dbml": dbml_header + dbml,
        output_dir / f"{stem}-erd.md": fenced_header + "```dbml\n" + dbml + "```\n",
    }

    for path, contents in outputs.items():
        path.write_text(contents)

    return list(outputs.keys())


def summarize(
    csv_path: Path,
    tables: list[Table],
    inferred: list[tuple[str, str, str]],
    unresolved: list[tuple[str, str]],
    outputs: list[Path],
) -> None:
    columns = sum(len(table.columns) for table in tables)
    with_id = [table for table in tables if table.has_id]
    without_id = [table for table in tables if not table.has_id]
    likely_views = [table for table in tables if table.is_likely_view]

    print(f"source: {csv_path}")
    print("generated:")
    for output in outputs:
        print(f"  - {output}")
    print(f"tables: {len(tables)}")
    print(f"columns: {columns}")
    print(f"tables_with_id: {len(with_id)}")
    print(f"objects_without_id: {len(without_id)}")
    print(f"likely_view_like_objects: {len(likely_views)}")
    if likely_views:
        print("likely_view_like_object_names:")
        for table in likely_views:
            print(f"  - {table.dbml_name}")
    print(f"inferred_refs: {len(inferred)}")
    print(f"unresolved_id_columns: {len(unresolved)}")
    if unresolved:
        print("unresolved_id_column_names:")
        for table_name, column_name in unresolved:
            print(f"  - {table_name}.{column_name}")


def generate(csv_path: Path) -> None:
    tables = load_tables(csv_path)
    inferred, unresolved = infer_relationships(tables)
    dbml = render_dbml(tables)
    outputs = write_outputs(csv_path, dbml)
    summarize(csv_path, tables, inferred, unresolved, outputs)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate DBML ERDs from Snowflake information_schema CSV exports."
    )
    parser.add_argument("csv_paths", nargs="+", type=Path, help="One or more *-schema.csv files")
    args = parser.parse_args()

    for index, csv_path in enumerate(args.csv_paths):
        if index:
            print("")
        generate(csv_path)


if __name__ == "__main__":
    main()
