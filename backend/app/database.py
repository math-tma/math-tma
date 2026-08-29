import asyncpg
from .config import DATABASE_URL

_pool: asyncpg.Pool | None = None


async def init_pool():
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    # Apply schema.sql idempotently on startup so a fresh Railway DB just works.
    with open(__file__.replace("app/database.py", "schema.sql")) as f:
        schema = f.read()
    async with _pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")  # gen_random_uuid()
        await conn.execute(schema)
    return _pool


def pool() -> asyncpg.Pool:
    assert _pool is not None, "call init_pool() first"
    return _pool


async def close_pool():
    if _pool:
        await _pool.close()


# ---------- users ----------

async def get_or_create_user(user_id: int, username: str | None, referrer_id: int | None = None):
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if row:
            return row
        # Only attach a referrer the very first time the user is seen, and never self-referral.
        ref = referrer_id if (referrer_id and referrer_id != user_id) else None
        if ref is not None:
            exists = await conn.fetchval("SELECT 1 FROM users WHERE id = $1", ref)
            if not exists:
                ref = None
        row = await conn.fetchrow(
            """INSERT INTO users (id, username, referrer_id, selected_background_id)
               VALUES ($1, $2, $3, 1) RETURNING *""",
            user_id, username, ref,
        )
        await conn.execute(
            "INSERT INTO user_backgrounds (user_id, background_id) VALUES ($1, 1) "
            "ON CONFLICT DO NOTHING", user_id,
        )
        return row


async def get_user(user_id: int):
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)


async def add_coins(user_id: int, amount: int):
    async with pool().acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins + $2 WHERE id = $1", user_id, amount)


async def add_diamonds(user_id: int, amount: int):
    async with pool().acquire() as conn:
        await conn.execute("UPDATE users SET diamonds = diamonds + $2 WHERE id = $1", user_id, amount)


# ---------- settings (cashout toggle + pool), race-safe ----------

async def get_settings():
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}


async def set_setting(key: str, value: str):
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ($1,$2) "
            "ON CONFLICT (key) DO UPDATE SET value = $2", key, value,
        )


async def try_reserve_stars_batch(user_id: int, diamonds_cost: int, stars_amount: int):
    """
    Atomically: check cashout is open, user has enough diamonds, pool has
    enough stars left -- then deduct both and create a payout_request.
    Locks the settings rows so two simultaneous requests can't both pass.
    Returns the payout_request row, or raises ValueError with a reason.
    """
    async with pool().acquire() as conn:
        async with conn.transaction():
            settings_rows = await conn.fetch(
                "SELECT key, value FROM settings WHERE key IN "
                "('is_cashout_open','stars_pool_remaining') FOR UPDATE"
            )
            s = {r["key"]: r["value"] for r in settings_rows}
            if s.get("is_cashout_open") != "true":
                raise ValueError("cashout_closed")
            remaining = int(s.get("stars_pool_remaining", "0"))
            if remaining < stars_amount:
                raise ValueError("pool_exhausted")

            user = await conn.fetchrow(
                "SELECT diamonds FROM users WHERE id = $1 FOR UPDATE", user_id
            )
            if user is None or user["diamonds"] < diamonds_cost:
                raise ValueError("not_enough_diamonds")

            await conn.execute(
                "UPDATE users SET diamonds = diamonds - $2 WHERE id = $1",
                user_id, diamonds_cost,
            )
            await conn.execute(
                "UPDATE settings SET value = $1 WHERE key = 'stars_pool_remaining'",
                str(remaining - stars_amount),
            )
            payout = await conn.fetchrow(
                """INSERT INTO payout_requests (user_id, stars, diamonds_spent)
                   VALUES ($1, $2, $3) RETURNING *""",
                user_id, stars_amount, diamonds_cost,
            )
            return payout


async def mark_payout_paid(payout_id: str):
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            """UPDATE payout_requests SET status = 'paid', paid_at = now()
               WHERE id = $1 AND status = 'pending' RETURNING *""",
            payout_id,
        )


# ---------- game sessions / problems ----------

async def create_session(user_id: int, ends_at):
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            """INSERT INTO game_sessions (user_id, ends_at) VALUES ($1, $2) RETURNING *""",
            user_id, ends_at,
        )


async def get_session(session_id: str):
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM game_sessions WHERE id = $1", session_id)


async def insert_problems(session_id: str, problems: list[dict]):
    async with pool().acquire() as conn:
        async with conn.transaction():
            for p in problems:
                await conn.execute(
                    """INSERT INTO problems (session_id, question, options, correct_index)
                       VALUES ($1, $2, $3, $4)""",
                    session_id, p["question"], p["options"], p["correct_index"],
                )


async def get_problem(problem_id: str):
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM problems WHERE id = $1", problem_id)


async def answer_problem(problem_id: str, selected_index: int) -> bool:
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM problems WHERE id = $1 FOR UPDATE", problem_id
            )
            if row is None or row["answered"]:
                raise ValueError("already_answered_or_missing")
            is_correct = (selected_index == row["correct_index"])
            await conn.execute(
                "UPDATE problems SET answered = TRUE, is_correct = $2 WHERE id = $1",
                problem_id, is_correct,
            )
            if is_correct:
                await conn.execute(
                    "UPDATE game_sessions SET correct_count = correct_count + 1 WHERE id = $1",
                    row["session_id"],
                )
            return is_correct


async def extend_session(session_id: str, seconds: int):
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE game_sessions SET ends_at = ends_at + make_interval(secs => $2), extended = TRUE "
            "WHERE id = $1 AND extended = FALSE",
            session_id, seconds,
        )


async def set_multiplier(session_id: str):
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE game_sessions SET multiplier_used = TRUE WHERE id = $1 AND multiplier_used = FALSE",
            session_id,
        )


async def finish_session(session_id: str):
    async with pool().acquire() as conn:
        async with conn.transaction():
            session = await conn.fetchrow(
                "SELECT * FROM game_sessions WHERE id = $1 FOR UPDATE", session_id
            )
            if session is None or session["finished"]:
                raise ValueError("already_finished_or_missing")
            coins = session["correct_count"]
            if session["multiplier_used"]:
                coins *= 2
            await conn.execute(
                "UPDATE game_sessions SET finished = TRUE, coins_earned = $2 WHERE id = $1",
                session_id, coins,
            )
            await conn.execute(
                "UPDATE users SET coins = coins + $2 WHERE id = $1", session["user_id"], coins
            )
            return coins


# ---------- ad views ----------

async def create_ad_view(user_id: int, purpose: str, session_id: str | None = None):
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            """INSERT INTO ad_views (user_id, purpose, session_id)
               VALUES ($1, $2, $3) RETURNING *""",
            user_id, purpose, session_id,
        )


async def get_ad_view(ad_view_id: str):
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM ad_views WHERE id = $1", ad_view_id)


async def complete_ad_view(ad_view_id: str):
    """Called only from the verified AdsGram postback. Returns the row, or None if
    it was already completed / doesn't exist (postbacks can be retried by AdsGram)."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM ad_views WHERE id = $1 FOR UPDATE", ad_view_id
            )
            if row is None or row["status"] != "pending":
                return None
            await conn.execute(
                "UPDATE ad_views SET status = 'completed', completed_at = now() WHERE id = $1",
                ad_view_id,
            )
            return row


async def bump_referral_ads_watched(user_id: int) -> int:
    """Increments the caller's own ad-watched counter (used for the friend's
    referral qualification) and returns the new count."""
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "UPDATE users SET referral_ads_watched = referral_ads_watched + 1 "
            "WHERE id = $1 RETURNING referral_ads_watched",
            user_id,
        )


async def maybe_reward_referrer(referred_user_id: int, threshold: int, reward: int):
    """If referred_user_id has a referrer who hasn't been rewarded yet, and the
    referred user has watched >= threshold ads, credit the referrer once."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE id = $1 FOR UPDATE", referred_user_id
            )
            if user is None or user["referrer_id"] is None or user["referral_rewarded"]:
                return None
            if user["referral_ads_watched"] < threshold:
                return None
            await conn.execute(
                "UPDATE users SET referral_rewarded = TRUE WHERE id = $1", referred_user_id
            )
            await conn.execute(
                "UPDATE users SET diamonds = diamonds + $2 WHERE id = $1",
                user["referrer_id"], reward,
            )
            return user["referrer_id"]


# ---------- sponsor tasks ----------

async def list_active_tasks():
    async with pool().acquire() as conn:
        return await conn.fetch("SELECT * FROM sponsor_tasks WHERE active = TRUE ORDER BY id")


async def get_task(task_id: int):
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM sponsor_tasks WHERE id = $1", task_id)


async def complete_task(task_id: int, user_id: int, reward: int) -> bool:
    async with pool().acquire() as conn:
        async with conn.transaction():
            already = await conn.fetchval(
                "SELECT 1 FROM sponsor_task_completions WHERE task_id = $1 AND user_id = $2",
                task_id, user_id,
            )
            if already:
                return False
            await conn.execute(
                "INSERT INTO sponsor_task_completions (task_id, user_id) VALUES ($1, $2)",
                task_id, user_id,
            )
            await conn.execute(
                "UPDATE users SET diamonds = diamonds + $2 WHERE id = $1", user_id, reward
            )
            return True


# ---------- daily bonus ----------

async def claim_daily(user_id: int, base_amount: int):
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT * FROM users WHERE id = $1 FOR UPDATE", user_id)
            today_already = user["last_daily_claim"] is not None and \
                str(user["last_daily_claim"]) == await conn.fetchval("SELECT CURRENT_DATE::text")
            if today_already:
                raise ValueError("already_claimed_today")
            await conn.execute(
                "UPDATE users SET diamonds = diamonds + $2, last_daily_claim = CURRENT_DATE, "
                "daily_doubled_today = FALSE WHERE id = $1",
                user_id, base_amount,
            )
            return base_amount


# ---------- shop (cosmetic backgrounds, paid with Coins only) ----------

async def list_backgrounds(user_id: int):
    async with pool().acquire() as conn:
        return await conn.fetch(
            """SELECT b.id, b.name, b.css_value, b.price, b.animated,
                      (ub.user_id IS NOT NULL) AS owned,
                      (u.selected_background_id = b.id) AS selected
               FROM backgrounds b
               LEFT JOIN user_backgrounds ub ON ub.background_id = b.id AND ub.user_id = $1
               JOIN users u ON u.id = $1
               ORDER BY b.price""",
            user_id,
        )


async def get_selected_background(user_id: int):
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            """SELECT b.id, b.css_value FROM users u
               JOIN backgrounds b ON b.id = u.selected_background_id
               WHERE u.id = $1""",
            user_id,
        )


async def buy_background(user_id: int, background_id: int):
    async with pool().acquire() as conn:
        async with conn.transaction():
            bg = await conn.fetchrow("SELECT * FROM backgrounds WHERE id = $1", background_id)
            if bg is None:
                raise ValueError("not_found")
            already = await conn.fetchval(
                "SELECT 1 FROM user_backgrounds WHERE user_id = $1 AND background_id = $2",
                user_id, background_id,
            )
            if already:
                raise ValueError("already_owned")
            user = await conn.fetchrow("SELECT coins FROM users WHERE id = $1 FOR UPDATE", user_id)
            if user["coins"] < bg["price"]:
                raise ValueError("not_enough_coins")
            await conn.execute(
                "UPDATE users SET coins = coins - $2 WHERE id = $1", user_id, bg["price"]
            )
            await conn.execute(
                "INSERT INTO user_backgrounds (user_id, background_id) VALUES ($1, $2)",
                user_id, background_id,
            )
            return bg


async def select_background(user_id: int, background_id: int):
    async with pool().acquire() as conn:
        owned = await conn.fetchval(
            "SELECT 1 FROM user_backgrounds WHERE user_id = $1 AND background_id = $2",
            user_id, background_id,
        )
        if not owned:
            raise ValueError("not_owned")
        await conn.execute(
            "UPDATE users SET selected_background_id = $2 WHERE id = $1", user_id, background_id
        )


async def double_daily(user_id: int, base_amount: int):
    async with pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT * FROM users WHERE id = $1 FOR UPDATE", user_id)
            today = await conn.fetchval("SELECT CURRENT_DATE::text")
            if user["last_daily_claim"] is None or str(user["last_daily_claim"]) != today:
                raise ValueError("claim_base_bonus_first")
            if user["daily_doubled_today"]:
                raise ValueError("already_doubled_today")
            await conn.execute(
                "UPDATE users SET diamonds = diamonds + $2, daily_doubled_today = TRUE WHERE id = $1",
                user_id, base_amount,
            )
            return base_amount


# ---------- global economy totals (admin) ----------

async def get_global_stats():
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 (SELECT COUNT(*) FROM users)                                   AS total_users,
                 (SELECT COALESCE(SUM(coins), 0) FROM users)                    AS total_coins,
                 (SELECT COALESCE(SUM(diamonds), 0) FROM users)                 AS total_diamonds,
                 (SELECT COALESCE(SUM(stars), 0) FROM payout_requests
                    WHERE status = 'paid')                                     AS total_stars_paid,
                 (SELECT COALESCE(SUM(stars), 0) FROM payout_requests
                    WHERE status = 'pending')                                  AS total_stars_pending,
                 (SELECT COUNT(*) FROM payout_requests WHERE status = 'pending') AS pending_requests_count,
                 (SELECT COALESCE(SUM(price), 0) FROM user_backgrounds ub
                    JOIN backgrounds b ON b.id = ub.background_id)             AS total_coins_spent_in_shop
            """
        )
        return row
