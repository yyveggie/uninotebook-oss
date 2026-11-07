"""
Route registration

The following routes are not registered, because the corresponding features don't apply to a
single-user local install:

- payment / credits  : nothing is metered — you use your own API keys
- admin / stats      : there are no other users to administer
- contact            : there's no service provider to contact
- issues             : a report stored in your own local database would never be seen
"""
from fastapi import FastAPI

from .auth import router as auth_router
from .document import router as document_router
from .chat import router as chat_router
from .generation import router as generation_router
from .interview import router as interview_router
from .notes import router as notes_router
from .prompt_templates import router as prompt_templates_router
from .editor import router as editor_router
from .system import router as system_router
from .polish import router as polish_router
from .bootstrap import router as bootstrap_router
from .sources import router as sources_router


def register_routers(app: FastAPI) -> None:
    """Register all business routes on the FastAPI app"""
    app.include_router(auth_router)
    app.include_router(document_router)
    app.include_router(chat_router)
    app.include_router(generation_router)
    app.include_router(interview_router)
    app.include_router(notes_router)
    app.include_router(prompt_templates_router)
    app.include_router(editor_router)
    app.include_router(system_router)
    app.include_router(polish_router)
    app.include_router(bootstrap_router)
    app.include_router(sources_router)
