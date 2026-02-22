"""TalosImageFactory Pulumi Component"""

import pulumi
import pulumi_proxmoxve as proxmoxve
import pulumiverse_talos as talos
import json


class TalosImageFactoryArgs:
    """Arguments for TalosImageFactory component"""

    def __init__(
        self,
        talos_version: str,
        platform: str = "nocloud",
        arch: str = "amd64",
        extensions: list[str] = None,
        node_name: str = "pve01",
        datastore_id: str = "local",
        proxmox_provider: proxmoxve.Provider = None,
        upload_to_proxmox: bool = True,
    ):
        self.talos_version = talos_version
        self.platform = platform
        self.arch = arch
        self.extensions = extensions or []
        self.node_name = node_name
        self.datastore_id = datastore_id
        self.proxmox_provider = proxmox_provider
        self.upload_to_proxmox = upload_to_proxmox


class TalosImageFactory(pulumi.ComponentResource):
    """
    A Pulumi ComponentResource that creates Talos image assets including:
    - Image factory schematic
    - ISO download URL
    - Installer image reference
    - Proxmox ISO file download
    """

    def __init__(
        self,
        name: str,
        args: TalosImageFactoryArgs,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("custom:talos:ImageFactory", name, {}, opts)

        # Create the image factory schematic
        schematic_data = {
            "customization": {
                "systemExtensions": {"officialExtensions": args.extensions}
            }
        }

        self.schematic = talos.imagefactory.Schematic(
            f"{name}-schematic",
            schematic=json.dumps(schematic_data),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Generate ISO URL from schematic
        self.iso_url = self.schematic.id.apply(
            lambda s_id: f"https://factory.talos.dev/image/{s_id}/{args.talos_version}/{args.platform}-{args.arch}.iso"
        )

        # Generate installer image reference
        self.installer_image = self.schematic.id.apply(
            lambda s_id: f"factory.talos.dev/{args.platform}-installer/{s_id}:{args.talos_version}"
        )

        # Optionally download ISO to Proxmox (skip for external-only artifacts)
        self.iso_file = None
        if args.proxmox_provider and args.upload_to_proxmox:
            self.iso_file = proxmoxve.download.File(
                f"{name}-iso",
                content_type="iso",
                datastore_id=args.datastore_id,
                node_name=args.node_name,
                url=self.iso_url,
                file_name=self.schematic.id.apply(
                    lambda s_id: f"talos-{args.talos_version}-{s_id[:12]}-{args.platform}-{args.arch}.iso"
                ),
                overwrite=True,
                opts=pulumi.ResourceOptions(
                    parent=self,
                    provider=args.proxmox_provider,
                ),
            )

        # Expose iso_file_id attribute safely (None when upload skipped)
        self.iso_file_id = self.iso_file.id if self.iso_file else None

        self.register_outputs(
            {
                "iso_url": self.iso_url,
                "installer_image": self.installer_image,
                "iso_file_id": (self.iso_file.id if self.iso_file else None),
            }
        )
