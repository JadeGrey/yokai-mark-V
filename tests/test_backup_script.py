"""scripts/backup_db.py: a verified backup, pruning, copying and clear failures."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from yokai.storage.db import Database


def load_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("backup_db", Path("scripts/backup_db.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_db = load_script()


async def make_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    db_path = tmp_path / "data" / "yokai.db"
    db_path.parent.mkdir()
    db = Database(db_path)
    await db.initialize()
    await db.credit_wallet(7, 123, "test", welcome_bonus=0)
    await db.close()
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    return db_path, env


@pytest.mark.asyncio
async def test_it_makes_a_verified_copy_beside_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, env = await make_env(tmp_path, monkeypatch)
    made = backup_db.run(env_path=env, now=1_700_000_000)
    assert made.parent == db_path.parent / "backups" and made.name.startswith("yokai-2023")
    con = sqlite3.connect(made)
    try:
        assert con.execute("SELECT balance FROM wallets WHERE user_id = 7;").fetchone() == (123,)
        assert con.execute("PRAGMA integrity_check;").fetchone() == ("ok",)
    finally:
        con.close()
    assert not list(made.parent.glob("*.tmp"))


@pytest.mark.asyncio
async def test_it_keeps_only_the_newest_and_leaves_other_files_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, env = await make_env(tmp_path, monkeypatch)
    folder = db_path.parent / "backups"
    for n in range(5):
        backup_db.run(keep=99, env_path=env, now=1_700_000_000 + n * 100)
    safety = folder / "yokai-20200101-000000-pre-restore.db"
    safety.write_bytes(b"safety")
    stranger = folder / "notes.txt"
    stranger.write_text("mine", encoding="utf-8")
    newest = backup_db.run(keep=2, env_path=env, now=1_700_001_000)
    names = sorted(p.name for p in folder.glob("yokai-*.db") if "pre-restore" not in p.name)
    assert len(names) == 2 and newest.name in names
    assert safety.exists() and stranger.exists()


@pytest.mark.asyncio
async def test_it_can_copy_the_new_backup_to_a_second_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, env = await make_env(tmp_path, monkeypatch)
    elsewhere = tmp_path / "usb" / "yokai"
    made = backup_db.run(env_path=env, copy_to=elsewhere, now=1_700_000_000)
    copy = elsewhere / made.name
    assert copy.exists() and copy.read_bytes() == made.read_bytes()


@pytest.mark.asyncio
async def test_a_custom_backup_dir_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, env = await make_env(tmp_path, monkeypatch)
    custom = tmp_path / "elsewhere"
    monkeypatch.setenv("BACKUP_DIR", str(custom))
    made = backup_db.run(env_path=env, now=1_700_000_000)
    assert made.parent == custom


def test_failures_return_an_error_code_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    assert backup_db.main(["--env", str(env)]) == 1
    assert "not found" in caplog.text
    assert backup_db.main(["--env", str(env), "--keep", "0"]) == 1


@pytest.mark.asyncio
async def test_a_file_that_is_not_the_bots_database_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, env = await make_env(tmp_path, monkeypatch)
    db_path.write_bytes(b"this is not sqlite")
    assert backup_db.main(["--env", str(env)]) == 1
