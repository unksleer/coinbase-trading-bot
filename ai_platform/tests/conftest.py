import os
import pytest

# Tests never touch the network or the real data dir.
os.environ["AI_PLATFORM_OFFLINE"] = "1"
os.environ["AI_PLATFORM_EMBEDDER"] = "hash"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from ai_platform import paths
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", d)
    monkeypatch.setattr(paths, "GATEWAY_DB", d / "gateway.sqlite")
    monkeypatch.setattr(paths, "PROPOSAL_QUEUE", d / "proposal_queue.jsonl")
    monkeypatch.setattr(paths, "INDEX_DIR", d / "index")
    return d
