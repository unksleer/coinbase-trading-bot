"""
The signal labels are the bot's own vocabulary and none of them explain
themselves. NO SETUP is the resting state of every mean-reversion bot, so it is
the state shown most often, and it reads like something is broken when it means
the opposite.

The risk with a legend is that it drifts: a state gets added or renamed in the
table and the legend keeps describing the old set, which is worse than no
legend because it is confidently wrong. So the test compares the two.
"""
import os
import re

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(DIR, "dashboard", "index.html")


def _page():
    with open(PAGE) as handle:
        return handle.read()


def table_states(page):
    """States the signal table can render."""
    return set(re.findall(r"'([A-Z ]+)':\s*\{ cls: 'sig-", page))


def legend_states(page):
    found = set()
    for badge in re.findall(
            r'<dt><span class="coin-signal sig-\w+">(.+?)</span></dt>', page):
        name = badge.split(" ", 1)[1].strip() if " " in badge else badge.strip()
        # The table spells this one out; the legend matches its wording.
        found.add("CLOSED" if name == "MARKET CLOSED" else name)
    return found


def test_legend_covers_every_state_the_table_renders():
    page = _page()
    missing = table_states(page) - legend_states(page)
    assert not missing, f"states with no legend entry: {sorted(missing)}"


def test_legend_describes_no_states_that_no_longer_exist():
    page = _page()
    stale = legend_states(page) - table_states(page)
    assert not stale, f"legend describes removed states: {sorted(stale)}"


def test_legend_badges_use_classes_the_stylesheet_defines():
    page = _page()
    defined = set(re.findall(r"^\s*\.(sig-[\w-]+)\s*\{", page, re.MULTILINE))
    used = set(re.findall(r'<dt><span class="coin-signal (sig-[\w-]+)"', page))
    assert used <= defined, f"undefined badge classes: {sorted(used - defined)}"


def test_no_setup_is_explained_as_normal():
    """
    The specific confusion that prompted this: NO SETUP looks like a fault.
    The entry must say plainly that nothing is wrong.
    """
    page = _page()
    # The <dd> that follows the NO SETUP badge — not the first mention of the
    # string on the page, which is the comment explaining why this exists.
    match = re.search(
        r'<dt><span class="coin-signal sig-\w+">\S+ NO SETUP</span></dt>\s*'
        r'<dd>(.*?)</dd>', page, re.DOTALL)
    assert match, "no legend entry for NO SETUP"
    body = match.group(1).lower()
    assert "nothing is wrong" in body
    assert "oversold" in body


def test_legend_is_attached_to_the_signal_panel():
    """A legend elsewhere on the page is not a legend."""
    page = _page()
    panel = page.split('id="bot-signal-panel"', 1)[1].split("</div>\n        </div>")[0]
    assert "signal-legend" in panel
