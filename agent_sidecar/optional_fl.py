"""Lazy integration points for the separately installed training extension."""
from importlib import import_module
from importlib.util import find_spec

INSTALL_HINT = "Install the optional FL package: python -m pip install ./packages/federated"


def available():
    return find_spec("sidecar_fl") is not None


def load(module):
    if not available():
        raise ValueError(INSTALL_HINT)
    return import_module("sidecar_fl." + module)
