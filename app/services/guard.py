"""Proteções do GET /dfd, que descarrega e analisa um repositório inteiro (até 100 MB) e é chamado diretamente do navegador.

- cache: o mesmo repositório não se analisa duas vezes em pouco tempo (poupa CPU e pedidos ao GitHub);
- limite por cliente: quantas análises novas cada cliente pode pedir por janela de tempo;
- vagas: quantas análises correm ao mesmo tempo; quem não consegue vaga a tempo recebe 503 em vez de ficar à espera.

Tudo em memória de um só processo: com vários processos cada um tem os seus limites.
"""
import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Hashable

from app.config import Settings


class TtlCache:
    """Guarda até `max_entries` valores durante `ttl` segundos; ao encher, sai o mais antigo."""

    def __init__(self, ttl: float, max_entries: int, clock: Callable[[], float]= time.monotonic):
        self.ttl= ttl
        self.max_entries= max_entries
        self._clock= clock
        self._items: OrderedDict[Hashable, tuple[float, object]]= OrderedDict()

    def get(self, key: Hashable):
        entry= self._items.get(key)
        if entry is None:
            return None
        stamp, value= entry
        if self._clock() - stamp >= self.ttl:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: Hashable, value: object) -> None:
        if self.max_entries <= 0 or self.ttl <= 0:
            return
        self._items[key]= (self._clock(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


class RateLimiter:
    """Janela deslizante: cada cliente pode fazer `limit` pedidos em cada `window` segundos. limit <= 0 desliga."""

    MAX_CLIENTS= 10_000  # para quem muda de endereço a cada pedido não encher a memória

    def __init__(self, limit: int, window: float, clock: Callable[[], float]= time.monotonic):
        self.limit= limit
        self.window= window
        self._clock= clock
        self._hits: dict[str, deque[float]]= {}

    def check(self, client: str) -> float:
        """0 se o pedido pode passar (e fica registado); senão, quantos segundos faltam para poder."""
        if self.limit <= 0:
            return 0.0
        now= self._clock()
        hits= self._hits.setdefault(client, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(hits[0] + self.window - now, 0.001)
        hits.append(now)
        if len(self._hits) > self.MAX_CLIENTS:
            self._forget_old(now)
        return 0.0

    def _forget_old(self, now: float) -> None:
        for client in [c for c, h in self._hits.items() if not h or now - h[-1] >= self.window]:
            del self._hits[client]
        while len(self._hits) > self.MAX_CLIENTS:  # todos recentes: sai o primeiro que entrou
            del self._hits[next(iter(self._hits))]


@dataclass
class DfdGuard:
    cache: TtlCache
    limiter: RateLimiter
    slots: asyncio.Semaphore
    queue_timeout: float
    trust_forwarded_for: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> "DfdGuard":
        return cls(
            cache=TtlCache(settings.dfd_cache_ttl_seconds, settings.dfd_cache_max_entries),
            limiter=RateLimiter(settings.dfd_rate_limit, settings.dfd_rate_window_seconds),
            slots=asyncio.Semaphore(max(settings.dfd_max_concurrent, 1)),
            queue_timeout=settings.dfd_queue_timeout_seconds,
            trust_forwarded_for=settings.trust_forwarded_for,
        )

    def client_id(self, host: str | None, forwarded_for: str | None) -> str:
        """Quem pede. Atrás de um proxy todos chegam com o endereço dele: só se confia no X-Forwarded-For se for dito."""
        if self.trust_forwarded_for and forwarded_for:
            first= forwarded_for.split(",")[0].strip()
            if first:
                return first
        return host or "unknown"

    async def acquire_slot(self) -> bool:
        """True se conseguiu uma vaga a tempo."""
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=self.queue_timeout)
            return True
        except asyncio.TimeoutError:
            return False
