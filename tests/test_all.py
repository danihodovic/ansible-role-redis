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
    for host in [
        target_host("primary"),
        target_host("replica-2"),
        target_host("sentinel"),
    ]:
        host.check_output(
            (
                "docker exec sentinel redis-cli -p 26379 "
                "sentinel set master down-after-milliseconds 1000"
            )
        )


@pytest.fixture(autouse=True)
def restore_master(target_host):
    primary = target_host("primary")
    replica1 = target_host("replica-1")
    replica2 = target_host("replica-2")

    primary.check_output("docker start redis")

    for host in [replica1, replica2]:
        host.check_output("docker stop redis")
        time.sleep(3)
        host.check_output("docker start redis")
        time.sleep(1)


def test_failover_master(target_host):
    primary = target_host("primary")
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
    primary = target_host("primary")
    replica1 = target_host("replica-1")
    replica2 = target_host("replica-2")

    primary.check_output("docker exec redis redis-cli set somekey somevalue")

    for host in [replica1, replica2]:
        value = host.check_output("docker exec redis redis-cli get somekey")
        assert value == "somevalue"


def test_prometheus_metrics(target_host):
    primary = target_host("primary")
    url = f'http://{primary.interface("ens5").addresses[0]}:9121/metrics'
    res = primary.ansible("uri", f"url={url} return_content=true", check=False)
    assert res["status"] == 200
    assert "redis_up 1" in res["content"]


def _get_master_addr(host):
    output = host.check_output(
        "docker exec sentinel redis-cli -p 26379 SENTINEL get-master-addr-by-name master"
    )
    return output.split()[0]
