import asyncio
import logging
from typing import Optional, List

from core import db_insert, db_update, db_select, db_rpc

logger = logging.getLogger("AGENCY_SERVICE")


# =========================================================
# 🔐 VAULT HELPERS
# =========================================================


async def store_agency_password(agency_id: str, app_password: str) -> Optional[str]:
    """
    Store agency password in Vault and update agency.vault_secret_id.
    """
    try:
        logger.info(f"🔐 Creating Vault secret for agency={agency_id}")

        secret_id = await db_rpc(
            "vault.create_secret",
            {
                "secret": app_password,
                "name": f"agency_password_{agency_id}",
            },
        )

        if not secret_id:
            logger.error("❌ Vault secret creation returned empty result")
            return None

        try:
            logger.info(f"🔗 Linking Vault secret to agency={agency_id}")
            update_result = await db_update(
                table="agencies",
                updates={"vault_secret_id": str(secret_id)},
                filters={"id": agency_id},
            )
        except Exception as e:
            logger.exception(f"💥 Failed to update agency with vault_secret_id: {e}")
            return None

        if update_result is None:
            logger.error("❌ Failed linking vault_secret_id to agency")
            return None

        logger.info(f"✅ Vault secret linked → agency={agency_id}")
        return str(secret_id)

    except Exception as e:
        logger.exception(f"💥 store_agency_password failed: {e}")
        return None


async def get_agency_password(agency: dict) -> Optional[str]:
    """
    Retrieve decrypted password from Vault.
    """
    try:
        secret_id = agency.get("vault_secret_id")

        if not secret_id:
            logger.warning(f"⚠️ Agency {agency.get('id')} has no vault_secret_id")
            return None

        logger.info(f"🔓 Fetching Vault secret for agency={agency.get('id')}")

        result = await db_rpc(
            "vault.decrypted_secrets",
            {"id": secret_id},
        )

        if not result or not isinstance(result, list):
            logger.error("❌ Vault returned invalid response")
            return None

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
# 🧪 CLI: SEED DUMMY DATA
# =========================================================


async def seed_dummy_agencies():
    """
    Insert dummy agencies for testing.
    """
    try:
        logger.info("🌱 Seeding dummy agencies...")

        await create_agency(
            name="Alpha Travel",
            issued_email="ahk3155263@gmail.com",
            agent_email="ahk3155264@gmail.com",
            app_password="fhcgozeenmbpnjbc",
        )

        await create_agency(
            name="Beta Voyages",
            issued_email="ahk3155262@gmail.com",
            agent_email="ahk3155265@gmail.com",
            app_password="lgebwclxolkuscjc",
        )

        logger.info("✅ Dummy agencies seeded successfully!")

    except Exception as e:
        logger.exception(f"💥 seed_dummy_agencies failed: {e}")


# =========================================================
# 🚀 RUN AS SCRIPT
# =========================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_dummy_agencies())
