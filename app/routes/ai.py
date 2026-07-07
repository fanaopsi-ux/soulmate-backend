"""
AI Routes — Chat VTuber, Text-to-Speech, dan Voice management.
Semua endpoint butuh subscription aktif kecuali /voices.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from app.limiter import limiter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.services.auth_service import get_current_user, get_current_active_subscriber
from app.services.groq_service import (
    chat_with_vtuber, chat_with_vtuber_stream, get_available_models, summarize_user_profile
)
from app.services.mem0_service import (
    add_memory, get_memories, format_memories_for_prompt,
    clear_user_memory, get_memory_stats, get_all_raw_memories
)
from app.tasks import save_memory_task, run_agent2_task
from app.services.elevenlabs_service import text_to_speech, get_available_voices, get_voice_settings
from app.services.agent_config import get_agent_config
from app.services.agent2.circuit_breaker import check_crisis
from app.services.agent2.screener import run_agent2, get_pending_directive
from app.services.text_chunking import extract_ready_sentences

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# Pydantic Schemas
# ============================================================

class ChatMessage(BaseModel):
    role: str    # "user" atau "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None         # ID sesi (auto-generate jika None)
    agent_id: str = "emily"                  # Karakter agent (emily/kai), default emily
    conversation_history: list[ChatMessage] = []  # Riwayat percakapan dari frontend
    with_tts: bool = False                   # Auto-generate audio untuk respons
    voice_id: Optional[str] = None           # Override voice ID
    mood: int = Field(2, ge=0, le=3)         # 0=cemas, 1=sedih, 2=biasa, 3=baik
    entry_type: Literal["direct", "quick_chat"] = "direct"  # "direct" (Mulai sesi) atau "quick_chat" (prompt cepat)
    language: str = "id"                     # Bahasa yang digunakan user ('id', 'en', dll)


class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None
    stability: float = 0.5
    similarity_boost: float = 0.75


# ============================================================
# Background helpers
# ============================================================

# _save_memory_background removed (moved to Celery task)


def _update_chat_session(
    db: Session,
    user_id: int,
    session_id: str,
    title: str,
    tokens_total: int = 0,
):
    """Update atau buat chat session di DB."""
    chat_session = (
        db.query(models.ChatSession)
        .filter(models.ChatSession.session_id == session_id)
        .first()
    )
    if not chat_session:
        chat_session = models.ChatSession(
            user_id=user_id,
            session_id=session_id,
            title=title[:50] + ("..." if len(title) > 50 else ""),
            message_count=0,
            total_tokens=0,
        )
        db.add(chat_session)

    if chat_session.message_count is None:
        chat_session.message_count = 0
    if chat_session.total_tokens is None:
        chat_session.total_tokens = 0

    chat_session.message_count += 2
    chat_session.total_tokens  += tokens_total
    chat_session.last_message_at = datetime.now(timezone.utc)
    db.commit()


# ============================================================
# Chat Route (non-streaming, backward compatible)
# ============================================================

@router.post("/chat")
@limiter.limit("30/minute")
def chat(
    request: Request,
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Chat dengan VTuber AI (Emily).

    Flow:
    1. Cek Circuit Breaker (krisis) — synchronous, non-LLM
    2. Ambil memories relevan dari Mem0 + directive Agent 2 (jika ada)
    3. Kirim ke Groq LLM dengan konteks memori
    4. Simpan percakapan baru ke Mem0 + jalankan Agent 2 (BACKGROUND — tidak blocking)
    5. (Optional) Generate audio dari respons via ElevenLabs

    Butuh subscription aktif.
    """
    session_id = req.session_id or str(uuid.uuid4())
    
    # 0. Check Quota for Free Users
    now = datetime.now(timezone.utc)
    # Reset quota if it's a new day
    if not current_user.last_chat_date or current_user.last_chat_date.date() != now.date():
        current_user.daily_chat_count = 0
        current_user.last_chat_date = now
        db.commit()

    if not current_user.is_subscription_active:
        if current_user.daily_chat_count >= 10:
            raise HTTPException(
                status_code=402,
                detail="Daily chat limit reached. Please upgrade to Pro for unlimited access."
            )
        current_user.daily_chat_count += 1
        db.commit()

    # 1. Circuit Breaker — cek krisis SEBELUM Agent 1 merespons, agar resource
    #    darurat muncul di respons yang SAMA, bukan menunggu giliran berikutnya.
    crisis = check_crisis(req.message)

    # 2. Ambil memori relevan + directive Agent 2 yang belum dipakai
    # limit=10 dibagi dua-tingkat (get_memories): fakta inti + konteks semantik
    memories = get_memories(
        user_id=str(current_user.id),
        agent_id=req.agent_id,
        query=req.message,
        limit=10,
    )
    memory_context = format_memories_for_prompt(memories)

    directive = get_pending_directive(db, session_id)
    if directive:
        memory_context = (
            (memory_context + "\n\n" if memory_context else "")
            + f"[Catatan tambahan untuk direspons]: {directive}"
        )

    # 3. Siapkan conversation history
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in req.conversation_history
    ]

    # Siapkan agent configuration (system prompt & voice)
    agent_config = get_agent_config(req.agent_id)

    logger.info(f"[Chat] user={current_user.id} mood={req.mood} entry_type={req.entry_type}")

    # 4. Chat dengan Groq
    ai_result = chat_with_vtuber(
        user_message=req.message,
        conversation_history=history,
        system_prompt=agent_config["system_prompt"],
        user_name=current_user.username,
        memories_context=memory_context if memory_context else None,
        mood=req.mood,
        language=req.language,
    )

    reply = ai_result.get("reply", "")

    # 5. Simpan ke Mem0 + jalankan Agent 2 via CELERY 🚀
    save_memory_task.delay(
        user_message=req.message,
        reply=reply,
        user_id=str(current_user.id),
        agent_id=req.agent_id,
        session_id=session_id,
    )
    run_agent2_task.delay(
        user_message=req.message,
        reply=reply,
        user_id=str(current_user.id),
        session_id=session_id,
        crisis_detected=crisis is not None,
    )

    # 6. Update chat session di DB
    _update_chat_session(
        db=db,
        user_id=current_user.id,
        session_id=session_id,
        title=req.message,
        tokens_total=ai_result.get("tokens_used", {}).get("total", 0),
    )

    # 7. Build response
    response = {
        "success":    ai_result["success"],
        "reply":      reply,
        "session_id": session_id,
        "tokens_used": ai_result.get("tokens_used", {}),
        "memories_used": len(memories),
        "audio":      None,
        "crisis":     crisis["resources"] if crisis else None,
    }

    # 8. (Optional) TTS
    if req.with_tts and reply:
        # Gunakan voice_id dari request jika ada, jika tidak gunakan default agent
        target_voice = req.voice_id or agent_config["default_voice_id"]
        voice_settings = agent_config.get("voice_settings", {})
        
        tts_result = text_to_speech(
            text=reply,
            voice_id=target_voice,
            stability=voice_settings.get("stability"),
            similarity_boost=voice_settings.get("similarity_boost"),
            style=voice_settings.get("style"),
            use_speaker_boost=voice_settings.get("use_speaker_boost", True),
        )
        if tts_result and tts_result.get("success"):
            response["audio"] = {
                "audio_base64": tts_result["audio_base64"],
                "format":       tts_result["format"],
                "audio_size_kb": tts_result["audio_size_kb"],
            }

    return response


# ============================================================
# Streaming Chat Route (SSE — Server-Sent Events)
# ============================================================

@router.post("/chat/stream")
@limiter.limit("30/minute")
def chat_stream(
    request: Request,
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Streaming chat dengan VTuber AI via Server-Sent Events (SSE).

    Mengirim respons per-chunk sehingga user melihat teks muncul
    secara real-time, tanpa menunggu seluruh respons selesai.

    SSE Events:
    - data: {"type": "crisis", "resources": [...]}          — krisis terdeteksi (Circuit Breaker)
    - data: {"type": "chunk", "content": "..."}   — teks per chunk
    - data: {"type": "done", "session_id": "...", "full_reply": "..."}  — selesai
    - data: {"type": "error", "message": "..."}   — error
    """
    session_id = req.session_id or str(uuid.uuid4())
    
    # 0. Check Quota for Free Users
    now = datetime.now(timezone.utc)
    # Reset quota if it's a new day
    if not current_user.last_chat_date or current_user.last_chat_date.date() != now.date():
        current_user.daily_chat_count = 0
        current_user.last_chat_date = now
        db.commit()

    if not current_user.is_subscription_active:
        if current_user.daily_chat_count >= 10:
            raise HTTPException(
                status_code=402,
                detail="Daily chat limit reached. Please upgrade to Pro for unlimited access."
            )
        current_user.daily_chat_count += 1
        db.commit()
    # Circuit Breaker — cek krisis SEBELUM streaming dimulai
    crisis = check_crisis(req.message)

    # Ambil memori relevan + directive Agent 2 yang belum dipakai
    # limit=10 dibagi dua-tingkat (get_memories): fakta inti + konteks semantik
    memories = get_memories(
        user_id=str(current_user.id),
        agent_id=req.agent_id,
        query=req.message,
        limit=10,
    )
    memory_context = format_memories_for_prompt(memories)

    directive = get_pending_directive(db, session_id)
    if directive:
        memory_context = (
            (memory_context + "\n\n" if memory_context else "")
            + f"[Catatan tambahan untuk direspons]: {directive}"
        )

    # Siapkan conversation history
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in req.conversation_history
    ]

    # Siapkan agent configuration (system prompt & voice)
    agent_config = get_agent_config(req.agent_id)

    target_voice = req.voice_id or agent_config["default_voice_id"]
    voice_settings = agent_config.get("voice_settings", {})
    stability = voice_settings.get("stability")
    similarity_boost = voice_settings.get("similarity_boost")
    style = voice_settings.get("style")
    use_speaker_boost = voice_settings.get("use_speaker_boost", True)

    logger.info(f"[ChatStream] user={current_user.id} mood={req.mood} entry_type={req.entry_type}")

    def _synthesize_sentence(sentence: str):
        """Sintesis satu kalimat ke audio SSE event. None jika gagal/kosong."""
        if not sentence:
            return None
        try:
            tts_res = text_to_speech(
                text=sentence,
                voice_id=target_voice,
                stability=stability,
                similarity_boost=similarity_boost,
                style=style,
                use_speaker_boost=use_speaker_boost,
            )
        except Exception as e:
            logger.error(f"TTS gagal untuk kalimat {sentence!r}: {e}")
            return None
        if tts_res and tts_res.get("success"):
            audio_data = json.dumps({
                "type": "audio",
                "audio_base64": tts_res["audio_base64"]
            })
            return f"data: {audio_data}\n\n"
        return None

    def event_generator():
        """Generator SSE events."""
        full_reply = ""
        sentence_buffer = ""
        try:
            # Krisis terdeteksi — kirim resource darurat SEGERA, sebelum chunk lain
            if crisis:
                crisis_data = json.dumps({"type": "crisis", "resources": crisis["resources"]})
                yield f"data: {crisis_data}\n\n"

            for chunk in chat_with_vtuber_stream(
                user_message=req.message,
                conversation_history=history,
                system_prompt=agent_config["system_prompt"],
                user_name=current_user.username,
                memories_context=memory_context if memory_context else None,
                mood=req.mood,
                language=req.language,
            ):
                full_reply += chunk
                event_data = json.dumps({"type": "chunk", "content": chunk})
                yield f"data: {event_data}\n\n"

                # Sintesis TTS per-kalimat begitu kalimat lengkap terbentuk —
                # supaya audio pertama keluar jauh lebih cepat daripada menunggu
                # seluruh balasan LLM selesai.
                if req.with_tts:
                    sentence_buffer += chunk
                    ready_sentences, sentence_buffer = extract_ready_sentences(sentence_buffer)
                    for sentence in ready_sentences:
                        audio_event = _synthesize_sentence(sentence)
                        if audio_event:
                            yield audio_event

            # Streaming selesai — kirim event done
            done_data = json.dumps({
                "type": "done",
                "session_id": session_id,
                "full_reply": full_reply,
                "memories_used": len(memories),
            })
            yield f"data: {done_data}\n\n"

            # Sisa buffer yang belum sempat diakhiri tanda baca (mis. balasan
            # diakhiri "..." atau emoji) tetap perlu disuarakan.
            if req.with_tts and sentence_buffer.strip():
                audio_event = _synthesize_sentence(sentence_buffer.strip())
                if audio_event:
                    yield audio_event

            # Background: simpan memory, jalankan Agent 2 via CELERY
            save_memory_task.delay(
                user_message=req.message,
                reply=full_reply,
                user_id=str(current_user.id),
                agent_id=req.agent_id,
                session_id=session_id,
            )
            run_agent2_task.delay(
                user_message=req.message,
                reply=full_reply,
                user_id=str(current_user.id),
                session_id=session_id,
                crisis_detected=crisis is not None,
            )
            _update_chat_session(
                db=db,
                user_id=current_user.id,
                session_id=session_id,
                title=req.message,
            )

        except Exception as e:
            error_data = json.dumps({
                "type": "error",
                "message": f"Kyaa~! Ada masalah teknis: {str(e)}"
            })
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx: jangan buffer SSE
        },
    )


# ============================================================
# TTS Route
# ============================================================

@router.post("/tts")
def tts_synthesis(
    req: TTSRequest,
    current_user: models.User = Depends(get_current_user),
):
    """
    Text-to-Speech synthesis menggunakan ElevenLabs.
    Return audio sebagai base64 string.
    Butuh subscription aktif (minimal Pro untuk TTS).
    """
    # Cek apakah paket user support TTS (DIBEBASKAN SEMENTARA)
    # if current_user.subscription_package == models.SubscriptionPackage.BASIC:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Text-to-Speech hanya tersedia untuk paket Pro dan Ultimate. "
    #                "Upgrade subscription kamu!"
    #     )

    result = text_to_speech(
        text=req.text,
        voice_id=req.voice_id,
        stability=req.stability,
        similarity_boost=req.similarity_boost,
    )

    if not result or not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"TTS gagal: {result.get('error', 'Unknown error')}"
        )

    return {
        "success":      True,
        "audio_base64": result["audio_base64"],
        "format":       result["format"],
        "voice_id":     result["voice_id"],
        "audio_size_kb": result["audio_size_kb"],
    }


# ============================================================
# Voice & Models Routes
# ============================================================

@router.get("/voices")
def list_voices():
    """
    List suara ElevenLabs yang tersedia.
    Tidak butuh login — info publik.
    """
    voices = get_available_voices()
    return {"voices": voices, "total": len(voices)}


@router.get("/voices/{voice_id}/settings")
def get_voice_settings_route(voice_id: str):
    """Ambil settings default di dashboard ElevenLabs untuk voice_id ini."""
    res = get_voice_settings(voice_id)
    if not res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gagal mengambil settings: {res.get('error')}"
        )
    return res


@router.get("/models")
def list_models():
    """List model Groq yang tersedia."""
    return {
        "models": get_available_models(),
        "current_default": "llama3-70b-8192",
    }


# ============================================================
# Memory Routes
# ============================================================

@router.get("/memory/stats")
def get_my_memory_stats(
    agent_id: str = "emily",
    current_user: models.User = Depends(get_current_user),
):
    """Lihat berapa banyak memori yang tersimpan untuk akunmu."""
    stats = get_memory_stats(user_id=str(current_user.id), agent_id=agent_id)
    return stats


@router.get("/memory/summary")
def get_memory_summary(
    agent_id: str = "emily",
    current_user: models.User = Depends(get_current_user),
):
    """
    Dapatkan ringkasan profil/memori AI tentang user.
    """
    raw_memories = get_all_raw_memories(user_id=str(current_user.id), agent_id=agent_id)
    summary = summarize_user_profile(memories=raw_memories, agent_id=agent_id)
    
    return {
        "success": True,
        "summary": summary
    }


@router.delete("/memory")
def clear_my_memory(
    agent_id: str = "emily",
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Hapus semua memori percakapan.
    ⚠️ Tidak bisa di-undo! VTuber akan lupa semua tentang kamu.
    """
    success = clear_user_memory(user_id=str(current_user.id), agent_id=agent_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gagal menghapus memori. Coba lagi nanti."
        )

    # Reset chat sessions di DB
    db.query(models.ChatSession).filter(
        models.ChatSession.user_id == current_user.id
    ).update({"is_active": False})
    db.commit()

    return {
        "success": True,
        "message": "Semua memori berhasil dihapus. VTuber sekarang fresh start! 🌱"
    }


@router.get("/sessions")
def get_chat_sessions(
    limit: int = 10,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Riwayat sesi chat user."""
    sessions = (
        db.query(models.ChatSession)
        .filter(
            models.ChatSession.user_id == current_user.id,
            models.ChatSession.is_active == True,
        )
        .order_by(models.ChatSession.last_message_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "sessions": [
            {
                "session_id":   s.session_id,
                "title":        s.title,
                "message_count": s.message_count,
                "total_tokens": s.total_tokens,
                "last_message_at": s.last_message_at,
                "created_at":   s.created_at,
            }
            for s in sessions
        ]
    }
