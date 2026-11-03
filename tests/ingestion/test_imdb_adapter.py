import gzip

import httpx
import pytest

from app.ingestion.adapters.imdb import ImdbAdapter
from app.ingestion.enums.imdb_file_enum import ImdbFile
from app.ingestion.exceptions import (
    CorruptDownloadError,
    DownloadTooLargeError,
    GoneError,
    NetworkError,
    PermanentError,
    TransientError,
    UntrustedSourceError,
)

BASE = "https://datasets.imdbws.com"
GZ = gzip.compress(b"tconst\ttitleType\ntt1\ttvSeries\n" * 100)
HEADERS = {"etag": '"abc-2"'}


class Chunks(httpx.AsyncByteStream):
    """A response body that is really streamed (a plain `content=` body is already read)."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def respond(status: int, *chunks: bytes, headers: dict | None = None, length: bool = True):
    headers = dict(HEADERS if headers is None else headers)
    if length:
        headers["content-length"] = str(sum(len(c) for c in chunks))
    return httpx.Response(status, stream=Chunks(*chunks), headers=headers)


def make(handler, **kwargs) -> ImdbAdapter:
    return ImdbAdapter(transport=httpx.MockTransport(handler), **kwargs)


def serve(content=GZ, status=200, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return respond(status, content, headers=headers)

    return handler


async def test_head_returns_etag_and_size():
    adapter = make(serve())

    info = await adapter.head(ImdbFile.RATINGS)

    assert info.etag == '"abc-2"'
    assert info.size_bytes == len(GZ)


async def test_download_writes_the_exact_bytes_and_reports_headers(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return respond(200, GZ)

    adapter = make(handler)

    result = await adapter.download(ImdbFile.BASICS, tmp_path)

    assert str(requests[0].url) == f"{BASE}/title.basics.tsv.gz"
    assert result.path == tmp_path / "title.basics.tsv.gz"
    assert result.path.read_bytes() == GZ
    assert result.size_bytes == len(GZ)
    assert result.etag == '"abc-2"'


@pytest.mark.parametrize("base_url", ["http://datasets.imdbws.com", "https://example.com"])
def test_other_scheme_or_host_is_refused(base_url):
    with pytest.raises(UntrustedSourceError):
        ImdbAdapter(base_url=base_url)


async def test_redirect_to_another_host_is_refused_and_not_followed(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/x.gz"})

    adapter = make(handler)

    with pytest.raises(UntrustedSourceError):
        await adapter.download(ImdbFile.BASICS, tmp_path)

    assert seen == [f"{BASE}/title.basics.tsv.gz"]
    assert list(tmp_path.iterdir()) == []


async def test_file_over_cap_by_content_length_is_refused(tmp_path):
    adapter = make(serve(), max_file_bytes=len(GZ) - 1)

    with pytest.raises(DownloadTooLargeError):
        await adapter.download(ImdbFile.RATINGS, tmp_path)

    assert list(tmp_path.iterdir()) == []


async def test_file_over_cap_without_content_length_is_stopped_while_streaming(tmp_path):
    def handler(request):
        # no Content-Length, so the cap can only be enforced while streaming
        return respond(200, GZ[:50], GZ[50:], length=False)

    adapter = make(handler, max_file_bytes=len(GZ) - 1)

    with pytest.raises(DownloadTooLargeError):
        await adapter.download(ImdbFile.RATINGS, tmp_path)

    assert list(tmp_path.iterdir()) == []


async def test_corrupt_gzip_is_refused_and_deleted(tmp_path):
    adapter = make(serve(content=GZ[: len(GZ) // 2]))

    with pytest.raises(CorruptDownloadError):
        await adapter.download(ImdbFile.RATINGS, tmp_path)

    assert list(tmp_path.iterdir()) == []


async def test_not_gzip_at_all_is_refused(tmp_path):
    adapter = make(serve(content=b"<html>not a gzip file</html>"))

    with pytest.raises(CorruptDownloadError):
        await adapter.download(ImdbFile.RATINGS, tmp_path)


async def test_404_is_gone(tmp_path):
    adapter = make(serve(content=b"", status=404))

    with pytest.raises(GoneError):
        await adapter.download(ImdbFile.RATINGS, tmp_path)


async def test_500_is_transient(tmp_path):
    adapter = make(serve(content=b"", status=503))

    with pytest.raises(TransientError):
        await adapter.head(ImdbFile.RATINGS)


async def test_network_error_is_wrapped(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused")

    adapter = make(handler)

    with pytest.raises(NetworkError):
        await adapter.download(ImdbFile.RATINGS, tmp_path)


async def test_response_without_an_etag_is_an_error(tmp_path):
    adapter = make(serve(headers={"content-type": "binary/octet-stream"}))

    with pytest.raises(PermanentError):
        await adapter.head(ImdbFile.RATINGS)
    with pytest.raises(PermanentError):
        await adapter.download(ImdbFile.RATINGS, tmp_path)

    assert list(tmp_path.iterdir()) == []
