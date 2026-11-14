from fastapi import FastAPI
from app.routers import github, health, me

app= FastAPI(title="RepoPrint Backend")
app.include_router(github.router)
app.include_router(health.router)
app.include_router(me.router)


@app.get("/health")
async def root():
    return {"status": "ok"}