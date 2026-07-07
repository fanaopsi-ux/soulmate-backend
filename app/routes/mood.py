from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app import models
from app.services.auth_service import get_current_user
from app.services.mem0_service import get_all_raw_memories
from app.services.groq_service import generate_personalized_affirmation

router = APIRouter()

def calculate_emotional_stats(assessments):
    """
    Calculate emotional score, stress level, and dominant positive days
    based on a list of ClinicalAssessment objects.
    """
    if not assessments:
        return None
        
    total_score = 0
    dominant_positive_days = 0
    high_stress_flags = 0
    
    positive_words = {"Bahagia", "Tenang", "Nyaman", "Senang", "Gembira", "Optimis", "Bersemangat"}
    
    for a in assessments:
        day_score = 50  # Base score
        
        # Positive factors
        alam = a.alam_perasaan if a.alam_perasaan else []
        adaptif = a.koping_adaptif if a.koping_adaptif else []
        maladaptif = a.koping_maladaptif if a.koping_maladaptif else []
        
        pos_traits = len(adaptif) + len([p for p in alam if p in positive_words])
        neg_traits = len(maladaptif) + len([p for p in alam if p not in positive_words])
        
        day_score += (pos_traits * 5) - (neg_traits * 5)
        
        if a.resiko_bunuh_diri_terdeteksi:
            day_score -= 20
            high_stress_flags += 1
            
        day_score = max(0, min(100, day_score))
        total_score += day_score
        
        if pos_traits > neg_traits and not a.resiko_bunuh_diri_terdeteksi:
            dominant_positive_days += 1

    avg_score = int(total_score / len(assessments))
    
    # Determine stress level
    stress_level = "Rendah"
    if high_stress_flags > 0 or avg_score < 40:
        stress_level = "Tinggi"
    elif avg_score < 70:
        stress_level = "Sedang"
        
    return {
        "emotional_score": avg_score,
        "dominant_positive_days": dominant_positive_days,
        "stress_level": stress_level
    }


def generate_mock_chart_data(base_score):
    """
    Generate 7 data points (e.g. for the last 7 months or days) ending in base_score
    so the chart isn't empty.
    """
    import random
    data = []
    current = base_score
    for _ in range(6):
        data.insert(0, current)
        current = max(0, min(100, current + random.randint(-15, 15)))
    data.append(base_score)
    return data


@router.get("/overview")
def get_mood_overview(
    agent_id: str = "emily",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Get mood tracker overview data for the authenticated user.
    """
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    # 1. Fetch structured assessments
    recent_assessments = (
        db.query(models.ClinicalAssessment)
        .filter(
            models.ClinicalAssessment.user_id == current_user.id,
            models.ClinicalAssessment.created_at >= thirty_days_ago
        )
        .order_by(desc(models.ClinicalAssessment.created_at))
        .all()
    )
    
    # 2. Calculate stats
    stats = calculate_emotional_stats(recent_assessments)
    
    # 3. Fetch Mem0 memories
    memories = get_all_raw_memories(user_id=str(current_user.id), agent_id=agent_id)
    
    # If no structured data, use fallback defaults
    if not stats:
        stats = {
            "emotional_score": 75, # Default optimistic score
            "dominant_positive_days": 0,
            "stress_level": "Normal"
        }
    
    # 4. Generate Chart Data (dummy historical for now, anchored on current score)
    chart_data = generate_mock_chart_data(stats["emotional_score"])
    
    # 5. Generate Personalized Affirmation
    affirmation = generate_personalized_affirmation(memories, agent_id)
    
    return {
        "success": True,
        "data": {
            "emotional_score": stats["emotional_score"],
            "dominant_positive_days": stats["dominant_positive_days"],
            "stress_level": stats["stress_level"],
            "chart_data": chart_data,
            "affirmation": affirmation,
            "agent_name": agent_id.capitalize(),
            "date_label": datetime.now().strftime("%d/%m")
        }
    }
