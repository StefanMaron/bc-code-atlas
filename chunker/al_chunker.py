"""AL-aware chunker plugin for cocoindex-code.

Splits an .al file into one chunk per procedure/trigger (falling back to one
chunk per object for objects with no procedures, e.g. plain table/enum value
lists), using the tree-sitter-al grammar to find object and procedure
boundaries. Each chunk's text is prefixed with a header carrying the metadata
cocoindex-code's CodeChunk schema has no columns for (object type/name,
procedure name, attributes), since that's the only way for this information
to reach the embedding model and appear in search results.

Registered via a project's .cocoindex_code/settings.yml:

    chunkers:
      - ext: "al"
        module: "al_chunker:al_chunker"
"""

from __future__ import annotations

import pathlib
import sys

import tree_sitter
import tree_sitter_al
from cocoindex.resources.chunk import Chunk, TextPosition

_LANGUAGE = tree_sitter.Language(tree_sitter_al.language())
_PARSER = tree_sitter.Parser(_LANGUAGE)

# Ollama's nomic-embed-text (and most local embedding models) has a ~2048
# token context window. A handful of real AL procedures run to 100K+
# characters (deeply nested case/if logic) and get rejected outright by the
# embedding backend with a 400 if sent as one chunk. Cap at a safe character
# budget and fall back to sub-splitting anything larger.
_MAX_CHUNK_CHARS = 6000
_SUBSPLIT_TARGET_CHARS = 1500
_SUBSPLIT_OVERLAP_CHARS = 150

_OBJECT_DECL_SUFFIX = "_declaration"
_OBJECT_DECL_TYPES = {
    "codeunit_declaration",
    "table_declaration",
    "page_declaration",
    "report_declaration",
    "query_declaration",
    "xmlport_declaration",
    "enum_declaration",
    "enumextension_declaration",
    "tableextension_declaration",
    "pageextension_declaration",
    "reportextension_declaration",
    "interface_declaration",
    "controladdin_declaration",
    "permissionset_declaration",
    "permissionsetextension_declaration",
    "profile_declaration",
    "entitlement_declaration",
}
_PROCEDURE_TYPES = {"procedure", "trigger_declaration"}


def _field_text(node: tree_sitter.Node, name: str) -> str | None:
    child = node.child_by_field_name(name)
    return child.text.decode("utf-8", "replace") if child is not None else None


def _object_name(node: tree_sitter.Node) -> str:
    return (
        _field_text(node, "object_name")
        or _field_text(node, "base_object")
        or "<unknown>"
    )


def _attributes_before(children: list[tree_sitter.Node], index: int) -> list[str]:
    """Collect attribute_item siblings immediately preceding children[index]."""
    attrs: list[str] = []
    for sibling in reversed(children[:index]):
        if sibling.type == "attribute_item":
            content = sibling.child_by_field_name("attribute")
            name = _field_text(content, "name") if content is not None else None
            args = content.child_by_field_name("arguments") if content is not None else None
            args_text = args.text.decode("utf-8", "replace") if args is not None else ""
            attrs.append(f"{name}{args_text}")
        elif sibling.type == "comment":
            continue
        else:
            break
    attrs.reverse()
    return attrs


def _text_position(source: bytes, byte_offset: int) -> TextPosition:
    prefix = source[:byte_offset]
    line = prefix.count(b"\n") + 1
    last_nl = prefix.rfind(b"\n")
    column = byte_offset - last_nl - 1 if last_nl != -1 else byte_offset
    return TextPosition(
        byte_offset=byte_offset,
        char_offset=len(prefix.decode("utf-8", "replace")),
        line=line,
        column=column,
    )


def _make_chunk(source: bytes, header: str, start_byte: int, end_byte: int) -> Chunk:
    body = source[start_byte:end_byte].decode("utf-8", "replace")
    return Chunk(
        text=f"{header}\n{body}",
        start=_text_position(source, start_byte),
        end=_text_position(source, end_byte),
    )


def _make_chunks(source: bytes, header: str, start_byte: int, end_byte: int) -> list[Chunk]:
    """Like _make_chunk, but sub-splits bodies that exceed _MAX_CHUNK_CHARS so
    no single chunk overflows the embedding backend's context window.

    Splits on fixed-size byte windows rather than trying to find natural
    boundaries: the rare oversized procedure is usually a giant blob (e.g. an
    inlined certificate or JSON literal) with no useful split points anyway,
    and a guaranteed hard bound matters more here than readability of the cut.
    """
    if end_byte - start_byte <= _MAX_CHUNK_CHARS:
        return [_make_chunk(source, header, start_byte, end_byte)]

    windows: list[tuple[int, int]] = []
    pos = start_byte
    while pos < end_byte:
        window_end = min(pos + _SUBSPLIT_TARGET_CHARS, end_byte)
        windows.append((pos, window_end))
        if window_end >= end_byte:
            break
        pos = window_end - _SUBSPLIT_OVERLAP_CHARS

    return [
        _make_chunk(source, f"{header} | part {i + 1}/{len(windows)}", s, e)
        for i, (s, e) in enumerate(windows)
    ]


def _iter_object_nodes(root: tree_sitter.Node):
    """Iterative (non-recursive) walk to find object declarations.

    Some real AL files (deeply nested if/case blocks) exceed Python's default
    recursion limit with a naive recursive walk.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _OBJECT_DECL_TYPES:
            yield node
            continue  # object bodies are handled separately, don't descend further
        stack.extend(node.children)


def al_chunker(path: pathlib.Path, content: str) -> tuple[str | None, list[Chunk]]:
    source = content.encode("utf-8", "replace")
    tree = _PARSER.parse(source)
    chunks: list[Chunk] = []

    for obj_node in _iter_object_nodes(tree.root_node):
        object_type = obj_node.type[: -len(_OBJECT_DECL_SUFFIX)]
        object_name = _object_name(obj_node)
        body = obj_node.child_by_field_name("body")
        if body is None:
            continue
        children = list(body.children)

        found_procedure = False
        for i, child in enumerate(children):
            if child.type not in _PROCEDURE_TYPES:
                continue
            found_procedure = True
            procedure_name = _field_text(child, "name") or "<unnamed>"
            attributes = _attributes_before(children, i)
            attr_str = f" | attributes: {', '.join(attributes)}" if attributes else ""
            header = (
                f"-- object_type: {object_type} | object_name: {object_name} "
                f"| procedure: {procedure_name}{attr_str}"
            )
            chunks.extend(_make_chunks(source, header, child.start_byte, child.end_byte))

        if not found_procedure:
            header = f"-- object_type: {object_type} | object_name: {object_name}"
            chunks.extend(_make_chunks(source, header, obj_node.start_byte, obj_node.end_byte))

    if not chunks:
        # No recognizable object (e.g. empty file, or a file this grammar
        # version doesn't cover) -- fall back to whole-file chunk(s) so
        # nothing silently disappears from the index.
        chunks = _make_chunks(source, "-- object_type: unknown", 0, len(source))

    return "al", chunks


if __name__ == "__main__":
    # Quick manual smoke test: al_chunker.py <file.al>
    p = pathlib.Path(sys.argv[1])
    _, cs = al_chunker(p, p.read_text(encoding="utf-8", errors="replace"))
    print(f"{len(cs)} chunks")
    for c in cs[:5]:
        print("---")
        print(c.text[:200])
