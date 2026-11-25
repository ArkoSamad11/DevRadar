import asyncio
import io
import resource
import sys

MAX_PAGES = 5
MAX_CHARACTERS = 40_000
CPU_SECONDS = 5
WALL_SECONDS = 8
MEMORY_BYTES = 400_000_000


class UnreadablePdf(Exception):
    pass


async def extract_text(pdf: bytes) -> str:
    """Read a PDF in a separate process, so a hostile file can only harm that process."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "devradar.matching.pdf",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={},
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(pdf), WALL_SECONDS)
    except TimeoutError as error:
        raise UnreadablePdf("the file took too long to read") from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        raise UnreadablePdf("the file could not be read as a PDF")
    return output.decode("utf-8", errors="replace")[:MAX_CHARACTERS]


def _limit_resources() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))


def main() -> None:
    from pypdf import PdfReader

    _limit_resources()
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read()))
    if reader.is_encrypted and not reader.decrypt(""):
        sys.exit(1)
    text = ""
    for page in reader.pages[:MAX_PAGES]:
        text += (page.extract_text() or "") + "\n"
        if len(text) > MAX_CHARACTERS:
            break
    sys.stdout.buffer.write(text[:MAX_CHARACTERS].encode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
