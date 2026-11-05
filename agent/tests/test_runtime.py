"""Container argv construction."""

from castor_agent.runtime import JobRunner


def _runner(job, tmp_path):
    return JobRunner(
        job,
        container_runtime="podman",
        allow_host=True,
        default_image="docker.io/library/alpine:3.20",
        job_dir=str(tmp_path),
        network="host",
        extra_run_args=["--cap-add", "NET_RAW"],
    )


def test_container_jobs_run_through_sh_regardless_of_image_entrypoint(tmp_path):
    argv, is_container = _runner(
        {"id": "j1", "command": "nmap -sn 127.0.0.1", "image": "docker.io/instrumentisto/nmap"}, tmp_path
    ).build_argv()
    assert is_container
    image_at = argv.index("docker.io/instrumentisto/nmap")
    # The entrypoint override and extra args come before the image; the shell args after it.
    assert argv[argv.index("--entrypoint") + 1] == "/bin/sh" and argv.index("--entrypoint") < image_at
    assert argv.index("--cap-add") < image_at
    assert argv[image_at + 1 :] == ["-c", "nmap -sn 127.0.0.1"]
    assert f"{tmp_path}:/work:Z" in argv


def test_host_jobs_use_the_shell_directly(tmp_path):
    argv, is_container = _runner({"id": "j2", "command": "id"}, tmp_path).build_argv()
    assert not is_container and argv == ["/bin/sh", "-c", "id"]


def test_container_jobs_include_container_flags(tmp_path):
    argv, is_container = _runner(
        {
            "id": "j3",
            "command": "nmap -sV 10.0.0.1",
            "image": "docker.io/instrumentisto/nmap",
            "container_flags": ["--privileged", "--network=bridge"],
        },
        tmp_path,
    ).build_argv()
    assert is_container
    assert "--privileged" in argv
    assert "--network=bridge" in argv
    # When custom network flag is provided, default runner network ("host") should not be added
    assert "host" not in argv
    # Both flags must precede --entrypoint
    assert argv.index("--privileged") < argv.index("--entrypoint")
    assert argv.index("--network=bridge") < argv.index("--entrypoint")


def test_container_variables_are_passed_by_name_only(tmp_path):
    job = {"id": "j4", "command": "env", "image": "alpine", "env": {"CASTOR_VAR_CREDS_PASSWORD": "hunter22"}}
    argv, _ = _runner(job, tmp_path).build_argv()
    assert argv[argv.index("-e") + 1] == "CASTOR_VAR_CREDS_PASSWORD"
    assert not any("hunter22" in a for a in argv)


def test_variable_files_are_written_privately_and_removed(tmp_path):
    import asyncio
    import base64
    import os

    job = {
        "id": "j5",
        "command": 'cat "$CASTOR_VAR_KEY_PRIVATE_KEY"; echo "$CASTOR_VAR_KEY_USERNAME"',
        "env": {"CASTOR_VAR_KEY_USERNAME": "root"},
        "files": [
            {
                "env": "CASTOR_VAR_KEY_PRIVATE_KEY",
                "path": ".castor/vars/key/private_key",
                "data": base64.b64encode(b"KEYDATA\n").decode(),
                "mode": 0o600,
            }
        ],
    }
    runner = _runner(job, tmp_path)
    paths = runner.write_files(is_container=True)
    assert paths == {"CASTOR_VAR_KEY_PRIVATE_KEY": "/work/.castor/vars/key/private_key"}
    written = tmp_path / ".castor/vars/key/private_key"
    assert written.read_bytes() == b"KEYDATA\n"
    assert oct(os.stat(written).st_mode & 0o777) == "0o600"
    runner.remove_files()

    out: list[str] = []

    async def sink(stream, data):
        out.append(data)

    runner.job["display_command"] = "cat .castor/vars/key/private_key; echo root"
    result = asyncio.run(runner.run(sink))
    assert result.exit_code == 0
    assert out[0].startswith("$ cat .castor/vars/key/private_key; echo root\n")
    assert "KEYDATA" in "".join(out) and "root" in "".join(out)
    assert not (tmp_path / ".castor").exists()


def test_variable_files_cannot_escape_the_job_directory(tmp_path):
    job = {"id": "j6", "command": "true", "files": [{"env": "X", "path": "../evil", "data": ""}]}
    import pytest

    with pytest.raises(RuntimeError, match="outside the job"):
        _runner(job, tmp_path).write_files(is_container=False)


def test_variable_values_reach_the_command_literally(tmp_path):
    """Values arrive via "${VAR}" references: the shell parses the command before expanding
    them and never re-parses the result, so quotes, $(...), backticks and globs in a value
    are just characters."""
    import asyncio

    nasty = "p\"a$(touch PWNED)`id`'q \\n!x $HOME *"
    job = {
        "id": "j7",
        "command": 'printf "[%s]\\n" "${CASTOR_VAR_C_USERNAME}" "${CASTOR_VAR_C_PASSWORD}"',
        "env": {"CASTOR_VAR_C_USERNAME": "o'brien \"x\"", "CASTOR_VAR_C_PASSWORD": nasty},
    }
    out: list[str] = []

    async def sink(stream, data):
        if stream == "stdout":
            out.append(data)

    assert asyncio.run(_runner(job, tmp_path).run(sink)).exit_code == 0
    assert "".join(out) == f"[o'brien \"x\"]\n[{nasty}]\n"
    assert not (tmp_path / "PWNED").exists()


def test_token_is_preferred_over_the_secret():
    from castor_agent.agent import credentials
    from castor_agent.config import AgentConfig

    cfg = AgentConfig.from_args(["--secret", "s", "--token", "castor_t", "--name", "a"])
    assert credentials(cfg) == {"token": "castor_t"}
    cfg = AgentConfig.from_args(["--secret", "s", "--name", "a"])
    assert credentials(cfg) == {"secret": "s"}
