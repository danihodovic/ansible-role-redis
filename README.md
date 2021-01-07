# ansible-role-redis [![Build Status](https://ci.depode.com/api/badges/danihodovic/ansible-role-redis/status.svg)](https://ci.depode.com/danihodovic/ansible-role-redis)

Ansible role to deploy HA Redis using Sentinel with automatic failover.

### Features

- Failover using Sentinel. Tested with [Molecule](https://github.com/ansible-community/molecule) on AWS
- Deployed as Docker containers
- Prometheus monitoring via [Redis exporter](https://github.com/oliver006/redis_exporter)
