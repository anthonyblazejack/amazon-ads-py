from __future__ import annotations

import pytest
import respx
from httpx import Response

from amazon_ads import ProfileClient, UnsupportedOperationError, catalog
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


# Invented invoice id. The wrapper shape is what the live API returned.
INVOICE_PAGE = {"status": "success", "payload": {"invoiceSummaries": [{"id": "INV-0001"}]}}


@pytest.mark.parametrize(
    ("operation", "url", "path_params"),
    [
        ("advertising-billing:getAdvertiserInvoices", f"{NA}/invoices", None),
        ("advertising-billing:getInvoice", f"{NA}/invoices/INV-0001", {"invoiceId": "INV-0001"}),
    ],
)
def test_invoice_reads_send_the_accept_type_amazon_answers(
    api: respx.MockRouter,
    us: ProfileClient,
    operation: str,
    url: str,
    path_params: dict[str, str] | None,
) -> None:
    """Amazon answers the only Accept type these specs document with 406 and an empty
    body. If a catalog regeneration or a lost correction brought it back, invoices could
    not be read at all."""
    route = api.get(url).mock(return_value=Response(200, json=INVOICE_PAGE))
    us.call(operation, path_params=path_params)
    assert route.calls.last.request.headers["Accept"] == "application/json"


def test_invoice_summaries_keep_the_media_types_their_spec_documents(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    """POST /invoiceSummaries/list works live with its vendor types. A correction keyed
    by the whole billing API instead of by operation id would switch it too."""
    op = catalog.get("advertising-billing:GetBillingInvoiceSummaries")
    route = api.post(f"{NA}/invoiceSummaries/list").mock(
        return_value=Response(200, json={"invoiceSummaries": []})
    )
    us.call(op.id, {"maxResults": 1})
    sent = route.calls.last.request.headers
    assert sent["Accept"] in op.accepts
    assert sent["Content-Type"] in op.content_types


def test_every_correction_still_applies_to_an_operation_whose_spec_disagrees() -> None:
    """A correction keyed by an id Amazon has since renamed silently stops applying, and
    one whose type the spec now documents may no longer be needed. Either way, re-check
    the live API and update catalog.CORRECTIONS."""
    ids = {op.id for op in catalog.operations()}
    for op_id, correction in catalog.CORRECTIONS.items():
        assert op_id in ids, f"{op_id} is gone from the catalog"
        op = catalog.get(op_id)
        assert correction.accept not in op.accepts, f"{op_id}'s spec now documents it"
        assert op.accept == correction.accept
        assert op.note


def test_no_operation_without_a_correction_sends_a_type_its_spec_lacks() -> None:
    """Only types checked against the live API may differ from the spec. Catches a
    correction leaking past the operations it was verified on, however it is keyed."""
    for op in catalog.operations():
        if op.id not in catalog.CORRECTIONS:
            assert op.accept is None or op.accept in op.accepts, op.id


def test_every_mcp_tool_and_nothing_else_is_refused() -> None:
    """Every hosted MCP tool is POST /mcp/<tool>, a path the Ads API hosts do not serve.
    Catches the tool specs moving to a folder the source check misses, and a check so
    broad it refuses real Ads API operations."""
    for op in catalog.operations():
        assert (op.unsupported_reason is not None) == op.path.startswith("/mcp/"), op.id


@pytest.mark.parametrize(
    ("tool", "instead"),
    [
        ("mcp-billing:list_invoices", "advertising-billing:getAdvertiserInvoices"),
        ("mcp-billing:list_invoice_summaries", "advertising-billing:GetBillingInvoiceSummaries"),
    ],
)
def test_calling_an_mcp_tool_sends_nothing_and_names_the_operation_to_use(
    api: respx.MockRouter, us: ProfileClient, tool: str, instead: str
) -> None:
    """Both were checked live: the tool gets a 403 and the named operation reads the same
    data, so an agent refused here knows its next call."""
    with pytest.raises(UnsupportedOperationError, match=instead):
        us.call(tool, {})
    assert not api.calls, "not even a token request should go out"


def test_search_hides_mcp_tools_unless_asked() -> None:
    """An agent searching for invoices should find the Ads API operations, not tools it
    cannot call. Scoping the search to an MCP tool API must explain itself rather than
    come back empty."""
    default = {op.id for op in catalog.search("invoices")}
    assert {
        "advertising-billing:getAdvertiserInvoices",
        "advertising-billing:getInvoice",
    } <= default
    assert not any(catalog.get(i).unsupported_reason for i in default)

    asked = catalog.search("invoices", include_unsupported=True)
    assert "mcp-billing:list_invoices" in {op.id for op in asked}

    scoped = catalog.search("invoices", api="mcp-billing")
    assert scoped
    assert all(op.unsupported_reason for op in scoped)


def test_operations_named_in_mcp_refusals_can_be_called() -> None:
    """A refusal that points at an operation call_operation would also refuse just moves
    the wasted call."""
    for tool, instead in catalog.INSTEAD_OF_MCP_TOOL.items():
        assert catalog.get(tool).unsupported_reason, tool
        target = catalog.get(instead)
        assert target.unsupported_reason is None, instead
        assert target.read_only, instead
