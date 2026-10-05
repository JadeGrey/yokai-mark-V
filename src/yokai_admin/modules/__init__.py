"""Admin panel modules.

To add a module: create a package here that exports `MODULE = AdminModule(...)`, then add its
"package.module:NAME" path below. A module that fails to import is shown as failed in the nav and
never stops the panel.
"""

from __future__ import annotations

MODULE_PATHS: tuple[str, ...] = ("yokai_admin.modules.dashboard:MODULE",)
