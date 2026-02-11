import pulumi
import pulumi_proxmoxve as proxmoxve
import pulumi_kubernetes as kubernetes
import yaml
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs, RepositoryOptsArgs
from components import TalosImageFactory, TalosImageFactoryArgs, TalosCluster, TalosClusterArgs

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

# Create Talos image factory (ISO and installer image)
image_factory = TalosImageFactory(
    "talos-image",
    TalosImageFactoryArgs(
        talos_version=talos_version,
        platform="nocloud",
        extensions=["siderolabs/qemu-guest-agent", "siderolabs/iscsi-tools"],
        node_name="pve01",
        datastore_id="local",
        proxmox_provider=proxmox_provider,
    ),
)

# Create Talos cluster with all nodes
cluster = TalosCluster(
    cluster_name,
    TalosClusterArgs(
        cluster_name=cluster_name,
        nodes=nodes,
        gateway=gateway,
        talos_installer_image=image_factory.installer_image,
        talos_iso_file_id=image_factory.iso_file.id,
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

# Exports
pulumi.export("kubeconfig", cluster.kubeconfig_raw)
pulumi.export("talosconfig", cluster.talosconfig_yaml)
pulumi.export("talos_image_url", image_factory.iso_url)
pulumi.export("talos_installer_image", image_factory.installer_image)
pulumi.export("talos_version", talos_version)
pulumi.export("cluster_endpoint", f"https://{cluster_endpoint_ip}:6443")
pulumi.export("controlplane_ips", cluster.controlplane_ips)
