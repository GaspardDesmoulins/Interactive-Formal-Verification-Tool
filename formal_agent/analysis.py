"""Extract syntactic observations without importing or executing a target."""

import ast
import hashlib
import io
import tokenize
from pathlib import Path
from typing import Any


class FunctionBodyVisitor(ast.NodeVisitor):
    """Visit one function body without attributing nested bodies to it."""

    def __init__(self) -> None:
        self.calls: set[str] = set()
        self.raises: list[int] = []
        self.returns: list[int] = []
        self.imports: list[dict[str, Any]] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append({"line": node.lineno, "statement": ast.unparse(node)})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append({"line": node.lineno, "statement": ast.unparse(node)})

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.add(ast.unparse(node.func))
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self.raises.append(node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append(node.lineno)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass


def analyze(path: Path) -> dict[str, Any]:
    """Return syntactic observations for module-level functions."""
    source_bytes = path.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(source_bytes).readline)
    source = source_bytes.decode(encoding)
    tree = ast.parse(source, filename=str(path))
    functions = []
    module_visitor = FunctionBodyVisitor()
    module_visitor.visit(tree)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        visitor = FunctionBodyVisitor()
        for statement in node.body:
            visitor.visit(statement)
        args = node.args
        parameters = [
            arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        ]
        if args.vararg:
            parameters.append("*" + args.vararg.arg)
        if args.kwarg:
            parameters.append("**" + args.kwarg.arg)
        functions.append({
            "name": node.name,
            "line": node.lineno,
            "end_line": node.end_lineno,
            "async": isinstance(node, ast.AsyncFunctionDef),
            "parameters": parameters,
            "calls": sorted(visitor.calls),
            "imports": visitor.imports,
            "return_lines": sorted(visitor.returns),
            "raise_lines": sorted(visitor.raises),
            "docstring": ast.get_docstring(node),
        })
    return {
        "source": str(path.resolve()),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "evidence_sources": [],
        "imports": module_visitor.imports,
        "functions": functions,
    }
