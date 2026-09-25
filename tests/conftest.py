import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _clean_dynamically_loaded_modules():
    """action_loader/plugin_loader import discovered files as actions.<stem> /
    plugins.<stem> into sys.modules. Without this, a stem reused by two tests
    (in different tmp_path dirs) would silently hit the other test's cached
    module instead of being freshly imported."""

    def _clean():
        for key in list(sys.modules):
            if key.startswith("actions.") or key.startswith("plugins."):
                del sys.modules[key]

    _clean()
    yield
    _clean()
