"""Various utility methods."""

import fire


def get_base_from_bundle_name(bundle_name):
    """Get the base name from a bundle name."""
    parts = bundle_name.split("-")
    return f"{parts[0]}-{parts[1]}" if parts[0] == "saas" else parts[0]


if __name__ == "__main__":
    fire.Fire()
