from rboto import kms


async def encrypt_value(key_id: str, value: bytes) -> bytes:
    response = await kms(region="us-east-1").encrypt(
        key_id=key_id,
        plaintext=value,
        encryption_context={"application": "example"},
    )
    if response.ciphertext_blob is None:
        raise RuntimeError("KMS returned no ciphertext")
    return response.ciphertext_blob
