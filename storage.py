# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import sys
import tempfile

# All mutable user data (configs, library, history) lives in one directory.
# Defaults to the process working directory, which is where the packaged
# binary is launched from. Tests override it via set_data_dir().
_data_dir = os.getcwd()


def set_data_dir(path):
    global _data_dir
    _data_dir = os.path.abspath(path)
    os.makedirs(_data_dir, exist_ok=True)


def get_data_dir():
    return _data_dir


def data_file(name):
    return os.path.join(_data_dir, name)


def read_json(name, default=None):
    """Load a JSON file from the data dir. Returns default on any failure."""
    path = data_file(name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, payload, private=False, indent=2):
    """
    Write JSON atomically: a crash mid-write leaves the previous file intact
    rather than a truncated one. private=True restricts the file to the owner.
    """
    path = data_file(name)
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.tmp-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp always creates 0600; widen non-secret files back to the
        # process umask so queries.json and settings.json stay readable.
        if private:
            os.chmod(tmp, 0o600)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- read-only bundled resources (static assets, the default query library) ---

_resource_dir = None


def _default_resource_dir():
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', None) or os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def set_resource_dir(path):
    """Override where bundled resources are read from. Used by tests."""
    global _resource_dir
    _resource_dir = os.path.abspath(path) if path else None


def get_resource_dir():
    return _resource_dir or _default_resource_dir()


def resource_path(relative_path):
    """Absolute path to a bundled, read-only resource."""
    return os.path.join(get_resource_dir(), relative_path)
