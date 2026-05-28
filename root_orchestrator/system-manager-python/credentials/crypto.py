import json
import os

from cryptography.fernet import Fernet

_fernet: Fernet | None = None
_credentials_enabled: bool = False


def _init_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
        if not key:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY environment variable is not set")
        try:
            _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            raise RuntimeError(f"Invalid CREDENTIAL_ENCRYPTION_KEY: {e}")
    return _fernet


def init_crypto() -> None:
    """Call at startup to validate config and fail fast if anything is missing.

    NOTE: All credentials are encrypted with a single static key. Rotating the
    key requires re-encrypting every data_ciphertext document in the credentials
    collection — there is no automated rotation utility. Back up the key and the
    collection before attempting a manual rotation.
    """
    global _credentials_enabled
    _init_fernet()
    _credentials_enabled = True


def is_enabled() -> bool:
    """Return True only when init_crypto() completed successfully."""
    return _credentials_enabled


def encrypt_payload(data: dict) -> str:
    """Fernet-encrypt a dict payload and return a base64-encoded ciphertext string."""
    plaintext = json.dumps(data).encode()
    return _init_fernet().encrypt(plaintext).decode()


def decrypt_payload(ciphertext: str) -> dict:
    """Fernet-decrypt a ciphertext string and return the original dict."""
    plaintext = _init_fernet().decrypt(ciphertext.encode())
    return json.loads(plaintext)
