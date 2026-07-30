#!/usr/bin/env python3
"""FinForge AI Bot — продавец AI-ботов для бизнеса. PTB v22."""

import os
import logging
import sqlite3
import re
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
import httpx

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
TOKEN = os.environ.get("FINFORGE_BOT_TOKEN", "")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "353133098"))
DB_PATH = "/root/.hermes/finforge.db"
ROI_URL = "https://victoria-hugo-fares-triumph.trycloudflare.com/finforge-roi.html"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("finforge")

# ═══════════════════════════════════════════════════════════════════
# CONVERSATION STATES
# ═══════════════════════════════════════════════════════════════════
(
    APPOINTMENT_DATE,
    APPOINTMENT_TIME,
    APPOINTMENT_SERVICE,
    CONTACT_PHONE,
    AUDIT_Q1,
    AUDIT_Q2,
    AUDIT_Q3,
) = range(7)

# ═══════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = (
    "ТЫ — FinForge AI. НЕ DeepSeek. НЕ ChatGPT. Ты продаёшь AI-ботов бизнесу. "
    "Цены: 4.900/9.900/29.900₽. Веди к /appointment или /audit. "
    "Никакой энциклопедии. Никаких общих знаний. Числа = ДАТЫ. "
    "Отвечай ТОЛЬКО по делу, максимум 2-3 предложения."
)

# ═══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════════════════════════════


def nav_kb():
    """Универсальная клавиатура с кнопкой 🏠 Меню (для диалогов)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Меню", callback_data="nav_home")]]
    )


HOME_KB = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("📅 Записаться", callback_data="nav_demo"),
            InlineKeyboardButton("💰 Цены", callback_data="nav_pricing"),
        ],
        [
            InlineKeyboardButton("📊 ROI", callback_data="nav_roi"),
            InlineKeyboardButton("🆓 Аудит", callback_data="nav_audit"),
        ],
        [InlineKeyboardButton("📞 Менеджер", callback_data="nav_contact")],
    ]
)

PRICING_KB = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("📊 ROI-калькулятор", callback_data="nav_roi")],
        [InlineKeyboardButton("🏠 Меню", callback_data="nav_home")],
    ]
)


# ═══════════════════════════════════════════════════════════════════
# SAFE SEND (Markdown fallback — никаких падений)
# ═══════════════════════════════════════════════════════════════════


async def safe_reply(update: Update, text: str, kb=None):
    """Безопасный reply_text с Markdown-fallback."""
    try:
        await update.message.reply_text(
            text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        try:
            await update.message.reply_text(text, reply_markup=kb)
        except Exception as e:
            logger.warning(f"safe_reply final fallback: {e}")


async def safe_edit(query, text: str, kb=None):
    """Безопасный edit_message_text с Markdown-fallback."""
    try:
        await query.edit_message_text(
            text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        try:
            await query.edit_message_text(text, reply_markup=kb)
        except Exception as e:
            logger.warning(f"safe_edit final fallback: {e}")


async def safe_callback_edit(update: Update, text: str, kb=None):
    """Для использования в callback-обработчиках: answer + edit."""
    q = update.callback_query
    await q.answer()
    await safe_edit(q, text, kb)


# ═══════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════


def _connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    db = _connect()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            name TEXT,
            phone TEXT,
            business TEXT,
            stage TEXT DEFAULT 'lead',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            date TEXT,
            time TEXT,
            service TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            message TEXT,
            classification TEXT,
            score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            answers TEXT,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """
    )
    db.commit()
    db.close()


def db_run(query, params=()):
    db = _connect()
    db.execute(query, params)
    db.commit()
    db.close()


def db_get(query, params=()):
    db = _connect()
    db.row_factory = sqlite3.Row
    cur = db.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    db.close()
    return rows


# ═══════════════════════════════════════════════════════════════════
# DEEPSEEK AI
# ═══════════════════════════════════════════════════════════════════


async def ask_deepseek(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Вызов DeepSeek v4 Flash API."""
    if not DEEPSEEK_KEY:
        return _fallback_answer(prompt)

    try:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-v4-flash",
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                },
            )
            if r.status_code == 200:
                data = r.json()
                choice = data["choices"][0]["message"]
                return choice.get("content", "") or choice.get(
                    "reasoning_content", ""
                )
            else:
                logger.warning(f"DeepSeek HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"DeepSeek error: {e}")

    return _fallback_answer(prompt)


def _fallback_answer(q: str) -> str:
    """Локальный fallback если API недоступен."""
    ql = q.lower()
    if any(w in ql for w in ["записа", "приём", "демо"]):
        return (
            "📅 Чтобы записаться на демо, нажмите кнопку «📅 Записаться» "
            "в главном меню /start"
        )
    if any(w in ql for w in ["цен", "сколько", "прайс", "стоимост"]):
        return (
            "💰 *Тарифы:*\n"
            "🔹 Базовый — 4.900₽/мес\n"
            "🚀 Бизнес — 9.900₽/мес\n"
            "🏢 Корп — от 29.900₽"
        )
    if any(w in ql for w in ["контакт", "телефон", "менеджер", "связ"]):
        return "📞 Нажмите кнопку «📞 Менеджер» в меню /start для связи"
    if any(w in ql for w in ["рои", "окупа", "экономи", "калькулятор"]):
        return f"📊 ROI-калькулятор доступен по кнопке в меню: {ROI_URL}"
    if any(w in ql for w in ["привет", "здравств", "добрый"]):
        return (
            "👋 Здравствуйте! Я FinForge AI — помогаю бизнесу с AI-ботами. "
            "Выберите действие в меню /start или задайте вопрос."
        )
    if any(w in ql for w in ["помощ", "help", "что ты", "команд"]):
        return (
            "🤖 *FinForge AI* — AI-продавец для вашего бизнеса.\n"
            "Могу: ответить на вопросы клиентов 24/7, "
            "оценивать заинтересованность, записывать на услуги.\n"
            "Используйте меню /start."
        )
    return (
        "✅ Я вас услышал. Выберите действие в меню "
        "или задайте вопрос про AI-ботов для бизнеса."
    )


# ═══════════════════════════════════════════════════════════════════
# LEAD CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════


def classify_lead(text: str):
    """Классификация лида: hot / warm / cold."""
    if re.search(
        r"(?i)(купить|оплатить|счёт|договор|готов|беру|заказываю|оформ)",
        text,
    ):
        return "hot", 0.95
    if re.search(
        r"(?i)(подробн|расскажи|консультац|записаться|пример|покаж|интересует|рассказать)",
        text,
    ):
        return "warm", 0.7
    return "cold", 0.3


# ═══════════════════════════════════════════════════════════════════
# NAVIGATION HANDLER (не-диалоговые callback'и)
# ═══════════════════════════════════════════════════════════════════


async def nav_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка nav_home, nav_pricing, nav_roi (без ConversationHandler)."""
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "nav_home":
        await safe_edit(
            q,
            (
                "🤖 *FinForge AI* — ваш цифровой сотрудник\n\n"
                "✅ Отвечает за 5 секунд\n"
                "✅ Оценивает заинтересованность\n"
                "✅ Запись 24/7\n\n"
                "Выберите действие:"
            ),
            HOME_KB,
        )

    elif data == "nav_pricing":
        await safe_edit(
            q,
            (
                "💰 *Тарифы*\n\n"
                "🔹 *Базовый* — 4.900₽/мес\n"
                " FAQ-бот 24/7, ответы за 5 сек\n\n"
                "🚀 *Бизнес* — 9.900₽/мес\n"
                " + оценка заинтересованности, запись на услуги\n\n"
                "🏢 *Корп* — от 29.900₽\n"
                " + CRM-интеграция, глубокая кастомизация"
            ),
            PRICING_KB,
        )

    elif data == "nav_roi":
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📊 Открыть ROI-калькулятор",
                        web_app=WebAppInfo(url=ROI_URL),
                    )
                ],
                [InlineKeyboardButton("🏠 Меню", callback_data="nav_home")],
            ]
        )
        await safe_edit(
            q,
            (
                "📊 *ROI-калькулятор*\n\n"
                "Узнайте, сколько вы сэкономите с AI-ботом "
                "вместо живого сотрудника.\n\n"
                "Нажмите кнопку ниже, чтобы открыть:"
            ),
            kb,
        )


# ═══════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню."""
    await update.message.reply_text(
        (
            "🤖 *FinForge AI* — ваш цифровой сотрудник\n\n"
            "✅ Отвечает за 5 секунд\n"
            "✅ Оценивает заинтересованность\n"
            "✅ Запись 24/7\n\n"
            "Выберите действие:"
        ),
        reply_markup=HOME_KB,
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════
# FREE TEXT → AI ANSWER
# ═══════════════════════════════════════════════════════════════════


async def handle_free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Любое текстовое сообщение → AI ответ с контекстом продавца."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    uid = update.effective_user.id
    username = update.effective_user.first_name or f"ID:{uid}"

    # Классифицируем лида
    lvl, score = classify_lead(text)
    db_run(
        "INSERT INTO leads(tg_id,message,classification,score) VALUES(?,?,?,?)",
        (uid, text[:500], lvl, score),
    )

    # Выбираем системный промпт в зависимости от «горячести»
    if lvl == "hot":
        sys = (
            "Ты FinForge AI. Предложи цену (4.900 / 9.900 / от 29.900), запиши на демо. "
        )
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"🔥 *ГОРЯЧИЙ ЛИД* от @{update.effective_user.username or username}:\n"
                f"{text[:200]}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    elif lvl == "warm":
        sys = (
            "Ты FinForge AI. Расскажи про возможности: FAQ-бот 24/7, запись на услуги, напоминания. "
            "Предложи /audit или /appointment."
        )
    else:
        sys = SYSTEM_PROMPT

    await update.message.reply_chat_action("typing")
    reply = await ask_deepseek(text, sys, 600)
    await safe_reply(update, reply, nav_kb())


# ═══════════════════════════════════════════════════════════════════
# APPOINTMENT CONVERSATION (nav_demo)
# ═══════════════════════════════════════════════════════════════════


async def appt_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Вход в диалог записи на демо."""
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await safe_edit(
            q,
            (
                "📅 *Запись на демо*\n\n"
                "Напишите желаемую дату\n"
                "Например: *25 июля* или *2026-07-25*"
            ),
            nav_kb(),
        )
    else:
        await safe_reply(update, "📅 Напишите желаемую дату для демо:", nav_kb())
    return APPOINTMENT_DATE


async def appt_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Принимаем дату."""
    text = update.message.text.strip()
    if text.lower() in ("отмена", "меню", "назад", "/cancel"):
        await safe_reply(update, "❌ Запись отменена.", HOME_KB)
        return ConversationHandler.END

    ctx.user_data["appt_date"] = text
    await safe_reply(
        update,
        f"📅 *{text}* — принято!\nТеперь напишите время (например: *14:00*):",
        nav_kb(),
    )
    return APPOINTMENT_TIME


async def appt_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Принимаем время."""
    text = update.message.text.strip()
    if text.lower() in ("отмена", "меню", "назад", "/cancel"):
        await safe_reply(update, "❌ Запись отменена.", HOME_KB)
        return ConversationHandler.END

    ctx.user_data["appt_time"] = text
    await safe_reply(
        update,
        (
            "💼 Какую услугу выбираете?\n\n"
            "• *Консультация*\n"
            "• *FAQ-бот*\n"
            "• *Бизнес*\n"
            "• *Корп*\n\n"
            "Напишите название:"
        ),
        nav_kb(),
    )
    return APPOINTMENT_SERVICE


async def appt_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сохраняем запись, уведомляем админа."""
    text = update.message.text.strip()
    if text.lower() in ("отмена", "меню", "назад", "/cancel"):
        await safe_reply(update, "❌ Запись отменена.", HOME_KB)
        return ConversationHandler.END

    uid = update.effective_user.id
    username = update.effective_user.first_name or f"ID:{uid}"
    dt = ctx.user_data.get("appt_date", "?")
    tm = ctx.user_data.get("appt_time", "?")

    # Сохраняем в БД
    db_run("INSERT OR IGNORE INTO clients(tg_id) VALUES(?)", (uid,))
    rows = db_get("SELECT id FROM clients WHERE tg_id=?", (uid,))
    if rows:
        db_run(
            "INSERT INTO appointments(client_id,date,time,service,status) "
            "VALUES(?,?,?,?,?)",
            (rows[0]["id"], dt, tm, text, "confirmed"),
        )

    # Ответ клиенту
    await safe_reply(
        update,
        (
            f"✅ *Запись подтверждена!*\n\n"
            f"📅 Дата: *{dt}*\n"
            f"🕐 Время: *{tm}*\n"
            f"💼 Услуга: *{text}*\n\n"
            f"Менеджер свяжется с вами в ближайшее время."
        ),
        nav_kb(),
    )

    # Уведомление админу
    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            (
                f"📅 *НОВАЯ ЗАПИСЬ*\n"
                f"Клиент: {username}\n"
                f"Дата: {dt}\n"
                f"Время: {tm}\n"
                f"Услуга: {text}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Admin notify failed: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════
# CONTACT CONVERSATION (nav_contact)
# ═══════════════════════════════════════════════════════════════════


async def contact_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Вход в диалог сохранения контакта."""
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await safe_edit(
            q,
            (
                "📞 *Связь с менеджером*\n\n"
                "Напишите ваш номер телефона\n"
                "Например: *89991234567*"
            ),
            nav_kb(),
        )
    else:
        await safe_reply(
            update, "📞 Напишите ваш номер телефона:", nav_kb()
        )
    return CONTACT_PHONE


async def contact_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Валидация и сохранение номера."""
    text = update.message.text.strip()
    if text.lower() in ("отмена", "меню", "назад", "/cancel"):
        await safe_reply(update, "❌ Отменено.", HOME_KB)
        return ConversationHandler.END

    # Валидация: только цифры, 6–15 знаков
    digits = re.sub(r"\D", "", text)
    if len(digits) < 6 or len(digits) > 15:
        await safe_reply(
            update,
            "❌ Некорректный номер. Введите номер цифрами (не менее 6 цифр):",
            nav_kb(),
        )
        return CONTACT_PHONE

    uid = update.effective_user.id
    username = update.effective_user.first_name or f"ID:{uid}"

    # Сохраняем
    db_run("INSERT OR IGNORE INTO clients(tg_id) VALUES(?)", (uid,))
    db_run("UPDATE clients SET phone=?, stage='lead' WHERE tg_id=?", (digits, uid))

    await safe_reply(
        update,
        "✅ *Номер сохранён!*\n\nМенеджер свяжется с вами в течение часа.",
        nav_kb(),
    )

    # Уведомление админу
    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            f"📞 *НОВЫЙ КОНТАКТ*\nКлиент: {username}\nТелефон: {digits}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Admin notify failed: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════
# AUDIT CONVERSATION (nav_audit)
# ═══════════════════════════════════════════════════════════════════


async def audit_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Вход в диалог бесплатного аудита."""
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await safe_edit(
            q,
            (
                "🆓 *Бесплатный аудит*\n\n"
                "*Вопрос 1/3:* Какими каналами вы получаете заявки?\n"
                "(сайт / Telegram / WhatsApp / звонки / другое)"
            ),
            nav_kb(),
        )
    else:
        await safe_reply(
            update,
            "🆓 *Бесплатный аудит*\n\n*Вопрос 1/3:* Какими каналами получаете заявки?",
            nav_kb(),
        )
    return AUDIT_Q1


async def audit_q1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ("отмена", "меню", "назад", "/cancel"):
        await safe_reply(update, "❌ Аудит отменён.", HOME_KB)
        return ConversationHandler.END

    ctx.user_data["a1"] = text
    await safe_reply(
        update,
        (
            "*Вопрос 2/3:* Какая скорость ответа у вас сейчас?\n"
            "Например: *5 минут*, *2 часа*, *сутки*"
        ),
        nav_kb(),
    )
    return AUDIT_Q2


async def audit_q2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ("отмена", "меню", "назад", "/cancel"):
        await safe_reply(update, "❌ Аудит отменён.", HOME_KB)
        return ConversationHandler.END

    ctx.user_data["a2"] = text
    await safe_reply(
        update,
        (
            "*Вопрос 3/3:* Сколько обращений в день вы получаете?\n"
            "Например: *10*, *50*, *100+*\n\n"
            "🔄 Анализирую после вашего ответа…"
        ),
        nav_kb(),
    )
    return AUDIT_Q3


async def audit_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ("отмена", "меню", "назад", "/cancel"):
        await safe_reply(update, "❌ Аудит отменён.", HOME_KB)
        return ConversationHandler.END

    uid = update.effective_user.id
    username = update.effective_user.first_name or f"ID:{uid}"
    answers = [
        ctx.user_data.get("a1", ""),
        ctx.user_data.get("a2", ""),
        text,
    ]

    # AI-анализ
    prompt = (
        f"Проанализируй бизнес:\n"
        f"- Каналы заявок: {answers[0]}\n"
        f"- Скорость ответа: {answers[1]}\n"
        f"- Обращений в день: {answers[2]}\n\n"
        f"Дай короткий ответ (без маркдауна):\n"
        f"1) Оценка готовности к AI-боту (готов / частично / не готов)\n"
        f"2) Конкретная рекомендация\n"
        f"3) Примерная экономия в ₽/мес"
    )
    analysis = await ask_deepseek(
        prompt, "Ты бизнес-аналитик FinForge AI. Отвечай коротко, по делу.", 400
    ) or "Рекомендуем записаться на консультацию для точного расчёта."

    # Сохраняем в БД
    db_run(
        "INSERT INTO audits(tg_id,answers,result) VALUES(?,?,?)",
        (uid, json.dumps(answers, ensure_ascii=False), analysis),
    )

    # Клавиатура с действиями после аудита
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 Записаться на демо", callback_data="nav_demo"),
                InlineKeyboardButton("📞 Менеджер", callback_data="nav_contact"),
            ],
            [InlineKeyboardButton("🏠 Меню", callback_data="nav_home")],
        ]
    )

    await safe_reply(
        update,
        f"📊 *Результат аудита*\n\n{analysis}\n\n_Что дальше?_",
        kb,
    )

    # Уведомление админу
    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            (
                f"🆓 *АУДИТ*\n"
                f"Клиент: {username}\n"
                f"Каналы: {answers[0]}\n"
                f"Скорость: {answers[1]}\n"
                f"Обращений: {answers[2]}\n\n"
                f"Результат: {analysis[:300]}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Admin notify failed: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════
# FALLBACK: 🏠 Меню во время любого диалога → возврат в /start
# ═══════════════════════════════════════════════════════════════════


async def cancel_to_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выход из любого диалога в главное меню (по кнопке 🏠 Меню)."""
    q = update.callback_query
    await q.answer()
    await safe_edit(
        q,
        (
            "🤖 *FinForge AI* — ваш цифровой сотрудник\n\n"
            "✅ Отвечает за 5 секунд\n"
            "✅ Оценивает заинтересованность\n"
            "✅ Запись 24/7\n\n"
            "Выберите действие:"
        ),
        HOME_KB,
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отмена диалога по команде /cancel."""
    await safe_reply(update, "❌ Отменено. Что хотите сделать?", HOME_KB)
    ctx.user_data.clear()
    return ConversationHandler.END


async def start_fallback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Fallback для /start внутри диалога."""
    await cmd_start(update, ctx)
    ctx.user_data.clear()
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════


def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    # ── Фабрика ConversationHandler ──
    # Каждый диалог имеет:
    #   entry_points: CallbackQueryHandler по прямому pattern=
    #   fallbacks:    🏠 Меню (nav_home) + /start
    #   Входные функции проверяют update.callback_query

    # 1. Запись на демо
    conv_app = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(appt_entry, pattern="^nav_demo$"),
        ],
        states={
            APPOINTMENT_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, appt_date),
            ],
            APPOINTMENT_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, appt_time),
            ],
            APPOINTMENT_SERVICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, appt_save),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_to_home, pattern="^nav_home$"),
            CommandHandler("start", start_fallback),
            CommandHandler("cancel", cancel_text),
        ],
        name="appointment_conv",
    )

    # 2. Контакт
    conv_contact = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(contact_entry, pattern="^nav_contact$"),
        ],
        states={
            CONTACT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, contact_save),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_to_home, pattern="^nav_home$"),
            CommandHandler("start", start_fallback),
            CommandHandler("cancel", cancel_text),
        ],
        name="contact_conv",
    )

    # 3. Аудит
    conv_audit = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(audit_entry, pattern="^nav_audit$"),
        ],
        states={
            AUDIT_Q1: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q1),
            ],
            AUDIT_Q2: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, audit_q2),
            ],
            AUDIT_Q3: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, audit_done),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_to_home, pattern="^nav_home$"),
            CommandHandler("start", start_fallback),
            CommandHandler("cancel", cancel_text),
        ],
        name="audit_conv",
    )

    # ── Регистрация в порядке: conv_app → conv_contact → conv_audit →
    #    nav_handler → commands → free_text ──

    app.add_handler(conv_app)
    app.add_handler(conv_contact)
    app.add_handler(conv_audit)

    # Навигационные callback'и (nav_home, nav_pricing, nav_roi)
    app.add_handler(
        CallbackQueryHandler(nav_handler, pattern="^(nav_home|nav_pricing|nav_roi)$")
    )

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))  # /help = меню

    # Свободный текст → AI (самый последний)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text)
    )

    logger.info("FinForge AI Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
