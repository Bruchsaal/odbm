# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import os

import storage
from logBroker import logger

KEY_FILE = ".odbm_key"
PREFIX = "enc:v1:"

try:
    from cryptography.fernet import Fernet, InvalidToken
    AVAILABLE = True
except ImportError:  # pragma: no cover - cryptography is a hard requirement
    Fernet = None
    InvalidToken = Exception
    AVAILABLE = False

_cache = {}


def _key_path():
    return storage.data_file(KEY_FILE)


def _load_key():
    """Read the local Fernet key, creating it on first use. Cached per path."""
    path = _key_path()
    if path in _cache:
        return _cache[path]

    key = None
    if os.path.exists(path):
        try:
            with open(path, 'rb') as f:
                candidate = f.read().strip()
            Fernet(candidate)  # validates length/encoding
            key = candidate
        except (OSError, ValueError, TypeError) as e:
            logger.error("Crypto", "Key file unreadable, generating a new one", str(e))

    if key is None:
        key = Fernet.generate_key()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(key)
            logger.info("Crypto", "Generated new credential encryption key")
        except OSError as e:
            logger.error("Crypto", "Could not persist encryption key", str(e))

    _cache[path] = key
    return key


def reset_cache():
    """Drop the cached key. Used by tests and after a data-dir switch."""
    _cache.clear()


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext):
    """Encrypt a password. Returns the input unchanged if it is empty."""
    if not plaintext:
        return ""
    if is_encrypted(plaintext):
        return plaintext
    if not AVAILABLE:
        logger.warn("Crypto", "cryptography unavailable, storing password as-is")
        return plaintext
    try:
        token = Fernet(_load_key()).encrypt(plaintext.encode('utf-8'))
        return PREFIX + token.decode('ascii')
    except Exception as e:
        logger.error("Crypto", "Encryption failed, storing password as-is", str(e))
        return plaintext


def decrypt(stored):
    """
    Turn a stored password back into plaintext. Values written before
    encryption was introduced have no prefix and are passed through.
    """
    if not stored:
        return ""
    if not is_encrypted(stored):
        return stored
    if not AVAILABLE:
        logger.error("Crypto", "Cannot decrypt: cryptography unavailable")
        return ""
    try:
        token = stored[len(PREFIX):].encode('ascii')
        return Fernet(_load_key()).decrypt(token).decode('utf-8')
    except (InvalidToken, ValueError, TypeError) as e:
        logger.error("Crypto", "Could not decrypt stored password", str(e))
        return ""
