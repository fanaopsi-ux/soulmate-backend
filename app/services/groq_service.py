"""
Groq AI Service — LLM Chat dengan karakter VTuber.
Model: Llama 3 70B (atau sesuai GROQ_MODEL di .env)
"""

import os
import json
import logging
from typing import Optional, Generator

from groq import Groq

from app.services.rag.prompts import AGENT_2_EXTRACTION_PROMPT, CLINICAL_VOCABULARY

logger = logging.getLogger(__name__)

# ============================================================
# Singleton Groq Client — dibuat sekali, dipakai terus
# ============================================================

_groq_client: Optional[Groq] = None


def _get_groq_client() -> Groq:
    """Ambil cached Groq client (singleton). Dibuat sekali saja."""
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY belum di-set di .env")
        _groq_client = Groq(api_key=api_key)
        logger.info("[Groq] Client created (singleton)")
    return _groq_client


# ============================================================
# Messages Builder
# ============================================================


def _build_messages(
    user_message: str,
    conversation_history: list[dict],
    system_prompt: str,
    user_name: Optional[str] = None,
    memories_context: Optional[str] = None,
) -> list[dict]:
    """Susun messages list: system prompt + history + pesan baru."""
    if memories_context:
        system_prompt += f"\n\n=== INGATAN PENTING TENTANG USER ===\n" \
                         f"Berikut adalah informasi/fakta penting yang kamu ingat tentang user dari percakapan sebelumnya.\n" \
                         f"Gunakan ingatan ini secara sangat NATURAL untuk kelanjutan hubungan pertemanan kalian. " \
                         f"Jangan pernah berkata seperti 'berdasarkan ingatan saya' atau 'saya ingat bahwa'. " \
                         f"Bicaralah seolah-olah kamu mengingatnya secara alami sebagai teman dekat:\n" \
                         f"{memories_context}\n" \
                         f"=== AKHIR INGATAN ===\n"
    if user_name:
        system_prompt += f"\n\nNama panggil user saat ini: {user_name}"

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})
    return messages


# ============================================================
# Chat Function (non-streaming, backward compatible)
# ============================================================

def chat_with_vtuber(
    user_message: str,
    conversation_history: list[dict],
    system_prompt: str,
    user_name: Optional[str] = None,
    memories_context: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.85,
    max_tokens: int = 400,
    mood: int = 2,
    language: str = "id",
) -> dict:
    """
    Kirim pesan ke VTuber AI dan dapatkan respons.

    Args:
        user_message: Pesan dari user
        conversation_history: List of {"role": "user/assistant", "content": "..."}
        user_name: Nama user (untuk personalisasi)
        memories_context: Konteks dari Mem0 memory
        model: Groq model name, default dari env
        temperature: Kreativitas respons (0.0 - 1.0)
        max_tokens: Maksimum token output
        mood: User mood (0-3: 0=cemas, 1=sedih, 2=biasa, 3=baik)

    Returns:
        Dict dengan respons dan usage stats
    """
    from app.services.agent_config import get_mood_modifier

    client = _get_groq_client()
    model_name = model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    # Tambahkan mood modifier & language ke system prompt
    modified_prompt = system_prompt + "\n" + get_mood_modifier(mood)
    if language:
        modified_prompt += f"\n\nIMPORTANT: The user has selected the language code '{language}'. You MUST respond EXCLUSIVELY in this language. Do not mix languages unless requested."

    messages = _build_messages(user_message, conversation_history, modified_prompt, user_name, memories_context)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=0.85,
            frequency_penalty=0.35,
            presence_penalty=0.25,
        )

        reply = response.choices[0].message.content
        usage = response.usage

        return {
            "success":     True,
            "reply":       reply,
            "model":       model_name,
            "tokens_used": {
                "prompt":     usage.prompt_tokens,
                "completion": usage.completion_tokens,
                "total":      usage.total_tokens,
            },
        }

    except Exception as e:
        logger.error(f"Groq chat error: {e}")
        return {
            "success": False,
            "reply":   "Kyaa~! Ada masalah teknis nih... Coba lagi ya! 🙏",
            "error":   str(e),
        }


# ============================================================
# Streaming Chat Function (SSE support)
# ============================================================

def chat_with_vtuber_stream(
    user_message: str,
    conversation_history: list[dict],
    system_prompt: str,
    user_name: Optional[str] = None,
    memories_context: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.85,
    max_tokens: int = 400,
    mood: int = 2,
    language: str = "id",
) -> Generator[str, None, None]:
    """
    Streaming version — yield teks per chunk dari Groq.

    Args:
        mood: User mood (0-3: 0=cemas, 1=sedih, 2=biasa, 3=baik)

    Yields:
        String chunks dari respons AI, satu per satu.

    Raises:
        Exception jika Groq API gagal.
    """
    from app.services.agent_config import get_mood_modifier

    client = _get_groq_client()
    model_name = model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    # Tambahkan mood modifier & language ke system prompt
    modified_prompt = system_prompt + "\n" + get_mood_modifier(mood)
    if language:
        modified_prompt += f"\n\nIMPORTANT: The user has selected the language code '{language}'. You MUST respond EXCLUSIVELY in this language. Do not mix languages unless requested."

    messages = _build_messages(user_message, conversation_history, modified_prompt, user_name, memories_context)

    stream = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=0.85,
        frequency_penalty=0.35,
        presence_penalty=0.25,
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


def summarize_user_profile(memories: list[str], agent_id: str) -> str:
    """
    Buat ringkasan profil user (sekitar 1 paragraf) berdasarkan list memori.
    Jika kosong, kembalikan string default.
    """
    if not memories:
        return "Belum ada memori atau ingatan yang tersimpan."

    client = _get_groq_client()
    model_name = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    
    memories_str = "\n".join(f"- {m}" for m in memories)
    
    prompt = (
        f"Kamu adalah AI asisten untuk karakter VTuber bernama {agent_id.capitalize()}. "
        "Tugasmu adalah membuat sebuah ringkasan singkat profil user (maksimal 1 paragraf pendek, sekitar 3-4 kalimat). "
        "Ringkasan ini akan ditampilkan kepada user, sehingga gunakan kata ganti 'Kamu' (mengacu kepada user) "
        "dan gaya bahasa Indonesia santai.\n\n"
        "Fakta-fakta user dari ingatan sebelumnya:\n"
        f"{memories_str}\n\n"
        "Buatlah ringkasannya sekarang tanpa salam pembuka atau penutup."
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, # Low temp for factual summary
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq summarize error: {e}")
        return "Terjadi masalah saat mengambil ringkasan memori."


def generate_personalized_affirmation(memories: list[str], agent_id: str) -> str:
    """
    Buat kalimat afirmasi personal untuk user berdasarkan memori mereka.
    """
    client = _get_groq_client()
    model_name = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    
    memories_str = "\n".join(f"- {m}" for m in (memories[-30:] if memories else []))
    
    if not memories:
        prompt = (
            f"Kamu adalah AI asisten untuk karakter VTuber bernama {agent_id.capitalize()}. "
            "Berikan satu kalimat afirmasi positif umum (pendek) untuk menguatkan atau menyemangati user hari ini. "
            "Gunakan bahasa Indonesia yang hangat, singkat, dan tanpa tanda kutip. Jangan sebutkan nama user."
        )
    else:
        prompt = (
            f"Kamu adalah AI asisten untuk karakter VTuber bernama {agent_id.capitalize()}. "
            "Berikut adalah beberapa hal yang sedang dialami atau diceritakan user akhir-akhir ini:\n"
            f"{memories_str}\n\n"
            "Tugasmu: Buat satu kalimat afirmasi positif yang SANGAT PERSONAL dan MENGUATKAN berdasarkan hal-hal di atas. "
            "Misal, jika user sedang banyak tugas, semangati soal tugasnya. "
            "Gunakan bahasa Indonesia yang hangat, singkat (maks 2 kalimat pendek), dan tanpa tanda kutip. "
            "Anggap kamu sedang berbicara langsung ke mereka (gunakan 'kamu')."
        )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=100,
        )
        return response.choices[0].message.content.strip().replace('"', '')
    except Exception as e:
        logger.error(f"Groq affirmation error: {e}")
        return "Kamu jauh lebih kuat dari apa yang kamu bayangkan."



def get_available_models() -> list[str]:
    """Return list model Groq yang didukung."""
    return [
        "llama3-70b-8192",
        "llama3-8b-8192",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ]


# ============================================================
# Agent 2 -- Clinical Extraction (JSON, background screener)
# ============================================================

def _empty_clinical_result() -> dict:
    """Struktur klinis kosong -- dipakai sebagai fallback jika ekstraksi gagal."""
    return {
        "alam_perasaan": [],
        "interaksi_selama_wawancara": None,
        "persepsi_halusinasi_jenis": [],
        "isi_pikir": [],
        "koping_adaptif": [],
        "koping_maladaptif": [],
        "hubungan_sosial": None,
        "konsep_diri": None,
        "catatan_klinis_a2": None,
    }


def _sanitize_clinical_json(raw: dict) -> dict:
    """Buang nilai apapun yang tidak ada di CLINICAL_VOCABULARY -- jangan pernah percaya output LLM mentah."""
    result = _empty_clinical_result()
    list_fields = (
        "alam_perasaan", "persepsi_halusinasi_jenis", "isi_pikir",
        "koping_adaptif", "koping_maladaptif",
    )
    for field in list_fields:
        values = raw.get(field) or []
        if isinstance(values, list):
            allowed = set(CLINICAL_VOCABULARY[field])
            result[field] = [v for v in values if v in allowed]

    text_fields = ("interaksi_selama_wawancara", "hubungan_sosial", "konsep_diri", "catatan_klinis_a2")
    for field in text_fields:
        value = raw.get(field)
        result[field] = value if isinstance(value, str) and value.strip() else None

    return result


def extract_clinical_json(transcript: str) -> dict:
    """
    Agent 2 -- ekstrak sinyal klinis dari transkrip percakapan (user + assistant) menjadi
    JSON terstruktur, dibatasi ke CLINICAL_VOCABULARY. Selalu mengembalikan struktur
    yang valid meski panggilan LLM gagal.
    """
    client = _get_groq_client()
    model_name = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    prompt = AGENT_2_EXTRACTION_PROMPT.format(
        vocabulary=json.dumps(CLINICAL_VOCABULARY, ensure_ascii=False),
        transcript=transcript,
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content)
        return _sanitize_clinical_json(raw)
    except Exception as e:
        logger.error(f"[Agent2] Clinical extraction error: {e}")
        return _empty_clinical_result()
