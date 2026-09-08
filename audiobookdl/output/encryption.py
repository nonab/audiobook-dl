from Crypto.Cipher import AES
from audiobookdl.utils.audiobook import (
    AudiobookFileEncryption,
    AESEncryption,
    LegimiAESEncryption,
)

def decrypt_file(path: str, encryption_method: AudiobookFileEncryption):
    """Decrypt encrypted file in place"""
    if isinstance(encryption_method, LegimiAESEncryption):
        decrypt_file_legimi(
            path,
            encryption_method.key,
            encryption_method.iv,
            encryption_method.offset,
        )
    elif isinstance(encryption_method, AESEncryption):
        decrypt_file_aes(path, encryption_method.key, encryption_method.iv)

def decrypt_file_aes(path: str, key: bytes, iv: bytes):
    """Decrypt AES encrypted file in place"""
    with open(path, "rb") as f:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(f.read())
    with open(path, "wb") as f:
        f.write(decrypted)

def decrypt_file_legimi(path: str, key: bytes, iv: bytes, offset: int = 10):
    """Decrypt Legimi AES encrypted file in place by skipping container header"""
    import struct
    with open(path, "rb") as f:
        data = f.read()
    payload = data[offset:]
    block_len = (len(payload) // 16) * 16
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(payload[:block_len])
    if offset >= 10 and len(data) >= 10:
        orig_len = struct.unpack("<q", data[2:10])[0]
        if 0 < orig_len <= len(decrypted):
            decrypted = decrypted[:orig_len]
    with open(path, "wb") as f:
        f.write(decrypted)
