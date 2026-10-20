"""Opt-in isolated Docker runtime proof; never uses the production project/volume."""
import json
import os
import subprocess
import time
import urllib.request
import uuid

import pytest


@pytest.mark.skipif(os.environ.get("RUN_COMPOSE") != "1", reason="Set RUN_COMPOSE=1 for isolated Docker runtime proof")
def test_compose_restart_restore_and_privacy(tmp_path, map_data):
    project = "antonella-smoke-" + uuid.uuid4().hex[:8]
    venue = tmp_path / "synthetic-map.json"
    venue.write_text(json.dumps({**map_data, "verification": {
        **map_data["verification"], "status": "pending"}}))
    override = tmp_path / "compose.test.yaml"
    override.write_text(json.dumps(dict(services=dict(app=dict(
        image="antonella-apply-isolated-app:latest",
        volumes=[f"{venue}:/srv/app/data/venue.json:ro"]
    )))))
    base = ["docker", "compose", "-p", project, "-f", "compose.yaml", "-f", str(override)]
    env = {**os.environ, "APP_PORT": "0"}

    def run(*args, check=True):
        result = subprocess.run([*base, *args], env=env, capture_output=True, text=True, timeout=90)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def address():
        port = run("port", "app", "8000").stdout.strip()
        assert port.startswith("127.0.0.1:"), f"Unusable runtime loopback publication: {port!r}; {run('ps', '--format', 'json').stdout}"
        return "http://" + port

    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(address() + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)

    def ready():
        for _ in range(60):
            try:
                return request("/api/tables")
            except (OSError, ValueError):
                time.sleep(.25)
        pytest.fail(run("logs", "--no-color").stdout)

    try:
        # An explicitly unapproved fixture must fail regardless of image map approval.
        gate = subprocess.run([*base, "run", "--rm", "--no-deps", "--entrypoint", "python", "app",
                               "-c", "from app import create_app; create_app()"],
                               env=env, capture_output=True, text=True, timeout=90)
        assert gate.returncode != 0 and "approval is required" in gate.stderr
        venue.write_text(json.dumps(map_data))
        run("up", "-d", "--no-build")
        assert ready()["sold"] == 0
        container_id = run("ps", "-q", "app").stdout.strip()
        info = json.loads(subprocess.check_output(["docker", "inspect", container_id]))[0]
        assert info["Config"]["User"] == "10001:10001"
        assert info["HostConfig"]["ReadonlyRootfs"]
        assert info["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
        assert info["HostConfig"]["CapDrop"] == ["ALL"]
        bindings = info["HostConfig"]["PortBindings"]
        assert set(bindings) == {"8000/tcp"}
        assert all(b["HostIp"] == "127.0.0.1" for b in bindings["8000/tcp"])
        assert not any("docker.sock" in m["Source"] for m in info["Mounts"])
        network = next(iter(info["NetworkSettings"]["Networks"]))
        assert set(info["NetworkSettings"]["Networks"]) == {project + "_private"}
        network_info = json.loads(subprocess.check_output(["docker", "network", "inspect", network]))[0]
        assert network_info["Driver"] == "bridge"
        assert network_info["Labels"]["com.docker.compose.project"] == project
        sale = dict(quantity=1, expected_version=0, actor="Synthetic operator", reason="Isolated smoke")
        request("/api/tables/1/sales", sale)
        expected_tables, expected_history = request("/api/tables"), request("/api/history")
        run("restart", "app")
        assert ready() == expected_tables
        assert request("/api/history") == expected_history
        run("exec", "-T", "app", "python", "-m", "tools.backup", "snapshot", "/data/sales.sqlite", "/data/snapshot.sqlite")
        request("/api/tables/2/sales", sale)
        assert request("/api/tables")["sold"] == 2
        run("stop", "app")
        run("run", "--rm", "--no-deps", "--entrypoint", "python", "app", "-m", "tools.backup", "restore",
            "/data/snapshot.sqlite", "/data/restored.sqlite", "--stopped")
        run("run", "--rm", "--no-deps", "--entrypoint", "python", "app", "-m", "tools.backup", "verify", "/data/restored.sqlite")
        # Reopen the restored snapshot through the actual app and compare full map/count/history.
        override.write_text(json.dumps(dict(services=dict(app=dict(
            image="antonella-apply-isolated-app:latest", environment=dict(DATABASE="/data/restored.sqlite"),
            volumes=[f"{venue}:/srv/app/data/venue.json:ro"]
        )))))
        run("up", "-d", "--no-build", "--force-recreate")
        assert ready() == expected_tables
        assert request("/api/history") == expected_history
        print("Isolated Compose proof: unapproved gate, nonroot/private config, sale, restart, backup, stopped restore, equality PASS")
    finally:
        run("down", "--volumes", "--remove-orphans")
