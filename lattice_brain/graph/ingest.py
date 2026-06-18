from __future__ import annotations

# ruff: noqa: F403,F405

from ._kg_common import *  # noqa: F403,F401


class KnowledgeGraphIngestMixin:
    def ingest_message(
        self,
        role: str,
        content: str,
        *,
        user_email: Optional[str] = None,
        user_nickname: Optional[str] = None,
        source: Optional[str] = None,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        content = str(content or "")
        digest = _sha256_text(
            "|".join([role or "", content, conversation_id or "", user_email or ""])
        )[:24]
        node_type = "AIResponse" if role == "assistant" else "Message"
        node_id = f"{node_type.lower()}:{digest}"
        conv_id = f"conversation:{_slug(conversation_id or 'default')}"
        metadata = {
            "role": role,
            "source": source,
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "user_email": user_email,
            "user_nickname": user_nickname,
            "chars": len(content),
        }
        concepts = _extract_concepts(content)
        triples = _extract_triples(content, concepts)
        semantic = _semantic_items(content)

        with self._connect() as conn:
            # ── 1. Chat node  (점: 명사 — 대화 세션 단위) ─────────────────────
            #    One Chat node per conversation_id; title = first 80 chars of
            #    the first user message in this session (updated on each call).
            chat_title = _clean_text(content)[:80] or (conversation_id or "대화")
            self._upsert_node(
                conn,
                conv_id,
                "Chat",
                chat_title,
                summary=_clean_text(content)[:400],
                metadata={"source": source, "conversation_id": conversation_id, "workspace_id": workspace_id},
                owner=user_email,
                workspace_id=workspace_id,
            )

            # ── 2. Person node  (점: 명사 — 사람) ─────────────────────────────
            person_id = None
            if user_email or user_nickname:
                person_key = user_email or user_nickname or "unknown"
                person_id = f"person:{_slug(person_key)}"
                self._upsert_node(
                    conn,
                    person_id,
                    "Person",
                    user_nickname or user_email or "Unknown",
                    metadata={"email": user_email, "nickname": user_nickname},
                    owner=user_email,
                    workspace_id=workspace_id,
                )
                # 선: 동사 — Person이 Chat을 "작성함"
                self._upsert_edge(
                    conn,
                    person_id,
                    conv_id,
                    "작성함",
                    weight=1.0,
                    metadata={"role": role},
                )

            # ── 3. Raw message node  (RAG 검색용, 그래프에서 숨김) ─────────────
            self._upsert_node(
                conn,
                node_id,
                node_type,
                _clean_text(content)[:80] or role,
                summary=_clean_text(content)[:500],
                metadata=metadata,
                raw=raw or metadata,
                owner=user_email,
                workspace_id=workspace_id,
            )
            # 선: Chat이 메시지를 "포함함"
            self._upsert_edge(
                conn, conv_id, node_id, "포함함", weight=0.3, metadata={"role": role}
            )

            # ── 4. RAG chunks  (검색용, 그래프에서 숨김) ──────────────────────
            for index, chunk in enumerate(_chunks(content)):
                chunk_id = f"chunk:{_sha256_text(f'{node_id}:{index}:{chunk}')[:24]}"
                self._upsert_node(
                    conn,
                    chunk_id,
                    "Chunk",
                    f"chunk {index + 1}",
                    summary=chunk[:500],
                    metadata={"index": index, "source_node": node_id},
                    owner=user_email,
                    workspace_id=workspace_id,
                )
                self._upsert_chunk(
                    conn,
                    chunk_id=chunk_id,
                    source_node=node_id,
                    text=chunk,
                    metadata={"index": index, "source_node": node_id},
                )
                self._upsert_edge(conn, node_id, chunk_id, "포함함")

            # ── 5. Concept / Feature / Error / Code 노드  (점: 명사) ───────────
            concept_ids: Dict[str, str] = {}
            for concept in concepts:
                node_t = _classify_node_type(concept, content)
                cid = f"{node_t.lower()}:{_slug(concept)}"
                concept_ids[concept.lower()] = cid
                self._upsert_node(
                    conn,
                    cid,
                    node_t,
                    concept,
                    metadata={"auto_extracted": True, "source": source, "workspace_id": workspace_id},
                    owner=user_email,
                    workspace_id=workspace_id,
                )
                # 선: Chat이 개념을 "언급함"
                self._upsert_edge(
                    conn,
                    conv_id,
                    cid,
                    "언급함",
                    weight=0.7,
                    metadata={"source": source},
                )

            # ── 6. Concept–Concept 엣지  (선: 동사형) ─────────────────────────
            for triple in triples:
                subj_id = concept_ids.get(triple["subject"].lower())
                obj_id = concept_ids.get(triple["object"].lower())
                if subj_id and obj_id and subj_id != obj_id:
                    self._upsert_edge(
                        conn,
                        subj_id,
                        obj_id,
                        triple["relation"],  # 동사형 레이블
                        weight=1.0,
                        metadata={"context": triple.get("context", "")[:240]},
                    )

            # ── 7. Task / Decision 노드  (점: 명사) ────────────────────────────
            for item in semantic:
                sem_type = item["type"]
                sem_title = item["title"]
                sem_id = f"{sem_type.lower()}:{_sha256_text(f'{conv_id}:{sem_type}:{sem_title}')[:24]}"
                self._upsert_node(
                    conn,
                    sem_id,
                    sem_type,
                    sem_title,
                    summary=item["summary"],
                    metadata={"auto_extracted": True, "source_node": node_id, "workspace_id": workspace_id},
                    raw=item,
                    owner=user_email,
                    workspace_id=workspace_id,
                )
                # 선: Chat이 Task/Decision을 "생성함"
                self._upsert_edge(conn, conv_id, sem_id, "생성함", weight=0.9)
                # Task/Decision이 관련 개념을 "언급함"
                for cid in list(concept_ids.values())[:3]:
                    self._upsert_edge(conn, sem_id, cid, "언급함", weight=0.6)

        return {"node_id": node_id, "type": node_type}

    def ingest_document(
        self,
        path: Path,
        *,
        original_filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        uploader: Optional[str] = None,
        conversation_id: Optional[str] = None,
        extracted: Optional[Dict[str, Any]] = None,
        source_type: Optional[str] = None,
        source_uri: Optional[str] = None,
        captured_at: Optional[str] = None,
        modified_at: Optional[str] = None,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        permissions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = Path(path)
        data = path.read_bytes()
        digest = _sha256_bytes(data)
        ext = path.suffix.lower()
        filename = original_filename or path.name
        captured_at = captured_at or _now()
        blob_path = self.blob_dir / digest[:2] / f"{digest}{ext}"
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if not blob_path.exists():
            shutil.copyfile(path, blob_path)

        doc_meta = self._document_structure(path, ext)
        text = str(
            (extracted or {}).get("content") or (extracted or {}).get("preview") or ""
        )
        file_id = f"file:{digest[:24]}"
        metadata = {
            "filename": filename,
            "ext": ext,
            "mime_type": mime_type,
            "bytes": len(data),
            "sha256": digest,
            "content_hash": digest,
            "blob_path": str(blob_path),
            "uploader": uploader,
            "owner": owner or uploader,
            "workspace_id": workspace_id,
            "permissions": permissions or {},
            "source_type": source_type or "file",
            "source_uri": source_uri or str(path),
            "captured_at": captured_at,
            "modified_at": modified_at,
            "conversation_id": conversation_id,
            "extracted": {k: v for k, v in (extracted or {}).items() if k != "content"},
            "structure": doc_meta,
        }
        full_text = f"{filename}\n{text}"
        concepts = _extract_concepts(full_text, limit=15)
        triples = _extract_triples(full_text, concepts)
        chunk_ids: List[str] = []
        source_node_id: Optional[str] = None

        with self._connect() as conn:
            duplicate = self._node_exists(conn, file_id)
            # ── Document 노드  (점: 명사 — 파일) ────────────────────────────────
            self._upsert_node(
                conn,
                file_id,
                "Document",
                filename,
                summary=(text or filename)[:500],
                metadata=metadata,
                raw=metadata,
                owner=owner or uploader,
                workspace_id=workspace_id,
            )
            self._ingest_structure_nodes(conn, file_id, filename, doc_meta)

            # ── SOURCE 노드 + indexed_from (v3.6.0, source_type 지정 시) ──────
            if source_type:
                source_node_id = self._attach_source_node(
                    conn,
                    file_id,
                    source_type=source_type,
                    source_uri=source_uri or str(path),
                    title=filename,
                    content_hash=digest,
                    captured_at=captured_at,
                    extra={
                        "owner": owner or uploader,
                        "workspace_id": workspace_id,
                        "ext": ext,
                    },
                )

            # ── Person 노드 + 동사형 엣지 ─────────────────────────────────────
            if uploader:
                person_id = f"person:{_slug(uploader)}"
                self._upsert_node(
                    conn,
                    person_id,
                    "Person",
                    uploader,
                    metadata={"email": uploader},
                    owner=uploader,
                    workspace_id=workspace_id,
                )
                # 선: 동사 — Person이 Document를 "업로드함"
                self._upsert_edge(conn, person_id, file_id, "업로드함", weight=1.0)

            # ── Chat 노드와 연결 ──────────────────────────────────────────────
            if conversation_id:
                conv_id = f"conversation:{_slug(conversation_id)}"
                self._upsert_node(
                    conn,
                    conv_id,
                    "Chat",
                    conversation_id,
                    metadata={"conversation_id": conversation_id, "workspace_id": workspace_id},
                    owner=owner or uploader,
                    workspace_id=workspace_id,
                )
                # 선: 동사 — Chat이 Document를 "언급함"
                self._upsert_edge(conn, conv_id, file_id, "언급함", weight=0.8)

            # ── RAG chunks (검색용, 그래프 비표시) ────────────────────────────
            for index, chunk in enumerate(_chunks(text)):
                chunk_id = f"chunk:{_sha256_text(f'{file_id}:{index}:{chunk}')[:24]}"
                chunk_ids.append(chunk_id)
                self._upsert_node(
                    conn,
                    chunk_id,
                    "Chunk",
                    f"{filename} chunk {index + 1}",
                    summary=chunk[:500],
                    metadata={"index": index, "source_node": file_id, "workspace_id": workspace_id},
                    owner=owner or uploader,
                    workspace_id=workspace_id,
                )
                self._upsert_chunk(
                    conn,
                    chunk_id=chunk_id,
                    source_node=file_id,
                    text=chunk,
                    metadata={"index": index, "source_node": file_id},
                )
                self._upsert_edge(conn, file_id, chunk_id, "포함함")

            # ── Concept / Feature / Error / Code 노드 + 동사형 엣지 ───────────
            concept_ids: Dict[str, str] = {}
            for concept in concepts:
                node_t = _classify_node_type(concept, full_text)
                cid = f"{node_t.lower()}:{_slug(concept)}"
                concept_ids[concept.lower()] = cid
                self._upsert_node(
                    conn,
                    cid,
                    node_t,
                    concept,
                    metadata={"auto_extracted": True, "source_file": filename, "workspace_id": workspace_id},
                    owner=owner or uploader,
                    workspace_id=workspace_id,
                )
                # 선: 동사 — Document가 Concept을 "포함함"
                self._upsert_edge(conn, file_id, cid, "포함함", weight=0.8)

            # ── Concept–Concept 엣지  (선: 동사형) ───────────────────────────
            for triple in triples:
                subj_id = concept_ids.get(triple["subject"].lower())
                obj_id = concept_ids.get(triple["object"].lower())
                if subj_id and obj_id and subj_id != obj_id:
                    self._upsert_edge(
                        conn,
                        subj_id,
                        obj_id,
                        triple["relation"],
                        weight=1.0,
                        metadata={"context": triple.get("context", "")[:240]},
                    )

            # ── Task / Decision 노드 ──────────────────────────────────────────
            for item in _semantic_items(text):
                sem_type = item["type"]
                sem_title = item["title"]
                sem_id = f"{sem_type.lower()}:{_sha256_text(f'{file_id}:{sem_type}:{sem_title}')[:24]}"
                self._upsert_node(
                    conn,
                    sem_id,
                    sem_type,
                    sem_title,
                    summary=item["summary"],
                    metadata={
                        "auto_extracted": True,
                        "source_node": file_id,
                        "filename": filename,
                        "workspace_id": workspace_id,
                    },
                    raw=item,
                    owner=owner or uploader,
                    workspace_id=workspace_id,
                )
                # 선: Document가 Task/Decision을 "포함함"
                self._upsert_edge(conn, file_id, sem_id, "포함함", weight=0.9)

        return {
            "node_id": file_id,
            "type": "Document",
            "sha256": digest,
            "content_hash": digest,
            "source_node_id": source_node_id,
            "chunk_ids": chunk_ids,
            "chunk_count": len(chunk_ids),
            "duplicate": duplicate,
            "captured_at": captured_at,
            "metadata": metadata,
        }

    def ingest_event(
        self,
        event_type: str,
        title: str,
        *,
        user_email: Optional[str] = None,
        user_nickname: Optional[str] = None,
        source: Optional[str] = None,
        conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event_type = str(event_type or "Event")
        title = str(title or event_type)
        payload = {
            "event_type": event_type,
            "title": title,
            "user_email": user_email,
            "user_nickname": user_nickname,
            "source": source,
            "conversation_id": conversation_id,
            "metadata": metadata or {},
            "timestamp": _now(),
        }
        event_id = f"event:{_sha256_text(_json(payload))[:24]}"
        conv_id = f"conversation:{_slug(conversation_id or 'default')}"
        with self._connect() as conn:
            self._upsert_node(
                conn,
                event_id,
                event_type,
                title,
                summary=title,
                metadata=payload,
                raw=payload,
            )
            self._upsert_node(
                conn,
                conv_id,
                "Conversation",
                conversation_id or "Default conversation",
                metadata={"source": source},
            )
            self._upsert_edge(
                conn, conv_id, event_id, "has_event", metadata={"source": source}
            )
            if user_email or user_nickname:
                person_key = user_email or user_nickname or "unknown"
                person_id = f"person:{_slug(person_key)}"
                self._upsert_node(
                    conn,
                    person_id,
                    "Person",
                    user_nickname or user_email or "Unknown user",
                    metadata={"email": user_email},
                )
                self._upsert_edge(
                    conn,
                    person_id,
                    event_id,
                    "triggered",
                    metadata={"event_type": event_type},
                )
        return {"node_id": event_id, "type": event_type}

    def _node_exists(self, conn: sqlite3.Connection, node_id: str) -> bool:
        row = conn.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return row is not None

    def node_is_embedded(self, node_id: str) -> bool:
        """True when a vector embedding exists for ``node_id`` (RAG-ready)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM vector_embeddings WHERE item_id = ? LIMIT 1",
                (node_id,),
            ).fetchone()
            return row is not None

    def _attach_source_node(
        self,
        conn: sqlite3.Connection,
        content_node_id: str,
        *,
        source_type: str,
        source_uri: Optional[str] = None,
        title: Optional[str] = None,
        content_hash: Optional[str] = None,
        captured_at: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create the SOURCE node for an ingested item and link it via INDEXED_FROM.

        Every ingested content node points at exactly one SOURCE node, so the
        graph is always able to explain *where* a node came from. The source id
        is derived from (source_type, source_uri | content_hash) so re-ingesting
        the same origin reuses the same SOURCE node (idempotent).
        """
        key = source_uri or content_hash or content_node_id
        source_id = f"source:{_sha256_text(f'{source_type}|{key}')[:24]}"
        meta = {
            "source_type": source_type,
            "source_uri": source_uri,
            "content_hash": content_hash,
            "captured_at": captured_at or _now(),
            **(extra or {}),
        }
        label = title or source_uri or source_type
        self._upsert_node(
            conn,
            source_id,
            "Source",
            label,
            summary=str(source_uri or title or source_type)[:400],
            metadata=meta,
            owner=meta.get("owner"),
            workspace_id=meta.get("workspace_id"),
        )
        # 선: 콘텐츠 노드가 "이 출처에서 색인됨" (indexed_from → SOURCE)
        self._upsert_edge(
            conn,
            content_node_id,
            source_id,
            "indexed_from",
            weight=1.0,
            metadata={"source_type": source_type},
        )
        return source_id

    def ingest_source(
        self,
        *,
        source_type: str,
        title: str,
        text: str,
        source_uri: Optional[str] = None,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        permissions: Optional[Dict[str, Any]] = None,
        captured_at: Optional[str] = None,
        modified_at: Optional[str] = None,
        conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Unified text/web ingestion: one shape for URL, browser tab, note, text.

        Creates a content ``Document`` node (idempotent by content hash), a
        ``Source`` node linked via ``indexed_from``, RAG chunks, and extracted
        Concept/Task/Decision nodes — mirroring ingest_document for non-file
        sources. Returns the full set of ids the caller needs to record
        provenance, including ``duplicate`` (was the content already indexed).
        """
        source_type = str(source_type or "text")
        text = str(text or "")
        title = (
            _clean_text(str(title or source_uri or source_type))[:240] or source_type
        )
        captured_at = captured_at or _now()
        content_hash = _sha256_text(f"{source_type}|{source_uri or ''}|{text}")
        content_id = f"webdoc:{content_hash[:24]}"
        full_text = f"{title}\n{text}"
        node_meta = {
            "source_type": source_type,
            "source_uri": source_uri,
            "content_hash": content_hash,
            "title": title,
            "captured_at": captured_at,
            "modified_at": modified_at,
            "owner": owner,
            "workspace_id": workspace_id,
            "permissions": permissions or {},
            "chars": len(text),
            **(metadata or {}),
        }
        concepts = _extract_concepts(full_text, limit=15)
        triples = _extract_triples(full_text, concepts)
        chunk_ids: List[str] = []

        with self._connect() as conn:
            duplicate = self._node_exists(conn, content_id)
            # ── 콘텐츠 노드 (점: 명사 — 문서) ────────────────────────────────
            self._upsert_node(
                conn,
                content_id,
                "Document",
                title,
                summary=(text or title)[:500],
                metadata=node_meta,
                raw=node_meta,
            )
            # ── SOURCE 노드 + indexed_from 엣지 (출처 추적) ──────────────────
            source_node_id = self._attach_source_node(
                conn,
                content_id,
                source_type=source_type,
                source_uri=source_uri,
                title=title,
                content_hash=content_hash,
                captured_at=captured_at,
                extra={"owner": owner, "workspace_id": workspace_id},
            )
            # ── 소유자(Person) + 동사형 엣지 ────────────────────────────────
            if owner:
                person_id = f"person:{_slug(owner)}"
                self._upsert_node(
                    conn, person_id, "Person", owner, metadata={"email": owner}
                )
                self._upsert_edge(conn, person_id, content_id, "업로드함", weight=1.0)
            # ── 대화 연결 ───────────────────────────────────────────────────
            if conversation_id:
                conv_id = f"conversation:{_slug(conversation_id)}"
                self._upsert_node(conn, conv_id, "Chat", conversation_id)
                self._upsert_edge(conn, conv_id, content_id, "언급함", weight=0.8)
            # ── RAG 청크 ────────────────────────────────────────────────────
            for index, chunk in enumerate(_chunks(text)):
                chunk_id = f"chunk:{_sha256_text(f'{content_id}:{index}:{chunk}')[:24]}"
                chunk_ids.append(chunk_id)
                self._upsert_node(
                    conn,
                    chunk_id,
                    "Chunk",
                    f"{title} chunk {index + 1}",
                    summary=chunk[:500],
                    metadata={"index": index, "source_node": content_id},
                )
                self._upsert_chunk(
                    conn,
                    chunk_id=chunk_id,
                    source_node=content_id,
                    text=chunk,
                    metadata={"index": index, "source_node": content_id},
                )
                self._upsert_edge(conn, content_id, chunk_id, "포함함")
            # ── Concept / Feature / Error / Code 노드 + 엣지 ────────────────
            concept_ids: Dict[str, str] = {}
            for concept in concepts:
                node_t = _classify_node_type(concept, full_text)
                cid = f"{node_t.lower()}:{_slug(concept)}"
                concept_ids[concept.lower()] = cid
                self._upsert_node(
                    conn,
                    cid,
                    node_t,
                    concept,
                    metadata={"auto_extracted": True, "source_type": source_type},
                )
                self._upsert_edge(conn, content_id, cid, "포함함", weight=0.8)
            for triple in triples:
                subj_id = concept_ids.get(triple["subject"].lower())
                obj_id = concept_ids.get(triple["object"].lower())
                if subj_id and obj_id and subj_id != obj_id:
                    self._upsert_edge(
                        conn,
                        subj_id,
                        obj_id,
                        triple["relation"],
                        weight=1.0,
                        metadata={"context": triple.get("context", "")[:240]},
                    )
            # ── Task / Decision 노드 ────────────────────────────────────────
            for item in _semantic_items(text):
                sem_type = item["type"]
                sem_title = item["title"]
                sem_id = f"{sem_type.lower()}:{_sha256_text(f'{content_id}:{sem_type}:{sem_title}')[:24]}"
                self._upsert_node(
                    conn,
                    sem_id,
                    sem_type,
                    sem_title,
                    summary=item["summary"],
                    metadata={"auto_extracted": True, "source_node": content_id},
                    raw=item,
                )
                self._upsert_edge(conn, content_id, sem_id, "포함함", weight=0.9)

        return {
            "node_id": content_id,
            "type": "Document",
            "source_node_id": source_node_id,
            "content_hash": content_hash,
            "chunk_ids": chunk_ids,
            "chunk_count": len(chunk_ids),
            "duplicate": duplicate,
            "captured_at": captured_at,
        }
