# Kubernetes Lab
Kubernets lab that currently uses K3d to setup a Kubernets cluster and deploy various applications with Argo CD.

## Proxmox
The lab is hosted on Proxmox.
Configuration is needed for the Pulumi Proxmox provider.

```bash
# Create custom pve role
pveum role add TerraformProv -privs "Datastore.Allocate Datastore.AllocateSpace Datastore.AllocateTemplate Datastore.Audit Pool.Allocate Sys.Audit Sys.Console Sys.Modify VM.Allocate VM.Audit VM.Clone VM.Config.CDROM VM.Config.Cloudinit VM.Config.CPU VM.Config.Disk VM.Config.HWType VM.Config.Memory VM.Config.Network VM.Config.Options VM.Migrate VM.PowerMgmt SDN.Use"

# Create user and assign role
pveum user add terraform-prov@pve --password <password>
pveum aclmod / -user terraform-prov@pve -role TerraformProv

```