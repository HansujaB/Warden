# rag/registry.py
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


@dataclass
class RegistryEntry:
    chunk_id: str
    document_id: str           # stable across re-chunks; used for citations
    file_path: str
    section: str               # heading/anchor; stable citation target
    content_hash: str          # SHA-256 of chunk text
    version: int
    indexed_at_sha: str        # docs repo commit SHA at index time
    ingestion_timestamp: datetime
    status: Literal["live", "archived", "staging"]


def compute_chunk_hash(text: str) -> str:
    """SHA-256 over chunk content — same text always produces same hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RagRegistry:
    def __init__(self, db):
        self.db = db

    def get_by_chunk_id(self, chunk_id: str) -> RegistryEntry | None:
        return self.db.query_one(
            "SELECT * FROM rag_registry WHERE chunk_id = ?", chunk_id
        )

    def upsert(self, entry: RegistryEntry) -> None:
        """
        Versioned replace:
        1. If content hash matches the stored entry → skip (nothing changed).
        2. Archive the current live version immediately.
        3. Insert the new version as 'live'.

        We never leave two 'live' rows for the same document_id.
        An archived version can outrank its replacement in similarity
        search because near-identical embeddings are hard to distinguish,
        and the old text is sometimes a better lexical match for stale
        queries. One current version in production, period.
        """
        existing = self.get_by_chunk_id(entry.chunk_id)

        if existing and existing.content_hash == entry.content_hash:
            return  # Hash unchanged — skip re-embedding this chunk

        if existing:
            # Archive the old version immediately; don't leave it live
            self.db.execute(
                "UPDATE rag_registry SET status = 'archived' WHERE chunk_id = ?",
                entry.chunk_id
            )

        new_version = (existing.version + 1) if existing else 1

        self.db.execute(
            """INSERT INTO rag_registry
               (chunk_id, document_id, file_path, section,
                content_hash, version, indexed_at_sha,
                ingestion_timestamp, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'live')""",
            (
                entry.chunk_id,
                entry.document_id,
                entry.file_path,
                entry.section,
                entry.content_hash,
                new_version,
                entry.indexed_at_sha,
                datetime.now(tz=timezone.utc).isoformat(),
            )
        )

    def delete_chunk(self, chunk_id: str) -> None:
        """
        Called when a chunk is removed from the source doc entirely.
        Archives rather than hard-deletes so rollback/audit is always possible.
        """
        self.db.execute(
            "UPDATE rag_registry SET status = 'archived' WHERE chunk_id = ?",
            chunk_id
        )

    def is_stale(self, chunk_id: str, current_docs_sha: str) -> bool:
        """
        Returns True if the indexed version of this chunk predates
        the current docs commit SHA.

        Used to flag answers as 'may be stale' without blocking the query —
        the retrieval gate already decided the chunk is relevant; this is a
        transparency signal on the answer, not a hard block.
        """
        entry = self.get_by_chunk_id(chunk_id)
        if entry is None:
            return True
        return entry.indexed_at_sha != current_docs_sha

    def live_chunks_for_document(self, document_id: str) -> list[RegistryEntry]:
        """Return all live chunks for a given document_id."""
        return self.db.query_all(
            "SELECT * FROM rag_registry WHERE document_id = ? AND status = 'live'",
            document_id
        )
