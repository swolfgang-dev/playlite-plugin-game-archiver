"""Optional integration fixtures: skip when required plugins are not installed."""
import importlib
import unittest
from playlite.providers import discover_plugins

def require_plugin(identity):
    plugin = discover_plugins(include_disabled=True).get(identity)
    if plugin is None:
        raise unittest.SkipTest(f'Install the {identity} plugin to run its integration tests.')
    return plugin
