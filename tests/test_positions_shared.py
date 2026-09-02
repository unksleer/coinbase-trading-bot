"""
One position reader, because this was written twice and wrong twice.

Crypto state records `holdings_base` and is named state_<pair>_dry.json; stock
state records `shares` and is named state_stock_<ticker>_paper.json. Code
written for the crypto shape reads a quantity of 0 for every equity position
and derives a symbol of STOCK_WMT_PAPER, which no price feed resolves.

Either failure alone yields $0.00 unrealised — a missing number that renders as
a real one. The dashboard showed six positions flat while they were down $0.78
between them; the emailed statement showed the same zeros for weeks.
"""
import json
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from utils import positions  # noqa: E402


@pytest.fixture
def root(tmp_path):
    def write(name, payload):
        (tmp_path / name).write_text(json.dumps(payload))
    write("state_stock_wmt_paper.json",
          {"position": "LONG", "entry_price": 102.69, "shares": 0.0974})
    write("state_ada-usdc_dry.json",
          {"position": "LONG", "entry_price": 0.20, "holdings_base": 50.0})
    write("state_stock_spy_paper.json",
          {"position": "NONE", "entry_price": 0.0, "shares": 0.0})
    return str(tmp_path)


def test_stock_symbol_is_the_ticker(root):
    wmt = next(p for p in positions.open_positions(root) if p["market"] == "stocks")
    assert wmt["symbol"] == "WMT", "not STOCK_WMT_PAPER"


def test_stock_quantity_comes_from_shares(root):
    """`holdings_base` is absent from stock state; reading it gave 0."""
    wmt = next(p for p in positions.open_positions(root) if p["market"] == "stocks")
    assert wmt["quantity"] == pytest.approx(0.0974)


def test_crypto_quantity_comes_from_holdings_base(root):
    ada = next(p for p in positions.open_positions(root) if p["market"] == "crypto")
    assert ada["symbol"] == "ADA-USDC"
    assert ada["quantity"] == pytest.approx(50.0)


def test_flat_positions_are_not_reported(root):
    assert all(p["symbol"] != "SPY" for p in positions.open_positions(root))


def test_unknown_price_is_none_not_zero(root):
    """
    A position with no available price is not flat. Returning 0.0 is how six
    underwater positions read as breaking even.
    """
    wmt = next(p for p in positions.open_positions(root) if p["market"] == "stocks")
    assert positions.unrealised(wmt, None) is None
    assert positions.unrealised(wmt, 0) is None


def test_unrealised_subtracts_the_entry_fee(root):
    wmt = next(p for p in positions.open_positions(root) if p["market"] == "stocks")
    wmt["entry_fee_usd"] = 0.05
    assert positions.unrealised(wmt, 104.83) == pytest.approx(
        (104.83 - 102.69) * 0.0974 - 0.05)


def test_a_losing_position_reports_a_loss(root):
    ada = next(p for p in positions.open_positions(root) if p["market"] == "crypto")
    assert positions.unrealised(ada, 0.18) < 0


def test_both_consumers_use_the_shared_reader():
    """The whole point: a third consumer gets it right for free."""
    for name in ("daily_pnl_report.py", "dashboard.py"):
        with open(os.path.join(DIR, name)) as handle:
            body = handle.read()
        assert "utils import positions" in body or "utils.positions" in body, \
            f"{name} must use the shared reader"


def test_report_does_not_key_on_an_unset_env_var():
    """
    pm2 does not set STATE_FILE for these bots, so keying on it matched nothing
    and every position silently valued at zero.
    """
    with open(os.path.join(DIR, "daily_pnl_report.py")) as handle:
        body = handle.read()
    assert 'p["symbol"] == want' in body, "must fall back to matching on symbol"
