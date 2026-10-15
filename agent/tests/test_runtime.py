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
