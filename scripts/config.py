import importlib.util
import pathlib


def _load_machine_config():
    """devcontainer/config.py, under another name than this module."""
    path = pathlib.Path(__file__).resolve().parents[1] / "devcontainer" / "config.py"
    spec = importlib.util.spec_from_file_location("odoo_dev_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load()


_CONFIG = _load_machine_config()
_ROOT = _CONFIG["REPO_ROOT"]
BUNDLE_SUFFIX = _CONFIG["BUNDLE_SUFFIX"]
folder_by_repo = {
    "design-themes": f"{_ROOT}/design-themes",
    "documentation": f"{_ROOT}/documentation",
    "enterprise": f"{_ROOT}/enterprise",
    "odoo": f"{_ROOT}/odoo",
    "owl": f"{_ROOT}/owl",
    "sfu": f"{_ROOT}/sfu",
    "upgrade-util": f"{_ROOT}/upgrade-util",
    "upgrade": f"{_ROOT}/upgrade",
}
remote_by_repo = {
    "design-themes": "odoo",
    "documentation": "odoo",
    "enterprise": "odoo",
    "odoo": "odoo",
    "owl": "origin",
    "sfu": "origin",
    "upgrade-util": "odoo",
    "upgrade": "odoo",
}
remote_dev_by_repo = {
    "design-themes": "odoo-dev",
    "documentation": "odoo-dev",
    "enterprise": "odoo-dev",
    "odoo": "odoo-dev",
    "owl": _CONFIG["OWL_DEV_REMOTE"],
    "sfu": "origin",
    "upgrade-util": "odoo-dev",
    "upgrade": "odoo-dev",
}
CLAUDE_CONFIG_CONTAINER = f"{_CONFIG['CACHE_ROOT']}/devcontainer/claude-config"
EXTENSIONS_CONTAINER = f"{_CONFIG['CACHE_ROOT']}/devcontainer/vscode-extensions"
FILESTORE_CONTAINER = f"{_CONFIG['SHARE_ROOT']}/Odoo/filestore"
MASTER_ONLY_REPOS = ("owl", "sfu", "upgrade", "upgrade-util")
STATE_ROOT = _CONFIG["STATE_ROOT"]
WORKTREE_CONTAINER = _CONFIG["WORKTREE_ROOT"]
STICKY_BUNDLES = [
    "master",
    "20.0",
    "saas-19.4",
    "saas-19.3",
    "saas-19.2",
    "saas-19.1",
    "19.0",
    "saas-18.4",
    "saas-18.3",
    "saas-18.2",
    "18.0",
    "17.0",
    "16.0",
]
