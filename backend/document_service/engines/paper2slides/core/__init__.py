"""
Core business logic for paper2slides
"""
from .paths import (
    get_base_dir,
    get_config_name,
    get_config_dir,
    get_rag_checkpoint,
    get_summary_checkpoint,
    get_summary_md,
    get_plan_checkpoint,
    get_output_dir,
)
from .state import (
    STAGES,
    load_state,
    save_state,
    create_state,
    detect_start_stage,
)


async def run_pipeline(*args, **kwargs):
    # The pipeline imports LightRAG and rendering extras. Status/download routes
    # must remain importable when only core dependencies are installed.
    from .pipeline import run_pipeline as execute

    return await execute(*args, **kwargs)


def list_outputs(*args, **kwargs):
    from .pipeline import list_outputs as execute

    return execute(*args, **kwargs)

__all__ = [
    # Path functions
    "get_base_dir",
    "get_config_name",
    "get_config_dir",
    "get_rag_checkpoint",
    "get_summary_checkpoint",
    "get_summary_md",
    "get_plan_checkpoint",
    "get_output_dir",
    # State management
    "STAGES",
    "load_state",
    "save_state",
    "create_state",
    "detect_start_stage",
    # Pipeline
    "run_pipeline",
    "list_outputs",
]

