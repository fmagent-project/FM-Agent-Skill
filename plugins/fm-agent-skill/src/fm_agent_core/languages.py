"""Single source of truth for FM-Agent Skill language capabilities.

The Skill intentionally does not import FM-Agent.  This module mirrors only a
small, host-neutral *interface*: source recognition, stable language identity,
function-boundary extraction and the approved probe ecosystem.  Consumers must
use these profiles instead of keeping their own extension maps.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable


@dataclass(frozen=True)
class LanguageProfile:
    key: str
    display_name: str
    extensions: frozenset[str]
    codegraph_languages: tuple[str, ...]
    treesitter_languages: tuple[str, ...]
    span_extractor: str
    public_entry_strategy: str
    probe_extension: str | None
    build_detectors: tuple[str, ...]
    requires_build_metadata: bool
    build_adapter: str | None
    dynamic_adapter: str | None
    support_level: str


def _profile(key, display_name, extensions, codegraph=(), treesitter=(), probe=None,
             detectors=(), required_metadata=False, build_adapter=None, dynamic_adapter=None, level="static_only", extractor="codegraph-then-tree-sitter") -> LanguageProfile:
    return LanguageProfile(
        key, display_name, frozenset(extensions), tuple(codegraph), tuple(treesitter),
        extractor, "structured-public-entrypoint", probe,
        tuple(detectors), required_metadata, build_adapter, dynamic_adapter, level,
    )


PROFILES: tuple[LanguageProfile, ...] = (
    _profile("python", "Python", {".py"}, ("python",), ("python",), ".py", ("pyproject.toml", "setup.py", "setup.cfg"), False, "python", "python-packaging", "full", "codegraph-then-tree-sitter-then-python-ast"),
    _profile("javascript", "JavaScript", {".js", ".jsx", ".mjs", ".cjs"}, ("javascript", "jsx"), ("javascript",), ".js", ("package.json",), False, "javascript", "node", "full"),
    _profile("typescript", "TypeScript", {".ts", ".tsx", ".mts", ".cts"}, ("typescript", "tsx"), ("typescript", "tsx"), ".ts", ("tsconfig.json",), True, "typescript", "node-typescript", "full"),
    _profile("go", "Go", {".go"}, ("go",), ("go",), ".go", ("go.mod",), True, "go", "go-modules", "full"),
    _profile("rust", "Rust", {".rs"}, ("rust",), ("rust",), ".rs", ("Cargo.toml", "Cargo.lock"), True, "cargo", "cargo", "full"),
    _profile("java", "Java", {".java"}, ("java",), ("java",), ".java", ("pom.xml", "build.gradle", "build.gradle.kts"), False, "java", None, "static_only"),
    _profile("c", "C", {".c"}, ("c",), ("c",), ".c", ("CMakeLists.txt",), True, "cmake", None, "static_only", "codegraph-then-tree-sitter-then-clang-ast"),
    _profile("cpp", "C++", {".cc", ".cpp", ".cxx", ".h", ".hpp"}, ("cpp",), ("cpp",), ".cpp", ("CMakeLists.txt",), True, "cmake", None, "static_only", "codegraph-then-tree-sitter-then-clang-ast"),
    _profile("cuda", "CUDA", {".cu", ".cuh"}, ("cpp",), ("cpp",), ".cu", ("CMakeLists.txt",), True, None, None, "capability_plugin"),
    _profile("arkts", "ArkTS", {".ets"}, (), ("typescript",), ".ets", ("hvigorfile.ts", "build-profile.json5"), True, None, None, "capability_plugin"),
    _profile("erlang", "Erlang", {".erl"}, (), (), ".erl", ("rebar.config", "mix.exs"), True, None, None, "capability_plugin"),
)

_BY_KEY = {profile.key: profile for profile in PROFILES}
_BY_EXTENSION = {extension: profile for profile in PROFILES for extension in profile.extensions}
CORE_LEVELS = {"full", "static_only"}


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
        "span_extractor": item.span_extractor,
        "public_entry_strategy": item.public_entry_strategy,
        "build_detectors": list(item.build_detectors),
        "requires_build_metadata": item.requires_build_metadata,
        "build_adapter": item.build_adapter,
        "dynamic_adapter": item.dynamic_adapter,
        "support_level": item.support_level,
    } for item in PROFILES]


def probe_adapter_choices() -> tuple[str, ...]:
    """Return the only accepted build-adapter values from the registry."""
    adapters = sorted({item.build_adapter for item in PROFILES if item.build_adapter})
    return tuple(["auto", *adapters, "none"])


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

    def declaration_span(node):
        """Include `const`/`export` only when a declaration owns one function."""
        span = node; parent = node.parent
        while parent is not None and parent.type in {"variable_declaration", "lexical_declaration", "export_statement"}:
            declarators = [child for child in parent.named_children if child.type == "variable_declarator"]
            if parent.type != "export_statement" and len(declarators) != 1:
                break
            span, parent = parent, parent.parent
        return span

    stack = [tree.root_node]
    while stack:
        node = stack.pop(); stack.extend(reversed(node.children))
        span = node
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            name_node = node.child_by_field_name("name")
            if value is None or value.type != "arrow_function":
                continue
            span = declaration_span(node)
        elif node.type in node_types:
            name_node = node.child_by_field_name("name")
        else:
            continue
        if name_node is None: continue
        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
        if re.fullmatch(r"[A-Za-z_$][\w$]*", name): found.append((name, span.start_point[0] + 1, span.end_point[0] + 1))
    return sorted(set(found), key=lambda item: (item[1], item[2], item[0]))


def _clang_ast_spans(path: Path, profile: LanguageProfile) -> list[tuple[str, int, int]] | None:
    """Use Clang's JSON AST only for C-family profiles when Tree-sitter lacks a grammar."""
    if profile.key not in {"c", "cpp"}:
        return None
    compiler = shutil.which("clang" if profile.key == "c" else "clang++")
    if compiler is None:
        return None
    language = "c" if profile.key == "c" else "c++"
    include_dirs = [path.parent]
    include_dirs.extend(parent / "include" for parent in path.parents if (parent / "include").is_dir())
    include_args = [argument for directory in dict.fromkeys(item.resolve() for item in include_dirs) for argument in ("-I", str(directory))]
    try:
        completed = subprocess.run(
            [compiler, "-Xclang", "-ast-dump=json", "-fsyntax-only", "-x", language, *include_args, str(path)],
            text=True, capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    try:
        tree = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    source = path.resolve(); source_bytes = path.read_bytes(); spans: list[tuple[str, int, int]] = []
    declaration_kinds = {"FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl", "CXXConversionDecl"}
    stack = [tree]
    while stack:
        node = stack.pop()
        children = node.get("inner", []) if isinstance(node, dict) else []
        if isinstance(children, list):
            stack.extend(reversed(children))
        if not isinstance(node, dict) or node.get("kind") not in declaration_kinds:
            continue
        if not any(isinstance(child, dict) and child.get("kind") == "CompoundStmt" for child in children):
            continue
        location = node.get("loc", {}); source_range = node.get("range", {})
        begin, end = source_range.get("begin", {}), source_range.get("end", {})
        if not isinstance(location, dict) or not isinstance(begin, dict) or not isinstance(end, dict):
            continue
        if "includedFrom" in location or "includedFrom" in begin:
            continue
        file_name = location.get("file") or begin.get("file")
        if isinstance(file_name, str):
            try:
                if Path(file_name).resolve() != source:
                    continue
            except OSError:
                continue
        def line_for(location: dict, fallback: int | None = None) -> int | None:
            line, offset = location.get("line"), location.get("offset")
            if isinstance(line, int):
                return line
            if isinstance(offset, int) and offset >= 0:
                return source_bytes.count(b"\n", 0, offset) + 1
            return fallback

        name = node.get("name")
        start = line_for(begin, line_for(location))
        finish = line_for(end, start)
        if isinstance(name, str) and isinstance(start, int) and isinstance(finish, int) and start >= 1 and finish >= start:
            spans.append((name, start, finish))
    return sorted(set(spans), key=lambda item: (item[1], item[2], item[0]))


def function_spans(path: Path, codegraph_spans: Iterable[tuple[str, int, int]] | None = None) -> tuple[list[tuple[str, int, int]], str]:
    """Return spans from CodeGraph first, then a real syntax tree.

    There is deliberately no text/regular-expression fallback: a guessed
    boundary would corrupt specification and call-graph artifacts. Python can
    use its standard-library AST when Tree-sitter is unavailable; all other
    profiles fail closed until their declared grammar is installed. C and C++
    additionally use their profile-declared Clang AST adapter, never text.
    """
    profile = profile_for_path(path)
    if profile is None: return [], "unsupported"
    if codegraph_spans is not None:
        spans = [(str(name), int(start), int(end)) for name, start, end in codegraph_spans if int(start) >= 1 and int(end) >= int(start)]
        if spans: return spans, "codegraph"
    parsed = _tree_sitter_spans(path, profile)
    if parsed is not None: return parsed, "tree-sitter"
    parsed = _clang_ast_spans(path, profile)
    if parsed is not None: return parsed, "clang-ast"
    if profile.key == "python":
        try: return _python_spans(path), "python-ast"
        except (SyntaxError, OSError): return [], "parse-error"
    return [], "tree-sitter-unavailable"
