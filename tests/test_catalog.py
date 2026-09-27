from __future__ import annotations

import pytest
import respx
from httpx import Response

from amazon_ads import ProfileClient, catalog
from tests.conftest import NA


def test_catalog_covers_every_published_api() -> None:
    apis = catalog.apis()
    assert len(apis) >= 200
    for slug in (
        "sponsored-products",
        "sponsored-brands-v4",
        "sponsored-display",
        "reporting",
        "history",
        "profiles",
        "dsp-campaigns",
    ):
        assert slug in apis
    assert len(catalog.operations()) >= 1000


def test_every_operation_can_be_sent() -> None:
    for op in catalog.operations():
        assert op.method in {"GET", "POST", "PUT", "DELETE", "PATCH"}, op.id
        assert op.path.startswith("/"), op.id
        for name in op.path_params:
            assert "{" + name + "}" in op.path, op.id


def test_sp_media_types_match_what_the_typed_layer_sends() -> None:
    from amazon_ads.sp import ALL_SPECS

    for spec in ALL_SPECS:
        op = next(
            o
            for o in catalog.operations(
                "sponsored-products" if spec.kind != "portfolios" else "portfolios"
            )
            if o.path == f"{spec.path}/list"
        )
        assert op.content_type == spec.media_type, spec.kind


@pytest.mark.parametrize(
    ("operation", "read_only"),
    [
        ("sponsored-products:ListSponsoredProductsKeywords", True),
        ("sponsored-products:CreateSponsoredProductsKeywords", False),
        ("sponsored-products:UpdateSponsoredProductsKeywords", False),
        ("sponsored-products:DeleteSponsoredProductsKeywords", False),
        ("history:getHistory", True),
        ("profiles:listProfiles", True),
    ],
)
def test_read_only_classification(operation: str, read_only: bool) -> None:
    assert catalog.get(operation).read_only is read_only


def test_lookup_by_bare_id_and_search() -> None:
    assert catalog.get("ListSponsoredProductsKeywords").api == "sponsored-products"
    assert any(op.path == "/sp/keywords/list" for op in catalog.search("keywords list"))
    with pytest.raises(KeyError):
        catalog.get("NoSuchOperation")


def test_call_sends_the_catalogued_method_path_and_types(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.get(f"{NA}/reporting/reports/abc").mock(
        return_value=Response(200, json={"reportId": "abc", "status": "PENDING"})
    )
    out = us.call("reporting:getAsyncReport", path_params={"reportId": "abc"})
    assert out["status"] == "PENDING"
    assert (
        route.calls.last.request.headers["Accept"]
        == "application/vnd.getasyncreportresponse.v3+json"
    )


def test_call_refuses_missing_path_parameters(us: ProfileClient) -> None:
    with pytest.raises(ValueError, match="reportId"):
        us.call("reporting:getAsyncReport")
