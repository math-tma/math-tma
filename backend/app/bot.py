import asyncio
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import database as db
from .config import ADMIN_IDS, BOT_TOKEN, WEBAPP_URL

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    referrer_id = None
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            referrer_id = int(parts[1].removeprefix("ref_"))
        except ValueError:
            referrer_id = None

    await db.get_or_create_user(message.from_user.id, message.from_user.username, referrer_id)

    kb = InlineKeyboardBuilder()
    if WEBAPP_URL:
        kb.button(text="🎮 O'yinni boshlash", web_app=WebAppInfo(url=WEBAPP_URL))
    await message.answer(
        "Xush kelibsiz! 60 soniyada imkon qadar ko'proq misol yeching, "
        "tanga va olmos yig'ing, keyin ularni Telegram Stars'ga almashtiring.",
        reply_markup=kb.as_markup() if WEBAPP_URL else None,
    )


# --------------------------------------------------------- admin panel --

def _is_admin(message: Message) -> bool:
    return message.from_user.id in ADMIN_IDS


@dp.message(Command("cashout_on"))
async def cashout_on(message: Message):
    if not _is_admin(message):
        return
    await db.set_setting("is_cashout_open", "true")
    await message.answer("✅ Stars almashtirish ochildi.")


@dp.message(Command("cashout_off"))
async def cashout_off(message: Message):
    if not _is_admin(message):
        return
    await db.set_setting("is_cashout_open", "false")
    await message.answer("⛔ Stars almashtirish yopildi.")


@dp.message(Command("pool_set"))
async def pool_set(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /pool_set 450")
        return
    amount = parts[1]
    await db.set_setting("stars_pool_remaining", amount)
    await db.set_setting("max_stars_pool", amount)
    await message.answer(f"✅ Stars pool {amount} ga o'rnatildi.")


@dp.message(Command("pool_status"))
async def pool_status(message: Message):
    if not _is_admin(message):
        return
    s = await db.get_settings()
    await message.answer(
        f"Cashout ochiqmi: {s.get('is_cashout_open')}\n"
        f"Qolgan Stars pool: {s.get('stars_pool_remaining')} / {s.get('max_stars_pool')}"
    )


@dp.message(Command("stats"))
async def stats(message: Message):
    """Umumiy iqtisod ko'rinishi — barcha foydalanuvchilardagi jami
    tanga/olmos/Stars, "airdrop qilsam qancha to'plaganman" degan savolga javob."""
    if not _is_admin(message):
        return
    s = await db.get_global_stats()
    await message.answer(
        "📊 Umumiy statistika\n\n"
        f"👥 Foydalanuvchilar: {s['total_users']}\n"
        f"🪙 Jami tangalar (barcha userlarda): {s['total_coins']}\n"
        f"💎 Jami olmoslar (barcha userlarda): {s['total_diamonds']}\n"
        f"🛍 Do'konda sarflangan tangalar: {s['total_coins_spent_in_shop']}\n\n"
        f"⭐ To'langan Stars (jami): {s['total_stars_paid']}\n"
        f"⏳ Kutilayotgan Stars so'rovlari: {s['total_stars_pending']} "
        f"({s['pending_requests_count']} ta so'rov)\n\n"
        "Eslatma: bu — foydalanuvchilarning o'yin ichidagi virtual balansi, "
        "haqiqiy pul emas. Real 'airdrop' qilish uchun buni qanday real "
        "qiymatga (Stars/TON/boshqa) bog'lashni alohida rejalashtirish kerak."
    )


@dp.message(F.text.regexp(r"^/paid_([a-f0-9]{32})$"))
async def mark_paid(message: Message):
    if not _is_admin(message):
        return
    hex_id = message.text.removeprefix("/paid_")
    payout_id = str(uuid.UUID(hex_id))
    payout = await db.mark_payout_paid(payout_id)
    if payout is None:
        await message.answer("Topilmadi yoki allaqachon to'langan.")
        return
    await message.answer(f"✅ {payout['stars']} Stars — to'landi deb belgilandi.")
    try:
        await bot.send_message(
            payout["user_id"],
            f"⭐ Sizga {payout['stars']} Telegram Stars yuborildi. Rahmat!",
        )
    except Exception:
        pass


async def main():
    await db.init_pool()
    try:
        await dp.start_polling(bot)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
