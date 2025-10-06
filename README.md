# GrandPaQuiz_bot (Telegram, Render-ready)

Семейная викторина ко Дню Рождения дедушки 👴🎂

## Возможности
- Одиночные и множественные ответы (чекбоксы)
- Начисление: **+1 балл за каждый правильный вариант** (в multi-вопросах — до числа правильных)
- Личный итог + общий рейтинг (`/leaders`), показ после завершения
- Хранение результатов в `results.json`
- На каждом запуске результаты обнуляются (свежий турнир)

## Быстрый старт (Render — Background Worker)
1. Создайте **Background Worker** на Render (Deploy from ZIP).
2. Загрузите архив проекта.
3. В **Environment Variables** добавьте:
   - `BOT_TOKEN=ваш_токен_из_BotFather`
   - (опционально) `LEADERS_TOP_N=10`
4. Render сам установит зависимости и запустит бота.

> Почему Background Worker? Для long polling не нужен веб-порт. Если хотите через Web Service — используйте вебхуки (настройка отдельно).

## Локальный запуск
```bash
pip install -r requirements.txt
export BOT_TOKEN=XXX:YYYY
python main.py
```

## Структура
- `main.py` — логика бота (aiogram 3.x)
- `questions.json` — ваши вопросы
- `results.json` — результаты участников
- `requirements.txt` — зависимости
- `Procfile` — `worker: bash start.sh` для Render
- `start.sh` — запуск бота
- `.env.example` — пример переменных окружения

## Формат `questions.json`
```json
[
  {
    "type": "single",            // или "multi"
    "question": "Текст вопроса",
    "options": ["A", "B", "C", "D"],
    "answer_index": 2            // для single — число, для multi — список индексов
  }
]
```

## Команды
- `/start` — начать викторину
- `/leaders` — показать текущий рейтинг
- `/reset_me` — сбросить прогресс пользователя

Хорошего праздника и тёплых семейных моментов! 🎉
