"""AI Semantic Search — vector-based message search using chromadb.

Optional module: only active when chromadb is installed.
  uv add chromadb   OR   pip install chromadb

Falls back gracefully when not available (Flask returns 503 for /api/agent/semantic/*).

Usage:
  import ai_semantic
  ai_semantic.init(config_dir)
  ai_semantic.index_contact(username, messages)   # background thread
  results = ai_semantic.search(username, "项目")   # returns list of message dicts
"""

import json
import os
import threading
import time

try:
    import chromadb
    from chromadb.utils import embedding_functions
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_client = None
_collection = None
_lock = threading.Lock()
_INDEX_DIR = None
_indexed_contacts: set = set()


def init(config_dir: str):
    global _client, _collection, _INDEX_DIR
    if not _AVAILABLE:
        return
    _INDEX_DIR = os.path.join(config_dir, "semantic_index")
    os.makedirs(_INDEX_DIR, exist_ok=True)
    _client = chromadb.PersistentClient(path=_INDEX_DIR)
    _collection = _client.get_or_create_collection(
        name="wechat_messages",
        metadata={"hnsw:space": "cosine"},
    )
    # Load already-indexed contacts
    with _lock:
        try:
            existing = _collection.get(include=[])
            ids = existing.get("ids", [])
            for id_ in ids:
                parts = id_.split("_", 1)
                if parts:
                    _indexed_contacts.add(parts[0])
        except Exception:
            pass
    print(f"[Semantic] Initialized. Indexed contacts: {len(_indexed_contacts)}")


def is_available() -> bool:
    return _AVAILABLE and _collection is not None


def is_indexed(username: str) -> bool:
    with _lock:
        return username in _indexed_contacts


def index_contact(username: str, messages: list):
    """Index all messages for a contact. Runs in current thread (call from daemon thread)."""
    if not is_available():
        return
    if not messages:
        return
    try:
        docs = []
        ids = []
        metadatas = []
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            # Skip HTML-heavy messages
            if content.count("<") > 5:
                continue
            doc_id = f"{username}_{msg.get('id', i)}_{i}"
            docs.append(content[:500])
            ids.append(doc_id)
            metadatas.append({
                "username": username,
                "time": msg.get("time", 0),
                "time_str": msg.get("time_str", ""),
                "is_self": int(msg.get("is_self", False)),
                "sender_name": msg.get("sender_name", ""),
            })

        if not docs:
            return

        # Upsert in batches of 500
        batch_size = 500
        for i in range(0, len(docs), batch_size):
            _collection.upsert(
                documents=docs[i:i+batch_size],
                ids=ids[i:i+batch_size],
                metadatas=metadatas[i:i+batch_size],
            )

        with _lock:
            _indexed_contacts.add(username)
        print(f"[Semantic] Indexed {len(docs)} messages for {username}")

    except Exception as e:
        print(f"[Semantic] Index error for {username}: {e}")


def search(username: str, query: str, limit: int = 20) -> list:
    """Semantic search for messages from a specific contact."""
    if not is_available():
        return []
    try:
        results = _collection.query(
            query_texts=[query],
            n_results=min(limit, 50),
            where={"username": username},
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        output = []
        for doc, meta, dist in zip(docs, metas, distances):
            output.append({
                "content": doc,
                "time_str": meta.get("time_str", ""),
                "time": meta.get("time", 0),
                "is_self": bool(meta.get("is_self", 0)),
                "sender_name": meta.get("sender_name", ""),
                "similarity": round(1 - dist, 3),
            })
        return output
    except Exception as e:
        print(f"[Semantic] Search error: {e}")
        return []


def get_status() -> dict:
    if not is_available():
        return {"available": False}
    with _lock:
        indexed = list(_indexed_contacts)
    try:
        count = _collection.count()
    except Exception:
        count = 0
    return {
        "available": True,
        "indexed_contacts": len(indexed),
        "indexed": indexed,
        "total_messages": count,
    }
