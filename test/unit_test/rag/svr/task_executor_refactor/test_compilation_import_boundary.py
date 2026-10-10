"""Exercise actual worker entrypoint bodies without importing the service dependency graph.

The live loader/GIL inversion remains a hypothesis, not something this regression simulates.
The verified boundary is that normalization never imports the web application, including when
templates are configured. Services/models are isolated; no provider or LLM is called.
"""

import __future__
import ast
import builtins
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[5]
WORKERS = ROOT / "rag/svr/task_executor_refactor"


def complete_without_io(coroutine):
    # These paths and the async tree-service double complete without external I/O.
    try:
        coroutine.send(None)
    except StopIteration:
        return
    finally:
        coroutine.close()
    raise AssertionError("unexpected external await in isolated entrypoint")


def test_shared_normalizer_import_is_pure_and_preserves_legacy_forms():
    # A fresh interpreter also detects future side effects added to common/__init__.py.
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import builtins, sys
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('api', 'quart', 'msgspec')):
        raise AssertionError('normalizer imported the web application')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from common.compilation_template import compilation_template_kind
assert compilation_template_kind(' Page-Index ') == 'timeline'
assert not any(name.startswith(('api.apps', 'quart', 'msgspec')) for name in sys.modules)
""",
            str(ROOT),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    helper = ast.parse((ROOT / "common/compilation_template.py").read_text(encoding="utf-8"))
    namespace = {}
    exec(compile(helper, "common/compilation_template.py", "exec"), namespace)
    normalize = namespace["compilation_template_kind"]
    structure = ast.parse((ROOT / "rag/advanced_rag/knowlege_compile/structure.py").read_text(encoding="utf-8"))
    legacy = next(node for node in structure.body if isinstance(node, ast.FunctionDef) and node.name == "_struct_normalize_kind")
    exec(compile(ast.Module(body=[legacy], type_ignores=[]), "legacy-normalizer", "exec"), namespace)
    for value, expected in (
        (None, ""),
        (True, ""),
        (0, ""),
        ([], ""),
        ({}, ""),
        ("", ""),
        (" \t", ""),
        ("PageIndex", "timeline"),
        (" page-index ", "timeline"),
        ("PAGE_INDEX", "timeline"),
        ("Knowledge-Graph", "timeline"),
        ("knowledge_graph", "timeline"),
        (" Timeline ", "timeline"),
        ("TREE", "tree"),
        (" Artifacts ", "artifacts"),
        ("List", "list"),
        ("set", "set"),
        ("hypergraph", "hypergraph"),
        ("Unknown-Kind", "unknown_kind"),
    ):
        assert normalize(value) == namespace["_struct_normalize_kind"](value) == expected


def test_worker_entrypoints_share_route_helper_without_web_imports(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    original_import = builtins.__import__
    services = SimpleNamespace(
        DocumentService=object(),
        KnowledgebaseService=SimpleNamespace(get_by_id=lambda _: (False, None)),
        CompilationTemplateService=object(),
        LLMBundle=object(),
        get_tenant_default_model_by_type=object(),
        get_model_config_from_provider_instance=object(),
    )

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith("api.apps"), "background worker imported the web application"
        if name.startswith("api.db."):
            return services
        return original_import(name, *args, **kwargs)

    def load(path, function=None):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "common.compilation_template"]
        assert len(imports) == 1
        assert [(alias.name, alias.asname) for alias in imports[0].names] == [("compilation_template_kind", "_compilation_template_kind")]
        if function:
            assert not any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("api.apps") for node in ast.walk(tree))
        body = imports + ([next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == function)] if function else [])
        namespace = {"__builtins__": {**vars(builtins), "__import__": guarded_import}}
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec", flags=__future__.annotations.compiler_flag), namespace)
        return namespace

    route = load(ROOT / "api/apps/restful_apis/chunk_api.py")
    document = load(WORKERS / "chunk_post_processor.py", "run_document_structure_compile")
    wiki = load(WORKERS / "dataset_wiki_generator.py", "run_wiki")
    assert route["_compilation_template_kind"] is document["_compilation_template_kind"] is wiki["_compilation_template_kind"]
    ctx = SimpleNamespace(parser_config={}, tenant_id="synthetic", language="en", kb_id="synthetic", progress_cb=lambda *args, **kwargs: None)
    handler = SimpleNamespace(_task_context=ctx)
    document["_parser_config_compilation_template_ids"] = lambda *_: []
    complete_without_io(document["run_document_structure_compile"](handler, None))
    called = []

    async def tree_templates(handler, templates, models, embedding):
        called.extend(templates)

    document.update(
        _parser_config_compilation_template_ids=lambda *_: ["template"],
        CompilationTemplateService=SimpleNamespace(get_saved=lambda *_: {"config": {"kind": " TREE "}}),
        _resolve_template_chat_llm_id=lambda *_: "model",
        get_model_config_from_provider_instance=lambda *_: {},
        LLMBundle=lambda *args, **kwargs: object(),
        LLMType=SimpleNamespace(CHAT="chat"),
        run_tree_templates=tree_templates,
    )
    complete_without_io(document["run_document_structure_compile"](handler, None))
    assert called == [("template", {"kind": " TREE "})]
    complete_without_io(wiki["run_wiki"](ctx, None, None))
