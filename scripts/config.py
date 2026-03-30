# change this config
_ROOT = "/home/seb/repo"
BUNDLE_SUFFIX = "--seb"
folder_by_repo = {
    "design-themes": f"{_ROOT}/design-themes",
    "documentation": f"{_ROOT}/documentation",
    "enterprise": f"{_ROOT}/enterprise",
    "odoo": f"{_ROOT}/odoo",
    "upgrade-util": f"{_ROOT}/upgrade-util",
    "upgrade": f"{_ROOT}/upgrade",
}
remote_by_repo = {
    "design-themes": "odoo",
    "documentation": "odoo",
    "enterprise": "odoo",
    "odoo": "odoo",
    "upgrade-util": "odoo",
    "upgrade": "odoo",
}
remote_dev_by_repo = {
    "design-themes": "odoo-dev",
    "documentation": "odoo-dev",
    "enterprise": "odoo-dev",
    "odoo": "odoo-dev",
    "upgrade-util": "odoo-dev",
    "upgrade": "odoo-dev",
}
WORKTREE_CONTAINER = "/home/seb/src/odoo"
