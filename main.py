from fastapi import FastAPI
from app.routers import github

app= FastAPI(title="RepoPrint Backend")
app.include_router(github.router)


@app.get("/health")
async def root():
    return {"status": "ok"}