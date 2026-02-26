from __future__ import annotations

DOCUMENTATION = r"""
    name: gns3
    author:
    - Tom Andrivet (CanardConfit) <canardconfit.development@gmail.com>
    short_description: Dynamic inventory from a GNS3 controller (REST API v2)
    description:
        - Queries the GNS3 controller REST API to discover nodes in a project and builds an Ansible inventory.
        - Uses C(/v2/projects) to find the project and C(/v2/projects/{project_id}/nodes) to list nodes.
        - By default, sets C(ansible_host) from node console_host and C(ansible_port) from node console port, with an optional C(port_offset) applied (e.g. telnet+1 -> ssh).
    extends_documentation_fragment:
        - constructed
        - inventory_cache
    options:
        plugin:
            description: The name of this plugin, it should always be set to V(canardconfit.gns3.gns3) for this plugin to recognize it as its own.
            required: true
            choices: ["canardconfit.gns3.gns3"]
        url:
            description:
                - Base URL of the GNS3 controller (e.g. C(http://gns3.example.com:3080)).
            type: str
            required: true
        project_id:
            description: Project UUID. Takes precedence over C(project_name).
            type: str
            required: false
        project_name:
            description: Project name to search in C(/v2/projects) if C(project_id) is not provided.
            type: str
            required: false
        validate_certs:
            description: Validate TLS certificates when using HTTPS.
            type: bool
            default: true
        group:
            description: Parent group name for all discovered nodes.
            type: str
            default: gns3
        host_naming:
            description: Which field to use as the inventory hostname.
            type: str
            choices: ["name", "node_id"]
            default: "name"
        port_offset:
            description:
                - Integer offset added to the port returned by the API (typically the telnet console port).
                - "Example: if console port is 5000 and SSH is 5001, use C(port_offset: 1)."
            type: int
            default: 0
        group_by_node_type:
            description: Create groups named C(gns3_type_<node_type>) and add hosts accordingly.
            type: bool
            default: true
"""

EXAMPLES = r"""
# gns3.yml
plugin: canardconfit.gns3.gns3
url: http://gns3.example.com:3080
project_name: MyLab
validate_certs: false
port_offset: 1

# Optional constructed features
keyed_groups:
  - key: gns3_node_type
    prefix: gns3_type
groups:
  running: gns3_status == "started"
"""

import json
import re
from urllib.parse import urlparse

from ansible.errors import AnsibleParserError
from ansible.module_utils.common.text.converters import to_native
from ansible.module_utils.urls import open_url
from ansible.plugins.inventory import BaseInventoryPlugin, Cacheable, Constructable


class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):
    NAME = "canardconfit.gns3.gns3"

    def _controller_host_from_url(self, base_url: str) -> str:
        return urlparse(base_url).hostname or base_url

    def verify_file(self, path):
            valid = False
            if super().verify_file(path):
                if path.endswith(("gns3.yaml", "gns3.yml")):
                    valid = True
                else:
                    self.display.vvv('Skipping due to inventory source not ending in "gns3.yaml" nor "gns3.yml"')
            return valid

    def _http_get_json(self, url: str, validate_certs: bool):
        headers = {
            "Accept": "application/json"
        }

        try:
            resp = open_url(url, method="GET", validate_certs=validate_certs, headers=headers)
            raw = resp.read()
            return json.loads(raw)
        except Exception as e:
            raise AnsibleParserError(to_native(f"GNS3 inventory: HTTP error for {url}: {e}"), orig_exc=e)

    @staticmethod
    def _find_project_id(projects, project_id: str | None, project_name: str | None) -> str:
        if project_id:
            for p in projects:
                if p.get("project_id") == project_id:
                    return project_id
            raise AnsibleParserError(f"GNS3 inventory: project_id not found: {project_id}")

        if project_name:
            matches = [p for p in projects if p.get("name") == project_name]
            if not matches:
                raise AnsibleParserError(f"GNS3 inventory: project_name not found: {project_name}")
            if len(matches) > 1:
                # Rare but possible if users reuse names
                raise AnsibleParserError(
                    f"GNS3 inventory: multiple projects match name '{project_name}'. Use project_id instead."
                )
            return matches[0].get("project_id")

        raise AnsibleParserError("GNS3 inventory: you must set either project_id or project_name.")

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path, cache)

        # Load YAML config and options from DOCUMENTATION
        self._read_config_data(path)

        base_url = self.get_option("url").rstrip("/")
        controller_host = self._controller_host_from_url(base_url)
        validate_certs = self.get_option("validate_certs")

        parent_group = self.get_option("group")
        host_naming = self.get_option("host_naming")
        port_offset = int(self.get_option("port_offset") or 0)
        group_by_node_type = self.get_option("group_by_node_type")

        project_id = self.get_option("project_id")
        project_name = self.get_option("project_name")

        strict = self.get_option("strict")
        
        self.load_cache_plugin()
        cache_key = self.get_cache_key(path)
        use_cache = cache and self.get_option("cache")

        source = None
        if use_cache:
            source = self._cache.get(cache_key)

        if not source:
            projects = self._http_get_json(f"{base_url}/v2/projects", validate_certs=validate_certs)

            pid = self._find_project_id(projects, project_id=project_id, project_name=project_name)

            nodes = self._http_get_json(f"{base_url}/v2/projects/{pid}/nodes", validate_certs=validate_certs)

            source = {"project_id": pid, "nodes": nodes}

            if use_cache:
                self._cache[cache_key] = source

        pid = source["project_id"]
        nodes = source["nodes"]

        # Ensure parent group exists
        self.inventory.add_group(parent_group)

        # Track duplicates to keep inventory stable
        seen = set()

        for n in nodes:
            node_id = n.get("node_id")
            node_name = n.get("name")
            node_type = n.get("node_type")
            status = n.get("status")

            host = node_name if host_naming == "name" else node_id
            host = host or node_name or node_id
            if not host:
                continue

            if host in seen:
                suffix = (node_id or "")[-6:] if node_id else "dup"
                host = f"{host}_{suffix}"
            seen.add(host)

            self.inventory.add_host(host, group=parent_group)

            # Standard hostvars
            self.inventory.set_variable(host, "gns3_project_id", pid)
            self.inventory.set_variable(host, "gns3_node_id", node_id)
            self.inventory.set_variable(host, "gns3_node_name", node_name)
            self.inventory.set_variable(host, "gns3_node_type", node_type)
            self.inventory.set_variable(host, "gns3_status", status)
            self.inventory.set_variable(host, "gns3_console_type", n.get("console_type"))
            self.inventory.set_variable(host, "gns3_console_host", n.get("console_host"))
            self.inventory.set_variable(host, "gns3_console_port", n.get("console"))

            # Connection vars for Ansible
            console_host = n.get("console_host") or controller_host
            if console_host in ("0.0.0.0", "::"):
                console_host = controller_host

            console_port = n.get("console")
            if isinstance(console_port, int):
                self.inventory.set_variable(host, "ansible_host", console_host)
                self.inventory.set_variable(host, "ansible_port", console_port + port_offset)
            else:
                # still set host, but no port if missing/not int
                self.inventory.set_variable(host, "ansible_host", console_host)

            # Group by node_type
            if group_by_node_type and node_type:
                g = f"gns3_type_{node_type}"
                self.inventory.add_group(g)
                self.inventory.add_child(g, host)

            hostvars = self.inventory.get_host(host).get_vars()
            self._set_composite_vars(self.get_option("compose"), hostvars, host, strict=strict)
            self._add_host_to_composed_groups(self.get_option("groups"), hostvars, host, strict=strict)
            self._add_host_to_keyed_groups(self.get_option("keyed_groups"), hostvars, host, strict=strict)
