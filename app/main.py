from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Load .env once at import so OPENAI_API_KEY/OPENAI_MODEL are available
# wherever services need them (LLM client, etc.). override=False so a
# real shell env can still take precedence in production.
load_dotenv(override=False)

from app.api import aeo, fanout  # noqa: E402
from app.services.content_parser import ContentParseError, URLFetchError  # noqa: E402
from app.services.llm_client import LLMUnavailableError  # noqa: E402

app = FastAPI(title="AEGIS — AI Engineer Assignment")

app.include_router(aeo.router, prefix="/api/aeo", tags=["aeo"])
app.include_router(fanout.router, prefix="/api/fanout", tags=["fanout"])


@app.exception_handler(URLFetchError)
async def _url_fetch_error_handler(_: Request, exc: URLFetchError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "url_fetch_failed",
            "message": "Could not retrieve content from the provided URL.",
            "detail": exc.detail,
        },
    )


@app.exception_handler(ContentParseError)
async def _content_parse_error_handler(_: Request, exc: ContentParseError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "content_unparseable",
            "message": "The provided content could not be parsed.",
            "detail": exc.detail,
        },
    )


@app.exception_handler(LLMUnavailableError)
async def _llm_unavailable_handler(_: Request, exc: LLMUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "llm_unavailable",
            "message": "Fan-out generation failed. The LLM returned an invalid response after 3 retries.",
            "detail": exc.detail,
        },
    )


@app.get("/")
async def root():
    return {"message": "Welcome to the AEGIS AI Engineer Assignment API"}
