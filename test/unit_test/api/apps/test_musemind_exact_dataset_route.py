"""Exercise exact dataset route dispatch without application/service startup."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.utils.musemind_provider_contract import MuseMindDatasetCreateOrAdoptV1, MuseMindDatasetCreateOrAdoptV2, MuseMindDatasetCreateOrAdoptV3


def load_route(raw):
    source = Path(__file__).resolve().parents[4] / "api/apps/restful_apis/dataset_api.py"
    node = next(node for node in ast.parse(source.read_text(encoding="utf-8")).body if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_or_adopt_musemind_exact_dataset")
    node.decorator_list = []
    validate = AsyncMock(return_value=({"synthetic": True}, None))
    service = MagicMock(return_value=(True, {"outcome": "CREATED"}))
    scope = {
        "request": SimpleNamespace(get_json=AsyncMock(return_value=raw)),
        "validate_and_parse_json_request": validate,
        "dataset_api_service": SimpleNamespace(create_or_adopt_musemind_dataset=service),
        "get_result": lambda **value: value,
        "get_error_argument_result": lambda value: {"error": value},
        "MuseMindDatasetCreateOrAdoptV1": MuseMindDatasetCreateOrAdoptV1,
        "MuseMindDatasetCreateOrAdoptV2": MuseMindDatasetCreateOrAdoptV2,
        "MuseMindDatasetCreateOrAdoptV3": MuseMindDatasetCreateOrAdoptV3,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), scope)  # noqa: S102 -- actual repository source
    return scope["create_or_adopt_musemind_exact_dataset"], validate, service


@pytest.mark.asyncio
@pytest.mark.parametrize(("version", "model"), [(1, MuseMindDatasetCreateOrAdoptV1), (2, MuseMindDatasetCreateOrAdoptV2), (3, MuseMindDatasetCreateOrAdoptV3)])
async def test_route_uses_the_explicit_version_model(version, model):
    route, validate, service = load_route({"schema": f"musemind.ragflow-dataset-create-or-adopt/v{version}"})
    assert await route("tenant-1") == {"data": {"outcome": "CREATED"}}
    assert validate.await_args.args[1] is model
    service.assert_called_once_with("tenant-1", {"synthetic": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, [], {}, {"schema": []}, {"schema": "musemind.ragflow-dataset-create-or-adopt/v99"}])
async def test_route_rejects_unknown_version_before_service(raw):
    route, validate, service = load_route(raw)
    assert await route("tenant-1") == {"error": "Unsupported MuseMind dataset contract"}
    validate.assert_not_awaited()
    service.assert_not_called()
