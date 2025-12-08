import pulumi
import pulumiverse_talos as talos
import json


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
):
    nameservers = nameservers or ["192.168.1.1"]

    network_patch = json.dumps(
        {
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
                }
            }
        }
    )

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
