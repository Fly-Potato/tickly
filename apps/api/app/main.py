from fastapi import FastAPI


app = FastAPI(title="Tickly API")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
