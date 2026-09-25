import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# Real, on-disk package directories — modules loaded from here are permanent
# imports (actions.web_search, plugins.n8n_analyzer, ...) and must survive
# between tests. Only modules loaded from elsewhere (tmp_path directories used
# by core/action_loader.py and core/plugin_loader.py discovery tests) are the
# ones that need cleaning up between tests.
_REAL_ACTIONS_DIR = os.path.join(_REPO_ROOT, "actions") + os.sep
_REAL_PLUGINS_DIR = os.path.join(_REPO_ROOT, "plugins") + os.sep


@pytest.fixture(autouse=True)
def _clean_dynamically_loaded_modules():
    """action_loader/plugin_loader import discovered files as actions.<stem> /
    plugins.<stem> into sys.modules. Without this, a stem reused by two tests
    (in different tmp_path dirs) would silently hit the other test's cached
    module instead of being freshly imported. Real, permanently-installed
    modules under the repo's actions/ and plugins/ directories are left alone
    so other tests that import them (e.g. actions.web_search) keep working."""

    def _clean():
        for key in list(sys.modules):
            if not (key.startswith("actions.") or key.startswith("plugins.")):
                continue
            mod_file = getattr(sys.modules[key], "__file__", "") or ""
            if mod_file.startswith(_REAL_ACTIONS_DIR) or mod_file.startswith(_REAL_PLUGINS_DIR):
                continue
            del sys.modules[key]

    _clean()
    yield
    _clean()
