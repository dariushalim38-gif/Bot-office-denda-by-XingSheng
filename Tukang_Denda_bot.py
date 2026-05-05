import asyncio
import sqlite3
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, List

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes

# ========== KONFIGURASI ==========
TOKEN = "8716960621:AAG7cFdVeb0Tio7lBBMoUSfiH32VpCqjfL8"
ADMIN_IDS = [7938242756, 8226764474, 6071806272]

JAM_KERJA_MULAI = "10:00"
ISTIRAHAT_1_MULAI = "11:00"
ISTIRAHAT_1_SELESAI = "12:00"
ISTIRAHAT_2_MULAI = "17:00"
ISTIRAHAT_2_SELESAI = "18:00"
JAM_PULANG = "22:00"

UCAPAN_PULANG_LIST = [
    "Kerja keras hari ini cukup, besok kita kerja keras lagi (kalau ingat).",
    "Pamit dulu, beban kerja sudah terlalu berat untuk punggung jompo ini.",
    "Hati lega, kerjaan beres, waktunya menghilang dari peradaban kantor.",
    "Jalan-jalan ke Semanggi, mampir warung beli mentega.",
    "Saat melihat jam pulang, hatiku langsung lega.",
]

UCAPAN_TELAT_PAGI = [
    "🌅 Matahari sudah tinggi, tapi semangatmu masih di kasur?",
    "⏰ Telat lagi? Jam dinding di kantor ini nggak pernah bohong.",
    "🚦 Macet? Atau alarmnya yang macet?",
    "🍳 Sarapan dulu ya? Lain kali sambil jalan.",
]

UCAPAN_NYANYI = [
    "Nyiurin @{} 🎤: 'Balonku ada lima...'",
    "@{} dinyanyiin sama admin: 'Halo-halo Bandung...'",
    "Untuk @{}: 'Ibu kita Kartini...' (suara fals)",
    "@{}, admin suruh nyanyi: 'Bangun tidur ku terus mandi...'",
    "Nyanyiin @{}: 'Kasih ibu...' (sambil nangis)",
]

DEFAULT_DURASI_TOILET = 10
DEFAULT_DURASI_ROKOK = 5
MAX_IZIN_PER_HARI = 6

DB_PATH = "absen_bot.db"
logging.basicConfig(level=logging.INFO)

def parse_time(time_str: str) -> datetime.time:
    return datetime.strptime(time_str, "%H:%M").time()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS absensi (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    tanggal TEXT,
                    shift TEXT,
                    waktu_masuk TEXT,
                    status TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS izin_aktif (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    jenis TEXT,
                    start_time TEXT,
                    expected_end_time TEXT,
                    chat_id INTEGER
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS counter_harian (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    tanggal TEXT,
                    jenis TEXT,
                    jumlah INTEGER,
                    UNIQUE(user_id, tanggal, jenis)
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER UNIQUE,
                    chat_type TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pulang_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    tanggal TEXT,
                    UNIQUE(chat_id, tanggal)
                )''')
    conn.commit()
    conn.close()

def get_or_create_user(telegram_user) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_user.id,))
    row = c.fetchone()
    if row:
        user_id = row[0]
    else:
        c.execute("INSERT INTO users (telegram_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                  (telegram_user.id, telegram_user.username, telegram_user.first_name, telegram_user.last_name))
        user_id = c.lastrowid
        conn.commit()
    conn.close()
    return user_id

def sudah_absen(user_id_db: int, tanggal: str, shift: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM absensi WHERE user_id = ? AND tanggal = ? AND shift = ?", (user_id_db, tanggal, shift))
    ok = c.fetchone() is not None
    conn.close()
    return ok

def catat_absen(user_id_db: int, tanggal: str, shift: str, waktu_str: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO absensi (user_id, tanggal, shift, waktu_masuk, status) VALUES (?, ?, ?, ?, ?)",
              (user_id_db, tanggal, shift, waktu_str, status))
    conn.commit()
    conn.close()

def register_chat(chat_id: int, chat_type: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))
    conn.commit()
    conn.close()

def get_all_chats() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id FROM chats")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def sudah_kirim_pulang_today(chat_id: int, tanggal: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM pulang_log WHERE chat_id = ? AND tanggal = ?", (chat_id, tanggal))
    ok = c.fetchone() is not None
    conn.close()
    return ok

def catat_kirim_pulang(chat_id: int, tanggal: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO pulang_log (chat_id, tanggal) VALUES (?, ?)", (chat_id, tanggal))
    conn.commit()
    conn.close()

def get_izin_count(user_id_db: int, tanggal: str, jenis: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT jumlah FROM counter_harian WHERE user_id = ? AND tanggal = ? AND jenis = ?", (user_id_db, tanggal, jenis))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def increment_izin_count(user_id_db: int, tanggal: str, jenis: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO counter_harian (user_id, tanggal, jenis, jumlah) VALUES (?, ?, ?, 1) ON CONFLICT(user_id, tanggal, jenis) DO UPDATE SET jumlah = jumlah + 1",
              (user_id_db, tanggal, jenis))
    conn.commit()
    conn.close()

def reset_counter_hari_ini_admin(tanggal: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM counter_harian WHERE tanggal = ?", (tanggal,))
    c.execute("DELETE FROM izin_aktif")
    conn.commit()
    conn.close()

def get_active_izin(user_id_db: int) -> tuple:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, jenis, expected_end_time, chat_id FROM izin_aktif WHERE user_id = ? ORDER BY start_time DESC LIMIT 1", (user_id_db,))
    row = c.fetchone()
    conn.close()
    return row

def add_active_izin(user_id_db: int, jenis: str, start_time, expected_end, chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO izin_aktif (user_id, jenis, start_time, expected_end_time, chat_id) VALUES (?, ?, ?, ?, ?)",
              (user_id_db, jenis, start_time.isoformat(), expected_end.isoformat(), chat_id))
    izin_id = c.lastrowid
    conn.commit()
    conn.close()
    return izin_id

def remove_active_izin(izin_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM izin_aktif WHERE id = ?", (izin_id,))
    conn.commit()
    conn.close()

active_timers: Dict[int, asyncio.Task] = {}

async def schedule_reminder(app, chat_id, user_id, username, jenis, durasi, izin_id, delay_seconds):
    async def reminder():
        await asyncio.sleep(delay_seconds)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id FROM izin_aktif WHERE id = ?", (izin_id,))
        row = c.fetchone()
        conn.close()
        if row:
            mention = f"@{username}" if username else f"User {user_id}"
            await app.bot.send_message(chat_id=chat_id, text=f"{mention}, waktu {jenis} {durasi} menit habis! Segera selesai.")
    task = asyncio.create_task(reminder())
    active_timers[user_id] = task

async def restore_timers(app):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT izin.id, izin.jenis, izin.expected_end_time, izin.durasi_menit, izin.chat_id, users.telegram_id, users.username
                 FROM izin_aktif izin
                 JOIN users ON izin.user_id = users.id''')
    rows = c.fetchall()
    for izin_id, jenis, expected_end_str, durasi, chat_id, telegram_id, username in rows:
        expected_end = datetime.fromisoformat(expected_end_str)
        now = datetime.now()
        if expected_end > now:
            sisa = (expected_end - now).total_seconds()
            await schedule_reminder(app, chat_id, telegram_id, username, jenis, durasi, izin_id, sisa)
    conn.close()

async def cmd_izin(update: Update, context: ContextTypes.DEFAULT_TYPE, jenis: str, default_durasi: int, nama_izin: str):
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return
    user_id_db = get_or_create_user(user)
    tanggal = datetime.now().strftime("%Y-%m-%d")
    used = get_izin_count(user_id_db, tanggal, jenis)
    remaining = MAX_IZIN_PER_HARI - used
    if used >= MAX_IZIN_PER_HARI:
        await update.message.reply_text(f"⚠️ Kuota {nama_izin} hari ini habis (max {MAX_IZIN_PER_HARI}x).")
        return

    aktif = get_active_izin(user_id_db)
    if aktif:
        await update.message.reply_text(f"⚠️ Masih ada izin {aktif[1]} aktif. Selesaikan dulu.")
        return

    args = context.args
    durasi = default_durasi
    if args and args[0].isdigit():
        durasi = int(args[0])
        if durasi <= 0:
            await update.message.reply_text("Durasi harus positif.")
            return

    start_time = datetime.now()
    expected_end = start_time + timedelta(minutes=durasi)
    izin_id = add_active_izin(user_id_db, jenis, start_time, expected_end, chat.id)

    mention = f"@{user.username}" if user.username else user.first_name
    await update.message.reply_text(
        f"{mention}, {nama_izin} {durasi} menit mulai {start_time.strftime('%H:%M')}. Selesai maksimal {expected_end.strftime('%H:%M')}.\n"
        f"Sisa kuota hari ini: {remaining-1}/{MAX_IZIN_PER_HARI}\n"
        f"Selesai? /selesai_{jenis}"
    )
    if user.id in active_timers:
        if not active_timers[user.id].done():
            active_timers[user.id].cancel()
        del active_timers[user.id]
    delay = durasi * 60
    await schedule_reminder(context.application, chat.id, user.id, user.username, nama_izin, durasi, izin_id, delay)
    increment_izin_count(user_id_db, tanggal, jenis)

async def cmd_toilet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_izin(update, context, "toilet", DEFAULT_DURASI_TOILET, "toilet 🚽")

async def cmd_rokok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_izin(update, context, "rokok", DEFAULT_DURASI_ROKOK, "rokok 🚬")

async def cmd_selesai_izin(update: Update, context: ContextTypes.DEFAULT_TYPE, jenis: str, nama_izin: str):
    user = update.effective_user
    user_id_db = get_or_create_user(user)
    aktif = get_active_izin(user_id_db)
    if not aktif:
        await update.message.reply_text("Tidak ada izin aktif.")
        return
    izin_id, aktif_jenis, expected_end_str, chat_id = aktif
    if aktif_jenis != jenis:
        await update.message.reply_text(f"Anda sedang izin {aktif_jenis}, bukan {nama_izin}. Gunakan /selesai_{aktif_jenis}.")
        return
    expected_end = datetime.fromisoformat(expected_end_str)
    now = datetime.now()
    if now > expected_end:
        selisih = int((now - expected_end).total_seconds() // 60)
        await update.message.reply_text(f"⚠️ Melebihi batas {selisih} menit. Pelanggaran tercatat.")
    else:
        await update.message.reply_text(f"✅ {nama_izin.capitalize()} selesai tepat waktu.")
    remove_active_izin(izin_id)
    if user.id in active_timers:
        if not active_timers[user.id].done():
            active_timers[user.id].cancel()
        del active_timers[user.id]

async def cmd_selesai_toilet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_selesai_izin(update, context, "toilet", "toilet")

async def cmd_selesai_rokok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_selesai_izin(update, context, "rokok", "rokok")

async def cmd_reset_hari_ini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Hanya admin.")
        return
    tanggal = datetime.now().strftime("%Y-%m-%d")
    reset_counter_hari_ini_admin(tanggal)
    for uid, task in list(active_timers.items()):
        if not task.done():
            task.cancel()
    active_timers.clear()
    await update.message.reply_text(f"✅ Data hari ini ({tanggal}) direset. Semua izin aktif dibatalkan.")

async def cmd_nyanyi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Hanya admin.")
        return
    if not context.args:
        await update.message.reply_text("Format: /nyanyi @username")
        return
    target_username = context.args[0].lstrip('@')
    pesan = random.choice(UCAPAN_NYANYI).format(target_username)
    await update.message.reply_text(pesan)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat:
        register_chat(chat.id, chat.type)
    await update.message.reply_text(
        "👋 Bot Absen & Denda\n\n"
        "📋 Perintah:\n"
        "/absen_masuk - Absen pagi (jam 10:00)\n"
        "/istirahat_siang_mulai - Mulai istirahat siang (11:00-11:59)\n"
        "/absen_istirahat_siang - Kembali dr istirahat siang (max 12:00)\n"
        "/istirahat_sore_mulai - Mulai istirahat sore (17:00-17:59)\n"
        "/absen_istirahat_sore - Kembali dr istirahat sore (max 18:00)\n"
        "/toilet 🚽 - Izin toilet (10 menit, max 6x/hari)\n"
        "/rokok 🚬 - Izin rokok (5 menit, max 6x/hari)\n"
        "/selesai_toilet - Selesai izin toilet\n"
        "/selesai_rokok - Selesai izin rokok\n"
        "/pulang - Pulang kerja (wajib ≥22:00)\n"
        "/laporan_bulanan [thn bln] - Laporan admin\n"
        "/nyanyi @user - (Admin) nyanyiin user\n"
        "/reset_hari_ini - (Admin) reset data izin hari ini"
    )

async def cmd_absen_masuk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return
    user_id_db = get_or_create_user(user)
    now = datetime.now()
    tanggal = now.strftime("%Y-%m-%d")
    jam_kerja = parse_time(JAM_KERJA_MULAI)
    jam_sekarang = now.time()
    status = "telat" if jam_sekarang > jam_kerja else "tepat"
    if sudah_absen(user_id_db, tanggal, "pagi"):
        await update.message.reply_text("⚠️ Sudah absen pagi.")
        return
    catat_absen(user_id_db, tanggal, "pagi", now.isoformat(), status)
    msg = f"✅ Absen pagi: {now.strftime('%H:%M:%S')} - {status.upper()}"
    if status == "telat":
        telat_menit = int((now - datetime.combine(now.date(), jam_kerja)).total_seconds() // 60)
        kata = random.choice(UCAPAN_TELAT_PAGI)
        msg += f"\n⚠️ Telat {telat_menit} menit!\n{kata}"
    await update.message.reply_text(msg)

async def cmd_mulai_istirahat(update: Update, context: ContextTypes.DEFAULT_TYPE, shift: str, jam_mulai_str: str, jam_selesai_str: str, nama_shift: str):
    user = update.effective_user
    now = datetime.now()
    tanggal = now.strftime("%Y-%m-%d")
    jam_mulai = parse_time(jam_mulai_str)
    jam_selesai = parse_time(jam_selesai_str)
    jam_sekarang = now.time()
    if jam_sekarang < jam_mulai or jam_sekarang >= jam_selesai:
        await update.message.reply_text(f"❌ {nama_shift.capitalize()} hanya antara {jam_mulai_str} - {jam_selesai_str}.")
        return
    if sudah_absen(get_or_create_user(user), tanggal, shift):
        await update.message.reply_text(f"⚠️ Sudah absen {nama_shift}.")
        return
    catat_absen(get_or_create_user(user), tanggal, shift, now.isoformat(), "tepat")
    await update.message.reply_text(f"✅ {nama_shift.capitalize()} mulai: {now.strftime('%H:%M:%S')}")

async def cmd_istirahat_siang_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_mulai_istirahat(update, context, "siang_mulai", ISTIRAHAT_1_MULAI, ISTIRAHAT_1_SELESAI, "istirahat siang")

async def cmd_istirahat_sore_mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_mulai_istirahat(update, context, "sore_mulai", ISTIRAHAT_2_MULAI, ISTIRAHAT_2_SELESAI, "istirahat sore")

async def cmd_absen_istirahat(update: Update, context: ContextTypes.DEFAULT_TYPE, shift: str, jam_selesai_str: str, nama_shift: str):
    user = update.effective_user
    now = datetime.now()
    tanggal = now.strftime("%Y-%m-%d")
    jam_selesai = parse_time(jam_selesai_str)
    jam_sekarang = now.time()
    if jam_sekarang > jam_selesai:
        status = "telat"
        telat_menit = int((now - datetime.combine(now.date(), jam_selesai)).total_seconds() // 60)
        await update.message.reply_text(f"⚠️ Telat {nama_shift} {telat_menit} menit.")
    else:
        status = "tepat"
    if sudah_absen(get_or_create_user(user), tanggal, shift):
        await update.message.reply_text(f"⚠️ Sudah absen {nama_shift}.")
        return
    catat_absen(get_or_create_user(user), tanggal, shift, now.isoformat(), status)
    await update.message.reply_text(f"✅ {nama_shift}: {now.strftime('%H:%M:%S')} - {status.upper()}")

async def cmd_absen_siang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_absen_istirahat(update, context, "siang", ISTIRAHAT_1_SELESAI, "setelah istirahat siang")

async def cmd_absen_sore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_absen_istirahat(update, context, "sore", ISTIRAHAT_2_SELESAI, "setelah istirahat sore")

async def cmd_pulang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    jam_pulang = parse_time(JAM_PULANG)
    if now.time() < jam_pulang:
        await update.message.reply_text(f"❌ Belum waktunya pulang. Pulang jam {JAM_PULANG}.")
    else:
        pesan = random.choice(UCAPAN_PULANG_LIST)
        await update.message.reply_text(f"🎉 Pulang Kerja! 🎉\n\n{pesan}")

async def cmd_laporan_bulanan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Hanya admin.")
        return
    args = context.args
    if len(args) >= 2:
        tahun, bulan = int(args[0]), int(args[1])
    else:
        now = datetime.now()
        tahun, bulan = now.year, now.month
    start_date = f"{tahun}-{bulan:02d}-01"
    if bulan == 12:
        end_date = f"{tahun+1}-01-01"
    else:
        end_date = f"{tahun}-{bulan+1:02d}-01"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, telegram_id, username, first_name FROM users")
    users = c.fetchall()
    laporan = f"📊 Laporan Bulanan {tahun}-{bulan:02d}\n\n"
    for uid, _, username, first_name in users:
        nama = username or first_name
        c.execute("SELECT COUNT(*) FROM absensi WHERE user_id = ? AND tanggal >= ? AND tanggal < ? AND shift='pagi' AND status='telat'", (uid, start_date, end_date))
        telat_pagi = c.fetchone()[0]
        c.execute("SELECT SUM(jumlah) FROM counter_harian WHERE user_id = ? AND tanggal >= ? AND tanggal < ? AND jenis='toilet'", (uid, start_date, end_date))
        total_toilet = c.fetchone()[0] or 0
        c.execute("SELECT SUM(jumlah) FROM counter_harian WHERE user_id = ? AND tanggal >= ? AND tanggal < ? AND jenis='rokok'", (uid, start_date, end_date))
        total_rokok = c.fetchone()[0] or 0
        laporan += f"👤 {nama}\n   Telat pagi: {telat_pagi}\n   Toilet: {total_toilet}x\n   Rokok: {total_rokok}x\n\n"
    conn.close()
    await update.message.reply_text(laporan)

async def daily_pulang_checker(app: Application):
    while True:
        now = datetime.now()
        jam_pulang = parse_time(JAM_PULANG)
        if now.hour == jam_pulang.hour and now.minute == jam_pulang.minute:
            tanggal = now.strftime("%Y-%m-%d")
            for chat_id in get_all_chats():
                if not sudah_kirim_pulang_today(chat_id, tanggal):
                    pesan = random.choice(UCAPAN_PULANG_LIST)
                    await app.bot.send_message(chat_id=chat_id, text=f"🎉 Pulang Kerja! 🎉\n\n{pesan}")
                    catat_kirim_pulang(chat_id, tanggal)
        await asyncio.sleep(60)

async def set_commands(app: Application):
    commands = [
        BotCommand("start", "Mulai"),
        BotCommand("absen_masuk", "Absen pagi jam 10:00"),
        BotCommand("istirahat_siang_mulai", "Mulai istirahat siang (11:00-11:59)"),
        BotCommand("absen_istirahat_siang", "Kembali dari istirahat siang (max 12:00)"),
        BotCommand("istirahat_sore_mulai", "Mulai istirahat sore (17:00-17:59)"),
        BotCommand("absen_istirahat_sore", "Kembali dari istirahat sore (max 18:00)"),
        BotCommand("toilet", "Izin toilet 🚽 (10 menit, max 6x/hari)"),
        BotCommand("rokok", "Izin rokok 🚬 (5 menit, max 6x/hari)"),
        BotCommand("selesai_toilet", "Selesai izin toilet"),
        BotCommand("selesai_rokok", "Selesai izin rokok"),
        BotCommand("pulang", "Pulang kerja (wajib ≥22:00)"),
        BotCommand("laporan_bulanan", "Laporan bulanan (admin)"),
        BotCommand("nyanyi", "(Admin) nyanyiin user"),
        BotCommand("reset_hari_ini", "(Admin) reset data izin hari ini"),
    ]
    await app.bot.set_my_commands(commands)

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("absen_masuk", cmd_absen_masuk))
    app.add_handler(CommandHandler("istirahat_siang_mulai", cmd_istirahat_siang_mulai))
    app.add_handler(CommandHandler("absen_istirahat_siang", cmd_absen_siang))
    app.add_handler(CommandHandler("istirahat_sore_mulai", cmd_istirahat_sore_mulai))
    app.add_handler(CommandHandler("absen_istirahat_sore", cmd_absen_sore))
    app.add_handler(CommandHandler("toilet", cmd_toilet))
    app.add_handler(CommandHandler("rokok", cmd_rokok))
    app.add_handler(CommandHandler("selesai_toilet", cmd_selesai_toilet))
    app.add_handler(CommandHandler("selesai_rokok", cmd_selesai_rokok))
    app.add_handler(CommandHandler("pulang", cmd_pulang))
    app.add_handler(CommandHandler("laporan_bulanan", cmd_laporan_bulanan))
    app.add_handler(CommandHandler("nyanyi", cmd_nyanyi))
    app.add_handler(CommandHandler("reset_hari_ini", cmd_reset_hari_ini))

    async def post_init(app: Application):
        await restore_timers(app)
        await set_commands(app)
        asyncio.create_task(daily_pulang_checker(app))

    app.post_init = post_init
    print("Bot sedang berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
