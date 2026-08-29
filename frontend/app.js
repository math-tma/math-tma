const API_BASE = "https://math-tma-production.up.railway.app"; 

const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

// ---------------------------------------------------------------- API --

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(API_BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": tg.initData,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${text}`);
  }
  return res.status === 204 ? null : res.json();
}

// ------------------------------------------------------------- tab nav --

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "tasks") loadTasks();
    if (btn.dataset.tab === "shop") loadShop();
  });
});

// ------------------------------------------------------- online/offline --

function updateNetBadge() {
  const badge = document.getElementById("netBadge");
  const online = navigator.onLine;
  badge.textContent = online ? "Onlayn" : "Offlayn";
  badge.className = online ? "online" : "offline";
  // Ads / diamonds / stars only make sense online — the rest of the UI still
  // works offline (solving problems for coins does not require the server
  // beyond starting/finishing a session, which does need connectivity too;
  // true "answer locally while offline" would require pre-fetching a batch
  // and syncing later — kept simple here: game requires connectivity to
  // start, but no ads/diamond UI is shown while offline).
  document.getElementById("multiplierBtn").classList.toggle("hidden", !online);
}
window.addEventListener("online", updateNetBadge);
window.addEventListener("offline", updateNetBadge);
updateNetBadge();

// ------------------------------------------------------------- profile --

let me = null;

async function loadMe() {
  me = await api("/api/me");
  document.getElementById("coinsBalance").textContent = `🪙 ${me.coins}`;
  document.getElementById("diamondsBalance").textContent = `💎 ${me.diamonds}`;
  document.getElementById("refLinkInput").value = me.referral_link;

  const need = me.diamonds_per_batch;
  const have = Math.min(me.diamonds, need);
  document.getElementById("starsProgressFill").style.width = `${(have / need) * 100}%`;
  document.getElementById("starsProgressText").textContent = `${me.diamonds} / ${need}`;
  document.getElementById("starsRatio").textContent = `${need} 💎 = ${me.stars_per_batch} ⭐`;

  if (me.selected_background_css) {
    document.getElementById("app").style.background = me.selected_background_css;
  }

  const btn = document.getElementById("starsRequestBtn");
  const notice = document.getElementById("cashoutNotice");
  if (!me.cashout_open) {
    btn.disabled = true;
    notice.classList.remove("hidden");
  } else if (me.diamonds < need || me.stars_pool_remaining < me.stars_per_batch) {
    btn.disabled = true;
    notice.classList.add("hidden");
  } else {
    btn.disabled = false;
    notice.classList.add("hidden");
  }
}

// ---------------------------------------------------------------- ads --

function requestAndShowAd(purpose, sessionId) {
  return new Promise(async (resolve) => {
    let adViewId;
    try {
      const r = await api("/api/ads/request", { method: "POST", body: { purpose, session_id: sessionId } });
      adViewId = r.ad_view_id;
    } catch (e) {
      resolve(false);
      return;
    }

    const AdController = window.Adsgram.init({ blockId: me.adsgram_block_id });

    AdController.show()
      .then(async () => {
        // Ad finished on the client — but we only trust the server postback.
        // Poll briefly for AdsGram's server-to-server callback to land.
        for (let i = 0; i < 10; i++) {
          await new Promise((r) => setTimeout(r, 1000));
          const status = await api(`/api/ads/status/${adViewId}`);
          if (status.status === "completed") {
            resolve(true);
            return;
          }
        }
        resolve(false); // postback didn't arrive in time
      })
      .catch(() => resolve(false));
  });
}

// -------------------------------------------------------------- daily --

document.getElementById("dailyBtn").addEventListener("click", async () => {
  try {
    const r = await api("/api/daily/claim", { method: "POST" });
    tg.showAlert(`🎁 ${r.diamonds} 💎 oldingiz!`);
    await loadMe();
  } catch (e) {
    tg.showAlert("Bugungi bonusni allaqachon oldingiz.");
  }
});

// --------------------------------------------------------------- game --

let session = null;
let problemQueue = [];
let currentProblem = null;
let liveScore = 0;
let timerInterval = null;
let usedContinue = false;

document.getElementById("startBtn").addEventListener("click", startGame);
document.getElementById("playAgainBtn").addEventListener("click", startGame);

async function startGame() {
  const r = await api("/api/game/start", { method: "POST" });
  session = { id: r.session_id, endsAt: new Date(r.ends_at).getTime() };
  problemQueue = r.problems;
  liveScore = 0;
  usedContinue = false;

  document.getElementById("startScreen").classList.add("hidden");
  document.getElementById("resultScreen").classList.add("hidden");
  document.getElementById("continueOffer").classList.add("hidden");
  document.getElementById("gameScreen").classList.remove("hidden");
  document.getElementById("liveScore").textContent = "0";

  nextQuestion();
  startTimer();
}

function startTimer() {
  clearInterval(timerInterval);
  const totalMs = session.endsAt - Date.now();
  const totalStart = totalMs;
  timerInterval = setInterval(() => {
    const remainingMs = session.endsAt - Date.now();
    if (remainingMs <= 0) {
      clearInterval(timerInterval);
      document.getElementById("timerText").textContent = "0";
      document.getElementById("timerFill").style.width = "0%";
      offerContinueOrFinish();
      return;
    }
    document.getElementById("timerText").textContent = Math.ceil(remainingMs / 1000);
    document.getElementById("timerFill").style.width = `${Math.max(0, (remainingMs / totalStart) * 100)}%`;
  }, 100);
}

function nextQuestion() {
  currentProblem = problemQueue.shift();
  if (!currentProblem) return; // batch exhausted (shouldn't happen within 60-75s)
  document.getElementById("questionText").textContent = currentProblem.question;
  const optionsEl = document.getElementById("options");
  optionsEl.innerHTML = "";
  currentProblem.options.forEach((opt, idx) => {
    const b = document.createElement("button");
    b.className = "optionBtn";
    b.textContent = opt;
    b.addEventListener("click", () => submitAnswer(idx, b));
    optionsEl.appendChild(b);
  });
}

async function submitAnswer(idx, btnEl) {
  if (Date.now() > session.endsAt) return;
  document.querySelectorAll(".optionBtn").forEach((b) => (b.disabled = true));
  try {
    const r = await api("/api/game/answer", {
      method: "POST",
      body: { session_id: session.id, problem_id: currentProblem.id, selected_index: idx },
    });
    btnEl.classList.add(r.correct ? "correct" : "wrong");
    if (r.correct) {
      liveScore++;
      document.getElementById("liveScore").textContent = liveScore;
    }
  } catch (e) {
    // ignore and move on
  }
  setTimeout(() => {
    document.querySelectorAll(".optionBtn").forEach((b) => {
      b.disabled = false;
      b.classList.remove("correct", "wrong");
    });
    nextQuestion();
  }, 300);
}

async function offerContinueOrFinish() {
  if (!usedContinue && navigator.onLine) {
    document.getElementById("gameScreen").classList.add("hidden");
    document.getElementById("continueOffer").classList.remove("hidden");
  } else {
    await endGame();
  }
}

document.getElementById("continueBtn").addEventListener("click", async () => {
  document.getElementById("continueBtn").disabled = true;
  const ok = await requestAndShowAd("continue", session.id);
  usedContinue = true;
  if (ok) {
    session.endsAt += 15000; // optimistic UI; server already extended ends_at authoritatively
    document.getElementById("continueOffer").classList.add("hidden");
    document.getElementById("gameScreen").classList.remove("hidden");
    startTimer();
  } else {
    tg.showAlert("Reklama tasdiqlanmadi. O'yin yakunlanadi.");
    await endGame();
  }
});

document.getElementById("stopBtn").addEventListener("click", endGame);

async function endGame() {
  document.getElementById("gameScreen").classList.add("hidden");
  document.getElementById("continueOffer").classList.add("hidden");
  const r = await api("/api/game/finish", { method: "POST", body: { session_id: session.id } });
  document.getElementById("finalScore").textContent = liveScore;
  document.getElementById("coinsEarned").textContent = r.coins_earned;
  document.getElementById("resultScreen").classList.remove("hidden");
  await loadMe();
}

document.getElementById("multiplierBtn").addEventListener("click", async () => {
  document.getElementById("multiplierBtn").disabled = true;
  const ok = await requestAndShowAd("multiplier", session.id);
  if (ok) {
    tg.showAlert("2x qo'llandi! Keyingi natijalar yangilanadi.");
  } else {
    tg.showAlert("Reklama tasdiqlanmadi.");
  }
  document.getElementById("multiplierBtn").disabled = false;
});

// --------------------------------------------------------------- shop --

async function loadShop() {
  const backgrounds = await api("/api/shop/backgrounds");
  const el = document.getElementById("shopGrid");
  el.innerHTML = "";
  backgrounds.forEach((bg) => {
    const card = document.createElement("div");
    card.className = "bgCard" + (bg.selected ? " selectedCard" : "");

    const preview = document.createElement("div");
    preview.className = "bgPreview" + (bg.animated ? " animated" : "");
    preview.style.background = bg.css_value;
    preview.style.backgroundSize = bg.animated ? "200% 200%" : "cover";

    const info = document.createElement("div");
    info.className = "bgInfo";
    const nameEl = document.createElement("div");
    nameEl.className = "bgName";
    nameEl.textContent = bg.name;

    const btn = document.createElement("button");
    if (bg.selected) {
      btn.textContent = "✅ Tanlangan";
      btn.className = "secondary";
      btn.disabled = true;
    } else if (bg.owned) {
      btn.textContent = "Tanlash";
      btn.className = "primary";
      btn.addEventListener("click", async () => {
        await api("/api/shop/select", { method: "POST", body: { background_id: bg.id } });
        await loadMe();
        await loadShop();
      });
    } else {
      btn.textContent = `${bg.price} 🪙`;
      btn.className = "secondary";
      btn.addEventListener("click", async () => {
        try {
          await api("/api/shop/buy", { method: "POST", body: { background_id: bg.id } });
          tg.showAlert(`✅ "${bg.name}" sotib olindi!`);
          await loadMe();
          await loadShop();
        } catch (e) {
          tg.showAlert("Tanga yetarli emas.");
        }
      });
    }

    info.appendChild(nameEl);
    info.appendChild(btn);
    card.appendChild(preview);
    card.appendChild(info);
    el.appendChild(card);
  });
}

// -------------------------------------------------------------- tasks --

async function loadTasks() {
  const tasks = await api("/api/tasks");
  const el = document.getElementById("tasksList");
  el.innerHTML = "";
  tasks.forEach((t) => {
    const div = document.createElement("div");
    div.className = "task";
    div.innerHTML = `<span>${t.channel_username} — ${t.reward} 💎</span>`;
    const btn = document.createElement("button");
    btn.textContent = "Tekshirish";
    btn.className = "secondary";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const r = await api("/api/tasks/verify", { method: "POST", body: { task_id: t.id } });
        tg.showAlert(`✅ ${r.reward} 💎 oldingiz!`);
        await loadMe();
      } catch (e) {
        tg.showAlert("Hali obuna bo'lmagansiz yoki mukofot olingan.");
      }
      btn.disabled = false;
    });
    div.appendChild(btn);
    el.appendChild(div);
  });
}

// ----------------------------------------------------------- referral --

document.getElementById("copyRefBtn").addEventListener("click", () => {
  navigator.clipboard.writeText(document.getElementById("refLinkInput").value);
  tg.showAlert("Havola nusxalandi!");
});
document.getElementById("shareRefBtn").addEventListener("click", () => {
  const link = document.getElementById("refLinkInput").value;
  tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}`);
});

// -------------------------------------------------------------- stars --

document.getElementById("starsRequestBtn").addEventListener("click", async () => {
  try {
    await api("/api/stars/request", { method: "POST" });
    tg.showAlert("So'rovingiz qabul qilindi! Admin tez orada Stars yuboradi.");
    await loadMe();
  } catch (e) {
    tg.showAlert("Hozircha so'rov yuborib bo'lmadi.");
  }
});

// ----------------------------------------------------------------- go --

loadMe();
