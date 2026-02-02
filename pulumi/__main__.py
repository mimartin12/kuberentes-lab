import pulumi
import pulumi_proxmoxve as proxmoxve
import pulumi_kubernetes as kubernetes
from pulumi_kubernetes.helm.v4 import Chart, RepositoryOptsArgs
import talos_image_factory
from talos_config import create_talos_secrets, apply_talos_config
import yaml

config = pulumi.Config()
talos_version = config.get("talos_version") or "v1.11.5"
kubernetes_version = config.get(
    "kubernetes_version"
)  # Optional, defaults to Talos default if not specified
cluster_name = config.get("cluster_name") or "talos-cluster"
gateway = config.get("gateway") or "192.168.1.1"
nodes = config.require_object("nodes")
cluster_endpoint_ip = config.get("cluster_endpoint_ip") or nodes[0]["ip"]
use_cilium = config.get_bool("use_cilium") or False
cilium_version = config.get("cilium_version") or "1.16.0"

# Load ArgoCD version from the ArgoCD application manifest
with open("../argocd/applications/argocd.yaml", "r") as f:
    argocd_app = yaml.safe_load(f)
    argocd_version = argocd_app["spec"]["sources"][0]["targetRevision"]

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
kubeconfig_raw = None

for node in nodes:
    node_type = node.get("type", "proxmox")
    
    if node_type == "external":
        vm = None  # No VM resource to manage
        pulumi.log.info(f"Skipping VM creation for {node['name']} (external node at {node['ip']})")
    else:
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
        use_cilium=use_cilium,
        cilium_version=cilium_version,
        kubernetes_version=kubernetes_version,
        install_disk=node.get("install_disk", "/dev/sda"),
    )
    if node["role"] == "controlplane":
        kubeconfig_raw = result["kubeconfig"].kubeconfig_raw


# Export talosconfig
def create_talosconfig_dict(client_config):
    return {
        "context": cluster_name,
        "contexts": {
            cluster_name: {
                "endpoints": [cluster_endpoint_ip],
                "nodes": [cluster_endpoint_ip],
                "ca": client_config["ca_certificate"],
                "crt": client_config["client_certificate"],
                "key": client_config["client_key"],
            }
        },
    }


talosconfig_dict = talos_secrets.client_configuration.apply(create_talosconfig_dict)

pulumi.export("kubeconfig", kubeconfig_raw)
pulumi.export(
    "talosconfig",
    talosconfig_dict.apply(lambda cfg: yaml.dump(cfg, default_flow_style=False)),
)

k8s_provider = kubernetes.Provider(
    "k8s-provider",
    kubeconfig=kubeconfig_raw,
)

# Deploy Argo CD
argocd = Chart(
    "argocd",
    chart="argo-cd",
    repository_opts=RepositoryOptsArgs(
        repo="https://argoproj.github.io/argo-helm",
    ),
    version=argocd_version,
    value_yaml_files=[pulumi.FileAsset("../argocd/applications/values/argocd.yaml")],
    namespace="argocd",
    opts=pulumi.ResourceOptions(provider=k8s_provider),
)


pulumi.export("talos_image_url", talos_iso_url)
pulumi.export("talos_version", talos_version)
