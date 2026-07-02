"""Locate a named object/procedure's span in an arbitrary blob of AL source.

`tools/graphify-al/graphify/source_lookup.py`'s exact-source lookups are
line-number-anchored: they start from a graph node's `source_location` and
one specific file already on disk, then walk down to the smallest
enclosing object/procedure at that line. Diff/history targets have no graph
node -- they come from an arbitrary historical commit's blob, fetched on the
fly via `git_ops.read_blob` -- so this module extends the same lookup to
work from a NAME instead of a line: `object_type`/`object_name`/
`procedure_name` -> extracted source text.

Reuses graphify-al's tree-sitter-al parser setup (`source_lookup._get_parser`)
and its `_AL_CONFIG`/`_resolve_name`/`_al_strip_quotes` helpers directly
rather than reimplementing any of it (plan.md Project Structure; constitution
"Prefer composing existing, verified primitives").
"""
from __future__ import annotations

from dataclasses import dataclass

from graphify.extract import _AL_CONFIG, _al_strip_quotes, _resolve_name
from graphify.source_lookup import _full_text, _get_parser


class SymbolLookupError(Exception):
    """Raised on a genuine failure to resolve -- e.g. missing/blank
    object_type or object_name. NOT raised when the named object/procedure
    simply doesn't exist in this particular blob -- that's an expected,
    common case (a symbol added/removed between two versions, per
    data-model.md's `SymbolSpan.found`) and is reported by
    `find_symbol_span` returning `None`, not by raising.
    """


@dataclass(frozen=True)
class SymbolSpan:
    """Text-shape contract matching data-model.md's SymbolSpan.text and
    source_lookup.py's get_signature/get_procedure_body/get_object_source:
    full extracted source text for the resolved symbol.
    """

    text: str


def _normalize(name: str) -> str:
    return _al_strip_quotes(name).strip().lower()


def _matches_name(node, source: bytes, target_normalized: str) -> bool:
    name = _resolve_name(node, source, _AL_CONFIG)
    return name is not None and _normalize(name) == target_normalized


def _find_object(root, target_node_type: str, target_name_normalized: str, source: bytes):
    """Depth-first search for the (unique -- AL object names are globally
    unique within a type) object declaration node matching both the node
    type and the resolved name.
    """
    if root.type == target_node_type and _matches_name(root, source, target_name_normalized):
        return root
    for child in root.children:
        found = _find_object(child, target_node_type, target_name_normalized, source)
        if found is not None:
            return found
    return None


def _find_procedure(container, target_name_normalized: str, source: bytes):
    """Depth-first search within an object's body for a procedure/trigger/
    interface-procedure node matching the resolved name. Does not recurse
    into a matched-but-wrong function's own body -- AL procedures aren't
    nested, so once a function_types node is reached that's a dead end
    either way.
    """
    if container.type in _AL_CONFIG.function_types:
        if _matches_name(container, source, target_name_normalized):
            return container
        return None
    for child in container.children:
        found = _find_procedure(child, target_name_normalized, source)
        if found is not None:
            return found
    return None


def find_symbol_span(
    source_bytes: bytes,
    object_type: str,
    object_name: str,
    procedure_name: str | None = None,
) -> SymbolSpan | None:
    """Locate a named object/procedure in an arbitrary blob of AL source.

    `object_type` is the short form used throughout this feature's data
    model (e.g. "codeunit", "table", "pageextension") -- matched against
    tree-sitter-al's `<object_type>_declaration` node type, which is how
    every entry in `_AL_CONFIG.class_types` is named, so no per-type mapping
    table is needed.

    Returns `None` if the object (or, when `procedure_name` is given, the
    procedure within it) does not exist in this blob -- an expected case,
    not an error (data-model.md `SymbolSpan.found`).

    Text shape matches source_lookup.py: full object source when
    `procedure_name` is `None` (get_object_source-equivalent -- "the symbol
    is the object itself", per data-model.md's Symbol.procedure_name),
    full procedure/trigger body when `procedure_name` is given
    (get_procedure_body-equivalent).
    """
    if not object_type or not object_type.strip():
        raise SymbolLookupError("object_type is required.")
    if not object_name or not object_name.strip():
        raise SymbolLookupError("object_name is required.")

    tree = _get_parser().parse(source_bytes)
    target_node_type = f"{object_type.strip().lower()}_declaration"

    obj_node = _find_object(tree.root_node, target_node_type, _normalize(object_name), source_bytes)
    if obj_node is None:
        return None

    if procedure_name is None:
        return SymbolSpan(text=_full_text(obj_node, source_bytes))

    body = obj_node.child_by_field_name(_AL_CONFIG.body_field)
    if body is None:
        return None
    proc_node = _find_procedure(body, _normalize(procedure_name), source_bytes)
    if proc_node is None:
        return None
    return SymbolSpan(text=_full_text(proc_node, source_bytes))
