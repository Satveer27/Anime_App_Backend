from enum import StrEnum


class ImdbFile(StrEnum):
    """The IMDb dataset files the pipeline downloads (ADR 0018). The value is the file's base name."""

    BASICS = "title.basics"
    EPISODE = "title.episode"
    RATINGS = "title.ratings"

    @property
    def filename(self) -> str:
        return f"{self.value}.tsv.gz"
