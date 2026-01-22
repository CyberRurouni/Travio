# Building Travio: A SaaS Solution for 24/7 Travel Lead Management with AI

![Banner Image](./repotoblog-assets/47d599e729aba81d316cb087becd08916124f020.jpeg)

## Introduction 🌐

The travel industry is highly competitive, and 24/7 lead management is crucial for success. Travio is a SaaS solution designed to help travel agencies manage leads effectively, leveraging AI to ensure instant responses and nurturing leads around the clock.

### What Inspired Travio?

Travio was born from the need for a seamless, AI-driven lead management system. The inspiration came from observing the challenges travel agencies face in maintaining 24/7 availability and responding promptly to inquiries.

### The Problem: Overnight Lead Loss

Travel agencies often miss out on potential clients due to overnight lead loss. If a lead is not responded to within the first hour, the chances of conversion drop significantly.

## The Vision: Real-Time AI-Powered Lead Management

Travio's vision is to provide a real-time, AI-powered system that ensures leads are responded to instantly, even outside business hours.

### Key Features

- **24/7 Availability**: Ensures leads are never missed.
- **AI-Powered Responses**: Automatically responds to inquiries based on predefined templates.
- **Lead Nurturing**: Continuously engages with leads to build relationships.

## Architecture Overview

Travio's architecture is designed for scalability and reliability, with a focus on real-time data processing and AI integration.

### Core Components

- **Database Layer**: PostgreSQL with Supabase for real-time data synchronization.
- **AI Layer**: Integration with Gemini for natural language processing.
- **Real-Time Layer**: Redis for real-time presence tracking and follow-ups.

## Key Technologies: The Stack Behind Travio

### Why FastAPI?

FastAPI was chosen for its speed and ease of use. It's perfect for building RESTful APIs quickly.

```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Welcome to Travio API"}
```

### PostgreSQL with Supabase

PostgreSQL was selected for its reliability and performance. Supabase adds real-time capabilities and ease of use.

```python
from supabase import create_client

supabase = create_client("your-supabase-url", "your-anon-key")
```

## Building the Database: Schema and Relationships

The database schema is designed to handle prospects, sessions, and AI interactions efficiently.

### Database Schema

```sql
CREATE TABLE prospects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT,
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### Relationships

- **Prospects**: Stores information about each prospect.
- **Sessions**: Tracks interactions and sessions.
- **AI Interactions**: Logs AI-generated responses and follow-ups.

## AI Integration: Intent Guard and Response Generation

Travio uses AI to detect intent and generate responses, ensuring leads are engaged with relevant content.

### Intent Detection

```python
def extract_constraints(body: str, model="gemini-2.5-flash-lite") -> Dict[str, Any]:
    prompt = f"""
    You are a travel agent AI assistant. Extract all travel-related constraints mentioned in the email.
    Focus on: dates, destination, budget, number of travelers, travel style, special requirements.
    Return JSON:
    {{
        "constraints": {{
            "budget": "string or null",
            "dates": "string or null",
            "destination": "string or null",
            "travelers_count": "int or null",
            "travel_style": "luxury / budget / adventure / family / other",
            "special_requirements": ["list any other constraints"]
        }}
    }}
    """
    result = call_openai_safe(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        max_tokens=400,
        response_format="json",
        fallback_response={
            "constraints": {
                "budget": None,
                "dates": None,
                "destination": None,
                "travelers_count": None,
                "travel_style": None,
                "special_requirements": []
            }
        }
    )
    return result
```

### Response Generation

```python
def generate_ai_response(
    chat_container: list,
    intent_guard_data: Dict[str, Any],
    model: str = "gemini-2.5-flash-lite",
) -> Dict[str, Any]:
    prompt = f"""
    You are an AI Travel Assistant. Your task is to generate a response based on the conversation context and intent guard data.
    """
    result = call_openai_safe(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        max_tokens=600,
        response_format="json",
        fallback_response={
            "message": "Sorry, I could not process this request at the moment.",
            "actions": {
                "type": "general_response",
                "details": {"reason": "fallback"}
            }
        }
    )
    return result
```

## Real-Time Follow-Up System

Travio's real-time system ensures that leads are followed up on promptly, even if they go silent for a few days.

### Presence Tracking

```python
async def upsert_prospect_presence(
    prospect_id: UUID,
    channel_type: str,
    raw_identifier: str
):
    try:
        payload = {
            "p_prospect_id": str(prospect_id),
            "p_channel_type": channel_type.lower(),
            "p_raw_identifier": raw_identifier,
        }
        result = await db_rpc("upsert_prospect_presence", params=payload)
        logger.info(
            "✅ Prospect presence upserted | Prospect ID=%s | Channel=%s",
            prospect_id,
            channel_type,
        )
        return result
    except Exception as e:
        logger.error("❌ Error upserting prospect presence: %s", str(e))
        raise
```

### Follow-Up Automation

Travio's follow-up system is designed to engage leads even when they go silent. The system tracks prospect activity and triggers automated follow-ups based on predefined rules.

```python
async def trigger_follow_up(prospect_id: UUID):
    try:
        # Fetch prospect data
        prospect = await get_prospect(prospect_id)
        
        # Generate follow-up message
        follow_up_message = await generate_ai_response(
            chat_container=[{"role": "user", "content": "Send a follow-up message"}],
            intent_guard_data={}
        )
        
        # Send message
        await send_message(
            prospect_id=prospect_id,
            message=follow_up_message["message"]
        )
        
        logger.info(
            "✅ Follow-up triggered | Prospect ID=%s",
            prospect_id
        )
    except Exception as e:
        logger.error("❌ Error triggering follow-up: %s", str(e))
        raise
```

### Silent Lead Engagement

When leads go silent, Travio uses a combination of AI and behavioral analysis to re-engage them. The system analyzes past interactions and sends personalized messages to reignite interest.

```python
async def reengage_silent_lead(prospect_id: UUID):
    try:
        # Analyze past interactions
        interaction_history = await get_interaction_history(prospect_id)
        
        # Generate re-engagement message
        re_engagement_message = await generate_ai_response(
            chat_container=[
                {"role": "user", "content": "Generate a re-engagement message"}
            ],
            intent_guard_data={}
        )
        
        # Send message
        await send_message(
            prospect_id=prospect_id,
            message=re_engagement_message["message"]
        )
        
        logger.info(
            "✅ Silent lead re-engaged | Prospect ID=%s",
            prospect_id
        )
    except Exception as e:
        logger.error("❌ Error re-engaging silent lead: %s", str(e))
        raise
```

## Conclusion

Travio is a comprehensive SaaS solution that addresses the challenges of 24/7 lead management in the travel industry. By leveraging AI, real-time data processing, and a robust database system, Travio ensures that travel agencies never miss a lead and maintain continuous engagement with potential clients. The platform's architecture, built on FastAPI, PostgreSQL with Supabase, and Redis, ensures scalability and reliability. With Travio, travel agencies can focus on building relationships with leads while the platform handles the rest.