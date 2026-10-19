import pytest

from app.ingestion.parsers.common import clean_html_text, clean_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ed&rsquo;s a girl", "Ed\u2019s a girl"),
        ("&ldquo;quoted&rdquo;", "\u201cquoted\u201d"),
        ("Spike &amp; Jet", "Spike & Jet"),
        ("caf&eacute;", "caf\u00e9"),
        ("a &amp;lt; b", "a &lt; b"),  # decoded once only
        ("  padded  ", "padded"),
        ("&nbsp;", None),
        ("   ", None),
        ("", None),
        ("plain text", "plain text"),
        (None, None),
        (5, None),
        (["x"], None),
    ],
)
def test_clean_html_text(raw, expected):
    assert clean_html_text(raw) == expected


def test_clean_text_leaves_entities_alone():
    assert clean_text("a &amp; b") == "a &amp; b"
