# ---------
# Genesis
# ---------

# Config
from genesis.config import client, supabase, async_supabase, get_async_supabase
from genesis.config import fetch_unread_emails

### ==========
### Core
### ==========

# ---------- Utilities ----------
from .utils.redis_utils import emails_broker, prospects_broker, session_broker
from .utils.redis_utils import emails_stream
from .utils.redis_utils import safe_redis_operation
from .utils.ai_utils import call_openai_safe
from .utils.instance_registry import InstanceRegistry
from .utils.general import generate_embeddings, populate_embeddings

# ---------- Services ----------
from .services.crud import fetch_table_schema, run_sql_query

# ---------- DB --------------
from .db.crud import (
    db_insert,
    db_upsert,
    db_select,
    db_update,
    db_delete,
    db_count,
    db_rpc,
)

# ---------
# Modules
# ---------

# Evaluation
from modules.ai.evaluation.eval import classify_sender
from modules.ai.evaluation.re_eval import send_clarification_email

# Intent Guard/Analyzer
from modules.ai.intent_guard.engine import TravelIntentGuard

# Session
from modules.prospect.prospect import HandleProspect
from modules.session.session import Session

# Follow Up
from modules.followup.followup import realtime_followup_handler

# Assistant
from modules.ai.assistant.assistant import Assistant

# Recommendation
from modules.ai.recommendation.recommend import db_scanning, get_packages_schema
from modules.ai.recommendation.components.reference import ReferenceMemory

# ----------
# Interface
# ----------
from interface.helpers.email_service import EmailService
from interface.helpers.smtp_service import SMTPService
