#!/usr/bin/env python3
"""
Generate a Kora 2.0 JDBC entity + repository from the templates in ../assets.

Kora 2.0 (io.koraframework): repositories are synchronous @Repository interfaces extending
JdbcRepository; @EntityJdbc comes from io.koraframework.database.jdbc.annotation; the connection
pool is configured under the `jdbc` config section.

Usage:
    python3 generate_repository.py --entity User --table users --id-type Long --lang java --dry-run
    python3 generate_repository.py --entity User --table users --id-type Long --lang java
    python3 generate_repository.py --entity OrderItem --table order_items --id-type composite --lang kotlin

Options:
    --entity           entity class name (e.g. User, OrderItem)
    --table            database table name (e.g. users, order_items)
    --id-type          Long, UUID, String, or composite
    --lang             java (default) or kotlin
    --package          package name (default: com.example.repository)
    --output-dir       output directory (default: current directory)
    --entity-only      generate only the entity file
    --repository-only  generate only the repository file
    --dry-run          print what would be written; create nothing
"""

import argparse
import sys
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# Placeholder values for a composite key. Adjust the generated file to your real columns.
COMPOSITE_DEFAULTS = {
    "id1_type": "Long",
    "id1_field": "orderId",
    "id1_column": "order_id",
    "id2_type": "Long",
    "id2_field": "productId",
    "id2_column": "product_id",
}


def template_path(template_name: str, lang: str) -> Path:
    """Resolve a template file; assets/ is a sibling of scripts/."""
    suffix = ".kt.template" if lang == "kotlin" else ".java.template"
    return ASSETS_DIR / f"{template_name}{suffix}"


def read_template(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text(encoding="utf-8")


def replace_placeholders(content: str, replacements: dict) -> str:
    for key, value in replacements.items():
        content = content.replace("${" + key + "}", value)
    return content


def generate_entity(entity_name: str, table_name: str, id_type: str, lang: str, package: str) -> str:
    if id_type == "composite":
        template_name = "jdbc-entity-composite-id"
        replacements = {
            "package": package,
            "entity_name": entity_name,
            "table_name": table_name,
            **COMPOSITE_DEFAULTS,
        }
    else:
        template_name = "jdbc-entity-single-id"
        replacements = {
            "package": package,
            "entity_name": entity_name,
            "table_name": table_name,
            "id_type": id_type,
        }

    return replace_placeholders(read_template(template_path(template_name, lang)), replacements)


def generate_repository(entity_name: str, table_name: str, id_type: str, lang: str, package: str) -> str:
    common = {
        "package": package,
        "entity_name": entity_name,
        "repository_name": f"{entity_name}Repository",
        # the delete queries name the table literally: %{entity#table} has no `entity` parameter
        # there and would fail with "Query macro target `entity` cannot be resolved"
        "table_name": table_name,
    }

    if id_type == "composite":
        template_name = "jdbc-crud-composite-id-repository"
        replacements = {**common, **COMPOSITE_DEFAULTS}
    else:
        template_name = "jdbc-crud-single-id-repository"
        replacements = {**common, "id_type": id_type}

    return replace_placeholders(read_template(template_path(template_name, lang)), replacements)


def write_file(content: str, output_dir: Path, filename: str, dry_run: bool) -> Path:
    file_path = output_dir / filename
    if dry_run:
        return file_path
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Kora 2.0 JDBC entity and repository files")
    parser.add_argument("--entity", required=True,
                        help="entity class name (e.g. User, OrderItem)")
    parser.add_argument("--table", required=True,
                        help="database table name (e.g. users, order_items)")
    parser.add_argument("--id-type", required=True, choices=["Long", "UUID", "String", "composite"],
                        help="id type: Long, UUID, String, or composite")
    parser.add_argument("--lang", default="java", choices=["java", "kotlin"],
                        help="target language (default: java)")
    parser.add_argument("--package", default="com.example.repository",
                        help="package name (default: com.example.repository)")
    parser.add_argument("--output-dir", default=".",
                        help="output directory (default: current directory)")
    parser.add_argument("--entity-only", action="store_true",
                        help="generate only the entity file")
    parser.add_argument("--repository-only", action="store_true",
                        help="generate only the repository file")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be written; create nothing")

    args = parser.parse_args()

    if args.entity_only and args.repository_only:
        parser.error("--entity-only and --repository-only are mutually exclusive")

    output_dir = Path(args.output_dir)
    ext = "kt" if args.lang == "kotlin" else "java"
    generated: list[Path] = []

    if not args.entity_only:
        content = generate_repository(
            args.entity, args.table, args.id_type, args.lang, args.package)
        path = write_file(content, output_dir, f"{args.entity}Repository.{ext}", args.dry_run)
        generated.append(path)
        if args.dry_run:
            print(f"--- would write: {path}")
            print(content)

    if not args.repository_only:
        content = generate_entity(
            args.entity, args.table, args.id_type, args.lang, args.package)
        path = write_file(content, output_dir, f"{args.entity}.{ext}", args.dry_run)
        generated.append(path)
        if args.dry_run:
            print(f"--- would write: {path}")
            print(content)

    verb = "Would generate" if args.dry_run else "Generated"
    for path in generated:
        print(f"{verb}: {path}")

    source_root = "src/main/kotlin" if args.lang == "kotlin" else "src/main/java"
    package_path = args.package.replace(".", "/")
    processor = ('ksp("io.koraframework:symbol-processors")' if args.lang == "kotlin"
                 else 'annotationProcessor "io.koraframework:annotation-processors"')

    print("\nNext steps:")
    print(f"1. Move the files to {source_root}/{package_path}/")
    print(f"2. Make sure the build declares the processor: {processor}")
    print("3. Add JdbcDatabaseModule to the @KoraApp interface")
    print("4. Configure the pool under the `jdbc` section (NOT `db`) of application.conf")
    print("5. Adjust the entity fields to the real schema, then write the migration for the table")

    return 0


if __name__ == "__main__":
    sys.exit(main())
