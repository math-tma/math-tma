import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]                     # from @BotFather
DATABASE_URL = os.environ["DATABASE_URL"]                # Railway Postgres connection string
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

ADSGRAM_POSTBACK_SECRET = os.environ["ADSGRAM_POSTBACK_SECRET"]  # shared secret configured in AdsGram dashboard
ADSGRAM_BLOCK_ID = os.environ.get("ADSGRAM_BLOCK_ID", "")        # sent to frontend

GAME_DURATION_SECONDS = 60
CONTINUE_EXTRA_SECONDS = 15
DIAMONDS_PER_STAR_BATCH = 300
STARS_PER_BATCH = 15
REFERRAL_ADS_REQUIRED = 3
REFERRAL_REWARD_DIAMONDS = 20
SPONSOR_TASK_DEFAULT_REWARD = 40
DAILY_BASE_BONUS = 5  # doubled to 10 if user watches an ad

WEBAPP_URL = os.environ.get("WEBAPP_URL", "")  # e.g. https://your-app.vercel.app, used to build referral links
