"""
Text chunking helpers — memecah teks streaming menjadi kalimat utuh,
supaya TTS bisa mulai sintesis per-kalimat tanpa menunggu balasan lengkap.
"""

import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def extract_ready_sentences(buffer: str) -> tuple[list[str], str]:
    """
    Pisahkan buffer teks menjadi kalimat-kalimat yang sudah lengkap
    (diakhiri . ! atau ?) dan sisa teks yang belum tentu lengkap.

    Fragmen terakhir hasil split selalu dianggap belum lengkap, karena
    buffer bisa terpotong di tengah kalimat berikutnya.

    Returns:
        (kalimat_siap, sisa_buffer)
    """
    parts = _SENTENCE_BOUNDARY.split(buffer)
    if len(parts) <= 1:
        return [], buffer

    *complete, remainder = parts
    ready = [p.strip() for p in complete if p.strip()]
    return ready, remainder
