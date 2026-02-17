import pulumi
import pulumi_proxmoxve as proxmoxve
import pulumi_kubernetes as kubernetes
import yaml
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs, RepositoryOptsArgs
from components import (
    TalosImageFactory,
    TalosImageFactoryArgs,
    TalosCluster,
    TalosClusterArgs,
    TalosUpgrade,
    TalosUpgradeArgs,
)

# Load configuration
config = pulumi.Config()
talos_version = config.get("talos_version") or "v1.11.5"
kubernetes_version = config.get("kubernetes_version")
cluster_name = config.get("cluster_name") or "talos-cluster"
gateway = config.get("gateway") or "192.168.1.1"
nodes = config.require_object("nodes")
cluster_endpoint_ip = config.get("cluster_endpoint_ip") or nodes[0]["ip"]
use_cilium = config.get_bool("use_cilium") or False
cilium_version = config.get("cilium_version") or "1.16.0"
force_upgrade = config.get_bool("force_upgrade") or False

# Load ArgoCD version from the ArgoCD application manifest
with open("../argocd/applications/argocd.yaml", "r") as f:
    argocd_app = yaml.safe_load(f)
    argocd_version = argocd_app["spec"]["sources"][0]["targetRevision"]

# Create Proxmox provider
proxmox_provider = proxmoxve.Provider(
    "proxmoxve",
    endpoint=config.require("proxmox_endpoint"),
    username=config.require("proxmox_username"),
    password=config.require_secret("proxmox_password"),
    insecure=True,
)

# Create Talos image factories for different node types
image_factory_default = TalosImageFactory(
    "talos-image-default",
    TalosImageFactoryArgs(
        talos_version=talos_version,
        platform="nocloud",
        extensions=[
            "siderolabs/iscsi-tools",
        ],
        node_name="pve01",
        datastore_id="local",
        proxmox_provider=proxmox_provider,
    ),
)

image_factory_gpu = TalosImageFactory(
    "talos-image-gpu",
    TalosImageFactoryArgs(
        talos_version=talos_version,
        platform="nocloud",
        extensions=[
            "siderolabs/iscsi-tools",
            "siderolabs/nvidia-open-gpu-kernel-modules-lts",
            "siderolabs/nvidia-container-toolkit",
        ],
        node_name="pve01",
        datastore_id="local",
        proxmox_provider=proxmox_provider,
    ),
)

image_factories = {
    "default": image_factory_default,
    "gpu": image_factory_gpu,
}

# Create Talos cluster with all nodes
cluster = TalosCluster(
    cluster_name,
    TalosClusterArgs(
        cluster_name=cluster_name,
        nodes=nodes,
        gateway=gateway,
        image_factories=image_factories,
        talos_version=talos_version,
        kubernetes_version=kubernetes_version,
        cluster_endpoint_ip=cluster_endpoint_ip,
        use_cilium=use_cilium,
        cilium_version=cilium_version,
        proxmox_provider=proxmox_provider,
    ),
)

# Install ArgoCD
argocd_namespace = kubernetes.core.v1.Namespace(
    "argocd-namespace",
    metadata={"name": "argocd"},
    opts=pulumi.ResourceOptions(provider=cluster.k8s_provider),
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
        provider=cluster.k8s_provider,
        depends_on=[argocd_namespace],
    ),
)

argocd_applications = kubernetes.yaml.ConfigFile(
    "argocd-applications",
    file="../argocd/all-the-apps.yaml",
    opts=pulumi.ResourceOptions(
        provider=cluster.k8s_provider,
        depends_on=[argocd],
    ),
)

# Upgrade Talos nodes when version changes (masters first, then workers)
# This runs talosctl upgrade commands directly, no ConfigurationApply needed
upgrade = TalosUpgrade(
    "talos-upgrade",
    TalosUpgradeArgs(
        nodes=nodes,
        image_factories=image_factories,
        force=force_upgrade,
    ),
)

# Exports
pulumi.export(
    "talos_images",
    {
        "default": {
            "iso_url": image_factory_default.iso_url,
            "installer_image": image_factory_default.installer_image,
        },
        "gpu": {
            "iso_url": image_factory_gpu.iso_url,
            "installer_image": image_factory_gpu.installer_image,
        },
    },
)
pulumi.export("talos_version", talos_version)

pulumi.export("kubeconfig", pulumi.Output.secret(cluster.kubeconfig_raw))
pulumi.export("talosconfig", pulumi.Output.secret(cluster.talosconfig_yaml))
