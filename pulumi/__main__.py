import pulumi
import pulumi_proxmoxve as proxmoxve
import pulumi_kubernetes as kubernetes
import base64
import yaml
import talos_image_factory
from pulumi_command import local as command
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs, RepositoryOptsArgs
from talos_config import create_talos_secrets, apply_talos_config

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


talos_iso_url, talos_installer_image = talos_image_factory.create_talos_image_assets(
    "talos-iso-schematic",
    talos_version=talos_version,
    platform="nocloud",
    extensions=["siderolabs/qemu-guest-agent", "siderolabs/iscsi-tools"],
)

talos_iso = proxmoxve.download.File(
    "talos-iso-download",
    content_type="iso",
    datastore_id="local",
    node_name="pve",
    url=talos_iso_url,
    file_name=talos_iso_url.apply(
        lambda url: f"talos-{talos_version}-{url.split('/')[-4]}-nocloud-amd64.iso"
    ),
    overwrite=True,  # Force overwrite to ensure correct version
    opts=pulumi.ResourceOptions(provider=provider),
)


def create_talos_vm(name: str, ip: str, gateway: str, cpu: int = 2, memory: int = 2048):
    return proxmoxve.vm.VirtualMachine(
        name,
        node_name="pve",
        agent=proxmoxve.vm.VirtualMachineAgentArgs(
            enabled=True,
            type="virtio",
        ),
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
controlplane_nodes = []
bootstrap_resources = []

for node in nodes:
    node_type = node.get("type", "proxmox")

    if node_type == "external":
        vm = None  # No VM resource to manage
        pulumi.log.info(
            f"Skipping VM creation for {node['name']} (external node at {node['ip']})"
        )
    else:
        vm = create_talos_vm(
            node["name"],
            node["ip"],
            gateway,
            node.get("cpu", 2),
            node.get("memory", 2048),
        )

    is_bootstrap_node = (
        node["role"] == "controlplane" and node["ip"] == cluster_endpoint_ip
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
        install_image=talos_installer_image,
        bootstrap=is_bootstrap_node,
    )
    if is_bootstrap_node:
        kubeconfig_raw = result["kubeconfig"].kubeconfig_raw
        controlplane_nodes.append(node["ip"])
        bootstrap_resources.append(result["bootstrap"])
    elif node["role"] == "controlplane":
        controlplane_nodes.append(node["ip"])


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
talosconfig_yaml = talosconfig_dict.apply(
    lambda cfg: yaml.dump(cfg, default_flow_style=False)
)

pulumi.export("kubeconfig", kubeconfig_raw)
pulumi.export(
    "talosconfig",
    talosconfig_yaml,
)

# Wait for Talos to report the node fully booted before creating Kubernetes resources
talosconfig_b64 = talosconfig_yaml.apply(
    lambda cfg: base64.b64encode(cfg.encode("utf-8")).decode("utf-8")
)

wait_for_cluster = command.Command(
    "wait-for-cluster",
    create=talosconfig_b64.apply(
        lambda b64: (
            "set -euo pipefail; "
            "printf %s '" + b64 + "' | base64 -d > /tmp/talosconfig.yaml; "
            "for i in $(seq 1 60); do "
            f"talosctl --talosconfig /tmp/talosconfig.yaml -n {cluster_endpoint_ip} health && exit 0; "
            "sleep 10; "
            "done; "
            "exit 1"
        )
    ),
    delete="true",
    opts=pulumi.ResourceOptions(
        depends_on=bootstrap_resources if kubeconfig_raw else []
    ),
)


k8s_provider = kubernetes.Provider(
    "k8s-provider",
    kubeconfig=kubeconfig_raw,
    enable_server_side_apply=True,
    opts=pulumi.ResourceOptions(depends_on=[wait_for_cluster]),
)

argocd_namespace = kubernetes.core.v1.Namespace(
    "argocd-namespace",
    metadata={"name": "argocd"},
    opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[wait_for_cluster]),
)

argocd = Release(
    "argocd",
    ReleaseArgs(
        name="argocd",
        chart="argo-cd",
        repository_opts=RepositoryOptsArgs(
            repo="https://argoproj.github.io/argo-helm",
        ),
        version=argocd_version,
        value_yaml_files=[
            pulumi.FileAsset("../argocd/applications/values/argocd.yaml")
        ],
        namespace="argocd",
    ),
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[wait_for_cluster, argocd_namespace],
    ),
)


argocd_applications = kubernetes.yaml.ConfigFile(
    "argocd-applications",
    file="../argocd/all-the-apps.yaml",
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[argocd],
    ),
)


pulumi.export("talos_image_url", talos_iso_url)
pulumi.export("talos_installer_image", talos_installer_image)
pulumi.export("talos_version", talos_version)
