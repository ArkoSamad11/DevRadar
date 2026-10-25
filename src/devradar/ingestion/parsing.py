from html.parser import HTMLParser

import httpx

from devradar.ingestion.models import MalformedResponse


def read_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError as error:
        raise MalformedResponse(f"{response.url} returned invalid JSON") from error


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(content: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(content)
    return " ".join(" ".join(extractor.parts).split())


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())
