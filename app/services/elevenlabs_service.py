"""
ElevenLabs Text-to-Speech Service — Voice synthesis untuk VTuber.
Docs: https://elevenlabs.io/docs
"""

import os
import base64
import logging
from typing import Optional

from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings

logger = logging.getLogger(__name__)

# ============================================================
# Suara Default VTuber
# ============================================================

# Voice IDs ElevenLabs yang bisa digunakan
AVAILABLE_VOICES = {
    "rachel":   {"id": "21m00Tcm4TlvDq8ikWAM", "description": "Suara perempuan, hangat & natural"},
    "bella":    {"id": "EXAVITQu4vr4xnSDxMaL", "description": "Suara perempuan, lembut & ceria"},
    "elli":     {"id": "MF3mGyEYCl7XYWbV9V6O", "description": "Suara perempuan, muda & energik"},
    "domi":     {"id": "AZnzlk1XvdvUeBnXmlld", "description": "Suara perempuan, kuat & ekspresif"},
    "sarah":    {"id": "EXAVITQu4vr4xnSDxMaL", "description": "Suara perempuan, profesional"},
}

DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")


def _get_elevenlabs_client() -> ElevenLabs:
    """Buat ElevenLabs client dari env vars."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY belum di-set di .env")
    return ElevenLabs(api_key=api_key)


# ============================================================
# Text-to-Speech
# ============================================================

def text_to_speech(
    text: str,
    voice_id: Optional[str] = None,
    stability: Optional[float] = None,
    similarity_boost: Optional[float] = None,
    style: Optional[float] = None,
    use_speaker_boost: bool = True,
    output_format: str = "mp3_22050_32",
) -> Optional[dict]:
    """
    Convert teks menjadi audio menggunakan ElevenLabs.
    
    Args:
        text: Teks yang akan diubah ke suara
        voice_id: ID suara ElevenLabs, default dari env
        stability: Konsistensi suara (0.0 - 1.0), None untuk default dashboard
        similarity_boost: Kesamaan dengan voice asli (0.0 - 1.0), None untuk default dashboard
        style: Ekspresi/style (0.0 - 1.0), None untuk default dashboard
        use_speaker_boost: Tingkatkan kualitas suara
        output_format: Format audio output
    
    Returns:
        Dict dengan audio base64 encoded, atau None jika gagal
    """
    if not text or not text.strip():
        return {"error": "Teks tidak boleh kosong"}

    # Batasi panjang teks (ElevenLabs ada limit per request)
    if len(text) > 2500:
        text = text[:2500] + "..."

    client = _get_elevenlabs_client()
    vid = voice_id or DEFAULT_VOICE_ID

    try:
        # Build API arguments dictionary
        kwargs = {
            "voice_id": vid,
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "output_format": output_format,
        }

        # If any voice settings are specified, send VoiceSettings object.
        # Otherwise, omit it completely to let ElevenLabs use the Dashboard settings!
        if stability is not None or similarity_boost is not None or style is not None:
            kwargs["voice_settings"] = VoiceSettings(
                stability=stability if stability is not None else 0.75,
                similarity_boost=similarity_boost if similarity_boost is not None else 0.75,
                style=style if style is not None else 0.0,
                use_speaker_boost=use_speaker_boost,
            )

        audio_generator = client.text_to_speech.convert_as_stream(**kwargs)

        # Convert generator ke bytes
        audio_bytes = b"".join(audio_generator)
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        return {
            "success":      True,
            "audio_base64": audio_base64,
            "format":       output_format,
            "voice_id":     vid,
            "text_length":  len(text),
            "audio_size_kb": round(len(audio_bytes) / 1024, 2),
        }

    except Exception as e:
        logger.error(f"ElevenLabs TTS error: {e}")
        return {
            "success": False,
            "error":   str(e),
        }


# ============================================================
# Voice Management
# ============================================================

def get_available_voices() -> list[dict]:
    """
    Ambil list suara yang tersedia dari ElevenLabs account.
    Gabungkan dengan preset voices kita.
    
    Returns:
        List dict dengan info setiap voice
    """
    try:
        client = _get_elevenlabs_client()
        voices_response = client.voices.get_all()
        
        voices = []
        for voice in voices_response.voices:
            voices.append({
                "voice_id":    voice.voice_id,
                "name":        voice.name,
                "category":    getattr(voice, "category", "premade"),
                "description": getattr(voice, "description", ""),
                "labels":      dict(voice.labels) if voice.labels else {},
                "preview_url": getattr(voice, "preview_url", None),
            })

        return voices

    except Exception as e:
        logger.error(f"ElevenLabs get_voices error: {e}")
        # Fallback ke preset voices kita
        return [
            {
                "voice_id": v["id"],
                "name":     name.capitalize(),
                "category": "premade",
                "description": v["description"],
            }
            for name, v in AVAILABLE_VOICES.items()
        ]


def get_voice_settings(voice_id: str) -> Optional[dict]:
    """
    Ambil setting suara default (stability, similarity_boost, dll)
    dari ElevenLabs untuk voice_id tertentu.
    """
    try:
        client = _get_elevenlabs_client()
        settings = client.voices.get_settings(voice_id=voice_id)
        return {
            "success": True,
            "stability": settings.stability,
            "similarity_boost": settings.similarity_boost,
            "style": settings.style,
            "use_speaker_boost": settings.use_speaker_boost,
        }
    except Exception as e:
        logger.error(f"ElevenLabs get_settings error: {e}")
        return {"success": False, "error": str(e)}
