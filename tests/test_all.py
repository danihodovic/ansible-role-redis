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


@pytest.fixture(autouse=True, scope="session")
def configure_test(target_host):
    sentinel = target_host("sentinel")
    primary = target_host("primary-1")
    replica2 = target_host("replica-2")
    sentinel_primary_name = sentinel.ansible.get_variables()["redis_primary"]
    for host in [sentinel, primary, replica2]:
        for cmd in [
            f"sentinel set {sentinel_primary_name} down-after-milliseconds 1000",
            f"sentinel set {sentinel_primary_name} failover-timeout 1000",
        ]:
            host.check_output(("docker exec sentinel redis-cli -p 26379 " + cmd))


@pytest.fixture(autouse=True)
def restore_master(target_host):
    primary = target_host("primary-1")
    sentinel_primary_ip = _get_master_addr(primary)
    primary_ip = primary.interface("ens5").addresses[0]
    # Only restore if not restored!
    if sentinel_primary_ip == primary_ip:
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
    sentinel_primary_ip = _get_master_addr(primary)
    assert sentinel_primary_ip == primary_ip


def test_failover_master(target_host):
    primary = target_host("primary-1")
    replica1 = target_host("replica-1")
    replica2 = target_host("replica-2")
    assert _get_master_addr(primary) == primary.interface("ens5").addresses[0]

    # Force failover
    primary.check_output("docker stop redis")

    time.sleep(10)
    assert _get_master_addr(primary) in [
        replica1.interface("ens5").addresses[0],
        replica2.interface("ens5").addresses[0],
    ]


def test_data_replication(target_host):
    primary = target_host("primary-1")
    replica1 = target_host("replica-1")
    replica2 = target_host("replica-2")
    standalone = target_host("standalone")

    primary.check_output("docker exec redis redis-cli FLUSHDB")
    primary.check_output("docker exec redis redis-cli set somekey somevalue")

    for host in [replica1, replica2]:
        value = host.check_output("docker exec redis redis-cli get somekey")
        assert value == "somevalue"

    value = standalone.check_output("docker exec redis redis-cli get somekey")
    assert value == ""


def test_prometheus_metrics(target_host):
    primary = target_host("primary-1")
    url = f'http://{primary.interface("ens5").addresses[0]}:9121/metrics'
    res = primary.ansible("uri", f"url={url} return_content=true", check=False)
    assert res["status"] == 200
    assert "redis_up 1" in res["content"]


def _get_master_addr(host):
    result = host.check_output(
        "docker exec sentinel redis-cli -p 26379 SENTINEL MASTERS"
    )
    sentinel_primary_ip = result.split()[3]
    return sentinel_primary_ip
