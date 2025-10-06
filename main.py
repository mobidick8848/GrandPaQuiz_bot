#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GrandPaQuiz_bot_web — Telegram quiz for grandpa Sergey 🎂
aiogram 3.x | Webhook-mode for Render Web Service
"""
import os
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Set

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

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

def reset_results(path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

def save_result(path: str, record: Dict[str, Any]):
    try:
        data = json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        data = []
    data.append(record)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def leaderboard_text(path: str, top: int = 10) -> str:
    try:
        results = json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        return "Пока нет результатов."
    if not results:
        return "Пока нет результатов."
    best = {}
    for r in results:
        n = r.get("name", "Без имени")
        s = int(r.get("score", 0))
        t = int(r.get("total", 0))
        if n not in best or s > best[n]["score"]:
            best[n] = {"score": s, "total": t}
    table = sorted(best.items(), key=lambda kv: (-kv[1]["score"], kv[0].lower()))
    lines = ["🏆 Рейтинг:", ""]
    for i, (n, st) in enumerate(table[:top], 1):
        lines.append(f"{i}. {n} — {st['score']}/{st['total']}")
    return "\n".join(lines)

questions = load_questions(QUESTIONS_FILE)

# --- FSM ---
class Quiz(StatesGroup):
    name = State()
    quiz = State()

from aiogram import Router
router = Router ()

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
    rows = [[InlineKeyboardButton(text=("☑ " if i in sel else "☐ ") + o, callback_data=f"m:{qid}:{i}")]
            for i, o in enumerate(opts)]
    rows += [
        [InlineKeyboardButton(text="✅ Готово", callback_data=f"ms:{qid}")],
        [InlineKeyboardButton(text="↩ Очистить выбор", callback_data=f"mc:{qid}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- Handlers ---
@router.message(CommandStart())
async def start_cmd(msg: types.Message):
    await msg.answer(
        "🎂 Привет! Это викторина про дедушку Серёжу 🎉\n"
        "Кто знает его лучше всех? 🏆",
        reply_markup=kb_start()
    )

@router.callback_query(F.data == "start_quiz")
async def begin(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Quiz.name)
    await cb.message.answer("Как тебя зовут, герой? 😊")
    await cb.answer()

@router.message(Quiz.name)
async def got_name(msg: types.Message, state: FSMContext):
    await state.update_data(name=msg.text.strip(), score=0, qid=0, multi={})
    await msg.answer(f"Отлично, {msg.text.strip()}! Поехали 🚀")
    await ask_next(msg.chat.id, state)

async def ask_next(cid: int, state: FSMContext):
    data = await state.get_data()
    qid = data.get("qid", 0)
    if qid >= len(questions):
        name, score = data["name"], data["score"]
        total = sum(len(q["answer_index"]) if q["type"] == "multi" else 1 for q in questions)
        save_result(RESULTS_FILE, {"name": name, "score": score, "total": total, "ts": datetime.now().isoformat()})
        bot: Bot = state.bot  # type: ignore
        await bot.send_message(cid, f"🎉 {name}, ты набрал <b>{score}</b> из <b>{total}</b> баллов! 💯",
                               parse_mode=ParseMode.HTML)
        await bot.send_message(cid, leaderboard_text(RESULTS_FILE, LEADERS_TOP_N))
        await state.clear()
        return
    q = questions[qid]
    text = f"Вопрос {qid+1}/{len(questions)}\n\n<b>{q['question']}</b>"
    bot: Bot = state.bot  # type: ignore
    if q["type"] == "single":
        await bot.send_message(cid, text, reply_markup=kb_single(q["options"], qid), parse_mode=ParseMode.HTML)
    else:
        multi = data.get("multi", {}).get(str(qid), [])
        await bot.send_message(cid, text + "\n(можно несколько вариантов)",
                               reply_markup=kb_multi(q["options"], qid, set(multi)), parse_mode=ParseMode.HTML)

# --- Single / Multi ---
@router.callback_query(F.data.startswith("s:"))
async def single(cb: CallbackQuery, state: FSMContext):
    _, qid, opt = cb.data.split(":")
    qid, opt = int(qid), int(opt)
    data = await state.get_data()
    if qid != data.get("qid", 0): return await cb.answer("Уже пройдено 🙂")
    q = questions[qid]
    right = (opt == q["answer_index"])
    sc = data["score"] + (1 if right else 0)
    await state.update_data(score=sc, qid=qid + 1)
    mark = "✅" if right else "❌"
    txt = (f"<b>{q['question']}</b>\n\nТы выбрал: {q['options'][opt]} {mark}\n"
           f"Правильный ответ: {q['options'][q['answer_index']]}")
    await cb.message.edit_text(txt, parse_mode=ParseMode.HTML)
    await cb.answer()
    await ask_next(cb.message.chat.id, state)

@router.callback_query(F.data.startswith("m:"))
async def toggle(cb: CallbackQuery, state: FSMContext):
    _, qid, opt = cb.data.split(":")
    qid, opt = int(qid), int(opt)
    data = await state.get_data()
    if qid != data.get("qid", 0): return await cb.answer("Уже пройдено 🙂")
    multi = data.get("multi", {})
    sel = set(multi.get(str(qid), []))
    sel.remove(opt) if opt in sel else sel.add(opt)
    multi[str(qid)] = list(sorted(sel))
    await state.update_data(multi=multi)
    await cb.message.edit_reply_markup(reply_markup=kb_multi(questions[qid]["options"], qid, sel))
    await cb.answer("Выбор обновлён")

@router.callback_query(F.data.startswith("mc:"))
async def clear_multi(cb: CallbackQuery, state: FSMContext):
    _, qid = cb.data.split(":")
    qid = int(qid)
    data = await state.get_data()
    if qid != data.get("qid", 0): return await cb.answer("Уже пройдено 🙂")
    multi = data.get("multi", {})
    multi[str(qid)] = []
    await state.update_data(multi=multi)
    await cb.message.edit_reply_markup(reply_markup=kb_multi(questions[qid]["options"], qid, set()))
    await cb.answer("Очищено")

@router.callback_query(F.data.startswith("ms:"))
async def submit_multi(cb: CallbackQuery, state: FSMContext):
    _, qid = cb.data.split(":")
    qid = int(qid)
    data = await state.get_data()
    if qid != data.get("qid", 0): return await cb.answer("Уже пройдено 🙂")
    q = questions[qid]
    corr, sel = set(q["answer_index"]), set(data.get("multi", {}).get(str(qid), []))
    gain = len(corr & sel)
    sc = data["score"] + gain
    await state.update_data(score=sc, qid=qid + 1)
    txt = (f"<b>{q['question']}</b>\n\n"
           f"Ты выбрал: {', '.join(q['options'][i] for i in sel) or 'ничего'}\n"
           f"Правильные: {', '.join(q['options'][i] for i in corr)}\n"
           f"+{gain} балл(ов)")
    await cb.message.edit_text(txt, parse_mode=ParseMode.HTML)
    await cb.answer()
    await ask_next(cb.message.chat.id, state)

@router.message(Command("leaders"))
async def leaders(msg: types.Message):
    await msg.answer(leaderboard_text(RESULTS_FILE, LEADERS_TOP_N))

# --- RUN ---
async def on_startup(bot: Bot):
    reset_results(RESULTS_FILE)
    await bot.set_webhook(WEBHOOK_URL)
    print("Webhook set to:", WEBHOOK_URL)

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
   
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    print("🚀 GrandPaQuiz_bot_web running on port", PORT)
    await site.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
