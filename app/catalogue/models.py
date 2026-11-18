from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)

from app.core.database.database import Base

# MAL's own status texts, as stored on `anime.status`.
STATUS_AIRING = "Currently Airing"
STATUS_NOT_YET_AIRED = "Not yet aired"

# The text a title search runs on: the three titles joined, each behind a space, with an
# unprintable separator (chr(31)) between them. The trigram index on `anime` is built on exactly
# this expression, and so must the search query, or the index silently stops being used. The
# migration that creates the index keeps its own copy (a test checks the two are identical).
SEARCH_TEXT_SQL = (
    "(' ' || coalesce(title, '') || ' ' || chr(31) || ' ' || coalesce(title_english, '')"
    " || ' ' || chr(31) || ' ' || coalesce(title_japanese, ''))"
)


class Anime(Base):
    """One MAL entry. Single-valued fields and name lists; related rows live in child tables."""

    __tablename__ = "anime"
    # The cohort percentile query filters on (type, year). Measured in METRICS.md section 7
    # (ADR 0015): 11.5 ms down to 2-4 ms.
    __table_args__ = (
        Index("ix_anime_type_year", "type", "year"),
        # Title search by the start of any word (needs the pg_trgm extension, created in the
        # migration). Measured in METRICS.md section 10 (ADR 0015): 105 ms down to 0.3 ms.
        Index(
            "ix_anime_search_trgm", text(f"{SEARCH_TEXT_SQL} gin_trgm_ops"), postgresql_using="gin"
        ),
    )

    mal_id = Column(Integer, primary_key=True, autoincrement=False)  # mal_id
    title = Column(Text, nullable=False)
    title_english = Column(Text, nullable=True)
    title_japanese = Column(Text, nullable=True)
    type = Column(Text, nullable=True)  # Such as aniem type movie, tv etc
    status = Column(Text, nullable=True)  # Airing, finished airing etc
    rating = Column(Text, nullable=True)  # rating such as 18+ etc
    source_material = Column(Text, nullable=True)  # Source material such as manga etc
    season = Column(Text, nullable=True)  # Spring, summer etc
    year = Column(Integer, nullable=True)
    episodes = Column(Integer, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    aired_from = Column(DateTime(timezone=True), nullable=True)
    aired_to = Column(DateTime(timezone=True), nullable=True)

    # All scores are normalised to 0-10 by the pipeline. Each source's pipeline writes only
    # its own column (IMDb and AniList arrive later, after a Match, ADR 0002).
    mal_score = Column(Numeric(4, 2), nullable=True)
    anilist_score = Column(Numeric(4, 2), nullable=True)
    imdb_score = Column(Numeric(4, 2), nullable=True)

    scored_by_mal = Column(Integer, nullable=True)
    members_mal = Column(Integer, nullable=True)
    favorites_mal = Column(Integer, nullable=True)
    rank_mal = Column(Integer, nullable=True)
    popularity_mal = Column(Integer, nullable=True)

    synopsis = Column(Text, nullable=True)
    synopsis_hash = Column(Text, nullable=True)
    # MAL trivia and production history; useful LLM context for why a show is notable.
    background = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# The default anime lists sort by members or score, highest first, with ties by id. Without these
# the page query read and sorted every row (about 20 ms); with them it reads 20 rows (0.07 ms).
# Measured in METRICS.md section 10 (ADR 0015).
Index("ix_anime_members_mal", Anime.members_mal.desc().nulls_last(), Anime.mal_id)
Index("ix_anime_mal_score", Anime.mal_score.desc().nulls_last(), Anime.mal_id)


class Tag(Base):
    """A MAL genre, theme or demographic (one shared id space), created as anime arrive."""

    __tablename__ = "tag"

    mal_id = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(Text, nullable=False)


class AnimeTag(Base):
    __tablename__ = "anime_tag"

    anime_id = Column(Integer, ForeignKey("anime.mal_id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tag.mal_id", ondelete="CASCADE"), primary_key=True)


class Studio(Base):
    __tablename__ = "studio"

    mal_id = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(Text, nullable=False)


class AnimeStudio(Base):
    __tablename__ = "anime_studio"

    anime_id = Column(Integer, ForeignKey("anime.mal_id", ondelete="CASCADE"), primary_key=True)
    studio_id = Column(Integer, ForeignKey("studio.mal_id", ondelete="CASCADE"), primary_key=True)


class AnimeStatisticsMal(Base):
    """Current list statistics, one row per anime, overwritten on refresh (ADR 0004)."""

    __tablename__ = "anime_statistics_mal"

    anime_id = Column(Integer, ForeignKey("anime.mal_id", ondelete="CASCADE"), primary_key=True)
    watching = Column(Integer, nullable=False)
    completed = Column(Integer, nullable=False)
    on_hold = Column(Integer, nullable=False)
    dropped = Column(Integer, nullable=False)
    plan_to_watch = Column(Integer, nullable=False)
    total = Column(Integer, nullable=False)
    # Score-vote distribution; the source's percentage is derivable so it is not stored.
    votes_1 = Column(Integer, nullable=True)
    votes_2 = Column(Integer, nullable=True)
    votes_3 = Column(Integer, nullable=True)
    votes_4 = Column(Integer, nullable=True)
    votes_5 = Column(Integer, nullable=True)
    votes_6 = Column(Integer, nullable=True)
    votes_7 = Column(Integer, nullable=True)
    votes_8 = Column(Integer, nullable=True)
    votes_9 = Column(Integer, nullable=True)
    votes_10 = Column(Integer, nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AnimeRelation(Base):
    """A directed anime-to-anime edge. No FK on the target: it may not be ingested.

    Manga relations are not stored (they stay in raw_tenrai), so the id needs no type.
    """

    __tablename__ = "anime_relation"

    anime_id = Column(Integer, ForeignKey("anime.mal_id", ondelete="CASCADE"), primary_key=True)
    # MAL's relation name (Sequel, Prequel, Side Story, Adaptation, ...); source-controlled, no CHECK.
    relation = Column(Text, primary_key=True)
    related_mal_id = Column(Integer, primary_key=True, autoincrement=False)


class Episode(Base):
    __tablename__ = "episode"
    anime_id = Column(Integer, ForeignKey("anime.mal_id", ondelete="CASCADE"), primary_key=True)
    episode_number = Column(Integer, primary_key=True, autoincrement=False)
    title = Column(Text, nullable=True)
    title_japanese = Column(Text, nullable=True)
    title_romanji = Column(Text, nullable=True)
    aired = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    synopsis = Column(Text, nullable=True)
    synopsis_hash = Column(Text, nullable=True)
    filler = Column(Boolean, nullable=False, server_default=text("false"))
    recap = Column(Boolean, nullable=False, server_default=text("false"))
    # Normalised to 0-10 by the pipeline (MAL's 0-5 is doubled, and its 0 means no score so
    # it is stored as NULL). Written by different pipelines, so each upsert must touch only
    # its own column.
    mal_score = Column(Numeric(4, 2), nullable=True)
    anilist_score = Column(Numeric(4, 2), nullable=True)
    imdb_score = Column(Numeric(4, 2), nullable=True)


class Review(Base):
    """A MAL review as returned on page 1 (most helpful first, spoiler-free by default)."""

    __tablename__ = "review"
    # "Reviews of this anime" scanned all 87,493 rows. Measured in METRICS.md section 7
    # (ADR 0015): 23.1 ms down to 0.06 ms.
    __table_args__ = (Index("ix_review_anime_id", "anime_id"),)

    mal_id = Column(Integer, primary_key=True, autoincrement=False)
    anime_id = Column(Integer, ForeignKey("anime.mal_id", ondelete="CASCADE"), nullable=False)
    score = Column(Integer, nullable=True)
    review = Column(Text, nullable=False)
    review_url = Column(Text, nullable=True)
    username = Column(Text, nullable=True)
    username_url = Column(Text, nullable=True)
    date = Column(DateTime(timezone=True), nullable=True)
    is_spoiler = Column(Boolean, nullable=False, server_default=text("false"))
    is_preliminary = Column(Boolean, nullable=False, server_default=text("false"))
    episodes_watched = Column(Integer, nullable=True)
    reaction_overall = Column(Integer, nullable=True)
    reaction_nice = Column(Integer, nullable=True)
    reaction_love_it = Column(Integer, nullable=True)
    reaction_funny = Column(Integer, nullable=True)
    reaction_confusing = Column(Integer, nullable=True)
    reaction_informative = Column(Integer, nullable=True)
    reaction_well_written = Column(Integer, nullable=True)
    reaction_creative = Column(Integer, nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
