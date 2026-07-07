"""
Circuit Breaker -- deteksi krisis non-LLM, berjalan SYNCHRONOUS sebelum Agent 1 merespons.
Ini adalah pengaman keras (hardcoded), bukan bergantung pada LLM, agar deteksi krisis
selalu konsisten dan cepat -- resource darurat harus muncul di respons yang SAMA,
bukan menunggu giliran berikutnya lewat Agent 2 yang berjalan async.
"""

import re
from typing import Optional

# TODO: Ganti dengan nomor/link hotline krisis resmi Indonesia sebelum production.
CRISIS_RESOURCES = [
    {
        "name": "Kemenkes SEJIWA (Sehat Jiwa)",
        "contact": "119 (Ekstensi 8)",
        "description": "Layanan hotline resmi dari Kementerian Kesehatan RI untuk pendampingan psikologis.",
    },
    {
        "name": "Layanan Psikologi untuk Sehat Jiwa (LISA)",
        "contact": "08113855472",
        "description": "Layanan dukungan psikososial dan kesehatan jiwa di Indonesia.",
    }
]

CRISIS_KEYWORDS = [
    # Indonesian
    "bunuh diri", "mengakhiri hidup", "mengakhiri nyawa", "menyakiti diri",
    "melukai diri", "ingin mati", "pengen mati", "capek hidup", "gak kuat hidup",
    "tidak ada gunanya hidup", "lebih baik mati",
    # English
    "suicide", "kill myself", "end my life", "self harm", "self-harm",
    "want to die", "better off dead", "hurt myself",
]

_KEYWORD_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in CRISIS_KEYWORDS),
    re.IGNORECASE,
)


def check_crisis(text: str) -> Optional[dict]:
    """
    Cek apakah teks mengandung sinyal krisis (bunuh diri/self-harm).
    Return None jika aman, atau dict berisi resources jika terdeteksi.
    """
    if not text or not _KEYWORD_PATTERN.search(text):
        return None

    return {
        "crisis": True,
        "resources": CRISIS_RESOURCES,
    }
