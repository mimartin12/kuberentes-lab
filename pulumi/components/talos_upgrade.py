"""TalosUpgrade Pulumi Component"""

import pulumi
from pulumi_command import local as command


class TalosUpgradeArgs:
    """Arguments for TalosUpgrade component"""

    def __init__(
        self,
        nodes: list[dict],
        image_factories: dict,  # {"default": factory, "gpu": factory, ...}
        talosconfig_path: str = "./talosconfig.yaml",
        preserve_data: bool = True,
        stage_upgrade: bool = False,
        force: bool = False,
    ):
        self.nodes = nodes
        self.image_factories = image_factories
        self.talosconfig_path = talosconfig_path
        self.preserve_data = preserve_data
        self.stage_upgrade = stage_upgrade
        self.force = force


class TalosUpgrade(pulumi.ComponentResource):
    """
    A Pulumi ComponentResource that manages Talos node upgrades:
    - Checks current version on each node
    - Upgrades control plane nodes first, one at a time
    - Then upgrades worker nodes, one at a time
    - Waits for node health between upgrades
    """

    def __init__(
        self,
        name: str,
        args: TalosUpgradeArgs,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:talos:Upgrade", name, {}, opts)

        self.upgrade_commands = []
        previous_upgrade = None

        # Separate nodes by role
        controlplane_nodes = [n for n in args.nodes if n["role"] == "controlplane"]
        worker_nodes = [n for n in args.nodes if n["role"] == "worker"]

        pulumi.log.info(
            f"Planning to upgrade {len(controlplane_nodes)} control plane nodes and {len(worker_nodes)} worker nodes"
        )

        # Upgrade control plane nodes first
        for node in controlplane_nodes:
            pulumi.log.info(
                f"Scheduling upgrade for control plane node {node['name']} at {node['ip']}"
            )
            upgrade_cmd = self._create_upgrade_command(
                node, args, depends_on=[previous_upgrade] if previous_upgrade else []
            )
            self.upgrade_commands.append(upgrade_cmd)
            previous_upgrade = upgrade_cmd

        # Upgrade worker nodes
        for node in worker_nodes:
            pulumi.log.info(
                f"Scheduling upgrade for worker node {node['name']} at {node['ip']}"
            )
            upgrade_cmd = self._create_upgrade_command(
                node, args, depends_on=[previous_upgrade] if previous_upgrade else []
            )
            self.upgrade_commands.append(upgrade_cmd)
            previous_upgrade = upgrade_cmd

        self.register_outputs(
            {
                "completed": pulumi.Output.all(
                    *[cmd.stdout for cmd in self.upgrade_commands]
                ),
            }
        )

    def _create_upgrade_command(
        self, node: dict, args: TalosUpgradeArgs, depends_on: list
    ) -> command.Command:
        """Create upgrade command for a single node"""

        # Get the target installer image for this node
        image_profile = node.get("talosImage", "default")
        if image_profile not in args.image_factories:
            raise ValueError(
                f"Image profile '{image_profile}' not found in image_factories"
            )

        target_installer_image = args.image_factories[image_profile].installer_image

        # Check current version first
        version_check = command.Command(
            f"{node['name']}-version-check",
            create=target_installer_image.apply(
                lambda img: f"talosctl --talosconfig {args.talosconfig_path} "
                f"--nodes {node['ip']} version --short || echo 'unknown'"
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=depends_on,
            ),
        )

        # Build upgrade command
        def build_upgrade_cmd(img: str) -> str:
            cmd_parts = [
                f"talosctl --talosconfig {args.talosconfig_path}",
                f"--nodes {node['ip']}",
                "upgrade",
                f"--image {img}",
            ]

            if args.preserve_data:
                cmd_parts.append("--preserve")

            if args.stage_upgrade:
                cmd_parts.append("--stage")

            if args.force:
                cmd_parts.append("--force")

            # Add wait for health check after upgrade
            # For workers, skip etcd check (workers don't run etcd)
            cmd_parts.append("&&")
            if node["role"] == "worker":
                # Workers: just wait for node to come back up and be ready
                cmd_parts.append(
                    f"talosctl --talosconfig {args.talosconfig_path} "
                    f"--nodes {node['ip']} version --short"
                )
            else:
                # Control plane: full health check including etcd
                cmd_parts.append(
                    f"talosctl --talosconfig {args.talosconfig_path} "
                    f"--nodes {node['ip']} health --wait-timeout=10m"
                )

            return " ".join(cmd_parts)

        upgrade_script = target_installer_image.apply(
            lambda img: f"""
set -e
echo "Checking if {node['name']} needs upgrade to {img}..."

# Get current version
CURRENT=$(talosctl --talosconfig {args.talosconfig_path} --nodes {node['ip']} version --short 2>/dev/null | grep 'Server:' || echo "unknown")

# Check if upgrade is needed
if echo "$CURRENT" | grep -q "{img.split(':')[-1]}"; then
    echo "{node['name']} is already on target version, skipping upgrade"
    exit 0
fi

echo "Upgrading {node['name']} from $CURRENT to {img}..."
{build_upgrade_cmd(img)}
echo "{node['name']} upgrade completed successfully"
"""
        )

        # Execute upgrade
        upgrade_cmd = command.Command(
            f"{node['name']}-upgrade",
            create=upgrade_script,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[version_check] + depends_on,
            ),
        )

        return upgrade_cmd
