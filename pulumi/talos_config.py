import pulumi
import pulumiverse_talos as talos
import json
from pathlib import Path


def _get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).parent.parent


def _read_cilium_values() -> str:
    """Read Cilium values from the ArgoCD values file."""
    values_path = (
        _get_repo_root() / "argocd" / "applications" / "values" / "cilium.yaml"
    )
    with open(values_path, "r") as f:
        return f.read()


def _get_cilium_inline_manifests(cilium_version: str = "1.16.0") -> list:
    """
    Build inline manifests for Cilium bootstrap.
    Returns list of dicts with 'name' and 'contents' keys.
    """
    cilium_values = _read_cilium_values()

    # ConfigMap containing Cilium values
    cilium_values_manifest = {
        "name": "cilium-values",
        "contents": f"""---
apiVersion: v1
kind: ConfigMap
metadata:
  name: cilium-values
  namespace: kube-system
data:
  values.yaml: |
{_indent(cilium_values, 4)}
""",
    }

    # Job that installs Cilium using the values ConfigMap
    cilium_install_manifest = {
        "name": "cilium-bootstrap",
        "contents": f"""---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: cilium-install
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: ServiceAccount
  name: cilium-install
  namespace: kube-system
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: cilium-install
  namespace: kube-system
---
apiVersion: batch/v1
kind: Job
metadata:
  name: cilium-install
  namespace: kube-system
spec:
  backoffLimit: 10
  template:
    metadata:
      labels:
        app: cilium-install
    spec:
      restartPolicy: OnFailure
      tolerations:
        - operator: Exists
        - effect: NoSchedule
          operator: Exists
        - effect: NoExecute
          operator: Exists
        - effect: PreferNoSchedule
          operator: Exists
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoExecute
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: PreferNoSchedule
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: node-role.kubernetes.io/control-plane
                    operator: Exists
      serviceAccountName: cilium-install
      hostNetwork: true
      containers:
      - name: cilium-install
        image: quay.io/cilium/cilium-cli-ci:latest
        env:
        - name: KUBERNETES_SERVICE_HOST
          valueFrom:
            fieldRef:
              apiVersion: v1
              fieldPath: status.podIP
        - name: KUBERNETES_SERVICE_PORT
          value: "6443"
        volumeMounts:
          - name: values
            mountPath: /root/app/values.yaml
            subPath: values.yaml
        command:
          - cilium
          - install
          - --version=v{cilium_version}
          - --values
          - /root/app/values.yaml
      volumes:
        - name: values
          configMap:
            name: cilium-values
""",
    }

    return [cilium_values_manifest, cilium_install_manifest]


def _indent(text: str, spaces: int) -> str:
    """Indent each line of text by the specified number of spaces."""
    indent_str = " " * spaces
    return "\n".join(
        indent_str + line if line.strip() else line for line in text.split("\n")
    )


def create_talos_secrets(name: str, talos_version: str = None):
    return talos.machine.Secrets(f"{name}-secrets", talos_version=talos_version)


def apply_talos_config(
    name: str,
    secrets: talos.machine.Secrets,
    cluster_name: str,
    cluster_endpoint: str,
    node_ip: str,
    role: str = "controlplane",
    install_disk: str = "/dev/sda",
    hostname: str = None,
    vm: pulumi.Resource = None,
    gateway: str = "192.168.1.1",
    nameservers: list = None,
    use_cilium: bool = False,
    cilium_version: str = "1.16.0",
    kubernetes_version: str = None,
):
    nameservers = nameservers or ["192.168.1.1"]

    # Build machine config patch
    machine_patch = {
        "machine": {
            "network": {
                "hostname": hostname or name,
                "nameservers": nameservers,
                "interfaces": [
                    {
                        "deviceSelector": {"busPath": "0*"},
                        "addresses": [f"{node_ip}/24"],
                        "routes": [{"network": "0.0.0.0/0", "gateway": gateway}],
                    }
                ],
            },
            # Enable kubelet certificate rotation for metrics-server
            # https://docs.siderolabs.com/kubernetes-guides/monitoring-and-observability/deploy-metrics-server
            "kubelet": {
                "extraArgs": {
                    "rotate-server-certificates": "true",
                },
            },
        }
    }

    # For control plane nodes with Cilium: disable default CNI, kube-proxy, and inject inline manifests
    if use_cilium and role == "controlplane":
        machine_patch["cluster"] = {
            "network": {"cni": {"name": "none"}},
            "proxy": {"disabled": True},
            "inlineManifests": _get_cilium_inline_manifests(cilium_version),
            # Install kubelet cert approver and metrics-server during bootstrap
            "extraManifests": [
                "https://raw.githubusercontent.com/alex1989hu/kubelet-serving-cert-approver/main/deploy/standalone-install.yaml",
                "https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml",
            ],
        }
    elif role == "controlplane":
        machine_patch["cluster"] = {
            "extraManifests": [
                "https://raw.githubusercontent.com/alex1989hu/kubelet-serving-cert-approver/main/deploy/standalone-install.yaml",
                "https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml",
            ],
        }

    network_patch = json.dumps(machine_patch)

    # Convert secrets output to the format expected by get_configuration_output
    machine_secrets_dict = secrets.machine_secrets.apply(
        lambda ms: {
            "certs": {
                "etcd": {"cert": ms.certs.etcd.cert, "key": ms.certs.etcd.key},
                "k8s": {"cert": ms.certs.k8s.cert, "key": ms.certs.k8s.key},
                "k8sAggregator": {
                    "cert": ms.certs.k8s_aggregator.cert,
                    "key": ms.certs.k8s_aggregator.key,
                },
                "k8sServiceaccount": {"key": ms.certs.k8s_serviceaccount.key},
                "os": {"cert": ms.certs.os.cert, "key": ms.certs.os.key},
            },
            "cluster": {"id": ms.cluster.id, "secret": ms.cluster.secret},
            "secrets": {
                "bootstrapToken": ms.secrets.bootstrap_token,
                "secretboxEncryptionSecret": ms.secrets.secretbox_encryption_secret,
            },
            "trustdinfo": {"token": ms.trustdinfo.token},
        }
    )

    machine_config = talos.machine.get_configuration_output(
        cluster_name=cluster_name,
        machine_type=role,
        cluster_endpoint=cluster_endpoint,
        machine_secrets=machine_secrets_dict,
        config_patches=[network_patch],
        kubernetes_version=kubernetes_version,
    )

    config_apply = talos.machine.ConfigurationApply(
        f"{name}-config-apply",
        client_configuration=secrets.client_configuration,
        machine_configuration_input=machine_config.machine_configuration,
        node=node_ip,
        opts=pulumi.ResourceOptions(depends_on=[vm] if vm else []),
    )

    result = {"config_apply": config_apply}

    if role == "controlplane":
        result["bootstrap"] = talos.machine.Bootstrap(
            f"{name}-bootstrap",
            client_configuration=secrets.client_configuration,
            node=node_ip,
            opts=pulumi.ResourceOptions(depends_on=[config_apply]),
        )

        result["kubeconfig"] = talos.cluster.Kubeconfig(
            f"{name}-kubeconfig",
            client_configuration=secrets.client_configuration,
            node=node_ip,
            opts=pulumi.ResourceOptions(depends_on=[result["bootstrap"]]),
        )

    return result
