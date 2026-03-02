# ---------
# Genesis
# ---------

# Config
from genesis.config import client, supabase, async_supabase, get_async_supabase
from genesis.config import fetch_unread_emails_for_agency

### ==========
### Core
### ==========

# ---------- Utilities ----------
from .utils.redis_utils import emails_broker, prospects_broker, session_broker, recommendation_broker
from .utils.redis_utils import emails_stream
from .utils.redis_utils import RedisStreamHandler
from .utils.redis_utils import safe_redis_operation
from .utils.ai_utils import call_openai_safe
from .utils.instance_registry import InstanceRegistry
from .utils.general import generate_embeddings, generate_package_embedding, hash_identifier, get_smtp_service

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

# Agency
from modules.agency.crud import get_agency_by_id, get_agency_password, get_or_create_agency_email_stream, list_agencies

# Agent
from modules.agent.case_agent import CaseAgent, is_internal_agent_email

# Evaluation
from modules.ai.evaluation.eval import classify_sender

# Intent Guard/Analyzer
from modules.ai.intent_guard.engine import TravelIntentGuard

# Prospect
from modules.prospect.prospect import HandleProspect
from modules.prospect.crud import is_registered_prospect_by_email

# Session
from modules.session.session import Session
from modules.session.helpers.session_helper import SessionHelper

# Follow Up
from modules.followup.followup import realtime_followup_handler

# Assistant
from modules.ai.assistant.assistant import Assistant

# Recommendation
from modules.ai.recommendation.recommend import db_scanning, get_packages_schema
from modules.ai.recommendation.components.reference import ReferenceMemory
from modules.ai.recommendation.utils import format_packages

# Essence
from modules.ai.essence.compaction import compact_dialogue_state

# ----------
# Interface
# ----------
from interface.helpers.email_service import EmailService
from interface.helpers.smtp_service import SMTPService
