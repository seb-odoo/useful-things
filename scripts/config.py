# change this config
_ROOT = "/home/seb/repo"
BUNDLE_SUFFIX = "--seb"
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
    "owl": "seb-odoo",
    "sfu": "origin",
    "upgrade-util": "odoo-dev",
    "upgrade": "odoo-dev",
}
FILESTORE_CONTAINER = "/home/seb/.local/share/Odoo/filestore"
MASTER_ONLY_REPOS = ("owl", "sfu", "upgrade", "upgrade-util")
WORKTREE_CONTAINER = "/home/seb/src/odoo"
STICKY_BUNDLES = [
    "master",
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
