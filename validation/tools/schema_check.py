#!/usr/bin/env python3
"""Stdlib-only JSON Schema checker for committed OpenBar contracts and fixtures.

Implements the JSON Schema 2020-12 keyword subset used by validation/schema/*.schema.json and
fails closed on any other keyword, so a schema cannot silently stop being enforced. Two
deliberate strictness choices match the Rust readers (serde_json):

- ``integer`` means a JSON number written without a fraction or exponent (``60``, not ``60.0``),
  including for ``const``/``enum`` comparisons;
- NaN, Infinity, out-of-range numbers such as ``1e999`` and duplicate object keys are rejected while
  parsing;
- a trailing ``$`` in ``pattern`` matches only at the very end of the string (ECMA-262), not before a
  final newline as Python's ``$`` would.

``format`` is treated as an annotation, as 2020-12 does by default.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "validation" / "schema"
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

ANNOTATION_KEYWORDS = frozenset(
    {"$schema", "$id", "$defs", "$comment", "title", "description", "examples", "default", "format"}
)
ASSERTION_KEYWORDS = frozenset(
    {
        "$ref", "type", "const", "enum", "properties", "required", "additionalProperties",
        "propertyNames", "items", "minItems", "maxItems", "uniqueItems", "minLength",
        "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
    }
)

# Committed JSON documents and the schema each must satisfy. Paths are relative to ROOT.
CATALOGUE: dict[str, tuple[str, ...]] = {
    "fixture-manifest-v1.schema.json": (
        "validation/fixtures/public/manifest.json",
        "validation/examples/fixture-manifest.example.json",
    ),
    "annotation-v1.schema.json": ("validation/fixtures/public/annotations/*.annotation-v1.json",),
    "manual-target-seed-v1.schema.json": (
        "validation/fixtures/public/seeds/*.manual-target-seed-v1.json",
        "validation/examples/manual-target-seed.example.json",
    ),
    "tracker-prediction-v1.schema.json": ("validation/fixtures/public/predictions/*.prediction-v1.json",),
    "benchmark-suite-v1.schema.json": ("validation/benchmarks/*.benchmark-v1.json",),
    "analysis-v1.schema.json": ("crates/openbar-core/tests/fixtures/analysis-v1.golden.json",),
}

# Committed JSON documents that intentionally have no JSON Schema, with the reason.
SCHEMALESS: dict[str, str] = {
    "validation/fixtures/public/annotations/synthetic-clean-side-12.repeatability.json":
        "repeatability report; validated by annotations.py repeatability tests",
    "validation/examples/annotation-import-metadata.example.json":
        "CSV import metadata; validated by annotations.py import-csv",
}

# Directories whose JSON files must each be either catalogued or listed in SCHEMALESS.
COVERED_DIRS = (
    "validation/fixtures/public",
    "validation/examples",
    "validation/benchmarks",
    "crates/openbar-core/tests/fixtures",
)


class SchemaError(ValueError):
    """The schema itself is unsupported or malformed."""


class DocumentError(ValueError):
    """A JSON document could not be parsed under the strict rules."""


def _reject_constant(name: str) -> Any:
    raise DocumentError(f"non-finite number {name} is not valid JSON")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DocumentError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _finite_float(literal: str) -> float:
    value = float(literal)
    if not math.isfinite(value):
        raise DocumentError(f"number {literal} is out of range")
    return value


def loads_strict(text: str) -> Any:
    return json.loads(
        text,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_unique_object,
    )


def load_strict(path: Path) -> Any:
    try:
        return loads_strict(path.read_text(encoding="utf-8"))
    except DocumentError as error:
        raise DocumentError(f"{path}: {error}") from error
    except ValueError as error:
        # JSONDecodeError, UnicodeDecodeError and int-digit-limit errors are all ValueErrors.
        raise DocumentError(f"{path}: invalid JSON: {error}") from error


def _is_local_pointer_ref(ref: Any) -> bool:
    return isinstance(ref, str) and (ref == "#" or ref.startswith("#/"))


def _ecma_pattern(pattern: str) -> str:
    # Python's "$" also matches before a trailing newline; ECMA-262's does not.
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise DocumentError(f"unsupported Python value {value!r}")


def _is_type(value: Any, expected: str) -> bool:
    actual = _json_type(value)
    return actual == expected or (expected == "number" and actual == "integer")


def _json_equal(left: Any, right: Any) -> bool:
    left_type, right_type = _json_type(left), _json_type(right)
    # Strict like serde: 1 and 1.0 are different values, so const/enum cannot admit 1.0.
    if left_type != right_type:
        return False
    if left_type == "array":
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if left_type == "object":
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    return left == right


def _pointer(path: str, token: str | int) -> str:
    escaped = str(token).replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


class Validator:
    def __init__(self, root: dict[str, Any]) -> None:
        self.root = root

    def resolve(self, ref: str) -> Any:
        if not _is_local_pointer_ref(ref):
            raise SchemaError(f"only local JSON-pointer $ref is supported, got {ref!r}")
        node: Any = self.root
        for raw in ref[1:].split("/")[1:]:
            token = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise SchemaError(f"unresolvable $ref {ref!r}")
            node = node[token]
        return node

    def errors(self, instance: Any, schema: Any = None, path: str = "") -> list[str]:
        schema = self.root if schema is None else schema
        if schema is True:
            return []
        if schema is False:
            return [f"{path or '/'}: no value is allowed here"]
        if not isinstance(schema, dict):
            raise SchemaError(f"schema must be an object or boolean, got {schema!r}")
        unknown = set(schema) - ANNOTATION_KEYWORDS - ASSERTION_KEYWORDS
        if unknown:
            raise SchemaError(f"unsupported schema keyword(s) {sorted(unknown)} at {path or '/'}")
        found: list[str] = []
        for keyword in sorted(set(schema) & ASSERTION_KEYWORDS):
            check = _CHECKS.get(keyword)
            if check is not None:
                found.extend(check(self, instance, schema, path or "/"))
        return found


Check = Callable[[Validator, Any, dict[str, Any], str], list[str]]


def _check_ref(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    return v.errors(value, v.resolve(schema["$ref"]), path.rstrip("/"))


def _check_type(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    expected = schema["type"]
    options = expected if isinstance(expected, list) else [expected]
    if any(_is_type(value, option) for option in options):
        return []
    return [f"{path}: expected type {expected}, got {_json_type(value)}"]


def _check_const(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    return [] if _json_equal(value, schema["const"]) else [f"{path}: must equal {schema['const']!r}"]


def _check_enum(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if any(_json_equal(value, option) for option in schema["enum"]):
        return []
    return [f"{path}: {value!r} is not one of {schema['enum']!r}"]


def _check_properties(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    found: list[str] = []
    for key, subschema in schema["properties"].items():
        if key in value:
            found.extend(v.errors(value[key], subschema, _pointer(path.rstrip("/"), key)))
    return found


def _check_required(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [f"{path}: missing required property {key!r}" for key in schema["required"] if key not in value]


def _check_additional(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    declared = schema.get("properties", {})
    found: list[str] = []
    for key in sorted(set(value) - set(declared)):
        child = _pointer(path.rstrip("/"), key)
        if schema["additionalProperties"] is False:
            found.append(f"{child}: unknown property {key!r}")
        else:
            found.extend(v.errors(value[key], schema["additionalProperties"], child))
    return found


def _check_property_names(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    found: list[str] = []
    for key in sorted(value):
        found.extend(f"{error} (property name {key!r})" for error in v.errors(key, schema["propertyNames"], path.rstrip("/")))
    return found


def _check_items(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if not isinstance(value, list):
        return []
    found: list[str] = []
    for index, item in enumerate(value):
        found.extend(v.errors(item, schema["items"], _pointer(path.rstrip("/"), index)))
    return found


def _check_min_items(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not isinstance(value, list) or len(value) >= schema["minItems"]
    return [] if ok else [f"{path}: expected at least {schema['minItems']} item(s)"]


def _check_max_items(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not isinstance(value, list) or len(value) <= schema["maxItems"]
    return [] if ok else [f"{path}: expected at most {schema['maxItems']} item(s)"]


def _check_unique(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if not schema["uniqueItems"] or not isinstance(value, list):
        return []
    for i, left in enumerate(value):
        if any(_json_equal(left, right) for right in value[i + 1:]):
            return [f"{path}: items must be unique"]
    return []


def _check_min_length(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not isinstance(value, str) or len(value) >= schema["minLength"]
    return [] if ok else [f"{path}: shorter than {schema['minLength']} character(s)"]


def _check_max_length(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not isinstance(value, str) or len(value) <= schema["maxLength"]
    return [] if ok else [f"{path}: longer than {schema['maxLength']} character(s)"]


def _check_pattern(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not isinstance(value, str) or re.search(_ecma_pattern(schema["pattern"]), value) is not None
    return [] if ok else [f"{path}: {value!r} does not match {schema['pattern']!r}"]


def _numeric(value: Any) -> bool:
    return _json_type(value) in {"integer", "number"}


def _check_minimum(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not _numeric(value) or value >= schema["minimum"]
    return [] if ok else [f"{path}: {value} is below minimum {schema['minimum']}"]


def _check_maximum(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not _numeric(value) or value <= schema["maximum"]
    return [] if ok else [f"{path}: {value} is above maximum {schema['maximum']}"]


def _check_exclusive_minimum(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not _numeric(value) or value > schema["exclusiveMinimum"]
    return [] if ok else [f"{path}: {value} must be greater than {schema['exclusiveMinimum']}"]


def _check_exclusive_maximum(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    ok = not _numeric(value) or value < schema["exclusiveMaximum"]
    return [] if ok else [f"{path}: {value} must be less than {schema['exclusiveMaximum']}"]


def _check_all_of(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    return [error for subschema in schema["allOf"] for error in v.errors(value, subschema, path.rstrip("/"))]


def _check_any_of(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if any(not v.errors(value, subschema, path.rstrip("/")) for subschema in schema["anyOf"]):
        return []
    return [f"{path}: does not match any anyOf branch"]


def _check_one_of(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    matches = sum(1 for subschema in schema["oneOf"] if not v.errors(value, subschema, path.rstrip("/")))
    return [] if matches == 1 else [f"{path}: must match exactly one oneOf branch, matched {matches}"]


def _check_not(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    return [f"{path}: matches a forbidden 'not' schema"] if not v.errors(value, schema["not"], path.rstrip("/")) else []


def _check_if(v: Validator, value: Any, schema: dict[str, Any], path: str) -> list[str]:
    branch = "then" if not v.errors(value, schema["if"], path.rstrip("/")) else "else"
    return v.errors(value, schema[branch], path.rstrip("/")) if branch in schema else []


_CHECKS: dict[str, Check] = {
    "$ref": _check_ref,
    "type": _check_type,
    "const": _check_const,
    "enum": _check_enum,
    "properties": _check_properties,
    "required": _check_required,
    "additionalProperties": _check_additional,
    "propertyNames": _check_property_names,
    "items": _check_items,
    "minItems": _check_min_items,
    "maxItems": _check_max_items,
    "uniqueItems": _check_unique,
    "minLength": _check_min_length,
    "maxLength": _check_max_length,
    "pattern": _check_pattern,
    "minimum": _check_minimum,
    "maximum": _check_maximum,
    "exclusiveMinimum": _check_exclusive_minimum,
    "exclusiveMaximum": _check_exclusive_maximum,
    "allOf": _check_all_of,
    "anyOf": _check_any_of,
    "oneOf": _check_one_of,
    "not": _check_not,
    "if": _check_if,
    # "then"/"else" are evaluated by "if"; on their own they assert nothing.
}


SUBSCHEMA_KEYWORDS = ("additionalProperties", "propertyNames", "items", "not", "if", "then", "else")
SUBSCHEMA_MAP_KEYWORDS = ("properties", "$defs")
SUBSCHEMA_LIST_KEYWORDS = ("allOf", "anyOf", "oneOf")


def audit_schema(schema: Any, path: str = "#") -> None:
    """Reject unsupported keywords anywhere in the schema, including unreferenced branches."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: schema must be an object or boolean")
    unknown = set(schema) - ANNOTATION_KEYWORDS - ASSERTION_KEYWORDS
    if unknown:
        raise SchemaError(f"{path}: unsupported schema keyword(s) {sorted(unknown)}")
    if "$ref" in schema and not _is_local_pointer_ref(schema["$ref"]):
        raise SchemaError(f"{path}: only local JSON-pointer $ref is supported, got {schema['$ref']!r}")
    for keyword in SUBSCHEMA_KEYWORDS:
        if keyword in schema:
            audit_schema(schema[keyword], f"{path}/{keyword}")
    for keyword in SUBSCHEMA_MAP_KEYWORDS:
        for name, subschema in schema.get(keyword, {}).items():
            audit_schema(subschema, f"{path}/{keyword}/{name}")
    for keyword in SUBSCHEMA_LIST_KEYWORDS:
        for index, subschema in enumerate(schema.get(keyword, [])):
            audit_schema(subschema, f"{path}/{keyword}/{index}")


def load_schema(path: Path) -> dict[str, Any]:
    schema = load_strict(path)
    if not isinstance(schema, dict) or schema.get("$schema") != DRAFT_2020_12:
        raise SchemaError(f"{path}: expected a JSON Schema 2020-12 document")
    try:
        audit_schema(schema)
    except SchemaError as error:
        raise SchemaError(f"{path}: {error}") from error
    return schema


def validate_document(document: Any, schema: dict[str, Any]) -> list[str]:
    return Validator(schema).errors(document)


def catalogued_documents(root: Path = ROOT) -> list[tuple[Path, Path]]:
    """Return (schema, document) pairs; a pattern that matches nothing is an error."""
    pairs: list[tuple[Path, Path]] = []
    for schema_name, patterns in CATALOGUE.items():
        for pattern in patterns:
            matches = sorted(root.glob(pattern))
            if not matches:
                raise SchemaError(f"catalogue pattern {pattern!r} matched no files")
            pairs.extend((SCHEMA_DIR / schema_name, match) for match in matches)
    return pairs


def uncovered_documents(root: Path = ROOT) -> list[str]:
    covered = {document.relative_to(root).as_posix() for _, document in catalogued_documents(root)}
    covered |= set(SCHEMALESS)
    found = {
        path.relative_to(root).as_posix()
        for directory in COVERED_DIRS
        for path in (root / directory).rglob("*.json")
    }
    return sorted(found - covered)


def stale_schemaless(root: Path = ROOT) -> list[str]:
    return sorted(path for path in SCHEMALESS if not (root / path).is_file())


def check_catalogue() -> list[str]:
    problems = [f"{path}: committed JSON has no schema mapping in schema_check.py" for path in uncovered_documents()]
    problems += [f"{path}: SCHEMALESS entry names a file that does not exist" for path in stale_schemaless()]
    for schema_path, document_path in catalogued_documents():
        schema = load_schema(schema_path)
        for error in validate_document(load_strict(document_path), schema):
            problems.append(f"{document_path.relative_to(ROOT).as_posix()} ({schema_path.name}): {error}")
    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schema", type=Path, help="validate DOCUMENTs against this schema instead of the catalogue")
    parser.add_argument("documents", nargs="*", type=Path, metavar="DOCUMENT")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.schema) != bool(args.documents):
        print("error: --schema and DOCUMENT must be given together, or neither", file=sys.stderr)
        return 2
    try:
        if args.schema:
            schema = load_schema(args.schema)
            problems = [
                f"{document}: {error}"
                for document in args.documents
                for error in validate_document(load_strict(document), schema)
            ]
            checked = len(args.documents)
        else:
            problems = check_catalogue()
            checked = len(catalogued_documents())
    except (SchemaError, DocumentError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"schema check failed: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"schema check passed: {checked} document(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
