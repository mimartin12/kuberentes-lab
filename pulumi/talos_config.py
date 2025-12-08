import pulumi
import pulumiverse_talos as talos
import json


def create_talos_secrets(name: str):
    """Create Talos cluster secrets. Call once per cluster."""
    return talos.machine.Secrets(f"{name}-secrets")


def apply_talos_config(
    name: str,
    secrets: talos.machine.Secrets,
    cluster_name: str,
    cluster_endpoint: str,
    node_ip: str,
    role: str = "controlplane",
    install_disk: str = "/dev/sda",
    installer_image: pulumi.Input[str] = None,
    hostname: str = None,
    vm: pulumi.Resource = None,
):
    """
    Generates and applies Talos config to a node.
    """
    # Build the machine_secrets args structure from the Secrets resource output
    machine_secrets_args = talos.machine.GetConfigurationMachineSecretsArgs(
        certs=talos.machine.GetConfigurationMachineSecretsCertsArgs(
            etcd=talos.machine.GetConfigurationMachineSecretsCertsEtcdArgs(
                cert=secrets.machine_secrets.certs.etcd.cert,
                key=secrets.machine_secrets.certs.etcd.key,
            ),
            k8s=talos.machine.GetConfigurationMachineSecretsCertsK8sArgs(
                cert=secrets.machine_secrets.certs.k8s.cert,
                key=secrets.machine_secrets.certs.k8s.key,
            ),
            k8s_aggregator=talos.machine.GetConfigurationMachineSecretsCertsK8sAggregatorArgs(
                cert=secrets.machine_secrets.certs.k8s_aggregator.cert,
                key=secrets.machine_secrets.certs.k8s_aggregator.key,
            ),
            k8s_serviceaccount=talos.machine.GetConfigurationMachineSecretsCertsK8sServiceaccountArgs(
                key=secrets.machine_secrets.certs.k8s_serviceaccount.key,
            ),
            os=talos.machine.GetConfigurationMachineSecretsCertsOsArgs(
                cert=secrets.machine_secrets.certs.os.cert,
                key=secrets.machine_secrets.certs.os.key,
            ),
        ),
        cluster=talos.machine.GetConfigurationMachineSecretsClusterArgs(
            id=secrets.machine_secrets.cluster.id,
            secret=secrets.machine_secrets.cluster.secret,
        ),
        secrets=talos.machine.GetConfigurationMachineSecretsSecretsArgs(
            bootstrap_token=secrets.machine_secrets.secrets.bootstrap_token,
            secretbox_encryption_secret=secrets.machine_secrets.secrets.secretbox_encryption_secret,
        ),
        trustdinfo=talos.machine.GetConfigurationMachineSecretsTrustdinfoArgs(
            token=secrets.machine_secrets.trustdinfo.token,
        ),
    )

    # Generate machine configuration
    machine_config = talos.machine.get_configuration_output(
        cluster_name=cluster_name,
        machine_type=role,
        cluster_endpoint=cluster_endpoint,
        machine_secrets=machine_secrets_args,
    )

    # Build config patches
    config_patches = []
    if hostname:
        config_patches.append(json.dumps({"machine": {"network": {"hostname": hostname}}}))
    
    if installer_image:
        if isinstance(installer_image, pulumi.Output):
            config_patches.append(
                installer_image.apply(lambda img: json.dumps({
                    "machine": {"install": {"disk": install_disk, "image": img}}
                }))
            )
        else:
            config_patches.append(json.dumps({
                "machine": {"install": {"disk": install_disk, "image": installer_image}}
            }))

    # Apply config to node
    depends = [vm] if vm else []
    config_apply = talos.machine.ConfigurationApply(
        f"{name}-config-apply",
        client_configuration=secrets.client_configuration,
        machine_configuration_input=machine_config.machine_configuration,
        node=node_ip,
        config_patches=config_patches if config_patches else None,
        opts=pulumi.ResourceOptions(depends_on=depends),
    )

    result = {"config_apply": config_apply}

    # Bootstrap if controlplane
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