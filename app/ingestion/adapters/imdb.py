import asyncio
import gzip
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

from app.ingestion.enums.imdb_file_enum import ImdbFile
from app.ingestion.exceptions import (
    AccessDeniedError,
    CorruptDownloadError,
    DownloadTooLargeError,
    GoneError,
    NetworkError,
    PermanentError,
    TransientError,
    UntrustedSourceError,
)

log = structlog.get_logger(__name__)

IMDB_HOST = "datasets.imdbws.com"
DEFAULT_BASE_URL = f"https://{IMDB_HOST}"
# Per file, compressed. title.basics is the biggest at a few hundred MB.
DEFAULT_MAX_FILE_BYTES = 1024**3
CHUNK_SIZE = 1024 * 1024
USER_AGENT = "AnimePlanet https://github.com/Satveer27/Anime_App_Backend"


@dataclass(frozen=True)
class FileInfo:
    """What the server says about a file, without downloading it."""

    etag: str
    size_bytes: int | None


@dataclass(frozen=True)
class DownloadedFile:
    file: ImdbFile
    path: Path
    etag: str
    size_bytes: int
    download_seconds: float


def _verify_gzip(path: Path) -> None:
    """Read the whole file through gzip: this checks the CRC and the stored length, so a
    truncated or corrupted download fails here and not later in Spark."""
    try:
        with gzip.open(path, "rb") as f:
            while f.read(CHUNK_SIZE):
                pass
    except (OSError, EOFError, zlib.error) as e:
        raise CorruptDownloadError(f"{path.name} failed the gzip check: {type(e).__name__}") from e


def _content_length(response: httpx.Response) -> int | None:
    try:
        return int(response.headers["content-length"])
    except (KeyError, ValueError):
        return None


class ImdbAdapter:
    """The only code that talks to IMDb's dataset host.

    HTTPS and `datasets.imdbws.com` only. Redirects are refused, so a response can never move
    the download to another host. There are no retries here: the Airflow task retries the run.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = httpx.URL(base_url)
        if url.scheme != "https" or url.host != IMDB_HOST:
            raise UntrustedSourceError(f"only https://{IMDB_HOST} is allowed, got {base_url}")
        self._max_file_bytes = max_file_bytes
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(60, connect=5),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _check_status(response: httpx.Response, file: ImdbFile) -> None:
        code = response.status_code
        if code == 200:
            return
        if 300 <= code < 400:
            log.error("imdb_redirect_refused", file=file.value, status=code)
            raise UntrustedSourceError(f"redirect refused for {file.filename}", status_code=code)
        if code in (401, 403):
            log.error("imdb_access_denied", file=file.value, status=code)
            raise AccessDeniedError(f"{code} {file.filename}", status_code=code)
        if code == 404:
            raise GoneError(f"404 {file.filename}", status_code=404)
        if code >= 500 or code == 429:
            raise TransientError(f"{code} {file.filename}", status_code=code)
        raise PermanentError(f"{code} {file.filename}", status_code=code)

    @staticmethod
    def _etag(response: httpx.Response, file: ImdbFile) -> str:
        """The ETag identifies the version of a file and the skip rule depends on it, so a
        response without one is an error and not a download we cannot compare."""
        etag = response.headers.get("etag")
        if not etag:
            log.error("imdb_etag_missing", file=file.value)
            raise PermanentError(f"{file.filename} came without an ETag")
        return etag

    async def head(self, file: ImdbFile) -> FileInfo:
        """ETag and size of a file, for the skip check."""
        try:
            response = await self._client.head(f"/{file.filename}")
        except httpx.TransportError as e:
            log.warning("imdb_network_error", file=file.value, error=type(e).__name__)
            raise NetworkError(f"{file.filename}: {type(e).__name__}") from e
        self._check_status(response, file)
        return FileInfo(etag=self._etag(response, file), size_bytes=_content_length(response))

    async def download(self, file: ImdbFile, dest_dir: Path) -> DownloadedFile:
        """Stream a file to `dest_dir`, byte for byte as sent, and check it is intact gzip.

        A file that fails any check is deleted, so a returned file is always safe to upload.
        """
        path = dest_dir / file.filename
        started = time.monotonic()
        try:
            etag, written = await self._stream_to_disk(file, path)
            await asyncio.to_thread(_verify_gzip, path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return DownloadedFile(
            file=file,
            path=path,
            etag=etag,
            size_bytes=written,
            download_seconds=time.monotonic() - started,
        )

    async def _stream_to_disk(self, file: ImdbFile, path: Path) -> tuple[str, int]:
        try:
            async with self._client.stream("GET", f"/{file.filename}") as response:
                self._check_status(response, file)
                etag = self._etag(response, file)
                expected = _content_length(response)
                if expected is not None and expected > self._max_file_bytes:
                    raise DownloadTooLargeError(
                        f"{file.filename} is {expected} bytes, the cap is {self._max_file_bytes}"
                    )
                written = 0
                with open(path, "wb") as f:
                    # iter_raw: the bytes exactly as sent, never decoded by httpx
                    async for chunk in response.aiter_raw(CHUNK_SIZE):
                        written += len(chunk)
                        if written > self._max_file_bytes:
                            raise DownloadTooLargeError(
                                f"{file.filename} passed the cap of {self._max_file_bytes} bytes"
                            )
                        f.write(chunk)
        except httpx.TransportError as e:
            log.warning("imdb_network_error", file=file.value, error=type(e).__name__)
            raise NetworkError(f"{file.filename}: {type(e).__name__}") from e
        if expected is not None and written != expected:
            raise CorruptDownloadError(
                f"{file.filename} is truncated: got {written} of {expected} bytes"
            )
        return etag, written
