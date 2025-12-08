import pulumi
import pulumi_proxmoxve as proxmoxve
import talos_image_factory
from talos_config import create_talos_secrets, apply_talos_config

config = pulumi.Config()
talos_version = config.get("talos_version") or "v1.11.5"
cluster_name = config.get("cluster_name") or "talos-cluster"
gateway = config.get("gateway") or "192.168.1.1"
nodes = config.require_object("nodes")
cluster_endpoint_ip = config.get("cluster_endpoint_ip") or nodes[0]["ip"]

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
    file_name=talos_iso_url.apply(
        lambda url: f"talos-{talos_version}-nocloud-amd64.iso"
    ),
    opts=pulumi.ResourceOptions(provider=provider),
)


def create_talos_vm(name: str, ip: str, gateway: str, cpu: int = 2, memory: int = 2048):
    return proxmoxve.vm.VirtualMachine(
        name,
        node_name="pve",
        bios="ovmf",
        efi_disk=proxmoxve.vm.VirtualMachineEfiDiskArgs(
            datastore_id="local-lvm", file_format="raw", type="4m"
        ),
        cpu=proxmoxve.vm.VirtualMachineCpuArgs(cores=cpu, sockets=1, type="host"),
        disks=[
            proxmoxve.vm.VirtualMachineDiskArgs(
                interface="scsi0", size=20, datastore_id="local-lvm", file_format="raw"
            ),
        ],
        memory=proxmoxve.vm.VirtualMachineMemoryArgs(dedicated=memory),
        network_devices=[
            proxmoxve.vm.VirtualMachineNetworkDeviceArgs(model="virtio", bridge="vmbr0")
        ],
        initialization=proxmoxve.vm.VirtualMachineInitializationArgs(
            datastore_id="local-lvm",
            type="nocloud",
            interface="ide0",
            ip_configs=[
                proxmoxve.vm.VirtualMachineInitializationIpConfigArgs(
                    ipv4=proxmoxve.vm.VirtualMachineInitializationIpConfigIpv4Args(
                        address=f"{ip}/24",
                        gateway=gateway,
                    )
                )
            ],
            dns=proxmoxve.vm.VirtualMachineInitializationDnsArgs(
                servers=[gateway],
            ),
        ),
        cdrom=proxmoxve.vm.VirtualMachineCdromArgs(
            file_id=talos_iso.id, interface="ide2"
        ),
        boot_orders=["scsi0"],
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[talos_iso]),
    )


talos_secrets = create_talos_secrets(cluster_name, talos_version=talos_version)

for node in nodes:
    vm = create_talos_vm(
        node["name"],
        node["ip"],
        gateway,
        node.get("cpu", 2),
        node.get("memory", 2048),
    )
    result = apply_talos_config(
        name=node["name"],
        secrets=talos_secrets,
        cluster_name=cluster_name,
        cluster_endpoint=f"https://{cluster_endpoint_ip}:6443",
        node_ip=node["ip"],
        role=node["role"],
        vm=vm,
        gateway=gateway,
    )
    if node["role"] == "controlplane":
        pulumi.export("kubeconfig", result["kubeconfig"].kubeconfig_raw)
