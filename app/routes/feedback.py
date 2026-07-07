from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import Feedback, User
from app.services.auth_service import get_current_user_optional, get_current_user

router = APIRouter(tags=["Feedback"])

class FeedbackCreate(BaseModel):
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    occupation: str
    necessity: str
    rating: int
    feedback_text: str
    category: str

@router.post("")
def submit_feedback(
    feedback_data: FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    # 1. Create Feedback entry
    new_feedback = Feedback(
        user_id=current_user.id if current_user else None,
        name=feedback_data.name,
        age=feedback_data.age,
        gender=feedback_data.gender,
        occupation=feedback_data.occupation,
        necessity=feedback_data.necessity,
        rating=feedback_data.rating,
        feedback_text=feedback_data.feedback_text,
        category=feedback_data.category
    )
    db.add(new_feedback)

    # 2. Update user's has_provided_feedback if logged in
    if current_user:
        current_user.has_provided_feedback = True

    db.commit()
    db.refresh(new_feedback)

    return {"status": "success", "message": "Feedback submitted successfully"}
