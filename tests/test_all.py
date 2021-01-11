# pylint: disable=invalid-name,redefined-outer-name
import time

import pytest
from testinfra.host import Host


@pytest.fixture(scope="session")
def target_host(request):
    def fn(host, sudo=True):
        return Host.get_host(
            f"ansible://{host}?ansible_inventory={request.config.option.ansible_inventory}",
            sudo=sudo,
        )

    return fn


def _cmd(target_host, host, redis_cmd, container, port_variable):
    if isinstance(host, str):
        h = target_host(host)
    else:
        h = host

    port = h.ansible.get_variables()[port_variable]
    return h.check_output(f"docker exec {container} redis-cli -p {port} {redis_cmd}")


@pytest.fixture(scope="session")
def redis_cmd(target_host):
    return lambda host, redis_cmd: _cmd(
        target_host, host, redis_cmd, "redis", "redis_port"
    )


@pytest.fixture(scope="session")
def sentinel_cmd(target_host):
    return lambda host, redis_cmd: _cmd(
        target_host, host, redis_cmd, "sentinel", "sentinel_port"
    )


@pytest.fixture(scope="session")
def redis_master_addr(sentinel_cmd):
    def fn():
        output = sentinel_cmd("sentinel", "SENTINEL MASTERS")
        sentinel_primary_ip = output.split()[3]
        return sentinel_primary_ip

    return fn


@pytest.fixture(autouse=True, scope="session")
def configure_test(target_host, sentinel_cmd):
    sentinel = target_host("sentinel")
    primary = target_host("primary-1")
    replica2 = target_host("replica-2")
    sentinel_primary_name = sentinel.ansible.get_variables()["redis_primary"]
    for host in [sentinel, primary, replica2]:
        for cmd in [
            f"sentinel set {sentinel_primary_name} down-after-milliseconds 1000",
            f"sentinel set {sentinel_primary_name} failover-timeout 1000",
        ]:
            sentinel_cmd(host, cmd)


@pytest.fixture(autouse=True)
def restore_master(target_host, redis_master_addr):
    primary = target_host("primary-1")
    primary_ip = primary.interface("ens5").addresses[0]
    # Only restore if not restored!
    if redis_master_addr() == primary_ip:
        return

    print(f"Master not set to primary, attempting to restore to {primary_ip}...")

    replica1 = target_host("replica-1")
    replica2 = target_host("replica-2")

    if not primary.docker("redis").is_running:
        primary.check_output("docker start redis")
        time.sleep(2)

    print("Stopping existing slaves to force re-election")
    for host in [replica1, replica2]:
        host.check_output("docker stop redis")

    time.sleep(10)

    print("Starting slaves again")
    for host in [replica1, replica2]:
        host.check_output("docker start redis")

    time.sleep(5)
    assert redis_master_addr() == primary_ip


def test_failover_master(target_host, redis_master_addr):
    primary = target_host("primary-1")
    replica1 = target_host("replica-1")
    replica2 = target_host("replica-2")
    assert redis_master_addr() == primary.interface("ens5").addresses[0]

    # Force failover
    primary.check_output("docker stop redis")

    time.sleep(10)
    assert redis_master_addr() in [
        replica1.interface("ens5").addresses[0],
        replica2.interface("ens5").addresses[0],
    ]


def test_data_replication(target_host, redis_cmd):
    primary = target_host("primary-1")
    replica1 = target_host("replica-1")
    replica2 = target_host("replica-2")
    standalone = target_host("standalone")

    redis_cmd(primary, "FLUSHDB")
    redis_cmd(primary, "SET somekey somevalue")

    for host in [replica1, replica2]:
        value = redis_cmd(host, "GET somekey")
        assert value == "somevalue", f"failed to replicate data to {host}"

    value = redis_cmd(standalone, "GET somekey")
    assert value == ""


def test_prometheus_metrics(target_host):
    primary = target_host("primary-1")
    url = f'http://{primary.interface("ens5").addresses[0]}:9121/metrics'
    res = primary.ansible("uri", f"url={url} return_content=true", check=False)
    assert res["status"] == 200
    assert "redis_up 1" in res["content"]


def test_listening(target_host):
    primary = target_host("primary-1")
    addr = primary.interface("ens5").addresses[0]

    # Redis
    assert primary.socket(f"tcp://{addr}:4444").is_listening
    assert primary.socket("tcp://127.0.0.1:4444").is_listening

    # Sentinel
    assert primary.socket(f"tcp://{addr}:55555").is_listening
    assert primary.socket("tcp://127.0.0.1:55555").is_listening
