from typing import Protocol

from app.schemas import ImagePost, IngestPayload


class LLMError(Exception):
    pass


class ImagePostGenerator(Protocol):
    """Common interface all LLM providers (and the stub) implement.

    Keeping this a `Protocol` (structural typing) instead of an ABC means
    providers don't need to inherit from a shared base class -- any object
    with a matching `generate_image_post` method works, which keeps provider
    modules independent and easy to test in isolation.
    """

    def generate_image_post(self, payload: IngestPayload) -> ImagePost: ...
