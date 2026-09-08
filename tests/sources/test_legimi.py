import pytest
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace
from Crypto.Cipher import AES

from audiobookdl.sources.legimi import LegimiSource
from audiobookdl.sources import find_compatible_source
from audiobookdl.output.encryption import decrypt_file_legimi
from audiobookdl.utils.audiobook import LegimiAESEncryption


@pytest.fixture
def source():
    options = SimpleNamespace(
        database_dir=".",
        skip_downloaded=False,
        username="test_user",
        password="test_password",
        device_id="6060841",
    )
    return LegimiSource(options)


def test_url_matching():
    urls = [
        "https://www.legimi.pl/audiobook-fiki-i-basia,b1569331.html",
        "https://mobile-gp.legimi.pl/audiobook-fiki-i-basia,b1569331.html",
        "https://www.legimi.pl/ebook-permakultura,b705484.html",
        "https://mobile-gp.legimi.pl/ebook-permakultura,b705484.html",
        "https://www.legimi.pl/ksiazka/test,b12345.html",
        "https://www.legimi.pl/ebooki/jadzia-petelka-1-3-lat,c4146/?filters=audiobooks,ebooks",
        "https://www.legimi.pl/autor/barbara-supel,ad495367/?filters=unlimited",
        "https://www.legimi.pl/kolekcja/bestsellery,cl1234/",
        "b1569331",
        "1569331",
    ]
    for url in urls:
        assert find_compatible_source(url) == LegimiSource


def test_is_series_url():
    assert LegimiSource.is_series_url("https://www.legimi.pl/ebooki/jadzia-petelka-1-3-lat,c4146/?filters=audiobooks") is True
    assert LegimiSource.is_series_url("https://www.legimi.pl/autor/barbara-supel,ad495367/?filters=audiobooks") is True
    assert LegimiSource.is_series_url("https://www.legimi.pl/kolekcja/top,cl123/") is True
    assert LegimiSource.is_series_url("https://www.legimi.pl/ebook-jej-chlopak-freida-mcfadden,b1619421.html") is False
    assert LegimiSource.is_series_url("1619421") is False


def test_extract_id_from_url():
    cases = {
        "https://www.legimi.pl/audiobook-fiki-i-basia,b1569331.html": "1569331",
        "https://mobile-gp.legimi.pl/ebook-permakultura,b705484.html": "705484",
        "b1569331": "1569331",
        "1569331": "1569331",
    }
    for url, expected in cases.items():
        assert LegimiSource.extract_id_from_url(url) == expected


def test_extract_id_invalid():
    with pytest.raises(ValueError):
        LegimiSource.extract_id_from_url("https://www.legimi.pl/invalid-link/")


def test_build_auth_req():
    pkt = LegimiSource._build_auth_req("test_user", "test_pass", 6060841)
    proto, ptype, plen = struct.unpack("<ihI", pkt[:10])
    assert proto == 21
    assert ptype == 80
    assert plen == len(pkt) - 10

    # Ensure credentials and device id are embedded
    assert b"test_user" in pkt
    assert b"test_pass" in pkt
    assert struct.pack("<q", 6060841) in pkt


def test_decrypt_file_legimi():
    # Test on-the-fly Legimi decryption (10-byte header + AES-128-CBC)
    key = b"0123456789abcdef"
    iv = b"fedcba9876543210"

    raw_payload = b"Hello Legimi Decryption World!!!"  # 32 bytes (multiple of 16)
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    encrypted_payload = cipher.encrypt(raw_payload)

    # 10 byte header: short cipher=0, long decrypted_length=32
    header = struct.pack("<hq", 0, len(raw_payload))
    container_bytes = header + encrypted_payload

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(container_bytes)
        temp_path = Path(f.name)

    try:
        decrypt_file_legimi(temp_path, key=key, iv=iv, offset=10)
        with open(temp_path, "rb") as f:
            decrypted = f.read()
        assert decrypted == raw_payload
    finally:
        if temp_path.exists():
            temp_path.unlink()
