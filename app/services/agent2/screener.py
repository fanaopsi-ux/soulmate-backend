"""
Agent 2 -- Background Clinical Screener.
Berjalan ASYNC (FastAPI BackgroundTasks) setelah Agent 1 merespons.
Tugas: ekstraksi klinis, simpan ke DB, dan trigger RAG jika perlu.
"""

import logging
from typing import Optional

from app.database import SessionLocal
from app import models
from app.services.groq_service import extract_clinical_json, _get_groq_client
from app.services.rag.prompts import RAG_TRIGGER_PROMPT
from app.services.rag.retrieve import generate_rag_answer

logger = logging.getLogger(__name__)


def _should_trigger_rag(transcript: str) -> bool:
    """Router cepat: apakah turn ini butuh RAG untuk directive giliran berikutnya?"""
    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": RAG_TRIGGER_PROMPT.format(transcript=transcript),
            }],
            temperature=0.0,
            max_tokens=5,
        )
        decision = response.choices[0].message.content.strip().lower()
        return "yes" in decision
    except Exception as e:
        logger.error(f"[Agent2] RAG trigger check failed: {e}")
        return False


def run_agent2(
    user_message: str,
    reply: str,
    user_id: str,
    session_id: str,
    crisis_detected: bool = False,
) -> None:
    """
    Entry point Agent 2 -- dipanggil via BackgroundTasks setelah Agent 1 membalas.
    Tidak boleh pernah melempar exception ke caller -- semua error di-log saja,
    supaya kegagalan Agent 2 tidak pernah mempengaruhi respons ke user.
    """
    transcript = f"User: {user_message}\nAssistant: {reply}"
    db = SessionLocal()
    try:
        clinical_data = extract_clinical_json(transcript)

        assessment = models.ClinicalAssessment(
            user_id=int(user_id),
            session_id=session_id,
            alam_perasaan=clinical_data["alam_perasaan"],
            interaksi_selama_wawancara=clinical_data["interaksi_selama_wawancara"],
            persepsi_halusinasi_jenis=clinical_data["persepsi_halusinasi_jenis"],
            isi_pikir=clinical_data["isi_pikir"],
            koping_adaptif=clinical_data["koping_adaptif"],
            koping_maladaptif=clinical_data["koping_maladaptif"],
            hubungan_sosial=clinical_data["hubungan_sosial"],
            konsep_diri=clinical_data["konsep_diri"],
            resiko_bunuh_diri_terdeteksi=crisis_detected,
            catatan_klinis_a2=clinical_data["catatan_klinis_a2"],
        )
        db.add(assessment)
        db.commit()

        if _should_trigger_rag(transcript):
            directive_text = generate_rag_answer(user_message)
            directive = models.A2Directive(
                session_id=session_id,
                directive=directive_text,
                is_used=False,
            )
            db.add(directive)
            db.commit()

    except Exception as e:
        logger.error(f"[Agent2] Screener pipeline failed for session={session_id}: {e}")
    finally:
        db.close()


def get_pending_directive(db, session_id: str) -> Optional[str]:
    """
    Ambil directive Agent 2 yang belum dipakai untuk sesi ini, lalu tandai sebagai used.
    Dipanggil oleh route SEBELUM membangun context Agent 1 (pakai request-scoped db session).
    """
    directive_row = (
        db.query(models.A2Directive)
        .filter(
            models.A2Directive.session_id == session_id,
            models.A2Directive.is_used == False,  # noqa: E712
        )
        .order_by(models.A2Directive.created_at.desc())
        .first()
    )
    if not directive_row:
        return None

    directive_text = directive_row.directive
    directive_row.is_used = True
    db.commit()
    return directive_text
