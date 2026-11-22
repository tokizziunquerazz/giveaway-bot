import os
import json
import time
import random
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    try:
        with open("token.txt", encoding="utf-8") as f:
            TOKEN = f.read().strip()
    except FileNotFoundError:
        raise SystemExit("Токен не найден: задайте переменную TELEGRAM_BOT_TOKEN")

# ID тех, кто может создавать розыгрыши. Пусто = создавать могут все.
# Указывается через запятую, например: ADMINS=123456789,987654321
ADMIN_IDS = {int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()}

DATA_FILE = "giveaways.json"
try:
    with open(DATA_FILE, encoding="utf-8") as f:
        giveaways = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    giveaways = {}


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(giveaways, f, ensure_ascii=False)


class Create(StatesGroup):
    title = State()
    channel = State()
    minutes = State()


dp = Dispatcher(storage=MemoryStorage())

WELCOME = (
    "🎁 <b>Бот розыгрышей</b>\n\n"
    "Команды:\n"
    "/new — создать розыгрыш\n"
    "/cancel — отменить создание\n"
    "/myid — узнать свой ID"
)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(WELCOME)


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Ваш ID: <code>{message.from_user.id}</code>")


@dp.message(Command("cancel"), state="*")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")


@dp.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав на создание розыгрышей.")
        return
    await state.set_state(Create.title)
    await message.answer("📝 Введите название и краткое описание розыгрыша (одним сообщением):")


@dp.message(Create.title)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(Create.channel)
    await message.answer(
        "📣 Укажите @юзернейм канала, куда отправить пост (например: @mychannel).\n"
        "⚠️ Бот должен быть <b>администратором канала</b> с правом публикации!"
    )


@dp.message(Create.channel)
async def process_channel(message: Message, state: FSMContext, bot: Bot):
    try:
        chat = await bot.get_chat(message.text.strip())
    except Exception:
        await message.answer("❌ Не нашёл такой канал. Проверьте @юзернейм и что бот добавлен в канал.")
        return
    await state.update_data(channel_id=chat.id)
    await state.set_state(Create.minutes)
    await message.answer("⏱ Сколько минут будет длиться розыгрыш? Введите число (например 60):")


@dp.message(Create.minutes)
async def process_minutes(message: Message, state: FSMContext, bot: Bot):
    try:
        minutes = int(message.text.strip())
        if minutes <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое число минут, например 60.")
        return

    data = await state.get_data()
    gid = str(int(time.time() * 1000))
    text = (
        f"🎁 <b>Розыгрыш: {data['title']}</b>\n\n"
        f"⏳ Итоги через {minutes} мин.\n"
        "Нажимайте кнопку ниже, чтобы участвовать!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Участвовать", callback_data=f"join:{gid}")]
    ])
    try:
        post = await bot.send_message(data["channel_id"], text, reply_markup=kb)
    except Exception:
        await message.answer("❌ Не смог отправить пост в канал. Дайте боту права администратора и попробуйте снова.")
        await state.clear()
        return

    giveaways[gid] = {
        "title": data["title"],
        "channel_id": data["channel_id"],
        "message_id": post.message_id,
        "end_time": time.time() + minutes * 60,
        "participants": [],
        "finished": False,
    }
    save_data()
    await state.clear()
    await message.answer("✅ Розыгрыш запущен! По окончании бот сам объявит победителя.")


@dp.callback_query(F.data.startswith("join:"))
async def join(call: CallbackQuery, bot: Bot):
    gid = call.data.split(":", 1)[1]
    g = giveaways.get(gid)
    if not g or g.get("finished"):
        await call.answer("Розыгрыш уже завершён", show_alert=True)
        return
    user = call.from_user
    if any(p[0] == user.id for p in g["participants"]):
        await call.answer("Вы уже участвуете! 🍀")
        return
    try:
        member = await bot.get_chat_member(g["channel_id"], user.id)
        if member.status not in ("member", "administrator", "creator"):
            await call.answer("Сначала подпишитесь на канал! 📢", show_alert=True)
            return
    except Exception:
        pass  # если проверка не сработала — не блокируем участника
    name = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
    g["participants"].append([user.id, name])
    save_data()
    await call.answer(f"Вы в игре! Участников: {len(g['participants'])} 🍀")


async def checker(bot: Bot):
    while True:
        await asyncio.sleep(15)
        now = time.time()
        for gid, g in list(giveaways.items()):
            if g.get("finished") or now < g["end_time"]:
                continue
            g["finished"] = True
            save_data()
            try:
                await bot.edit_message_reply_markup(g["channel_id"], g["message_id"], reply_markup=None)
            except Exception:
                pass
            participants = g.get("participants", [])
            if not participants:
                text = f"🏁 Розыгрыш «{g['title']}» завершён.\nК сожалению, никто не участвовал 😢"
            else:
                _, winner = random.choice(participants)
                text = f"🎉 <b>Итоги розыгрыша «{g['title']}»</b>\n\n🏆 Победитель: {winner}\n\nПоздравляем! 🎊"
            try:
                await bot.send_message(g["channel_id"], text)
            except Exception:
                logging.exception("Не удалось отправить итоги в канал")


async def main():
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(checker(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
