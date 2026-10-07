"""
Housekeeping guards: no shell strings around pm2, and the backup restores.
"""
import ast, glob, os, subprocess, sys, tarfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import backup


def test_no_shell_true_anywhere_in_project_scripts():
    offenders = []
    for path in glob.glob(os.path.join(DIR, "*.py")) + glob.glob(os.path.join(DIR, "utils", "*.py")):
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        offenders.append(os.path.relpath(path, DIR))
    assert not offenders, offenders


def test_restart_passes_identity_through_env_not_a_shell_string(monkeypatch):
    import restart_bots
    seen = {}
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd; seen["env"] = kw.get("env")
        class R: returncode = 0; stderr = ""
        return R()
    monkeypatch.setattr(restart_bots.subprocess, "run", fake_run)
    restart_bots.restart("sol-bot", {"TRADING_PAIR": "SOL-USDC", "TRADE_AMOUNT_USD": 100})
    assert isinstance(seen["cmd"], list) and seen["cmd"][:3] == ["pm2", "restart", "sol-bot"]
    assert seen["env"]["TRADING_PAIR"] == "SOL-USDC" and seen["env"]["TRADE_AMOUNT_USD"] == "100"


def test_backup_bundles_the_repo_and_tars_the_evidence_but_never_env(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], cwd=root, check=True)
    (root / "state_sol-usdc.json").write_text("{}")
    (root / "completed_trades_stock_spy_paper.csv").write_text("a,b\n")
    (root / "experiments").mkdir(); (root / "experiments" / "r.json").write_text("{}")
    (root / ".env").write_text("SECRET=1")
    dest = tmp_path / "bk"
    info = backup.run(root=str(root), dest=str(dest), retain_days=14)
    names = tarfile.open(os.path.join(info["dir"], "evidence.tar.gz")).getnames()
    assert "state_sol-usdc.json" in names and "experiments/r.json" in names
    assert not any(".env" in n for n in names)
    assert subprocess.run(["git", "bundle", "verify", os.path.join(info["dir"], "repo.bundle")],
                          cwd=root, capture_output=True).returncode == 0


def test_backup_prunes_only_old_dated_folders(tmp_path):
    dest = tmp_path / "bk"; dest.mkdir()
    now = time.time()
    old = time.strftime("%Y-%m-%d", time.gmtime(now - 20 * 86400))
    recent = time.strftime("%Y-%m-%d", time.gmtime(now - 2 * 86400))
    for n in (old, recent, "keep-me"):
        (dest / n).mkdir()
    gone = backup.prune(str(dest), 14, now)
    assert gone == [old] and (dest / recent).exists() and (dest / "keep-me").exists()
