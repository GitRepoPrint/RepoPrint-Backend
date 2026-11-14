from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import dfd, github


def create_app() -> FastAPI:
    app= FastAPI(title="RepoPrint Backend")
    # O frontend (outra origem) chama esta API diretamente do navegador; sem isto o navegador bloqueia as respostas.
    # Só leitura (GET), sem cookies nem credenciais.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["Accept"],
        max_age=600,
    )
    app.include_router(github.router)
    app.include_router(dfd.router)

    @app.get("/health")
    async def root():
        return {"status": "ok"}

    return app


app= create_app()
