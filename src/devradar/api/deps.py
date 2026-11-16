from collections.abc import AsyncIterator
from typing import Annotated

import psycopg
from fastapi import Depends, Request


async def get_connection(request: Request) -> AsyncIterator[psycopg.AsyncConnection]:
    async with request.app.state.pool.connection() as conn:
        yield conn


Connection = Annotated[psycopg.AsyncConnection, Depends(get_connection)]
