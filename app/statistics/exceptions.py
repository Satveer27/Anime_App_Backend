from app.exceptions import ResourceDoesNotExistError


class AnimeNotFoundError(ResourceDoesNotExistError):
    default_message = "Anime not found."
