"""Which SofaScore player ids Sports Data currently knows, without coupling the photo store to the sporting data.

The store only needs `player_id in known`. These adapters answer that through the same public read every client
uses, `/api/v1/players/{id}`, of whatever the API serves: the captured Generacion in control mode
(`generation.consultar`) or `queries.consultar` over the loaded data otherwise. They never open the sporting SQLite
and never match by name.
"""
from __future__ import annotations

from typing import Callable, Iterable, Protocol


class KnownPlayers(Protocol):
    def __contains__(self, player_id: object) -> bool: ...


class FixedPlayers:
    """A controlled set (tests, or a snapshot taken by the caller)."""

    def __init__(self, ids: Iterable[int]):
        self._ids = frozenset(ids)

    def __contains__(self, player_id: object) -> bool:
        return player_id in self._ids


class PlayersFromQuery:
    """Known if `/api/v1/players/{id}` answers 200 in one consistent read model. Pass `generation.consultar` of one
    captured generation so every lookup of an import sees the same data."""

    def __init__(self, consult: Callable[[str], tuple[int, object]]):
        self._consult = consult
        self._cache: dict[int, bool] = {}

    def __contains__(self, player_id: object) -> bool:
        if type(player_id) is not int or player_id <= 0:
            return False
        if player_id not in self._cache:
            status, _ = self._consult(f'/api/v1/players/{player_id}')
            self._cache[player_id] = status == 200
        return self._cache[player_id]
