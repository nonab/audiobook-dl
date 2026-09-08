import pytest
from audiobookdl.sources.empikgo import EmpikGoSource
from types import SimpleNamespace


@pytest.fixture
def source():
    options = SimpleNamespace(
        database_directory=".",
        skip_downloaded=False,
        password="test_access_token",
    )
    return EmpikGoSource(options)


def test_extract_id_from_url():
    urls = {
        "https://www.empik.com/schronisko-ktore-zostalo-zapomniane-slawomir-gortych,p1648295203,ebooki-i-mp3-p": "p1648295203",
        "https://empik.com/pucio-na-wakacjach,p1725961155,ebooki-i-mp3-p": "p1725961155",
        "https://www.empikgo.com/audiobook/p1234567890": "p1234567890",
        "https://empik.com/app/audiobook/p987654321/listen": "p987654321",
        "p1725961155": "p1725961155",
    }
    for url, expected_id in urls.items():
        assert EmpikGoSource.extract_id_from_url(url) == expected_id


def test_extract_id_invalid():
    with pytest.raises(ValueError):
        EmpikGoSource.extract_id_from_url("https://www.empik.com/invalid-url")


def test_format_metadata(source):
    product = {
        "title": "Pucio na wakacjach",
        "authors": ["Marta Galewska-Kustra"],
        "lector": "Artur Barciś, Waldemar Barwiński, Aleksandra Radwan",
        "publishingHouse": "Wydawnictwo Nasza Księgarnia",
        "description": "<p>Opis audiobooka</p>",
        "language": "pl",
    }
    metadata = source.format_metadata(product, "https://www.empik.com/test,p123456,p")

    assert metadata.title == "Pucio na wakacjach"
    assert metadata.authors == ["Marta Galewska-Kustra"]
    assert metadata.narrators == [
        "Artur Barciś",
        "Waldemar Barwiński",
        "Aleksandra Radwan",
    ]
    assert metadata.publisher == "Wydawnictwo Nasza Księgarnia"
    assert metadata.description == "<p>Opis audiobooka</p>"


def test_download_files_parsing(source, monkeypatch):
    mock_dl_response = {
        "format": "AUDIOBOOK",
        "chapters": [
            {
                "chapterTitle": "01_Wstep",
                "downloadLink": "https://warehouse.virtualo.pl/chap1.mp3",
                "chapterLength": "120",
                "fileNumber": 1,
            },
            {
                "chapterTitle": "02_Rozdzial_1",
                "downloadLink": "https://warehouse.virtualo.pl/chap2.mp3",
                "chapterLength": "300",
                "fileNumber": 2,
            },
        ],
    }

    monkeypatch.setattr(source, "_post", lambda endpoint, payload: mock_dl_response)

    files, chapters = source.download_files("p123456", "ABOV12345")
    assert len(files) == 2
    assert files[0].title == "01_Wstep"
    assert files[0].url == "https://warehouse.virtualo.pl/chap1.mp3"
    assert files[1].title == "02_Rozdzial_1"

    assert len(chapters) == 2
    assert chapters[0].start == 0
    assert chapters[0].title == "01_Wstep"
    assert chapters[1].start == 120 * 1000
    assert chapters[1].title == "02_Rozdzial_1"


def test_compute_body_hash():
    # Test vector 1: Product info request body
    body1 = '{"supportedFileFormats":["EPUB","PDF"]}'
    assert EmpikGoSource.compute_body_hash(body1) == "b0e85352cda1a6c1429bfc6cd2bdad30"

    # Test vector 2: Download request body
    body2 = '{"lineId":"ABOV64806041","supportedFileFormats":["EPUB","PDF"]}'
    assert EmpikGoSource.compute_body_hash(body2) == "364af202d5bffe16e6241172dce6033d"

    # Test vector 3: Cart / purchase body
    body3 = '{"productsInCart":[{"productId":"p1725961155"}]}'
    assert EmpikGoSource.compute_body_hash(body3) == "c60068ff1e3f125c44fcf8374fd58096"

