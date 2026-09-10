"""Provider-neutral, bounded structured-validation diagnostics."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from deep_research.providers.contracts import (
    StructuredDiagnosticCategory,
    StructuredValidationDiagnostic,
)

MAX_VALIDATION_FIELD_PATHS = 16
MAX_VALIDATION_SUMMARY_LENGTH = 1000

_CATEGORY_PRIORITY: tuple[tuple[StructuredDiagnosticCategory, frozenset[str]], ...] = (
    ("json_invalid", frozenset({"json_invalid"})),
    ("missing", frozenset({"missing"})),
    ("extra_forbidden", frozenset({"extra_forbidden"})),
    (
        "type_mismatch",
        frozenset(
            {
                "bool_parsing",
                "bool_type",
                "bytes_type",
                "callable_type",
                "dataclass_type",
                "date_parsing",
                "date_type",
                "datetime_parsing",
                "datetime_type",
                "decimal_parsing",
                "decimal_type",
                "dict_type",
                "float_parsing",
                "float_type",
                "frozenset_type",
                "int_parsing",
                "int_type",
                "is_instance_of",
                "is_subclass_of",
                "json_type",
                "list_type",
                "model_attributes_type",
                "model_type",
                "none_required",
                "set_type",
                "string_type",
                "time_delta_type",
                "time_parsing",
                "time_type",
                "tuple_type",
                "uuid_parsing",
                "uuid_type",
            }
        ),
    ),
    (
        "numeric_bounds",
        frozenset(
            {
                "decimal_max_digits",
                "decimal_places",
                "finite_number",
                "greater_than",
                "greater_than_equal",
                "less_than",
                "less_than_equal",
                "multiple_of",
            }
        ),
    ),
    (
        "string_bounds",
        frozenset(
            {
                "string_pattern_mismatch",
                "string_too_long",
                "string_too_short",
            }
        ),
    ),
)


def _single_schema_annotation(annotation: object) -> object | None:
    """Unwrap metadata and optionality without guessing among unions."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (Union, UnionType):
        members = tuple(
            member for member in get_args(annotation) if member is not type(None)
        )
        if len(members) != 1:
            return None
        return _single_schema_annotation(members[0])
    return annotation


def schema_field_path(schema: type[BaseModel], location: Sequence[object]) -> str:
    """Retain only field names proven by the requested schema."""
    annotation: object | None = schema
    retained: list[str] = []
    for segment in location:
        annotation = _single_schema_annotation(annotation)
        if annotation is None:
            break
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if not isinstance(segment, str):
                break
            matched = next(
                (
                    (field_name, field)
                    for field_name, field in annotation.model_fields.items()
                    if segment == field_name
                    or (isinstance(field.alias, str) and segment == field.alias)
                    or (
                        isinstance(field.validation_alias, str)
                        and segment == field.validation_alias
                    )
                ),
                None,
            )
            if matched is None:
                break
            field_name, field = matched
            retained.append(field_name)
            annotation = field.annotation
            continue

        origin = get_origin(annotation)
        arguments = get_args(annotation)
        if origin in (dict, Mapping):
            annotation = arguments[1] if len(arguments) == 2 else None
            continue
        if origin in (list, set, frozenset, Sequence):
            annotation = arguments[0] if arguments else None
            continue
        if origin is tuple:
            if len(arguments) == 2 and arguments[1] is Ellipsis:
                annotation = arguments[0]
            elif isinstance(segment, int) and 0 <= segment < len(arguments):
                annotation = arguments[segment]
            else:
                annotation = None
            continue
        break
    return ".".join(retained) or "$"


def _validation_error_items(error: BaseException) -> tuple[Mapping[str, Any], ...]:
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return ()
    try:
        items = errors(include_input=False)
    except TypeError:
        try:
            items = errors()
        except Exception:
            return ()
    except Exception:
        return ()
    if not isinstance(items, Sequence):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def validation_category(error: BaseException) -> StructuredDiagnosticCategory:
    """Map stable Pydantic error types to a finite safe category."""
    if isinstance(error, json.JSONDecodeError):
        return "json_invalid"
    error_types = {
        item.get("type")
        for item in _validation_error_items(error)
        if isinstance(item.get("type"), str)
    }
    for category, candidates in _CATEGORY_PRIORITY:
        if error_types.intersection(candidates):
            return category
    return "other_schema"


def validation_diagnostic(
    error: BaseException, *, attempt: int, schema: type[BaseModel]
) -> StructuredValidationDiagnostic:
    """Extract bounded paths and a stable category, never input values."""
    paths: list[str] = []
    items = _validation_error_items(error)
    for item in items:
        location = item.get("loc", ())
        if not isinstance(location, Sequence) or isinstance(location, (str, bytes)):
            location = ()
        paths.append(schema_field_path(schema, location))
        if len(paths) == MAX_VALIDATION_FIELD_PATHS:
            break
    return StructuredValidationDiagnostic(
        attempt=attempt,
        field_paths=tuple(paths) or ("$",),
        category=validation_category(error),
    )


def validation_diagnostic_from_text(
    text: object, *, attempt: int, schema: type[BaseModel]
) -> StructuredValidationDiagnostic:
    """Validate provider text only long enough to derive safe local telemetry."""
    if isinstance(text, str):
        try:
            schema.model_validate_json(text)
        except (json.JSONDecodeError, ValidationError) as error:
            return validation_diagnostic(error, attempt=attempt, schema=schema)
        except Exception:
            pass
    return StructuredValidationDiagnostic(
        attempt=attempt,
        field_paths=("$",),
        category="other_schema",
    )


def validation_summary(
    diagnostic: StructuredValidationDiagnostic,
    *,
    limit: int = MAX_VALIDATION_SUMMARY_LENGTH,
) -> str:
    """Render only a stable category and bounded normalized field paths."""
    category = diagnostic.category or "schema_output"
    summary = f"category={category}; field_paths="
    if len(summary) >= limit:
        return summary[:limit]
    retained: list[str] = []
    for path in diagnostic.field_paths:
        separator = ", " if retained else ""
        if len(summary) + len(separator) + len(path) > limit:
            break
        retained.append(path)
        summary += f"{separator}{path}"
    return summary
