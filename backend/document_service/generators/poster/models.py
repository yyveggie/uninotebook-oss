from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

class PosterCreate(BaseModel):
    document_id: Optional[int] = None
    title: str = "Academic Poster"
    style: str = "academic"
    
class PosterResponse(BaseModel):
    id: int
    title: str
    status: str
    progress: int
    from pydantic import Field
    poster_url: Optional[str] = Field(default=None, validation_alias="poster_file_path", serialization_alias="poster_url")
    preview_url: Optional[str] = Field(default=None, validation_alias="preview_image", serialization_alias="preview_url")
    error_message: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True
