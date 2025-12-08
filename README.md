# Kubernetes Lab

Kubernetes lab running Talos Linux on Proxmox, managed with Pulumi and ArgoCD.

## Prerequisites

- Proxmox VE
- Pulumi CLI
- ``kubectl``

## Proxmox Setup

Create a user with the required permissions:

```bash
pveum role add TerraformProv -privs "Datastore.Allocate Datastore.AllocateSpace Datastore.AllocateTemplate Datastore.Audit Pool.Allocate Sys.Audit Sys.Console Sys.Modify VM.Allocate VM.Audit VM.Clone VM.Config.CDROM VM.Config.Cloudinit VM.Config.CPU VM.Config.Disk VM.Config.HWType VM.Config.Memory VM.Config.Network VM.Config.Options VM.Migrate VM.PowerMgmt SDN.Use"

pveum user add terraform-prov@pve --password <password>
pveum aclmod / -user terraform-prov@pve -role TerraformProv
```

## Pulumi Configuration

```bash
cd pulumi
pulumi config set proxmox_endpoint https://<proxmox-ip>:8006
pulumi config set proxmox_username terraform-prov@pve
pulumi config set --secret proxmox_password <password>
pulumi config set talos_version v1.11.5  # optional
```

## Deploy

```bash
cd pulumi
pulumi up
```

## Get Kubeconfig

```bash
pulumi stack output kubeconfig --show-secrets > kubeconfig.yaml
export KUBECONFIG=$PWD/kubeconfig.yaml
kubectl get nodes
```

To merge with your existing kubeconfig:

```bash
KUBECONFIG=~/.kube/config:./kubeconfig.yaml kubectl config view --flatten > ~/.kube/config.new
mv ~/.kube/config.new ~/.kube/config
```
