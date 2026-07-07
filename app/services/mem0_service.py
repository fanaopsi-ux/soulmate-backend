"""
Mem0 Memory Service — Persistent AI memory per user.
Docs: https://docs.mem0.ai/
"""

import os
import logging
from typing import Optional

from mem0 import MemoryClient

logger = logging.getLogger(__name__)

# ============================================================
# Singleton Mem0 Client — dibuat sekali, dipakai terus
# ============================================================

_mem0_client: Optional[MemoryClient] = None


def _get_mem0_client() -> MemoryClient:
    """Ambil cached Mem0 client (singleton). Dibuat sekali saja."""
    global _mem0_client
    if _mem0_client is None:
        api_key = os.getenv("MEM0_API_KEY", "")
        if not api_key:
            raise ValueError("MEM0_API_KEY belum di-set di .env")
        _mem0_client = MemoryClient(api_key=api_key)
        logger.info("[Mem0] Client created (singleton)")
    return _mem0_client


# ============================================================
# Memory Operations
# ============================================================

def add_memory(
    messages: list[dict],
    user_id: str,
    agent_id: str,
    session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> bool:
    """
    Simpan percakapan ke memori Mem0.
    
    Args:
        messages: List of {"role": "user/assistant", "content": "..."}
        user_id: ID unik user
        agent_id: ID karakter agent (contoh: "emily" atau "kai")
        session_id: ID sesi (optional, untuk filter per sesi)
        metadata: Metadata tambahan (optional)
    
    Returns:
        True jika berhasil, False jika gagal
    """
    try:
        client = _get_mem0_client()
        meta = metadata or {}
        if session_id:
            meta["session_id"] = session_id

        client.add(
            messages=messages,
            user_id=f"{user_id}_{agent_id}",
            metadata=meta,
        )
        return True
    except Exception as e:
        logger.error(f"Mem0 add_memory error: {e}")
        return False


def _normalize_memories(results, tier: str) -> list[dict]:
    """Normalisasi hasil mentah Mem0 (get_all/search) ke format seragam + tag tier."""
    memories = []
    for item in results or []:
        if isinstance(item, dict):
            memories.append({
                "memory": item.get("memory", item.get("text", "")),
                "score":  item.get("score", 1.0),
                "tier":   tier,
            })
    return memories


def get_memories(
    user_id: str,
    agent_id: str,
    query: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
      supaya fakta penting (nama, situasi hidup, preferensi) tidak hilang hanya
      karena pesan saat ini tidak mirip secara semantik dengan memori itu.
    - "relevant": memori hasil semantic search berdasarkan pesan user saat ini.

    Hasil dari kedua tingkat digabung dan di-dedupe (core diprioritaskan).

    Args:
        user_id: ID user
        agent_id: ID karakter agent
        query: Query pencarian (semantic search); jika None hanya core yang diambil
        limit: Maksimum total memori yang dikembalikan

    Returns:
        List memori, format: [{"memory": "...", "score": 0.9, "tier": "core"|"relevant"}]
    """
    try:
        client = _get_mem0_client()

        core_limit = max(3, limit // 2)
        core_results = client.get_all(user_id=f"{user_id}_{agent_id}", limit=core_limit)
        core_memories = _normalize_memories(core_results, tier="core")

        relevant_memories = []
        if query:
            semantic_limit = max(limit - len(core_memories), 1)
            search_results = client.search(query=query, user_id=f"{user_id}_{agent_id}", limit=semantic_limit)
            relevant_memories = _normalize_memories(search_results, tier="relevant")

        # Dedupe berdasarkan teks memori — core menang kalau ada duplikat
        seen = {m["memory"] for m in core_memories}
        merged = core_memories + [m for m in relevant_memories if m["memory"] not in seen]
        return merged[:limit]

    except Exception as e:
        logger.error(f"Mem0 get_memories error: {e}")
        return []


def get_all_raw_memories(user_id: str, agent_id: str) -> list[str]:
    """
    Ambil seluruh memori mentah sebagai list of string tanpa limit pagination 
    (atau setidaknya dengan limit besar) untuk diringkas oleh LLM.
    """
    try:
        client = _get_mem0_client()
        # Ambil maksimal 100 memori terbaru untuk diringkas (mencegah payload terlalu besar)
        results = client.get_all(user_id=f"{user_id}_{agent_id}", limit=100)
        
        memories_text = []
        for item in results or []:
            if isinstance(item, dict):
                text = item.get("memory", item.get("text", ""))
                if text:
                    memories_text.append(text)
                    
        return memories_text
    except Exception as e:
        logger.error(f"Mem0 get_all_raw_memories error: {e}")
        return []


def format_memories_for_prompt(memories: list[dict]) -> str:
    """
    Format memori menjadi teks yang bisa dimasukkan ke system prompt,
    dikelompokkan supaya LLM tahu mana fakta inti vs konteks situasional.

    Returns:
        String berisi memori yang diformat dengan rapi
    """
    if not memories:
        return ""

    core = [m for m in memories if m.get("tier") == "core"]
    relevant = [m for m in memories if m.get("tier") != "core"]

    sections = []
    if core:
        core_lines = "\n".join(f"- {m['memory']}" for m in core)
        sections.append(f"Fakta inti tentang user:\n{core_lines}")
    if relevant:
        relevant_lines = "\n".join(f"- {m['memory']}" for m in relevant)
        sections.append(f"Konteks relevan dari percakapan sebelumnya:\n{relevant_lines}")

    return "\n\n".join(sections)


def clear_user_memory(user_id: str, agent_id: str) -> bool:
    """
    Hapus semua memori user.
    Gunakan dengan hati-hati!
    
    Returns:
        True jika berhasil, False jika gagal
    """
    try:
        client = _get_mem0_client()
        client.delete_all(user_id=f"{user_id}_{agent_id}")
        logger.info(f"Memory cleared for user {user_id} and agent {agent_id}")
        return True
    except Exception as e:
        logger.error(f"Mem0 clear_memory error: {e}")
        return False


def get_memory_stats(user_id: str, agent_id: str) -> dict:
    """
    Ambil statistik memori user.
    
    Returns:
        Dict dengan total memories dan info lainnya
    """
    try:
        client = _get_mem0_client()
        memories = client.get_all(user_id=f"{user_id}_{agent_id}")
        return {
            "total_memories": len(memories) if memories else 0,
            "user_id":        user_id,
        }
    except Exception as e:
        logger.error(f"Mem0 get_stats error: {e}")
        return {"total_memories": 0, "user_id": user_id, "error": str(e)}
