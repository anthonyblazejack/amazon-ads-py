from __future__ import annotations

import pytest

from amazon_ads import PartialFailureError
from amazon_ads.batch import parse_multi_status

SENT = [{"keywordText": "a"}, {"keywordText": "b"}, {"keywordText": "c"}]


def test_v3_mixed_result_pairs_each_outcome_with_its_input() -> None:
    body = {
        "keywords": {
            "success": [
                {"index": 0, "keywordId": "100", "keyword": {"keywordId": "100"}},
                {"index": 2, "keywordId": "102"},
            ],
            "error": [
                {
                    "index": 1,
                    "errors": [
                        {
                            "errorType": "duplicateValueError",
                            "errorValue": {
                                "duplicateValueError": {
                                    "reason": "DUPLICATE_VALUE",
                                    "message": "Keyword already exists",
                                }
                            },
                        }
                    ],
                }
            ],
        }
    }
    result = parse_multi_status(body, SENT, entity_key="keywords", id_field="keywordId")
    assert result.ids == ["100", "102"]
    assert result.successes[0].entity == {"keywordId": "100"}
    [error] = result.errors
    assert error.index == 1
    assert error.item == {"keywordText": "b"}
    assert error.code == "DUPLICATE_VALUE"
    assert error.message == "Keyword already exists"
    assert not result.ok


def test_all_failed_still_parses_instead_of_looking_successful() -> None:
    body = {"keywords": {"success": [], "error": [{"index": 0, "errors": [{"errorType": "x"}]}]}}
    result = parse_multi_status(body, SENT[:1])
    with pytest.raises(PartialFailureError) as info:
        result.raise_for_errors()
    assert "1 of 1 items failed" in str(info.value)


def test_offset_maps_chunk_indexes_back_to_the_full_list() -> None:
    body = {"keywords": {"success": [{"index": 0, "keywordId": "5"}], "error": []}}
    result = parse_multi_status(body, SENT[2:], offset=2, id_field="keywordId")
    assert result.successes[0].index == 2
    assert result.successes[0].item == {"keywordText": "c"}


def test_legacy_list_shape() -> None:
    body = [
        {"code": "SUCCESS", "keywordId": 11},
        {"code": "INVALID_ARGUMENT", "description": "Bid too low"},
    ]
    result = parse_multi_status(body, SENT[:2])
    assert result.ids == ["11"]
    assert result.errors[0].message == "Bid too low"
    assert result.errors[0].code == "INVALID_ARGUMENT"


def test_bid_errors_carry_amazons_allowed_range() -> None:
    body = {
        "keywords": {
            "error": [
                {
                    "index": 0,
                    "errors": [
                        {
                            "errorType": "biddingError",
                            "errorValue": {
                                "biddingError": {
                                    "reason": "BID_OUT_OF_MARKET_PLACE_RANGE",
                                    "message": "Bid is out of range",
                                    "lowerLimit": 0.02,
                                    "upperLimit": 1000,
                                }
                            },
                        }
                    ],
                }
            ]
        }
    }
    [error] = parse_multi_status(body, SENT[:1]).errors
    assert (error.error_type, error.lower_limit, error.upper_limit) == ("biddingError", 0.02, 1000)
    assert error.hint == "Allowed range is 0.02 to 1000.0."
    assert not error.transient
