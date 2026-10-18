from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

GENERATOR = Path(__file__).resolve().parent.parent / "scripts" / "generate_catalog.py"


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_catalog", GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_placeholder_the_spec_does_not_declare_is_still_a_path_parameter() -> None:
    # Amazon's marketing mix modeling spec writes {brandGroupId} and {reportId} into its
    # paths and declares neither, so the catalog said those operations took no path
    # parameters. Here {reportId} is declared and {brandGroupId} is not: both must be
    # listed, each once.
    spec = {
        "paths": {
            "/mmm/v1/brandGroups/{brandGroupId}/reports/{reportId}": {
                "get": {
                    "operationId": "getReport",
                    "parameters": [{"name": "reportId", "in": "path", "required": True}],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        }
    }
    [op] = load_generator().operations("example", spec)
    assert sorted(op["path_params"]) == ["brandGroupId", "reportId"]
