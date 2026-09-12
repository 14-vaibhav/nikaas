from __future__ import annotations

import agent.explain as explain_mod
from agent.explain import explain
from agent.tests.conftest import load_scenario, seeded_run


def test_explain_has_no_llm_import():
    src = (explain_mod.__file__)
    text = open(src).read()
    for bad in ("import anthropic", "from anthropic", "google.genai", "GeminiExtractionClient",
                "AnthropicExtractionClient"):
        assert bad not in text


def test_explain_renders_legs_totals_and_citations():
    sc = load_scenario("demo_cas_18")
    res = seeded_run(sc, "A")
    plan = res.plans.cheapest
    text = explain(plan, sc["constraints"], mode_label="Mode A", abstentions=res.abstentions)

    assert "What would be redeemed" in text
    assert "Cost summary" in text
    assert "Net to you" in text
    # every applied constraint id should surface as a citation (doc + clause)
    for leg in plan.legs:
        for cid in leg.constraints_applied:
            c = next(c for c in sc["constraints"] if c.id == cid)
            assert c.source.doc in text
            if c.source.clause:
                assert f"clause {c.source.clause}" in text
    # abstentions carried through
    assert "Deliberately not touched" in text
    assert "not investment advice" in text.lower()


def test_explain_flags_an_unverified_plan():
    sc = load_scenario("demo_cas_18")
    res = seeded_run(sc, "A")
    plan = res.plans.cheapest
    plan.verdict = "rejected"
    text = explain(plan, sc["constraints"])
    assert "did not pass the independent verifier" in text
