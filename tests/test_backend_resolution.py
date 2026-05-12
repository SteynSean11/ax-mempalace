import json

from mempalace.config import MempalaceConfig
from mempalace.palace import get_active_backend_name


def test_config_backend_postgres_alias_resolves_to_pgvector(tmp_path, monkeypatch):
    config_dir = tmp_path / "mempalace"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"backend": "postgres"}), encoding="utf-8")

    monkeypatch.delenv("MEMPALACE_BACKEND", raising=False)
    monkeypatch.delenv("MEMPAL_BACKEND", raising=False)

    cfg = MempalaceConfig(config_dir=str(config_dir))
    assert cfg.backend == "pgvector"
    assert get_active_backend_name(cfg) == "pgvector"


def test_env_backend_override_wins_over_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "mempalace"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"backend": "postgres"}), encoding="utf-8")

    monkeypatch.setenv("MEMPALACE_BACKEND", "chroma")

    cfg = MempalaceConfig(config_dir=str(config_dir))
    assert cfg.backend == "chroma"
    assert get_active_backend_name(cfg) == "chroma"
