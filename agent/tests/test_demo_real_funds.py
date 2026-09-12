"""demo_real_funds uses real, currently-traded scheme codes and exit-load
rules sourced from public scheme documents (see
tools/build_demo_real_funds.py for the citation trail), not invented
numbers. This pins the one thing that actually matters for that claim:
the executed plan pays a real, non-zero, traceable exit load - not a
placeholder that happens to be zero."""

from __future__ import annotations

from agent.tests.conftest import load_scenario, seeded_run

QUANT_SMALL_CAP = "100176"          # AMFI scheme code, real fund
HDFC_FLEXI_CAP = "101763"
UTI_NIFTY_50 = "100823"
HDFC_LIQUID = "100872"


def test_uses_real_amfi_scheme_codes():
    sc = load_scenario("demo_real_funds")
    assert set(sc["funds"]) == {QUANT_SMALL_CAP, HDFC_FLEXI_CAP, UTI_NIFTY_50, HDFC_LIQUID}


def test_plan_pays_a_real_nonzero_exit_load_on_quant_small_cap():
    sc = load_scenario("demo_real_funds")
    res = seeded_run(sc, "A")
    plan = res.plans.cheapest
    assert plan.verdict == "verified"
    assert plan.totals.exit_load > 0

    quant_leg = next(l for l in plan.legs if l.scheme_id == QUANT_SMALL_CAP)
    assert quant_leg.exit_load > 0
    # every other real scheme here is genuinely 0% right now (index fund,
    # liquid fund past its 7-day window, an old flexicap lot past 365d)
    for leg in plan.legs:
        if leg.scheme_id != QUANT_SMALL_CAP:
            assert leg.exit_load == 0


def test_exit_load_constraint_cites_the_real_sid():
    sc = load_scenario("demo_real_funds")
    quant_exit = next(
        c for c in sc["constraints"] if c.scheme_id == QUANT_SMALL_CAP and c.kind == "exit_load"
    )
    assert quant_exit.value == {"pct": 1.0, "window_days": 365}
    assert "quantmutual.com" in quant_exit.source.url
    assert quant_exit.source.page == 3
