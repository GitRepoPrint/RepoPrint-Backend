from fastapi import FastAPI
from app.routers import dfd, github

app= FastAPI(title="RepoPrint Backend")
app.include_router(github.router)
app.include_router(dfd.router)


@app.get("/health")
async def root():
    return {"status": "ok"}