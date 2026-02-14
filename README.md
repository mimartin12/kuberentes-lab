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

### PCIe Device Passthrough (Optional)

To pass PCIe devices (like GPUs) to VMs:

1. **Enable IOMMU** on your Proxmox host (required for PCIe passthrough)

2. **Create a PCI device mapping** (as root):
   ```bash
   # Example: GPU mapping
   pvesh create /cluster/mapping/pci --id nvidiaGPU --map "node=pve01,path=0000:01:00.0,path=0000:01:00.1"
   ```

3. **Grant mapping permissions** to the terraform-prov user:
   ```bash
   pveum acl modify /mapping/pci/nvidiaGPU -user terraform-prov@pve -role PVEMappingUser
   ```

4. **Configure nodes** with the mapping name in `Pulumi.dev.yaml`:
   ```yaml
   kubernets-lab:nodes:
     - name: talos-worker-01
       ip: "192.168.1.162"
       role: worker
       cpu: 2
       memory: 4096
       pcie_devices:
         - "nvidiaGPU"
   ```

Note: The Talos image must include the necessary drivers. NVIDIA GPU support is included by default via the image factory extensions.

## Pulumi Configuration

```bash
cd pulumi
pulumi config set proxmox_endpoint https://<proxmox-ip>:8006
pulumi config set proxmox_username terraform-prov@pve
pulumi config set --secret proxmox_password <password>
pulumi config set talos_version v1.11.5  # optional
```

## Adding Nodes

Configure nodes in the Pulumi stack, such as `Pulumi.dev.yaml`:

**Proxmox managed nodes:**
These are nodes that will be deployed on Proxmox by Pulumi and configuration can be managed with this project.

```yaml
kubernets-lab:nodes:
  - name: talos-01-master
    ip: "192.168.1.160"
    role: controlplane
    cpu: 2
    memory: 3072
    machine: q35  # Optional, defaults to q35 (required for PCIe passthrough)
    pcie_devices:  # Optional, for PCIe passthrough (requires mapping setup)
      - "nvidiaGPU"
```

**External nodes:**
Flexible external nodes that are not managed by this project, but can be adopted into the cluster.

```yaml
  - name: talos-02-master
    ip: "192.168.1.162"
    role: controlplane
    type: external
    install_disk: "/dev/vda"  # Optional, defaults to /dev/sda
```

For external nodes:

1. Create the VM manually with the Talos ISO (`pulumi stack output talos_image_url`)
2. Configure disk bus type:
   - **SATA/SCSI** → use `/dev/sda` (default)
   - **VirtIO** → use `/dev/vda`
   - Check with: `talosctl --nodes <ip> disks --insecure`
3. Assign a static IP
4. Ensure VM is running in maintenance mode
5. Run `pulumi up` to configure and join the cluster

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
