# Tez Hisob — Telegram Mini App (matematik o'yin)

## Arxitektura qisqacha

```
frontend/   → statik sayt (Vercel), Telegram WebApp SDK + AdsGram SDK
backend/    → FastAPI (web servis) + aiogram bot (alohida worker), Railway
              PostgreSQL bilan ishlaydi
```

Ikkita alohida Railway servisi kerak bo'ladi: **web** (FastAPI — Mini App
so'rovlarini qabul qiladi, AdsGram postback'ni qabul qiladi) va **worker**
(aiogram bot — `/start`, referral, admin buyruqlari). Ikkalasi ham bitta
PostgreSQL bazasiga ulanadi.

---

## 1. BotFather orqali bot yaratish

1. `@BotFather` ga `/newbot` yuboring, nom va username bering.
2. Olingan tokenni saqlab qo'ying — bu `BOT_TOKEN`.
3. `/mybots` → botingiz → **Bot Settings → Menu Button → Configure Menu
   Button** → frontend URL'ingizni kiriting (Vercel'ga joylagandan keyin).
4. Xuddi shu joyda **Mini App**'ni yoqing, agar so'ralsa.

## 2. Railway'da PostgreSQL

1. [railway.app](https://railway.app) → New Project → **Provision
   PostgreSQL**.
2. "Connect" bo'limidan `DATABASE_URL`ni nusxalang.
3. Jadval sxemasi (`backend/schema.sql`) FastAPI birinchi marta ishga
   tushganda avtomatik qo'llaniladi (`database.py` buni `lifespan`da
   bajaradi) — qo'lda migratsiya kerak emas.

## 3. Backend'ni Railway'ga joylash

1. `backend/` papkasini alohida GitHub repo (yoki mavjud repo ichidagi
   subfolder) sifatida push qiling.
2. Railway → New Service → **Deploy from GitHub repo** → shu repo'ni
   tanlang, root directory'ni `backend` qiling.
3. **Ikkita servis yarating**, ikkalasi ham bir xil repo/branch'dan:
   - `web` — Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - `worker` — Start command: `python -m app.bot`
   (Bular `Procfile`da allaqachon yozilgan, Railway avtomatik tanib olishi
   mumkin; agar tanimasa, Settings → Deploy → Custom Start Command'da qo'lda
   kiriting.)
4. Har ikkala servisga quyidagi Environment Variables'ni qo'shing
   (`.env.example`ga qarang):
   ```
   BOT_TOKEN=...
   DATABASE_URL=...          (Postgres servisidan "Reference variable" qilib ulang)
   ADMIN_IDS=sizning_telegram_id
   ADSGRAM_POSTBACK_SECRET=...
   ADSGRAM_BLOCK_ID=...
   WEBAPP_URL=https://sizning-frontend.vercel.app
   ```
   O'z Telegram ID'ingizni bilish uchun `@userinfobot`ga yozing.
5. Deploy tugagach, `web` servisning ochiq domenini (Settings → Networking
   → Generate Domain) nusxalang — bu backend URL'ingiz.

## 4. AdsGram sozlash

1. [adsgram.ai](https://adsgram.ai) da hisob oching, Mini App'ingizni
   qo'shing, **Rewarded Video** blokini yarating — `block_id` oling.
2. Dashboard'dagi **Postback / Server-to-server callback** bo'limida
   webhook URL'ni kiriting:
   ```
   https://SIZNING-BACKEND-URL/api/ads/postback?sub_id={sub_id}&sign={sign}
   ```
   Aniq parametr nomlari (`sub_id`, `sign` va h.k.) va imzolash usuli
   dashboard hujjatida ko'rsatiladi — ular AdsGram versiyasiga qarab farq
   qilishi mumkin, shuning uchun `backend/app/main.py`dagi
   `ads_postback()` funksiyasini o'sha hujjatga moslab sozlang (hozirgi
   kod — HMAC-SHA256 asosidagi umumiy shablon).
3. `ADSGRAM_BLOCK_ID` va `ADSGRAM_POSTBACK_SECRET`ni Railway'dagi
   environment variables'ga qo'shing.

## 5. Frontend'ni Vercel'ga joylash

1. `frontend/app.js` faylida `API_BASE` qiymatini backend domeningizga
   almashtiring.
2. `frontend/` papkasini GitHub'ga push qiling.
3. [vercel.com](https://vercel.com) → New Project → repo'ni tanlang, root
   directory `frontend`, Framework Preset: **Other** (static).
4. Deploy qiling, olingan URL'ni:
   - Railway'dagi `WEBAPP_URL` o'zgaruvchisiga qo'ying (worker + web
     servislarni qayta deploy qiling),
   - BotFather'dagi Menu Button URL'iga qo'ying.

## 6. Homiy kanal (sponsor task) qo'shish

Botingizni tegishli kanalga **admin** qilib qo'shing, so'ng bazaga qatordan
qo'shing (Railway PostgreSQL → Query yoki `psql` orqali):

```sql
INSERT INTO sponsor_tasks (channel_username, channel_id, reward)
VALUES ('@mening_kanalim', -1001234567890, 40);
```

`channel_id`ni olish uchun kanalga biror post forward qiling
`@userinfobot`ga, yoki botga vaqtincha `getUpdates` orqali qarang.

## 7. Cashout'ni boshqarish (admin)

Bot bilan shaxsiy chatda (faqat `ADMIN_IDS`dagi ID'lar uchun ishlaydi):

- `/cashout_on` — Stars almashtirishni ochadi
- `/cashout_off` — yopadi
- `/pool_set 450` — aksiya uchun umumiy Stars limitini o'rnatadi
- `/pool_status` — joriy holatni ko'rsatadi
- `/stats` — umumiy iqtisod ko'rinishi: barcha foydalanuvchilardagi jami tanga,
  jami olmos, to'langan va kutilayotgan Stars — "qancha to'plandi, airdrop
  qilsam bo'ladimi" degan savolga javob beradi

Foydalanuvchi 15 Stars so'raganda, sizga (admin) bot orqali xabar keladi —
unda so'rov ID bor. Foydalanuvchiga Stars'ni **qo'lda** (Telegram'ning
o'zidagi "Sovg'a" / Gift funksiyasi orqali, chunki bot API orqali avtomatik
yuborish imkoni yo'q — birinchi xabarimizda tushuntirilgandek) yuborgach,
xabardagi `/paid_...` buyrug'ini bosing — bu foydalanuvchiga avtomatik
tasdiqlash xabarini yuboradi.

## 8. Test qilish

1. Local: `cd backend && pip install -r requirements.txt && uvicorn
   app.main:app --reload` (localda test uchun `ngrok`orqali tunnel oching,
   chunki Telegram HTTPS talab qiladi).
2. Botga `/start` yozing → "O'yinni boshlash" tugmasi chiqishi kerak.
3. Mini App'ni oching, 60 soniyalik o'yinni sinab ko'ring.
4. AdsGram test rejimini yoqib, rewarded video → postback → 💎 qo'shilishini
   tekshiring.

---

## Xavfsizlik bo'yicha eslatmalar (o'zgartirmang!)

- Har bir `/api/*` so'rov `X-Telegram-Init-Data` header orqali HMAC bilan
  tekshiriladi (`app/auth.py`) — buni olib tashlamang.
- Diamond/continue/multiplier/daily-double mukofotlari **faqat**
  `/api/ads/postback` orqali beriladi, hech qachon clientdan kelgan
  "ad watched" signalidan emas.
- Stars pool va cashout holati `SELECT ... FOR UPDATE` bilan qulflanadi —
  bir vaqtning o'zida ko'p so'rov kelsa ham pool manfiyga tushmaydi.
