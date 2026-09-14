"""
Importing auto_learner must not configure the process-wide logger.

It did, at module level, so every research script and the hourly candle
collector — which import it only for fetch_candles — had their logging
redirected into logs/auto_learner.log. Collector output was filed as learner
activity every hour. Configuring logging belongs to whoever is running.
"""
import logging
import os
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_import_installs_no_handlers():
    """Run in a fresh interpreter so this test's own logging state is not in play."""
    code = (
        "import logging, sys; sys.path.insert(0, %r)\n"
        "before = len(logging.getLogger().handlers)\n"
        "import auto_learner\n"
        "after = len(logging.getLogger().handlers)\n"
        "print(before, after)\n" % DIR)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=DIR, timeout=60)
    assert out.returncode == 0, out.stderr[-400:]
    before, after = map(int, out.stdout.split())
    assert after == before, "import added a root handler"


def test_running_as_a_script_still_configures_the_file_handler():
    src = open(os.path.join(DIR, "auto_learner.py")).read()
    main_block = src[src.index('if __name__ == "__main__":'):]
    assert "configure_logging()" in main_block
    assert "logging.basicConfig(" in src[src.index("def configure_logging"):]


def test_no_module_level_basicconfig_remains():
    src = open(os.path.join(DIR, "auto_learner.py")).read()
    head = src[:src.index("def configure_logging")]
    assert "logging.basicConfig(" not in head
