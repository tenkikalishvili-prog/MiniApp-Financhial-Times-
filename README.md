# Financial Times — бот (Этап 1, MVP)

Telegram-бот для учёта личных финансов. Ввод операции по кнопкам:
**Статья → Категория → Подкатегория → Сумма**. Данные хранятся в локальной базе
SQLite (файл `fintimes.db`). Позже переедет на сервер + Postgres.

## Структура

```
app/
├── bot/                  # Telegram-бот (aiogram)
│   ├── main.py           #   запуск
│   ├── keyboards.py      #   кнопки
│   └── handlers/         #   /start и мастер ввода операции
├── backend/              # логика + доступ к данным
│   ├── config.py         #   настройки из .env
│   ├── db.py             #   подключение к БД
│   ├── models.py         #   таблицы (users, categories, budgets, transactions)
│   ├── seed.py           #   засев категорий из справочника
│   └── services/         #   бизнес-логика
├── requirements.txt
└── .env                  # токен бота (создать из .env.example, в git не попадает)
```

## Запуск на Mac (по шагам)

Все команды выполняй в Терминале, находясь в папке `app/`.

1. Перейти в папку проекта:
   ```bash
   cd "/Users/tengiz/Desktop/Проект. 💰MiniApp Financial Times/app"
   ```

2. Создать виртуальное окружение Python и активировать его:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Установить зависимости:
   ```bash
   pip install -r requirements.txt
   ```

4. Создать файл `.env` из шаблона и вписать токен бота:
   ```bash
   cp .env.example .env
   ```
   Затем открой `.env` в редакторе и вставь токен из BotFather в строку `BOT_TOKEN=`.

5. Запустить бота:
   ```bash
   python -m bot.main
   ```

6. Открой в Telegram бота **@FinTimes_money_bot**, нажми **/start** и попробуй
   «➕ Добавить операцию».

Остановить бота — `Ctrl + C` в терминале. Пока бот запущен и Mac включён — он работает.
```
