import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from . import database as db
from . import game
from .auth import current_user
from .config import (
    ADMIN_IDS,
    ADSGRAM_BLOCK_ID,
    ADSGRAM_POSTBACK_SECRET,
    BOT_TOKEN,
    CONTINUE_EXTRA_SECONDS,
    DAILY_BASE_BONUS,
    DIAMONDS_PER_STAR_BATCH,
    GAME_DURATION_SECONDS,
    REFERRAL_ADS_REQUIRED,
    REFERRAL_REWARD_DIAMONDS,
    STARS_PER_BATCH,
    WEBAPP_URL,
)
from .schemas import AdRequestIn, AnswerIn, BackgroundActionIn, TaskVerifyIn

bot = Bot(token=BOT_TOKEN)  # used only to call the API (send messages, getChatMember) — no polling here


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your deployed frontend origin once it's live
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- profile --

@app.get("/api/me")
async def me(user: dict = Depends(current_user)):
    row = await db.get_or_create_user(user["id"], user.get("username"))
    settings = await db.get_settings()
    selected_bg = await db.get_selected_background(row["id"])
    return {
        "id": row["id"],
        "coins": row["coins"],
        "diamonds": row["diamonds"],
        "cashout_open": settings.get("is_cashout_open") == "true",
        "stars_pool_remaining": int(settings.get("stars_pool_remaining", 0)),
        "adsgram_block_id": ADSGRAM_BLOCK_ID,
        "diamonds_per_batch": DIAMONDS_PER_STAR_BATCH,
        "stars_per_batch": STARS_PER_BATCH,
        "referral_link": f"https://t.me/{(await bot.get_me()).username}?start=ref_{row['id']}",
        "selected_background_css": selected_bg["css_value"] if selected_bg else None,
    }


# ---------------------------------------------------------------- shop --

@app.get("/api/shop/backgrounds")
async def shop_backgrounds(user: dict = Depends(current_user)):
    await db.get_or_create_user(user["id"], user.get("username"))
    rows = await db.list_backgrounds(user["id"])
    return [
        {
            "id": r["id"], "name": r["name"], "css_value": r["css_value"],
            "price": r["price"], "animated": r["animated"],
            "owned": r["owned"], "selected": r["selected"],
        }
        for r in rows
    ]


@app.post("/api/shop/buy")
async def shop_buy(body: BackgroundActionIn, user: dict = Depends(current_user)):
    try:
        bg = await db.buy_background(user["id"], body.background_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"purchased": bg["name"], "price": bg["price"]}


@app.post("/api/shop/select")
async def shop_select(body: BackgroundActionIn, user: dict = Depends(current_user)):
    try:
        await db.select_background(user["id"], body.background_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ------------------------------------------------------------- game flow --

@app.post("/api/game/start")
async def start_game(user: dict = Depends(current_user)):
    await db.get_or_create_user(user["id"], user.get("username"))
    ends_at = datetime.now(timezone.utc) + timedelta(seconds=GAME_DURATION_SECONDS)
    session = await db.create_session(user["id"], ends_at)
    batch = game.generate_batch(40)
    await db.insert_problems(str(session["id"]), batch)

    session_row = await db.get_session(str(session["id"]))
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, question, options FROM problems WHERE session_id = $1 ORDER BY id",
            session["id"],
        )
    return {
        "session_id": str(session["id"]),
        "ends_at": session_row["ends_at"].isoformat(),
        "problems": [
            {"id": str(r["id"]), "question": r["question"], "options": r["options"]}
            for r in rows
        ],
    }


@app.post("/api/game/answer")
async def answer(body: AnswerIn, user: dict = Depends(current_user)):
    session = await db.get_session(body.session_id)
    if session is None or session["user_id"] != user["id"]:
        raise HTTPException(404, "session not found")
    if session["finished"]:
        raise HTTPException(400, "session already finished")
    if datetime.now(timezone.utc) > session["ends_at"]:
        raise HTTPException(400, "time is up")

    problem = await db.get_problem(body.problem_id)
    if problem is None or str(problem["session_id"]) != body.session_id:
        raise HTTPException(404, "problem not found")

    try:
        correct = await db.answer_problem(body.problem_id, body.selected_index)
    except ValueError:
        raise HTTPException(400, "already answered")
    return {"correct": correct}


@app.post("/api/game/finish")
async def finish(body: dict, user: dict = Depends(current_user)):
    session_id = body["session_id"]
    session = await db.get_session(session_id)
    if session is None or session["user_id"] != user["id"]:
        raise HTTPException(404, "session not found")
    try:
        coins = await db.finish_session(session_id)
    except ValueError:
        raise HTTPException(400, "already finished")
    return {"coins_earned": coins}


# ---------------------------------------------------------- rewarded ads --
# Flow: 1) client asks us for an ad_view id  2) client passes it to AdsGram
# as the sub-id  3) AdsGram calls /api/ads/postback when the video is fully
# watched  4) only then do we credit anything. The client polls /status.

@app.post("/api/ads/request")
async def request_ad(body: AdRequestIn, user: dict = Depends(current_user)):
    if body.purpose not in ("diamond", "continue", "multiplier", "daily_double", "sponsor_recheck"):
        raise HTTPException(400, "invalid purpose")
    ad_view = await db.create_ad_view(user["id"], body.purpose, body.session_id)
    return {"ad_view_id": str(ad_view["id"])}


@app.get("/api/ads/status/{ad_view_id}")
async def ad_status(ad_view_id: str, user: dict = Depends(current_user)):
    row = await db.get_ad_view(ad_view_id)
    if row is None or row["user_id"] != user["id"]:
        raise HTTPException(404, "not found")
    return {"status": row["status"]}


@app.get("/api/ads/postback")
async def ads_postback(request: Request):
    """
    Server-to-server callback from AdsGram once a rewarded view completes.
    Configure this URL in your AdsGram dashboard as the postback/webhook URL,
    with `{sub_id}` mapped to our ad_view_id and a signature parameter.

    NOTE: verify the exact parameter names & signing scheme against your
    AdsGram dashboard docs when you set this up — network postback formats
    change between providers/versions, so treat the signature check below as
    a template to adapt, not a guaranteed-exact spec.
    """
    params = dict(request.query_params)
    ad_view_id = params.get("sub_id") or params.get("subid")
    signature = params.pop("sign", None)
    if not ad_view_id or not signature:
        raise HTTPException(400, "missing sub_id or sign")

    check_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    expected = hmac.new(
        ADSGRAM_POSTBACK_SECRET.encode(), check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "bad signature")

    ad_view = await db.complete_ad_view(ad_view_id)
    if ad_view is None:
        return {"ok": True, "note": "already processed or unknown id"}

    user_id, purpose, session_id = ad_view["user_id"], ad_view["purpose"], ad_view["session_id"]

    if purpose == "diamond":
        await db.add_diamonds(user_id, 1)
    elif purpose == "continue" and session_id:
        await db.extend_session(str(session_id), CONTINUE_EXTRA_SECONDS)
    elif purpose == "multiplier" and session_id:
        await db.set_multiplier(str(session_id))
    elif purpose == "daily_double":
        await db.double_daily(user_id, DAILY_BASE_BONUS)

    # any completed ad counts toward this user's referral qualification
    new_count = await db.bump_referral_ads_watched(user_id)
    if new_count >= REFERRAL_ADS_REQUIRED:
        referrer_id = await db.maybe_reward_referrer(
            user_id, REFERRAL_ADS_REQUIRED, REFERRAL_REWARD_DIAMONDS
        )
        if referrer_id:
            try:
                await bot.send_message(
                    referrer_id,
                    f"🎉 Do'stingiz reklamalarni ko'rdi — sizga {REFERRAL_REWARD_DIAMONDS} 💎 qo'shildi!",
                )
            except Exception:
                pass  # user may have blocked the bot — reward is still credited

    return {"ok": True}


# -------------------------------------------------------------- sponsor --

@app.get("/api/tasks")
async def tasks():
    rows = await db.list_active_tasks()
    return [
        {"id": r["id"], "channel_username": r["channel_username"], "reward": r["reward"]}
        for r in rows
    ]


@app.post("/api/tasks/verify")
async def verify_task(body: TaskVerifyIn, user: dict = Depends(current_user)):
    task = await db.get_task(body.task_id)
    if task is None or not task["active"]:
        raise HTTPException(404, "task not found")
    try:
        member = await bot.get_chat_member(task["channel_id"], user["id"])
    except Exception:
        raise HTTPException(400, "could not verify membership — is the bot an admin of the channel?")
    if member.status not in ("member", "administrator", "creator"):
        raise HTTPException(400, "not subscribed")
    granted = await db.complete_task(task["id"], user["id"], task["reward"])
    if not granted:
        raise HTTPException(400, "already claimed")
    return {"reward": task["reward"]}


# ---------------------------------------------------------------- daily --

@app.post("/api/daily/claim")
async def daily_claim(user: dict = Depends(current_user)):
    try:
        amount = await db.claim_daily(user["id"], DAILY_BASE_BONUS)
    except ValueError:
        raise HTTPException(400, "already claimed today")
    return {"diamonds": amount}


# ---------------------------------------------------------------- stars --

@app.post("/api/stars/request")
async def stars_request(user: dict = Depends(current_user)):
    try:
        payout = await db.try_reserve_stars_batch(
            user["id"], DIAMONDS_PER_STAR_BATCH, STARS_PER_BATCH
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💸 Yangi Stars so'rovi\n"
                f"Foydalanuvchi: {user['id']} (@{user.get('username', '-')})\n"
                f"Stars: {payout['stars']}\n"
                f"So'rov ID: {payout['id']}\n\n"
                f"Foydalanuvchiga qo'lda {payout['stars']} Stars yuboring, so'ng tasdiqlang:\n"
                f"/paid_{str(payout['id']).replace('-', '')}",
            )
        except Exception:
            pass
    return {"status": "pending", "payout_id": str(payout["id"])}
