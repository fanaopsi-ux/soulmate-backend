from app.celery_app import celery_app
from app.services.mem0_service import add_memory
from app.services.agent2.screener import run_agent2
import logging

logger = logging.getLogger(__name__)

@celery_app.task(name="save_memory_task", bind=True, max_retries=3)
def save_memory_task(self, user_message: str, reply: str, user_id: str, agent_id: str, session_id: str):
    messages_to_save = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": reply},
    ]
    success = add_memory(
        messages=messages_to_save,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    if not success:
        logger.warning(f"Mem0 add_memory gagal untuk user {user_id}, retrying...")
        raise self.retry(countdown=5)

@celery_app.task(name="run_agent2_task")
def run_agent2_task(user_message: str, reply: str, user_id: str, session_id: str, crisis_detected: bool):
    run_agent2(
        user_message=user_message,
        reply=reply,
        user_id=user_id,
        session_id=session_id,
        crisis_detected=crisis_detected
    )
