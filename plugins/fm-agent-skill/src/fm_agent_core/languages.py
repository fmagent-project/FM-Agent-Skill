"""Single source of truth for FM-Agent Skill language capabilities.

The Skill intentionally does not import FM-Agent.  This module mirrors only a
small, host-neutral *interface*: source recognition, stable language identity,
function-boundary extraction and the approved probe ecosystem.  Consumers must
use these profiles instead of keeping their own extension maps.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
from pathlib import Path
import re
from typing import Iterable


@dataclass(frozen=True)
class LanguageProfile:
    key: str
    display_name: str
    extensions: frozenset[str]
    codegraph_languages: tuple[str, ...]
    treesitter_languages: tuple[str, ...]
    probe_extension: str | None
    build_ecosystems: tuple[str, ...]
    runtime_ecosystems: tuple[str, ...]
    support_level: str


def _profile(key, display_name, extensions, codegraph=(), treesitter=(), probe=None,
             build=(), runtime=(), level="core") -> LanguageProfile:
    return LanguageProfile(key, display_name, frozenset(extensions), tuple(codegraph),
                           tuple(treesitter), probe, tuple(build), tuple(runtime), level)


PROFILES: tuple[LanguageProfile, ...] = (
    _profile("python", "Python", {".py"}, ("python",), ("python",), ".py", ("python",), ("python",)),
    _profile("javascript", "JavaScript", {".js", ".jsx", ".mjs", ".cjs"}, ("javascript", "jsx"), ("javascript",), ".js", ("node",), ("node",)),
    _profile("typescript", "TypeScript", {".ts", ".tsx", ".mts", ".cts"}, ("typescript", "tsx"), ("typescript", "tsx"), ".ts", ("typescript",), ("tsx",)),
    _profile("go", "Go", {".go"}, ("go",), ("go",), ".go", ("go",), ("go",)),
    _profile("rust", "Rust", {".rs"}, ("rust",), ("rust",), ".rs", ("cargo",), ("cargo",)),
    _profile("java", "Java", {".java"}, ("java",), ("java",), ".java", ("maven", "gradle"), ("maven", "gradle")),
    _profile("c", "C", {".c"}, ("c",), ("c",), ".c", ("cmake",), ("cmake",)),
    _profile("cpp", "C++", {".cc", ".cpp", ".cxx", ".h", ".hpp"}, ("cpp",), ("cpp",), ".cpp", ("cmake",), ("cmake",)),
    _profile("cuda", "CUDA", {".cu", ".cuh"}, ("cpp",), ("cpp",), ".cu", ("cuda",), (), "external-toolchain"),
    _profile("arkts", "ArkTS", {".ets"}, (), ("typescript",), ".ets", ("arkts",), (), "external-toolchain"),
    _profile("erlang", "Erlang", {".erl"}, (), (), ".erl", ("elp",), (), "external-plugin"),
)

_BY_KEY = {profile.key: profile for profile in PROFILES}
_BY_EXTENSION = {extension: profile for profile in PROFILES for extension in profile.extensions}
CORE_LEVELS = {"core", "external-toolchain"}


def profile_for_path(path: str | Path) -> LanguageProfile | None:
    return _BY_EXTENSION.get(Path(path).suffix.lower())


def profile_for_key(key: str) -> LanguageProfile | None:
    return _BY_KEY.get(key)


def source_extensions(include_external: bool = False) -> frozenset[str]:
    return frozenset(
        extension for profile in PROFILES
        if include_external or profile.support_level in CORE_LEVELS
        for extension in profile.extensions
    )


def language_name(path: str | Path) -> str:
    profile = profile_for_path(path)
    return profile.key if profile else "unknown"


def capability_matrix() -> list[dict]:
    return [{
        "key": item.key, "extensions": sorted(item.extensions),
        "codegraph_languages": list(item.codegraph_languages),
        "treesitter_languages": list(item.treesitter_languages),
        "build_ecosystems": list(item.build_ecosystems),
        "runtime_ecosystems": list(item.runtime_ecosystems),
        "support_level": item.support_level,
    } for item in PROFILES]


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _brace_end(text: str, opening: int) -> int:
    """Find a brace body without treating quoted text/comments as code."""
    depth, index, quote, block_comment = 0, opening, None, False
    while index < len(text):
        char, next_char = text[index], text[index + 1] if index + 1 < len(text) else ""
        if block_comment:
            if char == "*" and next_char == "/": block_comment, index = False, index + 2; continue
            index += 1; continue
        if quote:
            if char == "\\": index += 2; continue
            if char == quote: quote = None
            index += 1; continue
        if char in {"'", '"', "`"}: quote = char; index += 1; continue
        if char == "/" and next_char == "*": block_comment, index = True, index + 2; continue
        if char == "/" and next_char == "/":
            newline = text.find("\n", index + 2); index = len(text) if newline < 0 else newline + 1; continue
        if char == "{": depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0: return index
        index += 1
    return len(text) - 1


def _brace_spans(text: str, pattern: re.Pattern[str]) -> list[tuple[str, int, int]]:
    spans = []
    for match in pattern.finditer(text):
        opening = text.find("{", match.start(), match.end())
        if opening < 0: continue
        spans.append((match.group("name"), _line_number(text, match.start()), _line_number(text, _brace_end(text, opening))))
    return spans


_PATTERNS = {
    "go": re.compile(r"(?m)^\s*func\s+(?:\([^\n)]*\)\s*)?(?P<name>[A-Za-z_]\w*)[^\n{]*\{"),
    "rust": re.compile(r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+)?fn\s+(?P<name>[A-Za-z_]\w*)[^\n{]*\{"),
    "java": re.compile(r"(?m)^\s*(?:(?:public|protected|private|static|final|synchronized|native|abstract)\s+)*(?:<[^{;()]+>\s+)?[\w.$<>\[\],?]+\s+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:throws[^\n{]+)?\{"),
    "c": re.compile(r"(?m)^\s*(?:[\w:*&<>]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"),
    "cpp": re.compile(r"(?m)^\s*(?:[\w:~*&<> ,]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?\{"),
    "cuda": re.compile(r"(?m)^\s*(?:(?:__global__|__device__|__host__)\s+)?(?:[\w:*&<>]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"),
    "javascript": re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*\([^\n{]*\)\s*\{"),
    "typescript": re.compile(r"(?m)^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^\n>{}]+>)?\s*\([^\n{]*\)\s*(?::[^\n{=]+)?\{"),
    "arkts": re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\([^\n{]*\)\s*\{"),
}


def _python_spans(path: Path) -> list[tuple[str, int, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    return [(node.name, node.lineno, getattr(node, "end_lineno", node.lineno))
            for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _tree_sitter_spans(path: Path, profile: LanguageProfile) -> list[tuple[str, int, int]] | None:
    """Use Tree-sitter when installed; absence is an explicit portable fallback."""
    try:
        from tree_sitter_languages import get_parser  # type: ignore[import-not-found]
    except ImportError:
        return None
    parser = None
    for name in profile.treesitter_languages:
        try:
            parser = get_parser(name); break
        except Exception:
            continue
    if parser is None: return None
    source = path.read_bytes(); tree = parser.parse(source); found = []
    node_types = {"function_definition", "function_declaration", "function_item", "method_definition", "method_declaration", "generator_function_declaration"}
    stack = [tree.root_node]
    while stack:
        node = stack.pop(); stack.extend(reversed(node.children))
        if node.type not in node_types: continue
        name_node = node.child_by_field_name("name")
        if name_node is None: continue
        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
        if re.fullmatch(r"[A-Za-z_$][\w$]*", name): found.append((name, node.start_point[0] + 1, node.end_point[0] + 1))
    return found


def fallback_spans(path: Path, profile: LanguageProfile) -> list[tuple[str, int, int]]:
    if profile.key == "python": return _python_spans(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = _PATTERNS.get(profile.key)
    if pattern is None: return []
    spans = _brace_spans(text, pattern)
    # Named JS/TS arrow functions have no equivalent C-like declaration.
    if profile.key in {"javascript", "typescript"}:
        arrow = re.compile(r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^\n]*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{")
        spans.extend(_brace_spans(text, arrow))
    return sorted(set(spans), key=lambda item: (item[1], item[2], item[0]))


def function_spans(path: Path, codegraph_spans: Iterable[tuple[str, int, int]] | None = None) -> tuple[list[tuple[str, int, int]], str]:
    """Return spans with provenance: codegraph, tree-sitter, or language fallback."""
    profile = profile_for_path(path)
    if profile is None: return [], "unsupported"
    if codegraph_spans is not None:
        spans = [(str(name), int(start), int(end)) for name, start, end in codegraph_spans if int(start) >= 1 and int(end) >= int(start)]
        if spans: return spans, "codegraph"
    parsed = _tree_sitter_spans(path, profile)
    if parsed is not None: return parsed, "tree-sitter"
    try: return fallback_spans(path, profile), "language-fallback"
    except (SyntaxError, OSError): return [], "parse-error"
