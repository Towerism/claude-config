"""Errors the cfg modules raise. The command line prints the message and exits 2."""

from __future__ import annotations


class CfgError(Exception):
    """A refusal or a usage problem; the message says what to do."""


class LockBusy(CfgError):
    """Another cfg process holds the lock."""


class SettingsUnusable(CfgError):
    """settings.json is a symlink, not a regular file, unparseable, or not an object."""


class SettingsChanged(CfgError):
    """Another program wrote settings.json while cfg was writing it."""
