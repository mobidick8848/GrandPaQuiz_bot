#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GrandPaQuiz_bot_web — Telegram quiz for grandpa Sergey 🎉
aiogram 3.x | Webhook-mode for Render Web Service
"""

import os
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Set

from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    Message
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/") + "/webhook"
QUESTIONS_FILE = os.getenv("QUESTIONS_FILE", "questions.json")
RESULTS_FILE = os.getenv("RESULTS_FILE", "results.json")
PORT = int(os.getenv("PORT", "10000"))
LEADERS_TOP_N = int(os.getenv("LEADERS_TOP_N", "10"))

# --- DATA ---
def load_questions(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_results(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_results(path: str, results: List[Dict[str, Any]]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

def get_leaderboard(path: str, top_n: int) -> str:
    results = load_results(path)
    if not results:
        return "Пока нет результатов 😅"
    best = {}
    for r in results:
        n = r.get("name", "Без имени")
        s = int(r.get("score", 0))
        t = int(r.get("total", 0))
        if n not in best or s > best[n]["score"]:
            best[n] = {"score": s, "total": t}
    table = sorted(best.items(), key=lambda kv: (-kv[1]["score"], kv[0].lower()))
    lines = ["🏆 Рейтинг:\n"]
    for i, (n, st) in enumerate(table[:top_n], 1):
        lines.append(f"{i}. {n} — {st['score']}/{st['total']}")
    return "\n".join(lines)


questions = load_questions(QUESTIONS_FILE)

# --- FSM ---
class Quiz(StatesGroup):
    name = State()
    quiz = State()

router = Router()

# --- Keyboards ---
def kb_start():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать викторину", callback_data="start_quiz")]
    ])

def kb_single(opts, qid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=o, callback_data=f"s:{qid}:{i}")] for i, o in enumerate(opts)
    ])

def kb_multi(opts, qid, sel: Set[int]):
    rows = []
    for i, o in enumerate(opts):
        mark = "✅ " if i in sel else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{o}", callback_data=f"m:{qid}:{i}")])
    rows.append([InlineKeyboardButton(text="➡️ Готово", callback_data=f"m_done:{qid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- Bot logic ---
@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await msg.answer("🎂 Привет! Это викторина про дедушку Серёжу 🎉\nКто знает его лучше всех? 🏆", reply_markup=kb_start())

@router.callback_query(F.data == "start_quiz")
async def start_quiz(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Как тебя зовут, герой? 😊")
    await state.set_state(Quiz.name)

@router.message(Quiz.name)
async def set_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    await state.update_data(name=name, score=0, current_q=0)
    await msg.answer(f"Отлично, {name}! Поехали 🚀")
    await send_next_question(msg, state, 0)

async def send_next_question(msg_or_cb, state: FSMContext, qid: int):
    data = await state.get_data()
    if qid >= len(questions):
        score = data.get("score", 0)
        name = data.get("name", "Без имени")
        results = load_results(RESULTS_FILE)
        results.append({"name": name, "score": score, "total": len(questions)})
        save_results(RESULTS_FILE, results)
        await msg_or_cb.answer(f"✅ Викторина окончена!\nТы набрал {score}/{len(questions)}.\n\n{get_leaderboard(RESULTS_FILE, LEADERS_TOP_N)}")
        await state.clear()
        return
    q = questions[qid]
    await state.update_data(current_q=qid)
    if q["type"] == "single":
        await msg_or_cb.answer(q["question"], reply_markup=kb_single(q["options"], qid))
    else:
        await state.update_data(sel=[])
        await msg_or_cb.answer(q["question"], reply_markup=kb_multi(q["options"], qid, set()))

# --- SINGLE ---
@router.callback_query(F.data.startswith("s:"))
async def single_answer(callback: CallbackQuery, state: FSMContext):
    _, qid, idx = callback.data.split(":")
    qid, idx = int(qid), int(idx)
    q = questions[qid]
    data = await state.get_data()
    score = data.get("score", 0)
    if idx == q["answer_index"]:
        score += 1
    await state.update_data(score=score)
    await send_next_question(callback.message, state, qid + 1)

# --- MULTI ---
@router.callback_query(F.data.startswith("m:"))
async def multi_select(callback: CallbackQuery, state: FSMContext):
    qid = int(callback.data.split(":")[1])
    idx = int(callback.data.split(":")[2])
    data = await state.get_data()
    sel = set(data.get("sel", []))
    if idx in sel:
        sel.remove(idx)
    else:
        sel.add(idx)
    await state.update_data(sel=list(sel))
    q = questions[qid]
    await callback.message.edit_text(
        q["question"],
        reply_markup=kb_multi(q["options"], qid, sel)
    )

@router.callback_query(F.data.startswith("m_done:"))
async def multi_done(callback: CallbackQuery, state: FSMContext):
    qid = int(callback.data.split(":")[1])
    data = await state.get_data()
    sel = set(data.get("sel", []))
    q = questions[qid]
    correct = set(q["answer_index"])
    score = data.get("score", 0)
    if sel == correct:
        score += 1
    await state.update_data(score=score, sel=[])
    await send_next_question(callback.message, state, qid + 1)


# --- WEBHOOK SETUP ---
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    app = web.Application()
    app["bot"] = bot

    dp.startup.register(lambda: on_startup(bot))
    dp.shutdown.register(lambda: on_shutdown(bot))

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    print(f"🚀 GrandPaQuiz_bot_web running on port {PORT}")
    await site.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
