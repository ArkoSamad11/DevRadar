from html.parser import HTMLParser
from typing import Annotated

import httpx
from pydantic import StringConstraints

from devradar.ingestion.models import Board, MalformedResponse, NotModified

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


async def get_board_response(
    client: httpx.AsyncClient, url: str, board: Board, params: dict | None = None
) -> httpx.Response:
    headers = {"If-None-Match": board.etag} if board.etag else {}
    response = await client.get(url, params=params, headers=headers)
    if response.status_code == 304:
        raise NotModified
    response.raise_for_status()
    return response


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
