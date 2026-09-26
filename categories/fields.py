"""Category field definitions stored in categories.metadata.

Legacy rows use several shapes (kind vs type, dict maps, string options).
Reads normalize them. Subcategory rows store only their own fields and
required-overrides; effective fields merge the parent on read.
"""

from __future__ import annotations

import json
import re
from typing import Any

ALLOWED_TYPES = ("text", "number", "select", "date", "boolean")
_TYPE_ALIASES = {
    "string": "text",
    "input": "text",
    "textarea": "text",
    "integer": "number",
    "int": "number",
    "float": "number",
    "decimal": "number",
    "enum": "select",
    "dropdown": "select",
    "choice": "select",
    "choices": "select",
    "bool": "boolean",
    "checkbox": "boolean",
    "datetime": "date",
}
_KEY_RE = re.compile(r"[^a-z0-9_]+")


class FieldEditError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(message)


def normalize_key(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _KEY_RE.sub("_", raw).strip("_")[:64]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "required"}
    return False


def canonical_type(value: Any, *, strict: bool = False) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "text"
    if raw in _TYPE_ALIASES:
        return _TYPE_ALIASES[raw]
    if raw in ALLOWED_TYPES:
        return raw
    if strict:
        allowed = ", ".join(ALLOWED_TYPES)
        raise FieldEditError(422, "invalid_field", f"Field type must be one of: {allowed}")
    return "text"


def _entry_key(entry: dict) -> str:
    for name in ("key", "name", "field", "field_key", "id"):
        if entry.get(name) not in (None, ""):
            key = normalize_key(entry.get(name))
            if key:
                return key
    return ""


def override_names(entry: dict) -> list[str]:
    existing = entry.get("overrides")
    if isinstance(existing, list) and existing:
        return [str(item) for item in existing if str(item).strip()]
    names: list[str] = []
    if "required" in entry:
        names.append("required")
    if any(k in entry for k in ("label", "title")):
        names.append("label")
    if any(k in entry for k in ("type", "kind", "field_type", "input_type")):
        names.append("type")
    if any(k in entry for k in ("help_text", "helpText", "help", "hint")):
        names.append("help_text")
    if "placeholder" in entry:
        names.append("placeholder")
    if "options" in entry:
        names.append("options")
    return names


def is_partial(entry: dict) -> bool:
    if entry.get("partial") is True:
        return True
    has_label = any(isinstance(entry.get(k), str) and entry.get(k).strip() for k in ("label", "title"))
    has_type = any(
        isinstance(entry.get(k), str) and str(entry.get(k)).strip()
        for k in ("type", "kind", "field_type", "input_type")
    )
    return not has_label and not has_type


def normalize_options(raw: Any) -> list[dict[str, str]] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        return [{"value": part, "label": part} for part in parts] or None
    if isinstance(raw, dict):
        return [{"value": str(key), "label": str(label)} for key, label in raw.items()]
    if not isinstance(raw, list):
        return None
    options: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            options.append({"value": item.strip(), "label": item.strip()})
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or item.get("value") or "").strip()
            value = str(item.get("value") or item.get("id") or label).strip()
            if value:
                options.append({"value": value, "label": label or value})
    return options or None


def _help_text(entry: dict) -> str | None:
    for name in ("help_text", "helpText", "help", "hint"):
        value = entry.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _label(entry: dict, key: str) -> str:
    for name in ("label", "title"):
        value = entry.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return key.replace("_", " ").title()


def raw_entries(metadata: Any) -> list[dict]:
    if metadata is None:
        return []
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return []
    if isinstance(metadata, dict):
        if isinstance(metadata.get("fields"), list):
            return raw_entries(metadata.get("fields"))
        entries: list[dict] = []
        for key, spec in metadata.items():
            if key in {"fields", "version"}:
                continue
            if isinstance(spec, dict):
                entries.append({"key": key, **spec})
            elif isinstance(spec, str) and spec.strip():
                entries.append({"key": key, "label": spec})
        return _dedupe(entries)
    if isinstance(metadata, list):
        return _dedupe([item for item in metadata if isinstance(item, dict)])
    return []


def _dedupe(entries: list[dict]) -> list[dict]:
    order: list[str] = []
    by_key: dict[str, dict] = {}
    for entry in entries:
        key = _entry_key(entry)
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = entry
    return [by_key[key] for key in order]


def normalize_one(entry: dict) -> dict[str, Any] | None:
    key = _entry_key(entry)
    if not key:
        return None
    field_type = canonical_type(
        entry.get("type") or entry.get("kind") or entry.get("field_type") or entry.get("input_type")
    )
    placeholder = entry.get("placeholder")
    if not isinstance(placeholder, str) or not placeholder.strip():
        placeholder = None
    else:
        placeholder = placeholder.strip()
    return {
        "key": key,
        "label": _label(entry, key),
        "type": field_type,
        "kind": field_type,
        "required": _as_bool(entry.get("required")),
        "help_text": _help_text(entry),
        "placeholder": placeholder,
        "options": normalize_options(entry.get("options")) if "options" in entry else None,
        "partial": is_partial(entry),
        "inherited": bool(entry.get("inherited")),
        "overridden": bool(entry.get("overridden")),
        "overrides": override_names(entry),
    }


def normalize_fields(metadata: Any) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for entry in raw_entries(metadata):
        item = normalize_one(entry)
        if item:
            fields.append(item)
    return fields


def _overlay(base: dict[str, Any], raw: dict) -> dict[str, Any]:
    out = dict(base)
    names = override_names(raw)
    out["inherited"] = True
    out["partial"] = False
    out["overridden"] = True
    out["overrides"] = names
    if "label" in names:
        out["label"] = _label(raw, out["key"])
    if "type" in names:
        field_type = canonical_type(
            raw.get("type") or raw.get("kind") or raw.get("field_type") or raw.get("input_type")
        )
        out["type"] = field_type
        out["kind"] = field_type
    if "required" in names:
        out["required"] = _as_bool(raw.get("required"))
    if "help_text" in names:
        out["help_text"] = _help_text(raw)
    if "placeholder" in names:
        placeholder = raw.get("placeholder")
        out["placeholder"] = placeholder.strip() if isinstance(placeholder, str) and placeholder.strip() else None
    if "options" in names:
        out["options"] = normalize_options(raw.get("options"))
    return out


def merge_fields(parent_fields: list[dict[str, Any]], child_metadata: Any) -> list[dict[str, Any]]:
    """Parent fields first, with child overrides applied. Child-only fields follow."""
    child_entries = raw_entries(child_metadata)
    child_by_key = {_entry_key(entry): entry for entry in child_entries}
    child_by_key.pop("", None)
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in parent_fields:
        key = base["key"]
        seen.add(key)
        if key in child_by_key:
            merged.append(_overlay(base, child_by_key[key]))
        else:
            inherited = dict(base)
            inherited["inherited"] = True
            inherited["partial"] = False
            inherited["overridden"] = False
            merged.append(inherited)
    for entry in child_entries:
        key = _entry_key(entry)
        if not key or key in seen:
            continue
        item = normalize_one(entry)
        if not item:
            continue
        item["inherited"] = False
        item["partial"] = False
        merged.append(item)
    return merged


def display_own_fields(metadata: Any, parent_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parent_by_key = {field["key"]: field for field in parent_fields}
    own: list[dict[str, Any]] = []
    for entry in raw_entries(metadata):
        key = _entry_key(entry)
        if not key:
            continue
        if is_partial(entry) and key in parent_by_key:
            shown = _overlay(parent_by_key[key], entry)
            shown["partial"] = True
            shown["inherited"] = False
            shown["overrides"] = override_names(entry)
            own.append(shown)
        else:
            item = normalize_one(entry)
            if not item:
                continue
            item["partial"] = False
            item["inherited"] = False
            own.append(item)
    return own


def present_categories(rows: list[dict]) -> list[dict[str, Any]]:
    usable = [row for row in rows if row.get("slug")]
    by_slug = {row["slug"]: row for row in usable}
    presented: list[dict[str, Any]] = []
    for row in usable:
        parent = by_slug.get(row.get("parent_slug") or "")
        parent_fields = normalize_fields(parent.get("metadata")) if parent else []
        own = display_own_fields(row.get("metadata"), parent_fields)
        item = {key: value for key, value in row.items() if key != "metadata"}
        item["sort_order"] = int(row.get("sort_order") or 0)
        item["parent_slug"] = row.get("parent_slug")
        item["metadata"] = own
        item["fields"] = own
        item["effective_fields"] = merge_fields(parent_fields, row.get("metadata"))
        presented.append(item)
    return presented


def to_stored(field: dict) -> dict[str, Any] | None:
    key = normalize_key(field.get("key"))
    if not key:
        return None
    if field.get("partial"):
        stored: dict[str, Any] = {"key": key}
        for name in field.get("overrides") or ["required"]:
            if name == "required":
                stored["required"] = bool(field.get("required"))
            elif name == "type":
                stored["type"] = field.get("type") or "text"
                stored["kind"] = stored["type"]
            elif name == "label" and field.get("label"):
                stored["label"] = field["label"]
            elif name == "help_text" and field.get("help_text"):
                stored["help_text"] = field["help_text"]
            elif name == "placeholder" and field.get("placeholder"):
                stored["placeholder"] = field["placeholder"]
            elif name == "options" and field.get("options"):
                stored["options"] = field["options"]
        return stored
    field_type = canonical_type(field.get("type") or field.get("kind"))
    stored = {
        "key": key,
        "label": field.get("label") or key.replace("_", " ").title(),
        "type": field_type,
        "kind": field_type,
        "required": bool(field.get("required")),
    }
    if field.get("help_text"):
        stored["help_text"] = field["help_text"]
    if field.get("placeholder"):
        stored["placeholder"] = field["placeholder"]
    if field.get("options"):
        stored["options"] = field["options"]
    return stored


def stored_from_api(fields: list[dict] | None) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if field.get("inherited") and not field.get("partial") and not field.get("overridden"):
            continue
        if field.get("partial") or field.get("overridden"):
            field = {**field, "partial": True}
        item = to_stored(field)
        if item:
            stored.append(item)
    return stored


def blank_listing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def missing_required_fields(fields: list[dict], listing_meta: Any) -> list[dict[str, str]]:
    meta = listing_meta if isinstance(listing_meta, dict) else {}
    missing: list[dict[str, str]] = []
    for field in fields:
        if not field.get("required"):
            continue
        if blank_listing_value(meta.get(field.get("key"))):
            missing.append({"key": field["key"], "label": field.get("label") or field["key"]})
    return missing


def strict_field_type(value: str | None) -> str:
    return canonical_type(value or "text", strict=True)


def reorder_entries(metadata: Any, keys: list[str]) -> list[dict]:
    entries = raw_entries(metadata)
    by_key = {_entry_key(entry): entry for entry in entries}
    wanted = []
    for key in keys:
        normalized = normalize_key(key)
        if normalized in by_key and normalized not in wanted:
            wanted.append(normalized)
    rest = [_entry_key(entry) for entry in entries if _entry_key(entry) not in wanted]
    return [by_key[key] for key in wanted + rest if key in by_key]


def upsert_own_field(
    metadata: Any,
    *,
    key: str,
    provided: dict[str, Any],
    parent_fields: list[dict[str, Any]],
    creating: bool,
) -> list[dict]:
    """Insert or update one stored field. Parent matches can be required-only overrides."""
    normalized_key = normalize_key(key or provided.get("key") or provided.get("label") or "")
    if not normalized_key:
        raise FieldEditError(422, "invalid_field", "Field key is required")
    entries = raw_entries(metadata)
    index = next((i for i, entry in enumerate(entries) if _entry_key(entry) == normalized_key), None)
    if creating and index is not None:
        raise FieldEditError(409, "field_exists", "A field with this key already exists")
    if not creating and index is None:
        parent_keys = {field["key"] for field in parent_fields}
        if normalized_key not in parent_keys:
            raise FieldEditError(404, "field_not_found", "Field not found")
        creating = True

    parent = next((field for field in parent_fields if field["key"] == normalized_key), None)
    has_definition = any(name in provided for name in ("label", "type", "kind", "options"))
    if not has_definition and index is not None and not is_partial(entries[index]):
        current = dict(entries[index])
        if "required" in provided and provided["required"] is not None:
            current["required"] = bool(provided["required"])
        if "help_text" in provided:
            if isinstance(provided.get("help_text"), str) and provided["help_text"].strip():
                current["help_text"] = provided["help_text"].strip()
            else:
                current.pop("help_text", None)
        if "placeholder" in provided:
            if isinstance(provided.get("placeholder"), str) and provided["placeholder"].strip():
                current["placeholder"] = provided["placeholder"].strip()
            else:
                current.pop("placeholder", None)
        entries[index] = current
        return entries
    if not has_definition and (parent is not None or (index is not None and is_partial(entries[index]))):
        current = dict(entries[index]) if index is not None else {"key": normalized_key}
        current["key"] = normalized_key
        if "required" in provided and provided["required"] is not None:
            current["required"] = bool(provided["required"])
        elif "required" not in current:
            current["required"] = False
        if "help_text" in provided:
            current["help_text"] = provided.get("help_text")
        if "placeholder" in provided:
            current["placeholder"] = provided.get("placeholder")
        for name in ("label", "title", "type", "kind", "field_type", "options"):
            current.pop(name, None)
        current.pop("partial", None)
        current.pop("inherited", None)
        current.pop("overrides", None)
        if index is None:
            entries.append(current)
        else:
            entries[index] = current
        return entries

    field_type = strict_field_type(provided.get("type") or provided.get("kind") or "text")
    label = provided.get("label")
    if not isinstance(label, str) or not label.strip():
        if parent is not None:
            label = parent["label"]
        else:
            raise FieldEditError(422, "invalid_field", "Field label is required")
    options = normalize_options(provided.get("options")) if "options" in provided else None
    if field_type == "select" and not options:
        if parent and parent.get("options") and "options" not in provided:
            options = parent["options"]
        else:
            raise FieldEditError(422, "invalid_field", "Select fields need options")
    required = provided.get("required")
    if required is None and index is not None:
        required = _as_bool(entries[index].get("required"))
    stored = {
        "key": normalized_key,
        "label": label.strip(),
        "type": field_type,
        "kind": field_type,
        "required": bool(required),
    }
    help_text = provided.get("help_text")
    if isinstance(help_text, str) and help_text.strip():
        stored["help_text"] = help_text.strip()
    elif index is not None and entries[index].get("help_text") and "help_text" not in provided:
        stored["help_text"] = entries[index]["help_text"]
    placeholder = provided.get("placeholder")
    if isinstance(placeholder, str) and placeholder.strip():
        stored["placeholder"] = placeholder.strip()
    if options:
        stored["options"] = options
    if index is None:
        entries.append(stored)
    else:
        entries[index] = stored
    return entries


def delete_own_field(metadata: Any, key: str, parent_fields: list[dict[str, Any]]) -> list[dict]:
    normalized_key = normalize_key(key)
    entries = raw_entries(metadata)
    kept = [entry for entry in entries if _entry_key(entry) != normalized_key]
    if len(kept) == len(entries):
        if any(field["key"] == normalized_key for field in parent_fields):
            raise FieldEditError(
                400,
                "inherited_field",
                "That field belongs to the parent category. Override required instead of deleting it.",
            )
        raise FieldEditError(404, "field_not_found", "Field not found")
    return kept
