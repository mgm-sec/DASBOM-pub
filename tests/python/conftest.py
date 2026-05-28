"""
Shared fixtures and module-loading helpers.

Scripts use numeric prefixes (09_security_audit.py) so they can't be imported
with a normal `import` statement.  load_script() uses importlib to load them
by file path and returns the module object.
"""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent.parent / "scripts" / "python"
FIXTURES = Path(__file__).parent / "fixtures"


def load_script(name: str):
    """Load a numbered script as a module; cache in sys.modules."""
    if name in sys.modules:
        return sys.modules[name]
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
