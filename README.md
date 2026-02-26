# Ansible collection - canardconfit.gns3

An Ansible collection for automating and operating GNS labs.

It provide tools to interact with GNS3 environments (REST API automation, inventory sources, and related helpers).

## Features

- Dynamic inventory sourced from the GNS3 controller API
    - Port offset support (useful when the API exposes a console/telnet port and SSH is `port + 1`)

## Requirements

- Ansible (core) recent enough to support collection inventory plugins
- Network access to the GNS3 controller API (typically `http(s)://<controller>:3080`)

## Installation

### From Ansible Galaxy
```bash
ansible-galaxy collection install canardconfit.gns3
```

## Dynamic inventory

Create `inventory.gns3.yml`:

```yaml
plugin: canardconfit.gns3.gns3
url: http://gns3.example.com:3080
project_name: MyLab
validate_certs: false
port_offset: 1
```

Test it:

```bash
ansible-inventory -i inventory.gns3.yml --list
```

### Notes on `port_offset`

Some setups expose a console/telnet port in the GNS3 API, while SSH is reachable on `console_port + x`.
Set:

```yaml
port_offset: xxx
```

to have the inventory plugin publish:

* `ansible_host = console_host`
* `ansible_port = console_port + port_offset`

## Contributing

Issues and PRs are welcome!

## License

[MPL-2.0](LICENSE)
