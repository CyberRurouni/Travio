import asyncio
import logging
from typing import Optional, List

from core import (
    db_insert,
    db_update,
    db_select,
    db_rpc,
    RedisStreamHandler,
    emails_broker,
)

logger = logging.getLogger("AGENCY_SERVICE")


# =========================================================
# 🔐 VAULT HELPERS
# =========================================================
async def store_agency_password(agency_id: str, app_password: str) -> Optional[str]:
    """
    Store agency password in Vault using the PUBLIC wrapper function.
    """
    try:
        logger.info(f"🔐 Creating Vault secret for agency={agency_id}")

        secret_id = await db_rpc(
            "create_vault_secret",
            {
                "p_secret": app_password,
                "p_name": f"agency_password_{agency_id}",
                "p_description": f"Password for agency {agency_id}",
                "p_key_id": None,  # Using default key
            },
        )

        if not secret_id:
            logger.error("❌ Vault secret creation returned empty result")
            return None

        # Handle list vs string response from rpc
        actual_id = secret_id[0] if isinstance(secret_id, list) else secret_id

        try:
            logger.info(f"🔗 Linking Vault secret to agency={agency_id}")
            update_result = await db_update(
                table="agencies",
                updates={"vault_secret_id": str(actual_id)},
                filters={"id": agency_id},
            )
        except Exception as e:
            logger.exception(f"💥 Failed to update agency with vault_secret_id: {e}")
            return None

        return str(actual_id)

    except Exception as e:
        logger.exception(f"💥 store_agency_password failed: {e}")
        return None


async def get_agency_password(key: dict | str) -> Optional[str]:
    """
    Retrieve decrypted password using the PUBLIC wrapper function.
    """
    try:
        if isinstance(key, str):
            # If key is a string, assume it's an agency_id
            agency = await get_agency_by_id(key)
        else:
            # If key is a dict, assume it's the agency record
            agency = key

        if not agency:
            logger.warning("⚠️ Could not retrieve agency details")
            return None

        secret_id = agency.get("vault_secret_id")

        if not secret_id:
            logger.warning(f"⚠️ Agency {agency.get('id')} has no vault_secret_id")
            return None

        logger.info(f"🔓 Fetching Vault secret for agency={agency.get('id')}")

        result = await db_rpc(
            "get_vault_secret",
            {"p_secret_id": secret_id},
        )

        if not result or not isinstance(result, list):
            logger.error("❌ Vault returned invalid response")
            return None

        # result is a list of rows: [{'decrypted_secret': '...'}]
        password = result[0].get("decrypted_secret")

        if not password:
            logger.error("❌ Decrypted secret missing in Vault response")
            return None

        logger.info(f"✅ Password retrieved for agency={agency.get('id')}")

        return password

    except Exception as e:
        logger.exception(f"💥 get_agency_password failed: {e}")
        return None


# =========================================================
# 🏢 AGENCY CRUD
# =========================================================


async def create_agency(
    name: str, issued_email: str, agent_email: str, app_password: str
) -> Optional[dict]:
    """
    Create new agency.
    Optionally store password in Vault.
    """
    try:
        logger.info(f"🏢 Creating agency → {name}")

        agency = await db_insert(
            table="agencies",
            data={
                "name": name,
                "issued_email": issued_email,
                "agent_email": agent_email,
            },
            return_mode="one",
        )

        if not agency:
            logger.error("❌ Agency insert failed")
            return None

        agency_id = agency.get("id")

        if not agency_id:
            logger.error("❌ Inserted agency missing ID")
            return None

        logger.info(f"✅ Agency created → id={agency_id}")

        # Store password in Vault and link to agency
        vault_id = await store_agency_password(agency_id, app_password.strip())

        if not vault_id:
            logger.error("❌ Vault storage failed after agency creation")
            return None

        return agency

    except Exception as e:
        logger.exception(f"💥 create_agency failed: {e}")
        return None


async def get_agency_by_id(agency_id: str) -> Optional[dict]:
    """
    Fetch agency by ID.
    """
    try:
        result = await db_select(
            table="agencies",
            filters={"id": agency_id},
            limit=1,
        )

        if not result:
            logger.warning(f"⚠️ Agency not found → id={agency_id}")
            return None

        return result[0]

    except Exception as e:
        logger.exception(f"💥 get_agency_by_id failed: {e}")
        return None


async def list_agencies() -> list[dict]:
    """
    List all agencies ordered by created_at DESC.
    """
    try:
        result = await db_select(
            table="agencies",
            order_by="created_at",
            desc=True,
        )

        return result or []

    except Exception as e:
        logger.exception(f"💥 list_agencies failed: {e}")
        return []


# =========================================================
# 📦 CREATE AGENCY PACKAGE
# =========================================================
async def create_agency_package(
    agency_id: str,
    name: str,
    description: Optional[str] = None,
    price_amount: Optional[float] = None,
    price_currency: str = "USD",
    pricing_model: Optional[str] = None,  # fixed | per_person | per_day | custom
    category: Optional[str] = None,
    destination: Optional[str] = None,
    duration_days: Optional[int] = None,
    ideal_for: Optional[List[str]] = None,
    includes: Optional[List[str]] = None,
    is_active: bool = True,
    is_custom: bool = False,
) -> Optional[dict]:
    """
    📦 Create a new agency package with embedding generated immediately.
    Returns the full package including embedding if successful.
    """

    try:
        from core import generate_package_embedding

        logger.info(f"📦 Creating package '{name}' for agency={agency_id}")

        # Basic validation
        if not agency_id:
            logger.error("❌ agency_id is required")
            return None

        if not name:
            logger.error("❌ package name is required")
            return None

        package_data = {
            "agency_id": agency_id,
            "name": name,
            "description": description,
            "price_amount": price_amount,
            "price_currency": price_currency,
            "pricing_model": pricing_model,
            "category": category,
            "destination": destination,
            "duration_days": duration_days,
            "ideal_for": ideal_for,
            "includes": includes,
            "is_active": is_active,
            "is_custom": is_custom,
        }

        # 1️⃣ Insert package
        package = await db_insert(
            table="agency_packages",
            data=package_data,
            return_mode="one",
        )

        if not package:
            logger.error("❌ Failed to insert agency package")
            return None

        # 2️⃣ Generate and store embedding
        await generate_package_embedding(package)

        # 3️⃣ Fetch updated package with embedding
        refreshed = await db_select(
            table="agency_packages",
            filters={"id": package["id"]},
            limit=1,
        )

        final_package = refreshed[0] if refreshed else package
        logger.info(f"✅ Package created successfully → id={final_package.get('id')}")
        return final_package

    except Exception as e:
        logger.exception(f"💥 create_agency_package failed: {e}")
        return None


# =========================================================
# ✏️ UPDATE AGENCY PACKAGE
# =========================================================


async def update_agency_package(
    package_id: str,
    updates: dict,
) -> Optional[dict]:
    """
    Update package fields.
    """

    try:
        if not package_id:
            logger.error("❌ package_id required")
            return None

        if not updates:
            logger.warning("⚠️ No updates provided")
            return None

        result = await db_update(
            table="agency_packages",
            updates=updates,
            filters={"id": package_id},
        )

        if not result:
            logger.error("❌ Package update failed")
            return None

        logger.info(f"✅ Package updated → id={package_id}")

        refreshed = await db_select(
            table="agency_packages",
            filters={"id": package_id},
            limit=1,
        )

        return refreshed[0] if refreshed else None

    except Exception as e:
        logger.exception(f"💥 update_agency_package failed: {e}")
        return None


# =========================================================
# 🚀 REDIS STREAM FOR AGENCY EMAILS
# =========================================================
def get_or_create_agency_email_stream(agency_id: str) -> Optional[RedisStreamHandler]:
    """
    Ensure the Redis stream and consumer group exist for the given agency and stream name.
    """

    try:
        stream_key = f"agency:{agency_id}:email_events"
        group_name = f"email_events_group"

        consumer = RedisStreamHandler(
            redis_broker=emails_broker,
            stream_key=stream_key,
            group_name=group_name,
        )
        logger.info(
            f"✅ Redis stream and group ensured for {stream_key} with group {group_name}"
        )
        return consumer

    except Exception as e:
        logger.exception(f"💥 get_or_create_redis_stream failed: {e}")
        return None


# =========================================================
# 🧪 CLI: SEED DUMMY DATA
# =========================================================
async def seed_agencies():
    """
    Insert dummy agencies for testing.
    """
    try:
        logger.info("🌱 Seeding dummy agencies...")

        await create_agency(
            name="Alpha Travel",
            issued_email="ahk3155263@gmail.com",
            agent_email="ahk3155262@gmail.com",
            app_password="fhcgozeenmbpnjbc",
        )

        await create_agency(
            name="Beta Voyages",
            issued_email="ahk3155264@gmail.com",
            agent_email="ahk3155263@gmail.com",
            app_password="pkvciqwfbkxnsghq",
        )

        logger.info("✅ Dummy agencies seeded successfully!")

    except Exception as e:
        logger.exception(f"💥 seed_agencies failed: {e}")


import asyncio


async def dummy_packages():
    """
    Insert dummy packages for testing.
    """
    try:
        logger.info("🌱 Seeding dummy packages...")

        agencies = await list_agencies()
        if not agencies:
            logger.warning("⚠️ No agencies found to seed packages for")
            return

        for agency in agencies:
            agency_id = agency.get("id")
            if not agency_id:
                continue

            await asyncio.gather(
                # --- Original 13 packages ---
                create_agency_package(
                    agency_id=agency_id,
                    name="Tropical Paradise",
                    description="7-day all-inclusive stay at a luxury resort in the Maldives.",
                    price_amount=4999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Luxury",
                    destination="Maldives",
                    duration_days=7,
                    ideal_for=["honeymooners", "luxury travelers"],
                    includes=[
                        "Round-trip airfare",
                        "5-star resort accommodation",
                        "All meals and drinks",
                        "Spa treatments",
                        "Water sports activities",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="European Explorer",
                    description="14-day tour across Europe's iconic cities and landmarks.",
                    price_amount=2999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Europe",
                    duration_days=14,
                    ideal_for=["first-time European travelers", "culture enthusiasts"],
                    includes=[
                        "Round-trip airfare",
                        "3-star hotel accommodation",
                        "Daily breakfast",
                        "Guided city tours",
                        "Museum entrance fees",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Adventure Awaits",
                    description="10-day adrenaline-packed adventure in New Zealand's wilderness.",
                    price_amount=3999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="New Zealand",
                    duration_days=10,
                    ideal_for=["adventure seekers", "nature lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Camping and lodge accommodation",
                        "All meals",
                        "Guided hiking and rafting tours",
                        "Equipment rental",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Family Fun Getaway",
                    description="5-day family-friendly vacation at a theme park resort in Orlando.",
                    price_amount=1999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Family",
                    destination="Orlando",
                    duration_days=5,
                    ideal_for=["families with kids", "theme park lovers"],
                    includes=[
                        "Round-trip airfare",
                        "4-star resort accommodation",
                        "Park hopper tickets to major theme parks",
                        "Daily breakfast",
                        "Character dining experience",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Budget Backpacker",
                    description="14-day budget-friendly backpacking trip through Southeast Asia.",
                    price_amount=999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Budget",
                    destination="Southeast Asia",
                    duration_days=14,
                    ideal_for=["backpackers", "budget travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Hostel accommodation",
                        "Local transportation pass",
                        "Guided city tours",
                        "Street food tasting experience",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Customizable Dream Trip",
                    description="Build your own dream vacation with our customizable package.",
                    price_amount=None,
                    price_currency="USD",
                    pricing_model="custom",
                    category="Custom",
                    destination="Varies",
                    duration_days=None,
                    ideal_for=["travelers who want a personalized experience"],
                    includes=[
                        "Personalized itinerary planning",
                        "Access to exclusive experiences",
                        "Dedicated travel consultant support",
                        "Flexible accommodation and activity options",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Weekend City Break",
                    description="3-day quick getaway to a vibrant city with guided tours and dining experiences.",
                    price_amount=799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="City Break",
                    destination="Varies",
                    duration_days=3,
                    ideal_for=["couples", "solo travelers", "friends"],
                    includes=[
                        "Round-trip airfare",
                        "4-star hotel accommodation",
                        "Guided city tours",
                        "Dining experiences at local restaurants",
                        "Public transportation pass",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Cultural Immersion Experience",
                    description="10-day deep dive into the culture, cuisine, and traditions of Japan.",
                    price_amount=3499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Japan",
                    duration_days=10,
                    ideal_for=["culture enthusiasts", "food lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Traditional ryokan accommodation",
                        "Guided cultural tours",
                        "Cooking classes with local chefs",
                        "Tickets to cultural performances",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Nature Retreat",
                    description="7-day peaceful retreat in the serene landscapes of Costa Rica.",
                    price_amount=2799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Nature",
                    destination="Costa Rica",
                    duration_days=7,
                    ideal_for=["nature lovers", "wellness travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Eco-lodge accommodation",
                        "Daily yoga and meditation sessions",
                        "Guided nature hikes",
                        "Organic meals prepared with local ingredients",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Luxury Safari Adventure",
                    description="10-day exclusive safari experience in the heart of Africa's wildlife reserves.",
                    price_amount=8999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Luxury",
                    destination="Africa",
                    duration_days=10,
                    ideal_for=["luxury travelers", "wildlife enthusiasts"],
                    includes=[
                        "Round-trip airfare",
                        "5-star lodge accommodation",
                        "Daily guided safari drives",
                        "Private wildlife photography sessions",
                        "Gourmet meals and fine wines",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Romantic Getaway",
                    description="5-day romantic escape to a picturesque destination with curated experiences for couples.",
                    price_amount=2999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Romantic",
                    destination="Varies",
                    duration_days=5,
                    ideal_for=["couples", "honeymooners"],
                    includes=[
                        "Round-trip airfare",
                        "Luxury hotel accommodation",
                        "Couples spa treatment",
                        "Private dining experience",
                        "Guided romantic city tours",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Solo Traveler's Escape",
                    description="7-day empowering solo travel experience with a mix of adventure, culture, and relaxation.",
                    price_amount=1999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Solo Travel",
                    destination="Varies",
                    duration_days=7,
                    ideal_for=["solo travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Single occupancy accommodation",
                        "Guided group activities to meet other travelers",
                        "Free time for personal exploration",
                        "Safety and support services for solo travelers",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Gastronomic Journey",
                    description="10-day culinary tour through the world's most renowned food destinations.",
                    price_amount=3999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Culinary",
                    destination="Varies",
                    duration_days=10,
                    ideal_for=["food lovers", "culinary enthusiasts"],
                    includes=[
                        "Round-trip airfare",
                        "4-star hotel accommodation",
                        "Guided food tours in each destination",
                        "Cooking classes with local chefs",
                        "Exclusive dining experiences at top restaurants",
                    ],
                ),
                # --- 50 New Packages ---
                create_agency_package(
                    agency_id=agency_id,
                    name="Santorini Sunset Escape",
                    description="6-day luxury retreat on the cliffs of Santorini with breathtaking caldera views.",
                    price_amount=3799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Romantic",
                    destination="Greece",
                    duration_days=6,
                    ideal_for=["couples", "honeymooners", "luxury travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Cliffside boutique hotel",
                        "Daily breakfast and dinner",
                        "Sunset sailing cruise",
                        "Wine tasting tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Amazon Rainforest Expedition",
                    description="8-day deep-jungle expedition through the Amazon with expert naturalist guides.",
                    price_amount=4299.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Brazil",
                    duration_days=8,
                    ideal_for=[
                        "adventure seekers",
                        "nature lovers",
                        "wildlife enthusiasts",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Jungle lodge accommodation",
                        "All meals",
                        "Guided wildlife tracking",
                        "River canoe excursions",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Moroccan Desert Odyssey",
                    description="9-day journey through Morocco's imperial cities and the Sahara Desert.",
                    price_amount=2599.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Morocco",
                    duration_days=9,
                    ideal_for=["culture enthusiasts", "adventure seekers"],
                    includes=[
                        "Round-trip airfare",
                        "Riad and desert camp accommodation",
                        "All meals",
                        "Camel trekking",
                        "Guided medina tours",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Alaskan Wilderness Trek",
                    description="7-day guided trek through Alaska's glaciers, fjords, and wildlife-rich parks.",
                    price_amount=4499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Alaska, USA",
                    duration_days=7,
                    ideal_for=["adventure seekers", "nature lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Lodge and glamping accommodation",
                        "All meals",
                        "Glacier hike",
                        "Whale watching excursion",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Tuscany Wine & Dine",
                    description="7-day indulgent tour of Tuscany's rolling vineyards, hilltop towns, and fine cuisine.",
                    price_amount=3299.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Culinary",
                    destination="Italy",
                    duration_days=7,
                    ideal_for=["food lovers", "couples", "luxury travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Farmhouse villa accommodation",
                        "Daily breakfast",
                        "Wine and olive oil tastings",
                        "Truffle hunting experience",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Iceland Northern Lights",
                    description="6-day winter adventure in Iceland chasing the aurora borealis.",
                    price_amount=3599.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Iceland",
                    duration_days=6,
                    ideal_for=[
                        "adventure seekers",
                        "couples",
                        "photography enthusiasts",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Cozy cabin accommodation",
                        "Daily breakfast",
                        "Northern lights tour",
                        "Blue Lagoon geothermal spa entry",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Bali Spirit & Wellness",
                    description="10-day holistic wellness retreat combining yoga, meditation, and Balinese healing arts.",
                    price_amount=2499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Wellness",
                    destination="Bali, Indonesia",
                    duration_days=10,
                    ideal_for=["wellness travelers", "solo travelers", "couples"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique villa accommodation",
                        "All meals (vegetarian)",
                        "Daily yoga and meditation",
                        "Traditional Balinese spa treatments",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Patagonia End of the World",
                    description="12-day trekking adventure through the dramatic landscapes of Chilean and Argentine Patagonia.",
                    price_amount=5499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Patagonia",
                    duration_days=12,
                    ideal_for=["adventure seekers", "hikers", "nature lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Mountain lodge accommodation",
                        "All meals",
                        "Guided Torres del Paine treks",
                        "Glacier excursion",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Tokyo Tech & Pop Culture",
                    description="7-day immersive tour of Tokyo's futuristic districts, anime culture, and neon nightlife.",
                    price_amount=2799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Japan",
                    duration_days=7,
                    ideal_for=[
                        "pop culture fans",
                        "solo travelers",
                        "tech enthusiasts",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Capsule and boutique hotel stays",
                        "IC transport card",
                        "Guided Akihabara and Shibuya tours",
                        "TeamLab digital art experience",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Machu Picchu & Sacred Valley",
                    description="8-day spiritual journey through Peru's Inca heritage, highland markets, and ancient citadels.",
                    price_amount=3199.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Peru",
                    duration_days=8,
                    ideal_for=[
                        "history buffs",
                        "adventure seekers",
                        "culture enthusiasts",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel and lodge accommodation",
                        "Daily breakfast",
                        "Inca Trail guided hike",
                        "Machu Picchu entrance fee",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Dubai Ultra-Luxury Experience",
                    description="5-day over-the-top luxury tour of Dubai's iconic landmarks, desert, and world-class shopping.",
                    price_amount=6999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Luxury",
                    destination="Dubai, UAE",
                    duration_days=5,
                    ideal_for=["luxury travelers", "shoppers", "couples"],
                    includes=[
                        "Business class airfare",
                        "7-star Burj Al Arab stay",
                        "Private yacht dinner",
                        "Desert dune bashing",
                        "Personal shopping concierge",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Scottish Highlands Road Trip",
                    description="8-day self-drive adventure through Scotland's castles, lochs, and misty highlands.",
                    price_amount=2199.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Scotland, UK",
                    duration_days=8,
                    ideal_for=["road trippers", "history buffs", "nature lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Castle B&Bs and boutique inns",
                        "Rental car with GPS",
                        "Whisky distillery tour",
                        "Loch Ness boat trip",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Caribbean Island Hopper",
                    description="10-day cruise and fly island-hopping adventure across the best Caribbean islands.",
                    price_amount=3899.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Beach",
                    destination="Caribbean",
                    duration_days=10,
                    ideal_for=["beach lovers", "couples", "families"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique island resorts",
                        "Daily breakfast",
                        "Snorkeling and diving excursions",
                        "Inter-island ferry passes",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Himalayan Base Camp Trek",
                    description="14-day guided trek to Everest Base Camp with acclimatization stops and sherpa support.",
                    price_amount=4799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Nepal",
                    duration_days=14,
                    ideal_for=["hikers", "adventure seekers", "bucket-list travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Teahouse accommodation",
                        "All meals on trek",
                        "Licensed sherpa guide",
                        "Trekking permits",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="South African Cape Explorer",
                    description="9-day journey through Cape Town, the Garden Route, and wine country.",
                    price_amount=3499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="South Africa",
                    duration_days=9,
                    ideal_for=["culture enthusiasts", "nature lovers", "foodies"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel accommodation",
                        "Daily breakfast",
                        "Cape Peninsula tour",
                        "Stellenbosch wine tasting",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Vietnam From North to South",
                    description="12-day journey from Hanoi to Ho Chi Minh City through Vietnam's most stunning landscapes.",
                    price_amount=2299.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Vietnam",
                    duration_days=12,
                    ideal_for=["backpackers", "culture enthusiasts", "foodies"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotels and homestays",
                        "Daily breakfast",
                        "Ha Long Bay cruise",
                        "Street food walking tours",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Mayan Ruins & Cenotes",
                    description="7-day exploration of Mexico's ancient Mayan ruins, crystal cenotes, and Caribbean coastline.",
                    price_amount=2099.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Mexico",
                    duration_days=7,
                    ideal_for=["history buffs", "adventure seekers", "beach lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel accommodation",
                        "Daily breakfast",
                        "Chichén Itzá guided tour",
                        "Cenote snorkeling experience",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Antarctica Expedition Cruise",
                    description="14-day once-in-a-lifetime expedition cruise to the Antarctic Peninsula.",
                    price_amount=12999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Antarctica",
                    duration_days=14,
                    ideal_for=[
                        "adventure seekers",
                        "wildlife enthusiasts",
                        "bucket-list travelers",
                    ],
                    includes=[
                        "Round-trip airfare to Ushuaia",
                        "Expedition ship cabin",
                        "All meals on board",
                        "Zodiac landings on ice",
                        "Expert naturalist lectures",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Amalfi Coast Luxury Cruise",
                    description="7-day private yacht cruise along the stunning Amalfi Coast and Capri.",
                    price_amount=7999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Luxury",
                    destination="Italy",
                    duration_days=7,
                    ideal_for=["luxury travelers", "couples", "honeymooners"],
                    includes=[
                        "Round-trip airfare",
                        "Private yacht with crew",
                        "All meals and beverages",
                        "Capri island excursion",
                        "Pompeii guided tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="New York City Immersion",
                    description="5-day action-packed NYC experience with Broadway, museums, and iconic neighborhoods.",
                    price_amount=1799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="City Break",
                    destination="New York, USA",
                    duration_days=5,
                    ideal_for=[
                        "first-time visitors",
                        "culture enthusiasts",
                        "families",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Midtown hotel accommodation",
                        "Broadway show tickets",
                        "Metropolitan Museum of Art entry",
                        "NYC hop-on hop-off bus pass",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Canadian Rockies Rail Journey",
                    description="8-day scenic rail journey through Banff, Jasper, and the Canadian Rockies.",
                    price_amount=4199.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Nature",
                    destination="Canada",
                    duration_days=8,
                    ideal_for=["nature lovers", "couples", "retirees"],
                    includes=[
                        "Round-trip airfare",
                        "Rocky Mountaineer train ticket",
                        "Mountain lodge accommodation",
                        "Daily breakfast",
                        "Lake Louise guided walk",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Israel & Jordan Holy Land Tour",
                    description="10-day pilgrimage and cultural tour through Jerusalem, Petra, and the Dead Sea.",
                    price_amount=3699.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Israel & Jordan",
                    duration_days=10,
                    ideal_for=[
                        "religious travelers",
                        "history buffs",
                        "culture enthusiasts",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "4-star hotel accommodation",
                        "Daily breakfast and dinner",
                        "Guided Jerusalem Old City tour",
                        "Petra entrance and guided walk",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Galápagos Islands Discovery",
                    description="10-day naturalist expedition to the Galápagos Islands with expert wildlife guides.",
                    price_amount=6499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Nature",
                    destination="Galápagos, Ecuador",
                    duration_days=10,
                    ideal_for=["wildlife enthusiasts", "nature lovers", "divers"],
                    includes=[
                        "Round-trip airfare",
                        "Liveaboard boat accommodation",
                        "All meals",
                        "Daily snorkeling and diving",
                        "Expert naturalist guides",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Oktoberfest & Bavaria Experience",
                    description="6-day German cultural adventure including Oktoberfest, Neuschwanstein Castle, and Alpine scenery.",
                    price_amount=2399.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Germany",
                    duration_days=6,
                    ideal_for=["culture enthusiasts", "festival goers", "friends"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel accommodation",
                        "Daily breakfast",
                        "Oktoberfest reserved tent seating",
                        "Neuschwanstein Castle guided tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Maldives Overwater Bungalow",
                    description="5-day ultra-romantic stay in a private overwater bungalow with crystal-clear lagoon views.",
                    price_amount=5999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Luxury",
                    destination="Maldives",
                    duration_days=5,
                    ideal_for=["honeymooners", "couples", "luxury travelers"],
                    includes=[
                        "Round-trip airfare with seaplane transfer",
                        "Overwater bungalow accommodation",
                        "All-inclusive meals and drinks",
                        "Couples snorkeling trip",
                        "Sunset dolphin cruise",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Thailand Full Experience",
                    description="12-day journey covering Bangkok's temples, Chiang Mai's jungle, and Phuket's beaches.",
                    price_amount=2699.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Thailand",
                    duration_days=12,
                    ideal_for=["backpackers", "culture enthusiasts", "beach lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Mix of guesthouses and boutique hotels",
                        "Daily breakfast",
                        "Elephant sanctuary visit",
                        "Island boat tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Route 66 Road Adventure",
                    description="14-day classic American road trip driving the historic Route 66 from Chicago to LA.",
                    price_amount=3299.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="USA",
                    duration_days=14,
                    ideal_for=["road trippers", "history buffs", "adventure seekers"],
                    includes=[
                        "One-way airfare",
                        "Rental car",
                        "Motel and boutique hotel stops",
                        "Route 66 travel guide",
                        "Grand Canyon detour excursion",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Portuguese Coast & Culture",
                    description="8-day journey through Lisbon, Porto, and the Algarve's golden beaches.",
                    price_amount=2199.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Portugal",
                    duration_days=8,
                    ideal_for=["culture enthusiasts", "foodies", "couples"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel accommodation",
                        "Daily breakfast",
                        "Fado music evening",
                        "Douro Valley wine tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Serengeti Migration Safari",
                    description="8-day luxury tented safari witnessing the Great Wildebeest Migration in Tanzania.",
                    price_amount=9499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Luxury",
                    destination="Tanzania",
                    duration_days=8,
                    ideal_for=[
                        "luxury travelers",
                        "wildlife enthusiasts",
                        "photographers",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Luxury tented camp",
                        "All meals and drinks",
                        "Twice-daily game drives",
                        "Hot air balloon safari",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Cuba Rhythms & Revolution",
                    description="7-day cultural immersion in Havana's colonial streets, salsa clubs, and vintage car culture.",
                    price_amount=1899.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Cuba",
                    duration_days=7,
                    ideal_for=["culture enthusiasts", "music lovers", "history buffs"],
                    includes=[
                        "Round-trip airfare",
                        "Casa particular accommodation",
                        "Daily breakfast",
                        "Salsa dance classes",
                        "Classic car city tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Swiss Alps Ski & Stay",
                    description="7-day premium ski holiday in the Swiss Alps with first-class chalet accommodation.",
                    price_amount=5299.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Switzerland",
                    duration_days=7,
                    ideal_for=["skiers", "snowboarders", "luxury travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Alpine chalet accommodation",
                        "Daily breakfast and dinner",
                        "7-day ski lift pass",
                        "Ski equipment rental",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Egypt Pharaohs & Pyramids",
                    description="8-day historical tour through Cairo, Luxor, and a Nile River cruise.",
                    price_amount=2899.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Egypt",
                    duration_days=8,
                    ideal_for=["history buffs", "culture enthusiasts", "families"],
                    includes=[
                        "Round-trip airfare",
                        "4-star hotels and Nile cruise cabin",
                        "All meals on cruise",
                        "Pyramids and Sphinx guided tour",
                        "Valley of the Kings excursion",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Surf & Turf Costa Rica",
                    description="8-day surf and adventure trip combining Pacific beach waves with rainforest zip-lining.",
                    price_amount=2399.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Costa Rica",
                    duration_days=8,
                    ideal_for=["surfers", "adventure seekers", "young travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Beach hostel and jungle lodge stays",
                        "Daily breakfast",
                        "Surf lessons",
                        "Rainforest canopy zip-line tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Nordic Fjords Explorer",
                    description="9-day scenic journey through Norway's epic fjords, waterfalls, and Viking history.",
                    price_amount=4199.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Nature",
                    destination="Norway",
                    duration_days=9,
                    ideal_for=["nature lovers", "photographers", "adventure seekers"],
                    includes=[
                        "Round-trip airfare",
                        "Fjordside hotel and ferry accommodation",
                        "Daily breakfast",
                        "Fjord cruise",
                        "Viking museum entrance",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="India Golden Triangle",
                    description="10-day iconic tour through Delhi, Agra, and Jaipur exploring India's Mughal heritage.",
                    price_amount=2599.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="India",
                    duration_days=10,
                    ideal_for=[
                        "culture enthusiasts",
                        "history buffs",
                        "first-time India visitors",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Heritage hotel accommodation",
                        "Daily breakfast",
                        "Taj Mahal sunrise tour",
                        "Jaipur elephant sanctuary visit",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Hawaii Island Hopper",
                    description="10-day adventure across Oahu, Maui, and the Big Island's volcanic landscapes.",
                    price_amount=4599.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Beach",
                    destination="Hawaii, USA",
                    duration_days=10,
                    ideal_for=["beach lovers", "families", "couples"],
                    includes=[
                        "Round-trip airfare",
                        "Resort and boutique hotel stays",
                        "Daily breakfast",
                        "Pearl Harbor tour",
                        "Volcanoes National Park guided hike",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Argentina Tango & Steak",
                    description="8-day immersive experience in Buenos Aires with tango lessons and Argentine culinary delights.",
                    price_amount=2499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Argentina",
                    duration_days=8,
                    ideal_for=["culture enthusiasts", "foodies", "couples"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel in Palermo",
                        "Daily breakfast",
                        "Tango lessons and milonga night out",
                        "Asado cooking class",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Borneo Wildlife Encounter",
                    description="9-day jungle adventure to see wild orangutans, pygmy elephants, and proboscis monkeys.",
                    price_amount=3799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Nature",
                    destination="Borneo, Malaysia",
                    duration_days=9,
                    ideal_for=[
                        "wildlife enthusiasts",
                        "nature lovers",
                        "photographers",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Jungle lodge accommodation",
                        "All meals",
                        "Kinabatangan River wildlife cruise",
                        "Orangutan sanctuary visit",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Senior Traveler's Europe",
                    description="14-day relaxed, slow-paced tour of Europe's most accessible and beautiful destinations.",
                    price_amount=3499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Senior Travel",
                    destination="Europe",
                    duration_days=14,
                    ideal_for=["senior travelers", "retirees"],
                    includes=[
                        "Round-trip airfare",
                        "4-star accessible hotel accommodation",
                        "Daily breakfast and dinner",
                        "Small group guided tours",
                        "24/7 on-trip support",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Yoga & Meditation Retreat – India",
                    description="14-day transformative yoga and Ayurvedic wellness retreat in Rishikesh and Kerala.",
                    price_amount=2999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Wellness",
                    destination="India",
                    duration_days=14,
                    ideal_for=[
                        "wellness travelers",
                        "spiritual seekers",
                        "solo travelers",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Ashram and Ayurvedic resort stays",
                        "All vegetarian meals",
                        "Daily yoga and meditation",
                        "Ayurvedic treatments",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Kenya Wildlife & Masai Culture",
                    description="9-day safari and cultural immersion in Kenya's Maasai Mara and tribal villages.",
                    price_amount=4999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Kenya",
                    duration_days=9,
                    ideal_for=[
                        "wildlife enthusiasts",
                        "culture enthusiasts",
                        "adventure seekers",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Safari lodge and tented camp",
                        "All meals",
                        "Twice-daily game drives",
                        "Maasai village cultural visit",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Digital Nomad Pack – Lisbon",
                    description="30-day all-inclusive remote work package in Lisbon with co-working, accommodation, and community events.",
                    price_amount=3999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Digital Nomad",
                    destination="Portugal",
                    duration_days=30,
                    ideal_for=["digital nomads", "remote workers", "solo travelers"],
                    includes=[
                        "Furnished apartment accommodation",
                        "Co-working space membership",
                        "High-speed internet",
                        "Weekly community events and networking",
                        "City orientation tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Angkor Wat & Mekong Explorer",
                    description="8-day cultural odyssey through Cambodia's ancient temples and the Mekong River delta.",
                    price_amount=1999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Cambodia & Vietnam",
                    duration_days=8,
                    ideal_for=["history buffs", "budget travelers", "backpackers"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique guesthouse accommodation",
                        "Daily breakfast",
                        "Angkor Wat sunrise tour",
                        "Mekong Delta boat trip",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Luxury Maldives Dive & Spa",
                    description="7-day private island luxury focused on world-class scuba diving and bespoke spa experiences.",
                    price_amount=7499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Luxury",
                    destination="Maldives",
                    duration_days=7,
                    ideal_for=["divers", "luxury travelers", "couples"],
                    includes=[
                        "Business class airfare",
                        "Private island resort villa",
                        "All-inclusive meals",
                        "6 guided dives with PADI instructor",
                        "Bespoke spa day",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Peru & Bolivia Highlands",
                    description="12-day Andean adventure from Cusco to Lake Titicaca and La Paz's surreal salt flats.",
                    price_amount=3299.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Peru & Bolivia",
                    duration_days=12,
                    ideal_for=[
                        "adventure seekers",
                        "backpackers",
                        "culture enthusiasts",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotels and eco-lodges",
                        "Daily breakfast",
                        "Uyuni Salt Flat guided tour",
                        "Reed Island homestay on Lake Titicaca",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Phuket Beach & Party Escape",
                    description="7-day sun, sea, and nightlife getaway in Phuket and the surrounding islands.",
                    price_amount=1499.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Beach",
                    destination="Thailand",
                    duration_days=7,
                    ideal_for=["young travelers", "friends", "beach lovers"],
                    includes=[
                        "Round-trip airfare",
                        "Beachfront resort accommodation",
                        "Daily breakfast",
                        "Phi Phi Island day tour",
                        "Full Moon Party transfer and ticket",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Barcelona Art & Architecture",
                    description="5-day artistic deep dive into Barcelona's Gaudí masterpieces, tapas culture, and beachside living.",
                    price_amount=1899.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Spain",
                    duration_days=5,
                    ideal_for=["art lovers", "architecture enthusiasts", "couples"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel in Eixample",
                        "Daily breakfast",
                        "Sagrada Família skip-the-line tickets",
                        "Guided Gaudí architecture walk",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Trans-Siberian Rail Adventure",
                    description="14-day epic overland rail journey from Moscow to Beijing via the Trans-Siberian Railway.",
                    price_amount=5799.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Russia & Mongolia & China",
                    duration_days=14,
                    ideal_for=[
                        "adventure seekers",
                        "rail enthusiasts",
                        "bucket-list travelers",
                    ],
                    includes=[
                        "Round-trip airfare to Moscow",
                        "Train sleeper cabin",
                        "Hotel stopovers in Irkutsk and Ulaanbaatar",
                        "Guided Lake Baikal excursion",
                        "Mongolian steppe horseback ride",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Honeymoon Collection – Bespoke",
                    description="Fully bespoke honeymoon package designed around the couple's dream destination and style.",
                    price_amount=None,
                    price_currency="USD",
                    pricing_model="custom",
                    category="Romantic",
                    destination="Varies",
                    duration_days=None,
                    ideal_for=["honeymooners", "newlyweds"],
                    includes=[
                        "Personalized itinerary",
                        "Private transfers",
                        "Surprise romantic experiences",
                        "Dedicated honeymoon planner",
                        "Luxury accommodation options",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Great Wall & Beijing Highlights",
                    description="6-day cultural tour of Beijing's imperial landmarks, Great Wall hike, and modern skyline.",
                    price_amount=2099.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="China",
                    duration_days=6,
                    ideal_for=[
                        "history buffs",
                        "first-time China visitors",
                        "culture enthusiasts",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "4-star hotel accommodation",
                        "Daily breakfast",
                        "Great Wall guided hike",
                        "Forbidden City and Temple of Heaven tours",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Greek Island Sailing",
                    description="8-day catamaran sailing adventure around the Cyclades islands of Greece.",
                    price_amount=3999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Greece",
                    duration_days=8,
                    ideal_for=["sailors", "couples", "adventure seekers"],
                    includes=[
                        "Round-trip airfare",
                        "Catamaran cabin accommodation",
                        "All meals on board",
                        "Skipper and hostess",
                        "Island hopping itinerary",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Istanbul Between Two Continents",
                    description="5-day cultural experience in Istanbul straddling Europe and Asia with history, bazaars, and Bosphorus views.",
                    price_amount=1699.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Turkey",
                    duration_days=5,
                    ideal_for=["culture enthusiasts", "history buffs", "couples"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel in Sultanahmet",
                        "Daily breakfast",
                        "Hagia Sophia and Grand Bazaar guided tour",
                        "Bosphorus dinner cruise",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Australian Outback & Reef",
                    description="12-day adventure combining the Great Barrier Reef, Uluru, and Sydney's iconic harbour.",
                    price_amount=5199.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Adventure",
                    destination="Australia",
                    duration_days=12,
                    ideal_for=[
                        "adventure seekers",
                        "nature lovers",
                        "bucket-list travelers",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Resort and boutique hotel stays",
                        "Daily breakfast",
                        "Great Barrier Reef dive trip",
                        "Uluru sunrise guided walk",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Colombia Coffee & Cartagena",
                    description="9-day journey through Medellín's transformation, the coffee region, and Cartagena's colorful walls.",
                    price_amount=2299.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="Colombia",
                    duration_days=9,
                    ideal_for=["culture enthusiasts", "foodies", "solo travelers"],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel accommodation",
                        "Daily breakfast",
                        "Coffee farm tour and tasting",
                        "Cartagena Old Town walking tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Family Safari – East Africa",
                    description="10-day family-friendly safari designed for children and parents in Kenya and Tanzania.",
                    price_amount=6999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Family",
                    destination="Kenya & Tanzania",
                    duration_days=10,
                    ideal_for=["families with kids", "wildlife enthusiasts"],
                    includes=[
                        "Round-trip airfare",
                        "Family-friendly safari lodges",
                        "All meals",
                        "Twice-daily game drives",
                        "Junior ranger wildlife education program",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="South Korea K-Culture Tour",
                    description="8-day immersive journey into Seoul's K-pop, K-food, temples, and tech-forward cityscape.",
                    price_amount=2699.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Cultural",
                    destination="South Korea",
                    duration_days=8,
                    ideal_for=[
                        "K-pop fans",
                        "culture enthusiasts",
                        "foodies",
                        "solo travelers",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Boutique guesthouse and hotel stays",
                        "Daily breakfast",
                        "K-pop entertainment experience",
                        "DMZ border guided tour",
                    ],
                ),
                create_agency_package(
                    agency_id=agency_id,
                    name="Montenegro & Adriatic Coast",
                    description="7-day hidden gem tour of Montenegro's medieval towns, fjords, and Adriatic beaches.",
                    price_amount=1999.99,
                    price_currency="USD",
                    pricing_model="fixed",
                    category="Beach",
                    destination="Montenegro",
                    duration_days=7,
                    ideal_for=[
                        "off-the-beaten-path travelers",
                        "beach lovers",
                        "couples",
                    ],
                    includes=[
                        "Round-trip airfare",
                        "Boutique hotel and villa accommodation",
                        "Daily breakfast",
                        "Bay of Kotor boat tour",
                        "Durmitor National Park day hike",
                    ],
                ),
            )

        logger.info("✅ Dummy packages seeded successfully!")

    except Exception as e:
        logger.exception(f"💥 seed_packages failed: {e}")


# =========================================================
# 🚀 RUN AS SCRIPT
# =========================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(dummy_packages())
