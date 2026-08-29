from pydantic import BaseModel


class AnswerIn(BaseModel):
    session_id: str
    problem_id: str
    selected_index: int


class AdRequestIn(BaseModel):
    purpose: str  # 'diamond' | 'continue' | 'multiplier' | 'daily_double'
    session_id: str | None = None


class StarsRequestIn(BaseModel):
    pass  # user comes from the validated initData, nothing else needed


class TaskVerifyIn(BaseModel):
    task_id: int


class BackgroundActionIn(BaseModel):
    background_id: int
