"""Extra unit tests: readiness probe branches (section 4.3/4.4) and the Chroma retriever.

No network: chromadb and redis clients are replaced with small fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import chromadb
import redis
from fastapi import Response

from app.config import Settings
from app.retrieval import chroma as chroma_mod
from app.retrieval.chroma import ChromaRetriever, _deterministic_vector
from app.routes.health import healthz, readyz


class FakeCollection:
    def __init__(self, count: int = 5, metadata: dict | None = None) -> None:
        self._count = count
        self.metadata = metadata or {}

    def count(self) -> int:
        return self._count

    def query(self, **kwargs):  # noqa: ANN003
        self.last_where = kwargs.get("where")
        return {
            "ids": [["c1", "c2"]],
            "documents": [["text one", "text two"]],
            "metadatas": [[{"source": "rules.pdf", "section": "4.2", "page": 7}, {}]],
            "distances": [[0.1, 0.4]],
        }


def _fake_chroma(collection: FakeCollection | None, reachable: bool = True):
    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def heartbeat(self) -> int:
            if not reachable:
                raise ConnectionError("chroma down")
            return 1

        def get_collection(self, name: str) -> FakeCollection:
            assert collection is not None
            return collection

    return FakeClient


def _request(embed_model: str = "nomic-embed-text"):
    settings = Settings(embed_model=embed_model)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


class _OkRedis:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    def ping(self) -> bool:
        return True


class _DownRedis(_OkRedis):
    def ping(self) -> bool:
        raise ConnectionError("redis down")


def test_healthz_never_touches_dependencies() -> None:
    assert healthz() == {"status": "alive"}


def test_readyz_ready(monkeypatch) -> None:
    monkeypatch.setattr(chromadb, "HttpClient", _fake_chroma(FakeCollection(5, {"embed_model": "nomic-embed-text"})))
    monkeypatch.setattr(redis, "Redis", _OkRedis)
    resp = Response()
    body = readyz(_request(), resp)
    assert body["status"] == "ready"
    assert resp.status_code in (None, 200)


def test_readyz_empty_collection_is_503(monkeypatch) -> None:
    monkeypatch.setattr(chromadb, "HttpClient", _fake_chroma(FakeCollection(0)))
    monkeypatch.setattr(redis, "Redis", _OkRedis)
    resp = Response()
    body = readyz(_request(), resp)
    assert resp.status_code == 503
    assert body["dependency"] == "chroma"


def test_readyz_embedding_pin_mismatch_is_503(monkeypatch) -> None:
    monkeypatch.setattr(chromadb, "HttpClient", _fake_chroma(FakeCollection(5, {"embed_model": "other-model"})))
    monkeypatch.setattr(redis, "Redis", _OkRedis)
    resp = Response()
    body = readyz(_request("nomic-embed-text"), resp)
    assert resp.status_code == 503
    assert body["dependency"] == "chroma_model_pin"
    assert "other-model" in body["error"]


def test_readyz_chroma_unreachable_is_503(monkeypatch) -> None:
    monkeypatch.setattr(chromadb, "HttpClient", _fake_chroma(None, reachable=False))
    resp = Response()
    body = readyz(_request(), resp)
    assert resp.status_code == 503
    assert body["dependency"] == "chroma"


def test_readyz_redis_down_is_503(monkeypatch) -> None:
    monkeypatch.setattr(chromadb, "HttpClient", _fake_chroma(FakeCollection(5, {"embed_model": "nomic-embed-text"})))
    monkeypatch.setattr(redis, "Redis", _DownRedis)
    resp = Response()
    body = readyz(_request(), resp)
    assert resp.status_code == 503
    assert body["dependency"] == "redis"


def test_deterministic_vector_is_unit_length_and_stable() -> None:
    a = _deterministic_vector("attendance rule", 64)
    b = _deterministic_vector("attendance rule", 64)
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6


def _retriever(monkeypatch, collection: FakeCollection) -> ChromaRetriever:
    monkeypatch.setattr(chromadb, "HttpClient", _fake_chroma(collection))
    monkeypatch.setattr(chroma_mod, "_embed_query", lambda text, settings: [0.0] * 8)
    return ChromaRetriever(Settings())


def test_retriever_filters_superseded_by_default(monkeypatch) -> None:
    col = FakeCollection()
    passages = _retriever(monkeypatch, col).retrieve("What is the attendance rule?", 2)
    assert col.last_where == {"superseded": False}
    assert [p.chunk_id for p in passages] == ["c1", "c2"]
    assert passages[0].score == 0.9
    assert passages[0].source == "rules.pdf"
    assert passages[1].source == "unknown"


def test_retriever_allows_older_version_when_asked(monkeypatch) -> None:
    col = FakeCollection()
    _retriever(monkeypatch, col).retrieve("What did the 2023 rules say about attendance?", 2)
    assert col.last_where is None
