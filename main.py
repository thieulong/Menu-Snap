import json
import os
import uuid
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
import easyocr
from openai import OpenAI
import ast
from duckduckgo_search import DDGS
import aiohttp
from urllib.parse import quote, unquote

# Load config
with open("data.json") as f:
    config = json.load(f)

TOKEN = config["telegram_token"]
OPENAI_API_KEY = config["openai_key"]
client = OpenAI(api_key=OPENAI_API_KEY)

# Initialize OCR reader
reader = easyocr.Reader(['en', 'vi'])

# Prepare directories
os.makedirs("downloads", exist_ok=True)

# Global memory
menu_data = {}
user_category_choice = {}
user_dish_choice = {}
button_lookup = {}

# --- Utilities ---

# Generate safe callback data
def create_callback(prefix, text):
    uid = str(uuid.uuid4())[:8]
    button_lookup[uid] = text
    return f"{prefix}:{uid}"

def resolve_callback(data):
    return button_lookup.get(data, "Unknown")

# Download image
async def download_image(url, filepath):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                if len(data) < 1024:
                    return False
                with open(filepath, "wb") as f:
                    f.write(data)
                return True
    return False

# Search images (DuckDuckGo)
async def search_images(query, max_results=8):
    results = []
    with DDGS() as ddgs:
        for r in ddgs.images(query):
            results.append(r["image"])
            if len(results) >= max_results:
                break
    return results

# --- Handlers ---

# Handle images
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = f"downloads/{file.file_id}.jpg"
    await file.download_to_drive(file_path)

    await update.message.reply_text("Image received! Processing menu... 💭")

    result = reader.readtext(file_path)
    extracted_text = "\n".join([text for (_, text, _) in result])

    if not extracted_text.strip():
        await update.message.reply_text("Can't even see a thing 💀 Either your image is blurry or there is no menu. Please try again.")
        return

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Extract the menu into a JSON format with categories as keys and dish names as lists. Example: {'Starters': ['Garlic Bread', 'Spring Rolls'], 'Mains': ['Grilled Salmon']}. Keep in mind that categories and dish names have to make sense, otherwise don't add to JSON"},
                {"role": "user", "content": extracted_text}
            ]
        )

        menu_json_text = response.choices[0].message.content.strip()

        try:
            menu = ast.literal_eval(menu_json_text)
            global menu_data
            menu_data = menu
        except:
            await update.message.reply_text("Failed to parse the menu 💀 Are you sure you're sending me the menu? Please try again.")
            return

        categories = list(menu.keys())
        keyboard = [[InlineKeyboardButton(cat, callback_data=create_callback("category", cat))] for cat in categories]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text("Please choose a category", reply_markup=reply_markup)

    except Exception as e:
        await update.message.reply_text(f"Processing error: {e}")

# Handle category/dish selection and control flow
async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("category:"):
        category = resolve_callback(data.split(":", 1)[1])
        user_category_choice[user_id] = category

        dishes = menu_data.get(category, [])
        if dishes:
            keyboard = [[InlineKeyboardButton(dish, callback_data=create_callback("dish", dish))] for dish in dishes]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text=f"Category '{category}' selected. Choose a dish:", reply_markup=reply_markup)
        else:
            await query.edit_message_text(text="No dishes found in this category.")

    elif data.startswith("dish:"):
        dish = resolve_callback(data.split(":", 1)[1])
        user_dish_choice[user_id] = dish
        await query.edit_message_text(text=f"You selected '{dish}'. Searching for images... 🔎")

        images = await search_images(dish)
        media_group = []
        MAX_IMAGES = 4

        for idx, img_url in enumerate(images[:MAX_IMAGES]):
            filename = f"downloads/{user_id}_{idx}.jpg"
            success = await download_image(img_url, filename)
            if success:
                media_group.append(InputMediaPhoto(open(filename, "rb")))

        if media_group:
            await query.message.reply_media_group(media_group)
        else:
            await query.message.reply_text("I'm sorry but what is even this food? I can't find any relevant information about it 🥵")

        # After showing images -> offer continue/exit
        keyboard = [
            [InlineKeyboardButton("Explore More 🍽", callback_data="continue")],
            [InlineKeyboardButton("Exit ❌", callback_data="exit")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Would you like to continue exploring or exit?", reply_markup=reply_markup)

    elif data == "continue":
        # Go back to category selection
        categories = list(menu_data.keys())
        keyboard = [[InlineKeyboardButton(cat, callback_data=create_callback("category", cat))] for cat in categories]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Please choose another category to explore:", reply_markup=reply_markup)

    elif data == "exit":
        # Clean up all downloaded images
        for filename in os.listdir("downloads"):
            filepath = os.path.join("downloads", filename)
            try:
                os.remove(filepath)
            except Exception:
                pass

        await query.message.reply_text("Thank you for using MenuSnap! See you again soon. 👋")

# Handle non-image
async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey there! 👋 I am MenuSnap.\n\n"
        "Send a picture of a Menu and I'll help you explore it.\n\n"
        "Please send only images to get started 🍽"
    )

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.PHOTO, handle_image))
app.add_handler(CallbackQueryHandler(handle_selection))
app.add_handler(MessageHandler(~filters.PHOTO, handle_other))

print("Bot is running...")
app.run_polling()
