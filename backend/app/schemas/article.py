from datetime import datetime

from pydantic import BaseModel


class ArticleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    source: str
    title: str | None
    content: str
    url: str | None
    timestamp: datetime
