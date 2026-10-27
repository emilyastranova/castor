"""Agent-side artifact collection and URL building."""

from pathlib import Path

from castor_agent.artifacts import collect, upload_url


def test_upload_url_keeps_scheme_host_and_prefix():
    assert upload_url("ws://127.0.0.1:8000/ws/agent", "j1") == "http://127.0.0.1:8000/api/jobs/j1/artifacts"
    assert upload_url("wss://castor.example/c/ws/agent", "j1") == "https://castor.example/c/api/jobs/j1/artifacts"


def test_collect_declared_files_and_artifacts_dir(tmp_path: Path):
    job = tmp_path / "job"
    (job / "artifacts" / "shots").mkdir(parents=True)
    (job / "scan.xml").write_text("<x/>")
    (job / "noise.txt").write_text("not collected")
    (job / "artifacts" / "shots" / "a.png").write_bytes(b"png")
    outside = tmp_path / "secret.txt"
    outside.write_text("outside the job dir")
    (job / "artifacts" / "link.txt").symlink_to(outside)  # must be skipped
    (job / "escape.xml").symlink_to(outside)

    found = collect(job, ["/work/scan.xml", "/work/escape.xml", "/work/missing.xml", "/work/artifacts/**"])
    assert [rel for _, rel in found] == ["artifacts/shots/a.png", "scan.xml"]


def test_collect_never_returns_variable_files(tmp_path: Path):
    (tmp_path / ".castor/vars/key").mkdir(parents=True)
    (tmp_path / ".castor/vars/key/private_key").write_text("secret")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/out.txt").write_text("ok")
    found = collect(tmp_path, ["/work/artifacts/**", "/work/**", "/work/.castor/**"])
    assert [rel for _, rel in found] == ["artifacts/out.txt"]
