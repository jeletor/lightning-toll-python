"""
NIP-04 encryption helpers for Nostr Wallet Connect (NWC).

Uses coincurve (libsecp256k1) for ECDH and PyCryptodome for AES-256-CBC.
This implements the NIP-04 encryption standard used by NWC (NIP-47).
"""

from __future__ import annotations

import os
from base64 import b64decode, b64encode

from coincurve import PrivateKey, PublicKey
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


def get_public_key(private_key_hex: str) -> str:
    """
    Derive the Nostr public key (x-only, 32 bytes hex) from a private key.

    Args:
        private_key_hex: 32-byte hex-encoded private key.

    Returns:
        32-byte hex-encoded x-only public key.
    """
    sk = PrivateKey(bytes.fromhex(private_key_hex))
    # coincurve gives compressed pubkey (33 bytes). Drop the prefix byte.
    compressed = sk.public_key.format(compressed=True)
    return compressed[1:].hex()


def compute_shared_secret(private_key_hex: str, public_key_hex: str) -> bytes:
    """
    Compute the NIP-04 shared secret via ECDH.

    Args:
        private_key_hex: Our 32-byte hex-encoded private key.
        public_key_hex: Their 32-byte hex-encoded x-only public key.

    Returns:
        32-byte shared secret (x-coordinate of ECDH point).
    """
    sk = PrivateKey(bytes.fromhex(private_key_hex))
    # NIP-04 uses the x-only pubkey, so we need to add the 02 prefix
    pk_bytes = b"\x02" + bytes.fromhex(public_key_hex)
    pk = PublicKey(pk_bytes)

    # ECDH: multiply their pubkey by our privkey
    shared_point = pk.multiply(sk.secret)
    # Take just the x-coordinate (skip the 02/03 prefix byte)
    shared_x = shared_point.format(compressed=True)[1:]
    return shared_x


def nip04_encrypt(private_key_hex: str, public_key_hex: str, plaintext: str) -> str:
    """
    Encrypt a message using NIP-04 (AES-256-CBC with ECDH shared secret).

    Args:
        private_key_hex: Our 32-byte hex-encoded private key.
        public_key_hex: Their 32-byte hex-encoded x-only public key.
        plaintext: Message to encrypt.

    Returns:
        NIP-04 formatted ciphertext: "<base64_ciphertext>?iv=<base64_iv>"
    """
    shared_secret = compute_shared_secret(private_key_hex, public_key_hex)

    iv = os.urandom(16)
    cipher = AES.new(shared_secret, AES.MODE_CBC, iv)
    padded = pad(plaintext.encode("utf-8"), AES.block_size)
    ciphertext = cipher.encrypt(padded)

    ct_b64 = b64encode(ciphertext).decode("ascii")
    iv_b64 = b64encode(iv).decode("ascii")

    return f"{ct_b64}?iv={iv_b64}"


def nip04_decrypt(private_key_hex: str, public_key_hex: str, encrypted: str) -> str:
    """
    Decrypt a NIP-04 encrypted message.

    Args:
        private_key_hex: Our 32-byte hex-encoded private key.
        public_key_hex: Their 32-byte hex-encoded x-only public key.
        encrypted: NIP-04 formatted ciphertext: "<base64_ciphertext>?iv=<base64_iv>"

    Returns:
        Decrypted plaintext string.
    """
    shared_secret = compute_shared_secret(private_key_hex, public_key_hex)

    parts = encrypted.split("?iv=")
    if len(parts) != 2:
        raise ValueError("Invalid NIP-04 ciphertext format (expected '...?iv=...')")

    ciphertext = b64decode(parts[0])
    iv = b64decode(parts[1])

    cipher = AES.new(shared_secret, AES.MODE_CBC, iv)
    padded = cipher.decrypt(ciphertext)
    plaintext = unpad(padded, AES.block_size)

    return plaintext.decode("utf-8")
