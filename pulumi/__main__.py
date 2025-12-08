import pulumi
import pulumi_proxmoxve as proxmoxve
import talos_image_factory

config = pulumi.Config()

talos_version = config.get("talos_version") or "v1.11.5"

provider = proxmoxve.Provider(
    "proxmoxve",
    endpoint=config.require("proxmox_endpoint"),
    username=config.require("proxmox_username"),
    password=config.require_secret("proxmox_password"),
    insecure=True,
)

talos_iso_url = talos_image_factory.create_talos_image_url(
    "talos-iso-schematic",
    talos_version=talos_version,
    extensions=["siderolabs/qemu-guest-agent", "siderolabs/iscsi-tools"],
)

talos_iso = proxmoxve.download.File(
    "talos-iso-download",
    content_type="iso",
    datastore_id="local",
    node_name="pve",
    url=talos_iso_url,
    file_name=talos_iso_url.apply(lambda url: f"talos-{talos_version}-amd64.iso"),
    opts=pulumi.ResourceOptions(provider=provider),
)


def create_talos_vm(name: str, ip_address: str, gateway: str = "192.168.1.1"):
    return proxmoxve.vm.VirtualMachine(
        name,
        proxmoxve.vm.VirtualMachineArgs(
            node_name="pve",
            bios="ovmf",
            efi_disk=proxmoxve.vm.VirtualMachineEfiDiskArgs(
                datastore_id="local-lvm",
                file_format="raw",
                type="4m",
            ),
            cpu=proxmoxve.vm.VirtualMachineCpuArgs(cores=2, sockets=1, type="host"),
            disks=[
                proxmoxve.vm.VirtualMachineDiskArgs(
                    interface="scsi0",
                    size=20,
                    datastore_id="local-lvm",
                    file_format="raw",
                ),
            ],
            memory=proxmoxve.vm.VirtualMachineMemoryArgs(dedicated=2048),
            network_devices=[
                proxmoxve.vm.VirtualMachineNetworkDeviceArgs(
                    model="virtio", bridge="vmbr0"
                )
            ],
            initialization=proxmoxve.vm.VirtualMachineInitializationArgs(
                datastore_id="local-lvm",
                type="nocloud",
                interface="ide0",
                ip_configs=[
                    proxmoxve.vm.VirtualMachineInitializationIpConfigArgs(
                        ipv4=proxmoxve.vm.VirtualMachineInitializationIpConfigIpv4Args(
                            address=f"{ip_address}/24", gateway="192.168.1.1"
                        )
                    )
                ],
            ),
            cdrom=proxmoxve.vm.VirtualMachineCdromArgs(
                file_id=talos_iso.id, interface="ide2"
            ),
            boot_orders=["scsi0"],
        ),
        opts=pulumi.ResourceOptions(
            provider=provider,
            depends_on=[talos_iso],
        ),
    )


talos_master_01 = create_talos_vm("talos-01-master", "192.168.1.160")

# Add Talos config
from talos_config import create_talos_secrets, apply_talos_config

# Create secrets once for the cluster
talos_secrets = create_talos_secrets("talos-cluster")

# Apply config to node
talos_node = apply_talos_config(
    name="talos-01-master",
    secrets=talos_secrets,
    cluster_name="talos-cluster",
    cluster_endpoint="https://192.168.1.160:6443",
    node_ip="192.168.1.160",
    role="controlplane",
    hostname="talos-01-master",
    vm=talos_master_01,
)

pulumi.export("kubeconfig", talos_node["kubeconfig"].kubeconfig_raw)
