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
        database_directory=".",
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


def _make_recycle_resp(status_code: int = 0) -> bytes:
    payload = struct.pack("<h", 2)
    payload += struct.pack("<hI", 1047, 2) + struct.pack("<h", status_code)
    payload += struct.pack("<hI", 1046, 4) + struct.pack("<i", 14)
    return struct.pack("<ihI", 4, 4102, len(payload)) + payload


def test_recycle_device_success(source, monkeypatch):
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = _make_recycle_resp(status_code=0)

    monkeypatch.setattr(source._session, "post", lambda *args, **kwargs: mock_resp)
    assert source.recycle_device() is True


def test_recycle_device_failure(source, monkeypatch):
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = _make_recycle_resp(status_code=1)

    monkeypatch.setattr(source._session, "post", lambda *args, **kwargs: mock_resp)
    assert source.recycle_device() is False


def test_borrow_book_success(source, monkeypatch):
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = struct.pack("<ihI", 4, 2086, 0)

    monkeypatch.setattr(source._session, "post", lambda *args, **kwargs: mock_resp)
    assert source.borrow_book("123456") is True


def test_borrow_book_retry_after_recycle(source, monkeypatch):
    from unittest.mock import MagicMock

    call_count = 0

    def mock_post(url, *args, **kwargs):
        nonlocal call_count
        resp = MagicMock()
        resp.status_code = 200
        if "recycle" in url:
            resp.content = _make_recycle_resp(status_code=0)
        elif "download" in url:
            call_count += 1
            if call_count == 1:
                # First attempt: device limit 293
                resp.content = struct.pack("<ihI", 4, 293, 0)
            else:
                # Second attempt after recycle: success 2086
                resp.content = struct.pack("<ihI", 4, 2086, 0)
        else:
            resp.content = b"{}"
        return resp

    monkeypatch.setattr(source._session, "post", mock_post)
    assert source.borrow_book("123456") is True
    assert call_count == 2


def test_borrow_book_switch_limit_exceeded(source, monkeypatch):
    from unittest.mock import MagicMock
    from audiobookdl.exceptions import UserNotAuthorized

    def mock_post(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "recycle" in url:
            # Recycle fails (switch limit exceeded)
            resp.content = _make_recycle_resp(status_code=1)
        elif "download" in url:
            resp.content = struct.pack("<ihI", 4, 293, 0)
        return resp

    monkeypatch.setattr(source._session, "post", mock_post)
    with pytest.raises(UserNotAuthorized) as exc_info:
        source.borrow_book("123456")

    assert "--device-id" in str(exc_info.value)
