import pulumi
import pulumi_proxmoxve as proxmox

config = pulumi.Config()

provider = proxmox.Provider(
    "proxmoxve",
    endpoint=config.require("proxmox_endpoint"),
    username=config.require("proxmox_username"),
    password=config.require_secret("proxmox_password"),
    insecure=True,
)

virtual_machine = proxmox.vm.VirtualMachineArgs(
    node_name="pve",
    agent=proxmox.vm.VirtualMachineAgentArgs(
        enabled=False,
        trim=True,
        type="virtio"
    ),
    bios="ovmf",
    efi_disk=proxmox.vm.VirtualMachineEfiDiskArgs(
        datastore_id="local-lvm",
        file_format="raw",
        type="4m",
    ),
    cpu=proxmox.vm.VirtualMachineCpuArgs(
        cores=2,
        sockets=1,
        type="host"
    ),
    disks=[
        proxmox.vm.VirtualMachineDiskArgs(
            interface="scsi0",
            size=20,
            datastore_id="local-lvm",
            file_format="raw",
        )
    ],
    memory=proxmox.vm.VirtualMachineMemoryArgs(
        dedicated=2048
    ),
    network_devices=[
        proxmox.vm.VirtualMachineNetworkDeviceArgs(
            model="virtio",
            bridge="vmbr0"
        )
    ],
    started=False,
    cdrom=proxmox.vm.VirtualMachineCdromArgs(
        file_id="none"
    )
)

# Lab VMs
k3d_master = proxmox.vm.VirtualMachine(
    resource_name="k3d-master",
    args=virtual_machine,
    opts=pulumi.ResourceOptions(provider=provider)
)
