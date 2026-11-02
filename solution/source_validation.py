"""Independent structural checks for the two languages present in the assignment."""

import ast

import tree_sitter_javascript
from tree_sitter import Language, Parser

from .models import Function


def extract_functions(source: str, language: str) -> list[Function] | None:
    if language.strip().lower() == "python":
        tree = ast.parse(source)
        nodes = sorted(
            (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
            key=lambda n: (n.lineno, n.col_offset),
        )
        return [
            Function(
                name=n.name,
                argument_count=(
                    len(n.args.posonlyargs)
                    + len(n.args.args)
                    + len(n.args.kwonlyargs)
                    + int(n.args.vararg is not None)
                    + int(n.args.kwarg is not None)
                ),
            )
            for n in nodes
        ]
    if language.strip().lower() in {"javascript", "js"}:
        js_tree = Parser(Language(tree_sitter_javascript.language())).parse(source.encode())
        if js_tree.root_node.has_error:
            raise ValueError("JavaScript syntax validation failed")
        functions = []
        stack = [js_tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in {
                "function_declaration",
                "generator_function_declaration",
                "function_expression",
                "generator_function",
                "method_definition",
            }:
                name = node.child_by_field_name("name")
                parameters = node.child_by_field_name("parameters")
                if name is not None and parameters is not None:
                    functions.append(
                        Function(
                            name=source.encode()[name.start_byte : name.end_byte].decode(),
                            argument_count=sum(
                                c.type != "comment" for c in parameters.named_children
                            ),
                        )
                    )
            stack.extend(reversed(node.named_children))
        return functions
    return None
