from __future__ import annotations

from agent.fund_identity import IDENTITY_TOOL
from agent.gemini_client import _to_gemini_schema


def test_nullable_union_becomes_type_plus_nullable_flag():
    schema = {"type": ["string", "null"], "description": "x"}
    out = _to_gemini_schema(schema)
    assert out["type"] == "string"
    assert out["nullable"] is True


def test_non_union_type_is_untouched():
    schema = {"type": "string", "enum": ["a", "b"]}
    out = _to_gemini_schema(schema)
    assert out == schema


def test_recurses_into_properties_and_items():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"]},
            "tags": {"type": "array", "items": {"type": ["string", "null"]}},
        },
    }
    out = _to_gemini_schema(schema)
    assert out["properties"]["name"] == {"type": "string", "nullable": True}
    assert out["properties"]["tags"]["items"] == {"type": "string", "nullable": True}


def test_real_identity_tool_schema_converts_without_error():
    out = _to_gemini_schema(IDENTITY_TOOL["input_schema"])
    assert out["properties"]["scheme_name"]["nullable"] is True
    assert out["properties"]["doc_type"]["type"] == "string"  # non-union field untouched
