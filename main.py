#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GrandPaQuiz_bot — Telegram quiz for grandpa's birthday
aiogram 3.x, long polling, Render-ready (Background Worker)

Features:
- Single- and multi-answer questions
- Checkbox UI for multi-select
- Scoring: +1 point per each correct option selected (no penalty for extra choices)
- Personal result + global leaderboard shown at the end and via /leaders
- Results persisted to results.json
- Results file is cleared on each bot start (fresh tournament per run)

Environment variables:
- BOT_TOKEN (required)
- QUESTIONS_FILE (default: questions.json)
- RESULTS_FILE (default: results.json)
- LEADERS_TOP_N (default: 10)
"""
import asyncio
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Set

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("❌ Please set BOT_TOKEN environment variable.")

QUESTIONS_FILE = os.getenv("QUESTIONS_FILE", "questions.json")
RESULTS_FILE = os.getenv("RESULTS_FILE", "results.json")
LEADERS_TOP_N = int(os.getenv("LEADERS_TOP_N", "10"))

# ---------- Data layer ----------
def load_questions(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        qs = json.load(f)
    for i, q in enumerate(qs, start=1):
        if "question" not in q or "options" not in q or "answer_index" not in q or "type" not in q:
            raise ValueError(f"Question {i}: missing required fields (type, question, options, answer_index).")
        if q["type"] not in ("single", "multi"):
            raise ValueError(f"Question {i}: 'type' must be 'single' or 'multi'.")
        if not isinstance(q["options"], list) or len(q["options"]) < 2:
            raise ValueError(f"Question {i}: options must be a list of at least 2.")
        if q["type"] == "single":
            if not isinstance(q["answer_index"], int) or not (0 <= q["answer_index"] < len(q["options"])):
                raise ValueError(f"Question {i}: answer_index must be int within options range.")
        else:
            if not isinstance(q["answer_index"], list) or not q["answer_index"]:
                raise ValueError(f"Question {i}: multi requires non-empty list of answer_index.")
            if any((not isinstance(x, int) or x < 0 or x >= len(q["options"])) for x in q["answer_index"]):
                raise ValueError(f"Question {i}: multi answer_index values out of range.")
    return qs

def reset_results_file(path: str):
    # Clear results on each run
    with open(path, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

def safe_read_results(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def append_result(path: str, record: Dict[str, Any]):
    results = safe_read_results(path)
    results.append(record)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

def build_leaderboard_text(path: str, top_n: int = 10) -> str:
    results = safe_read_results(path)
    if not results:
        return "Пока нет результатов."
    # Keep best score per name
    best = {}
    for r in results:
        name = r.get("name", "Без имени")
        score = int(r.get("score", 0))
        total = int(r.get("total", 0))
        prev = best.get(name)
        if prev is None or score > prev["score"]:
            best[name] = {"score": score, "total": total}
    table = sorted(best.items(), key=lambda kv: (-kv[1]["score"], kv[0].lower()))
    lines = ["🏆 Текущий рейтинг:", ""]
    for i, (name, st) in enumerate(table[:top_n], start=1):
        lines.append(f"{i}. {name} — {st['score']}/{st['total']}")
    return "\n".join(lines)

questions_cache: List[Dict[str, Any]] = load_questions(QUESTIONS_FILE)

# ---------- FSM ----------
class QuizStates(StatesGroup):
    waiting_for_name = State()
    in_quiz = State()

router = types.Router()

def kb_single(options: List[str], q_index: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"s:{q_index}:{i}")] for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_multi(options: List[str], q_index: int, selected: Set[int]) -> InlineKeyboardMarkup:
    rows = []
    for i, opt in enumerate(options):
        mark = "☑" if i in selected else "☐"
        rows.append([InlineKeyboardButton(text=f"{mark} {opt}", callback_data=f"m:{q_index}:{i}")])
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"ms:{q_index}")])
    rows.append([InlineKeyboardButton(text="↩ Очистить выбор", callback_data=f"mc:{q_index}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def send_next_question(chat_id: int, state: FSMContext):
    data = await state.get_data()
    q_index = int(data.get("q_index", 0))
    if q_index >= len(questions_cache):
        # Finish
        name = data.get("name", "Без имени")
        score = int(data.get("score", 0))
        total_correct = sum(len(q["answer_index"]) if q["type"] == "multi" else 1 for q in questions_cache)
        append_result(RESULTS_FILE, {
            "name": name,
            "score": score,
            "total": total_correct,
            "finished_at": datetime.now().isoformat(timespec="seconds")
        })
        bot: Bot = state.bot  # type: ignore
        await bot.send_message(
            chat_id,
            f"🎉 Готово! {name}, твой результат: <b>{score}</b> из <b>{total_correct}</b>.\n"
            "Спасибо за участие! 🥳",
            parse_mode=ParseMode.HTML
        )
        leaders = build_leaderboard_text(RESULTS_FILE, LEADERS_TOP_N)
        await bot.send_message(chat_id, leaders)
        await state.clear()
        return

    q = questions_cache[q_index]
    header = f"Вопрос {q_index + 1}/{len(questions_cache)}"
    text = f"{header}\n\n<b>{q['question']}</b>"
    bot: Bot = state.bot  # type: ignore
    if q["type"] == "single":
        await bot.send_message(
            chat_id,
            text,
            reply_markup=kb_single(q["options"], q_index),
            parse_mode=ParseMode.HTML
        )
    else:
        multi_selected = data.get("multi_selected", {})
        selected_set = set(multi_selected.get(str(q_index), []))
        await bot.send_message(
            chat_id,
            text + "\n\n(Можно выбрать несколько вариантов)",
            reply_markup=kb_multi(q["options"], q_index, selected_set),
            parse_mode=ParseMode.HTML
        )

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎂 Привет! Это семейная викторина про дедушку: кто знает его лучше всех? 🏆\n"
        "Сначала напиши, как тебя зовут — чтобы мы посчитали баллы и внесли в рейтинг."
    )
    await state.set_state(QuizStates.waiting_for_name)

@router.message(QuizStates.waiting_for_name, F.text.len() >= 1)
async def got_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=name, score=0, q_index=0, multi_selected={})
    await message.answer(f"Отлично, {name}! Поехали! 🚀")
    await send_next_question(message.chat.id, state)

@router.callback_query(F.data.startswith("s:"))
async def handle_single(callback: CallbackQuery, state: FSMContext):
    try:
        _, q_index_str, opt_index_str = callback.data.split(":")
        q_index = int(q_index_str)
        opt_index = int(opt_index_str)
    except Exception:
        await callback.answer("Ошибка формата ответа.", show_alert=True)
        return

    data = await state.get_data()
    current_idx = int(data.get("q_index", 0))
    if q_index != current_idx:
        await callback.answer("Этот вопрос уже пройден 👍")
        return

    q = questions_cache[current_idx]
    correct = (opt_index == int(q["answer_index"]))
    score = int(data.get("score", 0))
    if correct:
        score += 1
        await callback.answer("Верно! ✅")
    else:
        await callback.answer("Принято 🙂")

    await state.update_data(score=score, q_index=current_idx + 1)

    # Lock message
    try:
        mark = "✅" if correct else "❌"
        chosen = q["options"][opt_index]
        right_text = q["options"][q["answer_index"]]
        await callback.message.edit_text(
            f"<b>{q['question']}</b>\n\n"
            f"Ты выбрал: {chosen} {mark}\n"
            f"Правильный ответ: {right_text}",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    await send_next_question(callback.message.chat.id, state)

@router.callback_query(F.data.startswith("m:"))
async def handle_multi_toggle(callback: CallbackQuery, state: FSMContext):
    # Toggle selection for multi-choice
    try:
        _, q_index_str, opt_index_str = callback.data.split(":")
        q_index = int(q_index_str)
        opt_index = int(opt_index_str)
    except Exception:
        await callback.answer("Ошибка формата.", show_alert=True)
        return

    data = await state.get_data()
    current_idx = int(data.get("q_index", 0))
    if q_index != current_idx:
        await callback.answer("Этот вопрос уже пройден 👍")
        return

    q = questions_cache[current_idx]
    if q["type"] != "multi":
        await callback.answer("Для этого вопроса одиночный выбор.")
        return

    multi_selected = data.get("multi_selected", {})
    sel = set(multi_selected.get(str(q_index), []))
    if opt_index in sel:
        sel.remove(opt_index)
    else:
        sel.add(opt_index)
    multi_selected[str(q_index)] = list(sorted(sel))
    await state.update_data(multi_selected=multi_selected)

    # Refresh keyboard
    try:
        kb = kb_multi(q["options"], q_index, sel)
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Выбор обновлён.")

@router.callback_query(F.data.startswith("mc:"))
async def handle_multi_clear(callback: CallbackQuery, state: FSMContext):
    try:
        _, q_index_str = callback.data.split(":")
        q_index = int(q_index_str)
    except Exception:
        await callback.answer("Ошибка формата.")
        return

    data = await state.get_data()
    current_idx = int(data.get("q_index", 0))
    if q_index != current_idx:
        await callback.answer("Этот вопрос уже пройден 👍")
        return

    multi_selected = data.get("multi_selected", {})
    multi_selected[str(q_index)] = []
    await state.update_data(multi_selected=multi_selected)

    q = questions_cache[current_idx]
    try:
        kb = kb_multi(q["options"], q_index, set())
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Выбор очищен.")

@router.callback_query(F.data.startswith("ms:"))
async def handle_multi_submit(callback: CallbackQuery, state: FSMContext):
    try:
        _, q_index_str = callback.data.split(":")
        q_index = int(q_index_str)
    except Exception:
        await callback.answer("Ошибка формата.")
        return

    data = await state.get_data()
    current_idx = int(data.get("q_index", 0))
    if q_index != current_idx:
        await callback.answer("Этот вопрос уже пройден 👍")
        return

    q = questions_cache[current_idx]
    corr: Set[int] = set(q["answer_index"])
    multi_selected = data.get("multi_selected", {})
    sel: Set[int] = set(multi_selected.get(str(q_index), []))

    gained = len(corr.intersection(sel))  # per-correct scoring; no penalty for extras
    score = int(data.get("score", 0)) + gained
    await state.update_data(score=score, q_index=current_idx + 1)

    # Prepare feedback
    chosen_list = [q["options"][i] for i in sorted(sel)]
    correct_list = [q["options"][i] for i in sorted(corr)]
    feedback = (
        f"<b>{q['question']}</b>\n\n"
        f"Ты выбрал: {', '.join(chosen_list) if chosen_list else 'ничего'}\n"
        f"Правильные ответы: {', '.join(correct_list)}\n"
        f"Заработано баллов: {gained}"
    )
    try:
        await callback.message.edit_text(feedback, parse_mode=ParseMode.HTML)
    except Exception:
        pass

    await callback.answer(f"+{gained} балл(ов)")
    await send_next_question(callback.message.chat.id, state)

@router.message(Command("leaders"))
async def cmd_leaders(message: types.Message):
    leaders = build_leaderboard_text(RESULTS_FILE, LEADERS_TOP_N)
    await message.answer(leaders)

@router.message(Command("reset_me"))
async def cmd_reset_me(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Сбросил твой прогресс. Напиши /start, чтобы начать заново.")

async def main():
    # Fresh tournament: clear results file on each start
    reset_results_file(RESULTS_FILE)

    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    print("✅ GrandPaQuiz_bot is running. Press Ctrl+C to stop.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")
