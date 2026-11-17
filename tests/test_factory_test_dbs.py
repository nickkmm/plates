"""Tests for factory/bin/job's cleanup of a finished job's test databases.

They never reach a live Postgres: the credentials are removed or the port is
a closed one."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import socket
from pathlib import Path

import pytest

JOB_SCRIPT = Path(__file__).resolve().parents[1] / "factory" / "bin" / "job"


def _load_job():
    loader = importlib.machinery.SourceFileLoader("factory_job", str(JOB_SCRIPT))
    spec = importlib.util.spec_from_loader("factory_job", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


job = _load_job()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def no_postgres(monkeypatch, tmp_path):
    """A password, but Postgres on a port where nothing listens."""
    monkeypatch.setattr(job, "REPO", tmp_path)
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setattr(job, "DB_PORT", _closed_port())


@pytest.fixture
def no_password(monkeypatch, tmp_path):
    """No POSTGRES_PASSWORD in the environment and no .env."""
    monkeypatch.setattr(job, "REPO", tmp_path)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setattr(job, "DB_PORT", _closed_port())


class TestJobDbNames:
    def test_keeps_only_the_jobs_own_databases(self):
        names = ["plates", "plates_test", "postgres", "plates_test_1", "plates_test_1_gw0",
                 "plates_test_1_gw11", "plates_test_12", "plates_test_1x", "plates_test_1_gwx",
                 "plates_test_10_gw0"]
        assert job.job_db_names(1, names) == ["plates_test_1", "plates_test_1_gw0", "plates_test_1_gw11"]

    @pytest.mark.parametrize("name", ["plates_test_12", "plates_test_1x", "plates_test_10_gw0",
                                      "plates_test_01", "plates_test_21"])
    def test_other_numbers_never_match(self, name):
        assert job.job_db_names(1, [name]) == []

    @pytest.mark.parametrize("name", ["plates_test_7_gw0", "plates_test_7_gw3", "plates_test_7_gw123"])
    def test_keeps_worker_copies(self, name):
        assert job.job_db_names(7, [name]) == [name]

    @pytest.mark.parametrize("name", ["plates_test_7_gw", "plates_test_7_gwx", "plates_test_7_gw0x",
                                      "plates_test_7_gw0_gw1", "plates_test_7_", "plates_test_7_gw-1"])
    def test_rejects_bad_worker_suffixes(self, name):
        assert job.job_db_names(7, [name]) == []

    @pytest.mark.parametrize("name", ["plates", "plates_test", "postgres", "template1", "",
                                      "plates_test_7;drop", "plates_test_7 ", "PLATES_TEST_7"])
    def test_refuses_anything_else(self, name):
        assert job.job_db_names(7, [name]) == []

    def test_job_number_follows_the_gate(self):
        assert job.job_number("004-dark-mode") == 4
        assert job.job_number("013-drop-a-job-s") == 13


class TestCleanupWithoutPostgres:
    def test_unreachable_raises_cleanup_failed(self, no_postgres):
        with pytest.raises(job.CleanupFailed, match="not reachable"):
            job.drop_test_dbs([1])

    def test_no_password_raises_cleanup_failed(self, no_password):
        with pytest.raises(job.CleanupFailed, match="POSTGRES_PASSWORD"):
            job.drop_test_dbs([1])

    @pytest.mark.parametrize("env", ["no_postgres", "no_password"])
    def test_cleanup_prints_a_note_and_does_not_raise(self, env, request, capsys):
        request.getfixturevalue(env)
        job.cleanup_test_dbs("004-dark-mode")
        out, err = capsys.readouterr()
        assert out == ""
        assert err.startswith("job: the test databases of job 4 weren't cleaned up: ")
        assert len(err.strip().splitlines()) == 1


class TestCleanupDbsCommand:
    def test_exits_1_with_one_note_when_it_cannot_connect(self, no_password, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(job, "JOBS", tmp_path / "jobs")
        with pytest.raises(SystemExit) as exc:
            job.cmd_cleanup_dbs()
        assert exc.value.code == 1
        out, err = capsys.readouterr()
        assert out == ""
        assert err.startswith("job: ")
        assert "POSTGRES_PASSWORD" in err
        assert len(err.strip().splitlines()) == 1


class TestTransitionToFinalStage:
    def test_rejected_completes_with_postgres_down(self, no_postgres, monkeypatch, tmp_path, capsys):
        jobs = tmp_path / "jobs"
        d = jobs / "005-some-job"
        d.mkdir(parents=True)
        (d / "intake.md").write_text("DECISION: REJECT\n\nNot something the factory builds.\n")
        monkeypatch.setattr(job, "JOBS", jobs)
        calls = []
        monkeypatch.setattr(job, "gh", lambda *a: calls.append(("gh", a)) or "")
        monkeypatch.setattr(job, "comment", lambda *a: calls.append(("comment", a)))
        monkeypatch.setattr(job, "label", lambda *a, **k: calls.append(("label", a, k)))
        j = {
            "id": "005-some-job", "issue": 5, "title": "t", "issue_url": "u",
            "pr": None, "pr_url": None, "branch": "factory/005-some-job",
            "worktree": str(tmp_path / "missing-worktree"),
            "stage": "intake", "round": 0, "round_limit": 3,
            "intake": None, "agents": None, "gate": None, "reviews": {},
            "cost_usd": 0.0, "started": "2026-01-01T00:00:00Z", "ended": None,
        }
        result = job.advance(j, "rejected")
        assert result["stage"] == "rejected"
        assert result["ended"]
        out, err = capsys.readouterr()
        assert out == ""
        assert "job: the test databases of job 5 weren't cleaned up" in err
        assert [c[0] for c in calls] == ["comment", "label"]
