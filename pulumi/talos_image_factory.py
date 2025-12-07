import pulumi
import pulumiverse_talos as talos
import json


def create_talos_image_url(
    name: str,
    talos_version: str,
    platform: str = "nocloud",
    arch: str = "amd64",
    extensions: list[str] = None,
) -> pulumi.Output[str]:
    """
    Creates a Talos Image Schematic and returns the ISO image URL.
    """
    if extensions is None:
        extensions = []

    schematic_data = {
        "customization": {"systemExtensions": {"officialExtensions": extensions}}
    }

    schematic = talos.imagefactory.Schematic(name, schematic=json.dumps(schematic_data))

    # Format: https://factory.talos.dev/image/<schematic_id>/<version>/<platform>-<arch>.iso
    return schematic.id.apply(
        lambda s_id: f"https://factory.talos.dev/image/{s_id}/{talos_version}/{platform}-{arch}.iso"
    )
