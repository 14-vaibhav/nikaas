"""Gemini implementation of the same `LLMClient` interface `extractor.py`
defines (`extract(system, page_text, tool) -> dict`), so document intake
can run on a free Google AI Studio key instead of the metered Anthropic
API. Nothing else in the pipeline (`extractor.py`, `fund_identity.py`,
`statement_extractor.py`) needs to know which provider is running -
`api/main.py` picks one via the `LLM_PROVIDER` env var.

This repo's tool schemas are written in Anthropic's tool-use shape
(`{"name", "description", "input_schema"}`) with an occasional nullable
union field (`"type": ["string", "null"]`). `_to_gemini_schema` converts
that into Gemini's `type` + `nullable` shape before it's sent; everything
else in the schema passes through unchanged.
"""

from __future__ import annotations

import os
from typing import Optional

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")


class GeminiExtractionClient:
    """Forces exactly one function call per request, same job as the
    Anthropic client's `tool_choice` - `extract()` always returns the
    called function's arguments, never freeform text."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        from google import genai  # imported here, not at module load, so

        # importing this module never requires the package to be installed
        # unless Gemini is the provider actually in use.
        self._client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))
        self._model = model or GEMINI_MODEL

    def extract(self, system: str, page_text: str, tool: dict) -> dict:
        from google.genai import types

        declaration = types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters_json_schema=_to_gemini_schema(tool["input_schema"]),
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=page_text,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=[declaration])],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=[tool["name"]],
                    ),
                ),
            ),
        )
        for candidate in response.candidates or []:
            for part in candidate.content.parts:
                fc = getattr(part, "function_call", None)
                if fc is not None and fc.name == tool["name"]:
                    return dict(fc.args or {})
        return {}

    def extract_image(self, system: str, image_bytes: bytes, mime_type: str, tool: dict) -> dict:
        from google.genai import types

        declaration = types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters_json_schema=_to_gemini_schema(tool["input_schema"]),
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=[declaration])],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=[tool["name"]],
                    ),
                ),
            ),
        )
        for candidate in response.candidates or []:
            for part in candidate.content.parts:
                fc = getattr(part, "function_call", None)
                if fc is not None and fc.name == tool["name"]:
                    return dict(fc.args or {})
        return {}


def _to_gemini_schema(schema):
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    t = out.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        out["type"] = non_null[0] if non_null else "string"
        if "null" in t:
            out["nullable"] = True
    if "properties" in out:
        out["properties"] = {k: _to_gemini_schema(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _to_gemini_schema(out["items"])
    return out
