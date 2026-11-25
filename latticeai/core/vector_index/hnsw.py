from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HNSW_BACKEND = "hnsw"
HNSWLIB_MODULE = "hnswlib"
HNSW_SUFFIX = ".hnsw"
HNSW_META_SUFFIX = ".hnsw.meta.json"
DELETE_REBUILD_RATIO = 0.10

IndexItem = Tuple[str, Sequence[float], Mapping[str, Any]]
ScoredId = Tuple[str, float]


def load_hnswlib() -> Tuple[Optional[Any], Optional[str]]:
    try:
        import hnswlib
    except (ImportError, OSError, RuntimeError) as exc:
        return None, (
            f"{HNSWLIB_MODULE} is not available ({exc}); install it with "
            "`pip install hnswlib` to use the approximate index"
        )
    return hnswlib, None


def hnswlib_available() -> bool:
    module, _ = load_hnswlib()
    return module is not None


def sidecar_paths(db_path: Any) -> Tuple[Path, Path]:
    base = Path(db_path)
    return (
        base.with_suffix(HNSW_SUFFIX),
        base.with_suffix(HNSW_META_SUFFIX),
    )


class HnswIndex:
    def __init__(
        self,
        *,
        dim: int,
        space: str = "cosine",
        ef_construction: int = 400,
        m: int = 32,
        ef_search: int = 400,
        model_id: str = "",
    ) -> None:
        self._dim = int(dim)
        self._space = str(space)
        self._ef_construction = int(ef_construction)
        self._m = int(m)
        self._ef_search = int(ef_search)
        self._model_id = str(model_id)
        self._module, self._detail = load_hnswlib()
        self._vectors: Dict[str, List[float]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._labels: List[str] = []
        self._ann: Any = None
        self._dirty = True
        self._loaded = False
        self._stamp: Optional[str] = None
        self._indexed_at: Dict[str, str] = {}

    @property
    def backend(self) -> str:
        return HNSW_BACKEND

    @property
    def approx(self) -> bool:
        return True

    @property
    def exhaustive(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self._module is not None

    @property
    def unavailable_detail(self) -> Optional[str]:
        return self._detail

    @property
    def loaded_from_sidecar(self) -> bool:
        return self._loaded

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def labels(self) -> List[str]:
        return list(self._labels)

    @property
    def stamp(self) -> Optional[str]:
        return self._stamp

    @property
    def indexed_at(self) -> Dict[str, str]:
        return dict(self._indexed_at)

    def _identity_matches(self, dim: int, space: str, model_id: str) -> bool:
        return (
            int(dim) == self._dim
            and str(space) == self._space
            and str(model_id) == self._model_id
        )

    def add(
        self,
        id: str,
        vector: Sequence[float],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if self._loaded:
            raise RuntimeError(
                "this HnswIndex was loaded from a sidecar and holds no source "
                "vectors; rebuild it from vector_embeddings before mutating"
            )
        key = str(id)
        self._vectors[key] = [float(value) for value in vector]
        self._metadata[key] = dict(metadata or {})
        self._dirty = True

    def remove(self, id: str) -> None:
        if self._loaded:
            raise RuntimeError(
                "this HnswIndex was loaded from a sidecar and holds no source "
                "vectors; rebuild it from vector_embeddings before mutating"
            )
        key = str(id)
        self._vectors.pop(key, None)
        self._metadata.pop(key, None)
        self._dirty = True

    def rebuild(self, items: Iterable[IndexItem]) -> None:
        self._vectors.clear()
        self._metadata.clear()
        self._labels.clear()
        self._ann = None
        self._loaded = False
        for item_id, vector, metadata in items:
            self.add(item_id, vector, metadata)
        self._dirty = True

    def add_items(
        self,
        items: Iterable[IndexItem],
        *,
        dim: Optional[int] = None,
        space: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> str:
        incoming: Dict[str, IndexItem] = {}
        for item_id, vector, metadata in items:
            incoming[str(item_id)] = (str(item_id), vector, metadata)

        want_dim = self._dim if dim is None else int(dim)
        want_space = self._space if space is None else str(space)
        want_model = self._model_id if model_id is None else str(model_id)
        if not self._identity_matches(want_dim, want_space, want_model):
            self._dim = want_dim
            self._space = want_space
            self._model_id = want_model
            self.rebuild(incoming.values())
            return "rebuild"

        if self._loaded:
            current = set(self._labels)
            incoming_ids = set(incoming)
            overlap = current & incoming_ids
            deleted = current - incoming_ids
            delta_only = bool(incoming_ids) and not overlap
            if deleted and not delta_only:
                self.rebuild(incoming.values())
                return "rebuild"
            new_ids = [item_id for item_id in incoming if item_id not in current]
            if not new_ids:
                return "append"
            for item_id in new_ids:
                _, vector, metadata = incoming[item_id]
                self._vectors[item_id] = [float(value) for value in vector]
                self._metadata[item_id] = dict(metadata or {})
            if self._ann is not None and self._module is not None:
                self._append_live(new_ids)
                return "append"
            self.rebuild(incoming.values())
            return "rebuild"

        current = set(self._vectors)
        incoming_ids = set(incoming)
        deleted = current - incoming_ids
        overlap = current & incoming_ids
        if deleted and not (bool(incoming_ids) and not overlap):
            ratio = len(deleted) / max(1, len(current))
            if ratio >= DELETE_REBUILD_RATIO or not current:
                self.rebuild(incoming.values())
                return "rebuild"
            self.rebuild(incoming.values())
            return "rebuild"

        new_ids = [item_id for item_id in incoming if item_id not in current]
        if not new_ids and not self._dirty and self._ann is not None:
            return "append"
        for item_id in new_ids:
            _, vector, metadata = incoming[item_id]
            self.add(item_id, vector, metadata)
        if self._ann is not None and new_ids and self._module is not None:
            self._append_live(new_ids)
            return "append"
        self._dirty = True
        return "append" if new_ids or self._ann is not None else "rebuild"

    def _new_graph(self, module: Any, capacity: int) -> Any:
        graph = module.Index(space=self._space, dim=self._dim)
        graph.init_index(
            max_elements=max(1, capacity),
            ef_construction=self._ef_construction,
            M=self._m,
        )
        return graph

    def _append_live(self, new_ids: Sequence[str]) -> None:
        module = self._module
        graph = self._ann
        if module is None or graph is None:
            self._dirty = True
            return
        start = len(self._labels)
        needed = start + len(new_ids)
        try:
            current_max = int(graph.get_max_elements())
        except (RuntimeError, ValueError, TypeError, AttributeError):
            current_max = start
        if needed > current_max:
            graph.resize_index(max(needed, current_max * 2 if current_max else needed))
        graph.add_items(
            [self._vectors[key] for key in new_ids],
            list(range(start, start + len(new_ids))),
        )
        self._labels.extend(new_ids)
        self._dirty = False

    def _ensure_graph(self) -> Any:
        module = self._module
        if module is None:
            return None
        if self._ann is not None and not self._dirty:
            return self._ann
        labels = list(self._vectors)
        graph = self._new_graph(module, max(len(labels), 1))
        if labels:
            graph.add_items(
                [self._vectors[key] for key in labels],
                list(range(len(labels))),
            )
        self._ann = graph
        self._labels = labels
        self._dirty = False
        return graph

    def search(
        self,
        query: Sequence[float],
        top_k: int,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[ScoredId]:
        graph = self._ensure_graph()
        if graph is None or not self._labels:
            return []
        floor = float("-inf")
        if filter and filter.get("min_score") is not None:
            try:
                floor = float(filter["min_score"])
            except (TypeError, ValueError):
                floor = float("-inf")
        wanted = max(1, min(int(top_k), len(self._labels)))
        graph.set_ef(max(self._ef_search, wanted))
        labels, distances = graph.knn_query([list(query)], k=wanted)
        scored: List[ScoredId] = []
        for label, distance in zip(labels[0], distances[0], strict=True):
            score = 1.0 - float(distance)
            if score < floor:
                continue
            scored.append((self._labels[int(label)], score))
        return scored

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "size": len(self._labels) if self._loaded else len(self._vectors),
            "dim": self._dim,
            "approx": True,
            "exhaustive": False,
            "available": self.available,
            "detail": self._detail,
            "model_id": self._model_id,
        }

    def save(
        self,
        db_path: Any,
        *,
        fingerprint: str,
        stamp: Optional[str] = None,
        indexed_at: Optional[Mapping[str, str]] = None,
    ) -> bool:
        graph = self._ensure_graph()
        if graph is None or not self._labels:
            return False
        if stamp is not None:
            self._stamp = str(stamp)
        if indexed_at is not None:
            self._indexed_at = {str(k): str(v) for k, v in indexed_at.items()}
        index_path, meta_path = sidecar_paths(db_path)
        payload: Dict[str, Any] = {
            "fingerprint": str(fingerprint),
            "dim": self._dim,
            "space": self._space,
            "model_id": self._model_id,
            "labels": self._labels,
        }
        if self._stamp is not None:
            payload["stamp"] = self._stamp
        if self._indexed_at:
            payload["indexed_at"] = self._indexed_at
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            graph.save_index(str(index_path))
            meta_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            return False
        return True

    def load(
        self,
        db_path: Any,
        *,
        fingerprint: str,
        strict: bool = True,
        stamp: Optional[str] = None,
    ) -> bool:
        module = self._module
        if module is None:
            return False
        index_path, meta_path = sidecar_paths(db_path)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            matches = int(meta["dim"]) == self._dim
            if meta.get("model_id") not in (None, "", self._model_id):
                matches = False
            if strict:
                matches = matches and str(meta["fingerprint"]) == str(fingerprint)
                if stamp is not None and meta.get("stamp") is not None:
                    matches = matches and str(meta["stamp"]) == str(stamp)
            labels = [str(label) for label in meta["labels"]]
            loaded_stamp = str(meta["stamp"]) if meta.get("stamp") is not None else None
            raw_ts = meta.get("indexed_at")
            loaded_ts = (
                {str(k): str(v) for k, v in raw_ts.items()}
                if isinstance(raw_ts, dict)
                else {}
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            return False
        if not (matches and labels):
            return False
        try:
            graph = module.Index(space=self._space, dim=self._dim)
            graph.load_index(str(index_path), max_elements=max(len(labels) + 64, 1))
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            return False
        self._ann = graph
        self._labels = labels
        self._vectors.clear()
        self._metadata.clear()
        self._dirty = False
        self._loaded = True
        self._stamp = loaded_stamp
        self._indexed_at = loaded_ts
        return True


__all__ = [
    "DELETE_REBUILD_RATIO",
    "HNSW_BACKEND",
    "HNSW_META_SUFFIX",
    "HNSW_SUFFIX",
    "HNSWLIB_MODULE",
    "HnswIndex",
    "hnswlib_available",
    "load_hnswlib",
    "sidecar_paths",
]
