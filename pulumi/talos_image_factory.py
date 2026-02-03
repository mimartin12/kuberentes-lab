import pulumi
import pulumiverse_talos as talos
import json


def create_talos_image_assets(
    name: str,
    talos_version: str,
    platform: str = "nocloud",
    arch: str = "amd64",
    extensions: list[str] = None,
) -> tuple[pulumi.Output[str], pulumi.Output[str]]:
    """
    Creates a Talos Image Schematic and returns the ISO image URL
    along with the installer image reference.
    """
    if extensions is None:
        extensions = []

    schematic_data = {
        "customization": {"systemExtensions": {"officialExtensions": extensions}}
    }

    schematic = talos.imagefactory.Schematic(name, schematic=json.dumps(schematic_data))

    iso_url = schematic.id.apply(
        lambda s_id: f"https://factory.talos.dev/image/{s_id}/{talos_version}/{platform}-{arch}.iso"
    )

    installer_image = schematic.id.apply(
        lambda s_id: f"factory.talos.dev/{platform}-installer/{s_id}:{talos_version}"
    )

    return iso_url, installer_image


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
    iso_url, _ = create_talos_image_assets(
        name=name,
        talos_version=talos_version,
        platform=platform,
        arch=arch,
        extensions=extensions,
    )
    return iso_url
