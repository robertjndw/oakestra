import base64
import json
import os
from typing import Any

from cryptography.fernet import Fernet

_fernet: Fernet | None = None
_hybrid_registered = False
_enc_cache: dict[str, Any] = {}  # key_id -> HybridEncrypt primitive
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


def _init_hybrid() -> None:
    global _hybrid_registered
    if not _hybrid_registered:
        from tink import hybrid
        hybrid.register()
        _hybrid_registered = True


def init_crypto() -> None:
    """Call at startup to validate config and fail fast if anything is missing."""
    global _credentials_enabled
    _init_fernet()
    _init_hybrid()
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


def _get_enc_primitive(pub_keyset_b64: str, key_id: str) -> Any:
    if key_id not in _enc_cache:
        import tink
        from tink import hybrid

        _init_hybrid()
        pub_keyset_bytes = base64.b64decode(pub_keyset_b64)
        pub_handle = tink.read_no_secret_keyset_handle(tink.BinaryKeysetReader(pub_keyset_bytes))
        _enc_cache[key_id] = pub_handle.primitive(hybrid.HybridEncrypt)
    return _enc_cache[key_id]


def seal_for_worker(pub_keyset_b64: str, materialized: dict, context_info: bytes, key_id: str = "") -> str:
    """
    HPKE-seal a materialized credential dict for a specific worker.

    Uses the worker's Tink public keyset (base64-encoded) and binds the
    ciphertext to context_info so it can only be decrypted with the right
    context (credential_id, job_id, worker_id, unix_ts, etc.).

    Returns the ciphertext as a base64 string.
    """
    enc = _get_enc_primitive(pub_keyset_b64, key_id)
    plaintext = json.dumps(materialized).encode()
    ciphertext = enc.encrypt(plaintext, context_info)
    return base64.b64encode(ciphertext).decode()


def canonical_context_info(
    credential_id: str,
    job_id: str,
    instance_number: int,
    worker_id: str,
    key_id: str,
    unix_ts: int,
) -> bytes:
    """
    Build the canonical HPKE context_info bytes that bind a sealed credential to
    a specific deployment context.  Changing any field causes decryption to fail,
    which prevents cross-deployment replay attacks.
    """
    parts = [
        credential_id.encode("utf-8"),
        job_id.encode("utf-8"),
        str(instance_number).encode("utf-8"),
        worker_id.encode("utf-8"),
        key_id.encode("utf-8"),
        str(unix_ts).encode("utf-8"),
    ]
    return b"\x00".join(parts)
