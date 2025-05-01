import os
import logging
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram import Update, ParseMode
import requests
from bs4 import BeautifulSoup
from googlesearch import search
import random
import time
import json
import re
from typing import List, Dict
import threading
from queue import Queue
import sqlite3
from datetime import datetime
import nltk
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
import pandas as pd

# تنظیمات اولیه
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# کلاس اصلی ربات
class SmartBot:
    def __init__(self, token: str):
        self.token = token
        self.updater = Updater(token, use_context=True)
        self.dp = self.updater.dispatcher
        self.search_queue = Queue()
        self.db_conn = self.init_database()
        self.cache = {}
        self.user_sessions = {}
        
        # دانلود دیتاست‌های مورد نیاز NLTK
        nltk.download('punkt')
        nltk.download('stopwords')
        nltk.download('averaged_perceptron_tagger')
        
        # تنظیم هندلرها
        self.setup_handlers()
        
        # شروع پردازش‌های پس‌زمینه
        self.start_background_tasks()

    def init_database(self) -> sqlite3.Connection:
        """راه‌اندازی پایگاه داده"""
        conn = sqlite3.connect('bot_database.db', check_same_thread=False)
        c = conn.cursor()
        
        # ایجاد جداول مورد نیاز
        c.execute('''CREATE TABLE IF NOT EXISTS messages
                    (user_id INTEGER, message TEXT, response TEXT, timestamp DATETIME)''')
        c.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY, username TEXT, first_seen DATETIME)''')
        c.execute('''CREATE TABLE IF NOT EXISTS cache
                    (query TEXT PRIMARY KEY, response TEXT, timestamp DATETIME)''')
        
        conn.commit()
        return conn

    def setup_handlers(self):
        """تنظیم هندلرهای ربات"""
        self.dp.add_handler(CommandHandler("start", self.start_command))
        self.dp.add_handler(CommandHandler("help", self.help_command))
        self.dp.add_handler(CommandHandler("stats", self.stats_command))
        self.dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        self.dp.add_error_handler(self.error_handler)

    def start_background_tasks(self):
        """شروع تسک‌های پس‌زمینه"""
        # پردازش صف جستجو
        search_thread = threading.Thread(target=self.process_search_queue, daemon=True)
        search_thread.start()
        
        # پاکسازی کش
        cache_thread = threading.Thread(target=self.clean_cache, daemon=True)
        cache_thread.start()

    async def start_command(self, update: Update, context: CallbackContext):
        """هندلر دستور /start"""
        user = update.effective_user
        welcome_message = f"""
سلام {user.first_name}! 👋
من یک ربات هوشمند هستم که با استفاده از هوش مصنوعی و موتور جستجوی گوگل به سؤالات شما پاسخ می‌دهم.

🔹 قابلیت‌های من:
• پاسخگویی هوشمند به سؤالات
• جستجوی پیشرفته در گوگل
• یادگیری از تعاملات
• پشتیبانی از زبان فارسی و انگلیسی

برای شروع، کافیست سؤال خود را بپرسید!
        """
        await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)
        
        # ذخیره اطلاعات کاربر
        self.save_user_info(user.id, user.username)

    def save_user_info(self, user_id: int, username: str):
        """ذخیره اطلاعات کاربر در دیتابیس"""
        with self.db_conn:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, first_seen)
                VALUES (?, ?, ?)
            """, (user_id, username, datetime.now()))

    async def help_command(self, update: Update, context: CallbackContext):
        """هندلر دستور /help"""
        help_text = """
🤖 راهنمای استفاده از ربات:

1️⃣ سؤال خود را به صورت واضح بپرسید
2️⃣ منتظر پردازش و دریافت پاسخ بمانید
3️⃣ می‌توانید از دستورات زیر استفاده کنید:

/start - شروع مجدد ربات
/help - نمایش این راهنما
/stats - مشاهده آمار استفاده

📝 نکات:
• سعی کنید سؤالات را کامل و دقیق مطرح کنید
• ربات از هر دو زبان فارسی و انگلیسی پشتیبانی می‌کند
• پاسخ‌ها بر اساس داده‌های گوگل است
        """
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def stats_command(self, update: Update, context: CallbackContext):
        """هندلر دستور /stats"""
        user_id = update.effective_user.id
        
        with self.db_conn:
            cursor = self.db_conn.cursor()
            
            # تعداد پیام‌های کاربر
            cursor.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,))
            message_count = cursor.fetchone()[0]
            
            # تاریخ اولین استفاده
            cursor.execute("SELECT first_seen FROM users WHERE user_id = ?", (user_id,))
            first_seen = cursor.fetchone()[0]
            
        stats_text = f"""
📊 آمار استفاده شما از ربات:

• تعداد پیام‌ها: {message_count}
• تاریخ شروع استفاده: {first_seen}
• وضعیت: فعال ✅
        """
        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

    async def handle_message(self, update: Update, context: CallbackContext):
        """پردازش پیام‌های دریافتی"""
        user_message = update.message.text
        user_id = update.effective_user.id
        
        # بررسی کش
        if user_message in self.cache:
            cached_response = self.cache[user_message]
            await update.message.reply_text(cached_response)
            return
        
        # ارسال پیام در حال پردازش
        processing_message = await update.message.reply_text("در حال پردازش پیام شما... ⏳")
        
        try:
            # پردازش پیام و دریافت پاسخ
            response = await self.process_message(user_message)
            
            # ذخیره در کش
            self.cache[user_message] = response
            
            # ذخیره در دیتابیس
            self.save_interaction(user_id, user_message, response)
            
            # ارسال پاسخ
            await processing_message.edit_text(response, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            error_message = "متأسفانه در پردازش پیام شما مشکلی پیش آمد. لطفاً دوباره تلاش کنید."
            await processing_message.edit_text(error_message)
            logger.error(f"Error processing message: {str(e)}")

    async def process_message(self, message: str) -> str:
        """پردازش پیام و تولید پاسخ"""
        # تحلیل متن
        tokens = word_tokenize(message)
        pos_tags = nltk.pos_tag(tokens)
        
        # جستجو در گوگل
        search_results = []
        for url in search(message, num_results=5, lang='fa'):
            search_results.append(url)
        
        # دریافت و پردازش محتوا
        responses = []
        for url in search_results:
            try:
                response = requests.get(url, timeout=5)
                soup = BeautifulSoup(response.text, 'html.parser')
                text = soup.get_text()
                # پردازش و تمیزسازی متن
                text = self.clean_text(text)
                responses.append(text[:300])  # محدود کردن طول پاسخ
            except:
                continue
        
        # انتخاب بهترین پاسخ
        if responses:
            final_response = self.select_best_response(responses, message)
        else:
            final_response = "متأسفانه نتوانستم پاسخ مناسبی پیدا کنم. لطفاً سؤال خود را به شکل دیگری مطرح کنید."
        
        return final_response

    def clean_text(self, text: str) -> str:
        """تمیزسازی و نرمال‌سازی متن"""
        # حذف کاراکترهای اضافی
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s\u0600-\u06FF]', ' ', text)
        return text.strip()

    def select_best_response(self, responses: List[str], query: str) -> str:
        """انتخاب بهترین پاسخ با استفاده از الگوریتم رتبه‌بندی"""
        best_response = max(responses, key=lambda x: self.calculate_relevance(x, query))
        return best_response

    def calculate_relevance(self, response: str, query: str) -> float:
        """محاسبه میزان ارتباط پاسخ با پرسش"""
        query_tokens = set(word_tokenize(query.lower()))
        response_tokens = set(word_tokenize(response.lower()))
        
        # محاسبه اشتراک کلمات
        common_words = query_tokens.intersection(response_tokens)
        relevance_score = len(common_words) / len(query_tokens)
        
        return relevance_score

    def save_interaction(self, user_id: int, message: str, response: str):
        """ذخیره تعامل در دیتابیس"""
        with self.db_conn:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                INSERT INTO messages (user_id, message, response, timestamp)
                VALUES (?, ?, ?, ?)
            """, (user_id, message, response, datetime.now()))

    def process_search_queue(self):
        """پردازش صف جستجو"""
        while True:
            if not self.search_queue.empty():
                search_task = self.search_queue.get()
                try:
                    self.perform_search(search_task)
                except Exception as e:
                    logger.error(f"Error in search queue processing: {str(e)}")
            time.sleep(0.1)

    def clean_cache(self):
        """پاکسازی دوره‌ای کش"""
        while True:
            current_time = datetime.now()
            keys_to_remove = []
            
            for key, (value, timestamp) in self.cache.items():
                if (current_time - timestamp).total_seconds() > 3600:  # حذف پس از 1 ساعت
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self.cache[key]
            
            time.sleep(3600)  # بررسی هر ساعت

    def error_handler(self, update: Update, context: CallbackContext):
        """مدیریت خطاها"""
        logger.error(f"Error occurred: {context.error}")
        if update:
            update.message.reply_text("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")

    def run(self):
        """اجرای ربات"""
        self.updater.start_polling()
        logger.info("Bot started successfully!")
        self.updater.idle()

def main():
    # تنظیم توکن ربات
    TOKEN = "7654789396:AAGM33TcTzpVWE6TGKcZccw95AWskC4tFBU"
    
    # ایجاد و اجرای ربات
    bot = SmartBot(TOKEN)
    bot.run()

if __name__ == '__main__':
    main()
