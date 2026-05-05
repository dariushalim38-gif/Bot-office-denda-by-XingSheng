import asyncio
import sqlite3
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, List

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8716960621:AAG7cFdVeb0Tio7lBBMoUSfiH32VpCqjfL8"
ADMIN_IDS = [7938242756, 8226764474, 6071806272]

# Konfigurasi waktu
JAM_KERJA_MULAI = "10:00"
ISTIRAHAT_1_MULAI = "11:00"
ISTIRAHAT_1_SELESAI = "12:00"
ISTIRAHAT_2_MULAI = "17:00"
ISTIRAHAT_2_SELESAI = "18:00"
JAM_PULANG = "22:00"

UCAPAN_PULANG_LIST = ["Kerja keras hari ini cukup, besok kita kerja keras lagi.", "Pamit dulu, beban kerja sudah terlalu berat untuk punggung jompo ini."]
UCAPAN_TELAT_PAGI = ["🌅 Matahari sudah tinggi, tapi semangatmu masih di kasur?", "⏰ Telat lagi? Jam dinding di kantor ini nggak pernah bohong."]
UCAPAN_NYANYI = ["Nyiurin @{} 🎤: 'Balonku ada lima...'", "@{} dinyanyiin sama admin: 'Halo-halo Bandung...'"]

DEFAULT_DURASI_TOILET = 10
DEFAULT_DURASI_ROKOK = 5
MAX_IZIN_PER_HARI = 6
DB_PATH = "absen_bot.db"

logging.basicConfig(level=logging.INFO)

def parse_time(time_str: str):
    return datetime.strptime(time_str, "%H:%M").time()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE, username TEXT, first_name TEXT, last_name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS absensi (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tanggal TEXT, shift TEXT, waktu_masuk TEXT, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS izin_aktif (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, jenis TEXT, start_time TEXT, expected_end_time TEXT, chat_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS counter_harian (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tanggal TEXT, jenis TEXT, jumlah INTEGER, UNIQUE(user_id, tanggal, jenis))''')
    c.execute('''CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER UNIQUE, chat_type TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pulang_log (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, tanggal TEXT, UNIQUE(chat_id, tanggal))''')
    conn.commit()
    conn.close()

def get_or_create_user(telegram_user):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_user.id,))
    row = c.fetchone()
    if row:
        user_id = row[0]
    else:
        c.execute("INSERT INTO users (telegram_id, username, first_name, last_name) VALUES (?, ?, ?, ?)", (telegram_user.id, telegram_user.username, telegram_user.first_name, telegram_user.last_name))
        user_id = c.lastrowid
        conn.commit()
    conn.close()
    return user_id

def sudah_absen(user_id_db, tanggal, shift):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM absensi WHERE user_id = ? AND tanggal = ? AND shift = ?", (user_id_db, tanggal, shift))
    ok = c.fetchone() is not None
    conn.close()
    return ok

def catat_absen(user_id_db, tanggal, shift, waktu_str, status):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO absensi (user_id, tanggal, shift, waktu_masuk, status) VALUES (?, ?, ?, ?, ?)", (user_id_db, tanggal, shift, waktu_str, status))
    conn.commit()
    conn.close()

def register_chat(chat_id, chat_type):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))
    conn.commit()
    conn.close()

def get_all_chats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id FROM chats")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def sudah_kirim_pulang_today(chat_id, tanggal):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM pulang_log WHERE chat_id = ? AND tanggal = ?", (chat_id, tanggal))
    ok = c.fetchone() is not None
    conn.close()
    return ok

def catat_kirim_pulang(chat_id, tanggal):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO pulang_log (chat_id, tanggal) VALUES (?, ?)", (chat_id, tanggal))
    conn.commit()
    conn.close()

def get_izin_count(user_id_db, tanggal, jenis):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT jumlah FROM counter_harian WHERE user_id = ? AND tanggal = ? AND jenis = ?", (user_id_db, tanggal, jenis))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def increment_izin_count(user_id_db, tanggal, jenis):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO counter_harian (user_id, tanggal, jenis, jumlah) VALUES (?, ?, ?, 1) ON CONFLICT(user_id, tanggal, jenis) DO UPDATE SET jumlah = jumlah + 1", (user_id_db, tanggal, jenis))
    conn.commit()
    conn.close()

def reset_counter_hari_ini_admin(tanggal):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM counter_harian WHERE tanggal = ?", (tanggal,))
    c.execute("DELETE FROM izin_aktif")
    conn.commit()
    conn.close()

def get_active_izin(user_id_db):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, jenis, expected_end_time, chat_id FROM izin_aktif WHERE user_id = ? ORDER BY start_time DESC LIMIT 1", (user_id_db,))
    row = c.fetchone()
    conn.close()
    return row

def add_active_izin(user_id_db, jenis, start_time, expected_end, chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO izin_aktif (user_id, jenis, start_time, expected_end_time, chat_id) VALUES (?, ?, ?, ?, ?)", (user_id_db, jenis, start_time.isoformat(), expected_end.isoformat(), chat_id))
    izin_id = c.lastrowid
    conn.commit()
    conn.close()
    return izin_id

def remove_active_izin(izin_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM izin_aktif WHERE id = ?", (izin_id,))
    conn.commit()
    conn.close()

active_timers = {}

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
                 FROM izin_aktif izin JOIN users ON izin.user_id = users.id''')
    rows = c.fetchall()
    for izin_id, jenis, expected_end_str, durasi, chat_id, telegram_id, username in rows:
        expected_end = datetime.fromisoformat(expected_end_str)
        now = datetime.now()
        if expected_end > now:
            sisa = (expected_end - now).total_seconds()
            await schedule_reminder(app, chat_id, telegram_id, username, jenis, durasi, izin_id, sisa)
    conn.close()

async def cmd_izin(update, context, jenis, default_durasi, nama_izin):
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat: return
    user_id_db = get_or_create_user(user)
    tanggal = datetime.now().strftime("%Y-%m-%d")
    used = get_izin_count(user_id_db, tanggal, jenis)
    if used >= MAX_IZIN_PER_HARI:
        await update.message.reply_text(f"⚠️ Kuota {nama_izin} habis (max {MAX_IZIN_PER_HARI}x).")
        return
    aktif = get_active_izin(user_id_db)
    if aktif:
        await update.message.reply_text(f"⚠️ Masih ada izin {aktif[1]} aktif.")
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
    await update.message.reply_text(f"{mention}, {nama_izin} {durasi} menit mulai {start_time.strftime('%H:%M')}. Selesai {expected_end.strftime('%H:%M')}. Sisa kuota: {MAX_IZIN_PER_HARI - used - 1}. Selesai? /selesai_{jenis}")
    if user.id in active_timers:
        if not active_timers[user.id].done():
            active_timers[user.id].cancel()
        del active_timers[user.id]
    delay = durasi * 60
    await schedule_reminder(context.application, chat.id, user.id, user.username, nama_izin, durasi, izin_id, delay)
    increment_izin_count(user_id_db, tanggal, jenis)

async def cmd_toilet(update, context): await cmd_izin(update, context, "toilet", DEFAULT_DURASI_TOILET, "toilet")
async def cmd_rokok(update, context): await cmd_izin(update, context, "rokok", DEFAULT_DURASI_ROKOK, "rokok")

async def cmd_selesai_izin(update, context, jenis, nama_izin):
    user = update.effective_user
    user_id_db = get_or_create_user(user)
    aktif = get_active_izin(user_id_db)
    if not aktif:
        await update.message.reply_text("Tidak ada izin aktif.")
        return
    izin_id, aktif_jenis, expected_end_str, chat_id = aktif
    if aktif_jenis != jenis:
        await update.message.reply_text(f"Anda sedang izin {aktif_jenis}, bukan {nama_izin}.")
        return
    expected_end = datetime.fromisoformat(expected_end_str)
    now = datetime.now()
    if now > expected_end:
        selisih = int((now - expected_end).total_seconds() // 60)
        await update.message.reply_text(f"⚠️ Melebihi batas {selisih} menit.")
    else:
        await update.message.reply_text(f"✅ {nama_izin.capitalize()} selesai tepat waktu.")
    remove_active_izin(izin_id)
    if user.id in active_timers:
        if not active_timers[user.id].done():
            active_timers[user.id].cancel()
        del active_timers[user.id]

async def cmd_selesai_toilet(update, context): await cmd_selesai_izin(update, context, "toilet", "toilet")
async def cmd_selesai_rokok(update, context): await cmd_selesai_izin(update, context, "rokok", "rokok")

async def cmd_reset_hari_ini(update, context):
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
    await update.message.reply_text(f"✅ Data hari ini ({tanggal}) direset.")

async def cmd_nyanyi(update, context):
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Hanya admin.")
        return
    if not context.args:
        await update.message.reply_text("Format: /nyanyi @username")
        return
    target = context.args[0].lstrip('@')
    await update.message.reply_text(random.choice(UCAPAN_NYANYI).format(target))

async def cmd_start(update, context):
    chat = update.effective_chat
    if chat:
        register_chat(chat.id, chat.type)
    await update.message.reply_text(
        "👋 Bot Absen & Denda\n\n"
        "/absen_masuk - Absen pagi\n"
        "/istirahat_siang_mulai - Mulai istirahat siang\n"
        "/absen_istirahat_siang - Kembali siang\n"
        "/istirahat_sore_mulai - Mulai istirahat sore\n"
        "/absen_istirahat_sore - Kembali sore\n"
        "/toilet - Izin toilet\n"
        "/rokok - Izin rokok\n"
        "/selesai_toilet /selesai_rokok\n"
        "/pulang - Pulang\n"
        "/laporan_bulanan - Laporan admin\n"
        "/nyanyi @user - Nyanyiin user\n"
        "/reset_hari_ini - Reset data hari ini"
    )

async def cmd_absen_masuk(update, context):
    user = update.effective_user
    if not user: return
    user_id_db = get_or_create_user(user)
    now = datetime.now()
    tanggal = now.strftime("%Y-%m-%d")
    jam_kerja = parse_time(JAM_KERJA_MULAI)
    status = "telat" if now.time() > jam_kerja else "tepat"
    if sudah_absen(user_id_db, tanggal, "pagi"):
        await update.message.reply_text("Sudah absen pagi.")
        return
    catat_absen(user_id_db, tanggal, "pagi", now.isoformat(), status)
    msg = f"✅ Absen pagi {now.strftime('%H:%M:%S')} - {status}"
    if status == "telat":
        msg += f"\n⚠️ Telat! {random.choice(UCAPAN_TELAT_PAGI)}"
    await update.message.reply_text(msg)

async def cmd_mulai_istirahat(update, context, shift, jam_mulai, jam_selesai, nama):
    user = update.effective_user
    now = datetime.now()
    jam = now.time()
    if jam < parse_time(jam_mulai) or jam >= parse_time(jam_selesai):
        await update.message.reply_text(f"{nama} hanya antara {jam_mulai} - {jam_selesai}.")
        return
    if sudah_absen(get_or_create_user(user), now.strftime("%Y-%m-%d"), shift):
        await update.message.reply_text(f"Sudah {nama}.")
        return
    catat_absen(get_or_create_user(user), now.strftime("%Y-%m-%d"), shift, now.isoformat(), "tepat")
    await update.message.reply_text(f"✅ {nama} mulai {now.strftime('%H:%M:%S')}")

async def cmd_istirahat_siang_mulai(update, context): await cmd_mulai_istirahat(update, context, "siang_mulai", ISTIRAHAT_1_MULAI, ISTIRAHAT_1_SELESAI, "istirahat siang")
async def cmd_istirahat_sore_mulai(update, context): await cmd_mulai_istirahat(update, context, "sore_mulai", ISTIRAHAT_2_MULAI, ISTIRAHAT_2_SELESAI, "istirahat sore")

async def cmd_absen_istirahat(update, context, shift, jam_selesai, nama):
    user = update.effective_user
    now = datetime.now()
    jam_sel = parse_time(jam_selesai)
    status = "telat" if now.time() > jam_sel else "tepat"
    if status == "telat":
        telat = int((now - datetime.combine(now.date(), jam_sel)).total_seconds() // 60)
        await update.message.reply_text(f"⚠️ Telat {nama} {telat} menit.")
    if sudah_absen(get_or_create_user(user), now.strftime("%Y-%m-%d"), shift):
        await update.message.reply_text(f"Sudah {nama}.")
        return
    catat_absen(get_or_create_user(user), now.strftime("%Y-%m-%d"), shift, now.isoformat(), status)
    await update.message.reply_text(f"✅ {nama} {now.strftime('%H:%M:%S')} - {status}")

async def cmd_absen_siang(update, context): await cmd_absen_istirahat(update, context, "siang", ISTIRAHAT_1_SELESAI, "setelah istirahat siang")
async def cmd_absen_sore(update, context): await cmd_absen_istirahat(update, context, "sore", ISTIRAHAT_2_SELESAI, "setelah istirahat sore")

async def cmd_pulang(update, context):
    now = datetime.now()
    if now.time() < parse_time(JAM_PULANG):
        await update.message.reply_text(f"Belum waktunya pulang. Pulang jam {JAM_PULANG}.")
    else:
        await update.message.reply_text(f"🎉 Pulang Kerja! 🎉\n{random.choice(UCAPAN_PULANG_LIST)}")

async def cmd_laporan_bulanan(update, context):
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("Hanya admin.")
        return
    args = context.args
    if len(args) >= 2:
        tahun, bulan = int(args[0]), int(args[1])
    else:
        now = datetime.now()
        tahun, bulan = now.year, now.month
    start = f"{tahun}-{bulan:02d}-01"
    if bulan == 12:
        end = f"{tahun+1}-01-01"
    else:
        end = f"{tahun}-{bulan+1:02d}-01"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, telegram_id, username, first_name FROM users")
    users = c.fetchall()
    laporan = f"📊 Laporan {tahun}-{bulan:02d}\n\n"
    for uid, _, uname, fname in users:
        nama = uname or fname
        c.execute("SELECT COUNT(*) FROM absensi WHERE user_id=? AND tanggal>=? AND tanggal<? AND shift='pagi' AND status='telat'", (uid, start, end))
        tp = c.fetchone()[0]
        c.execute("SELECT SUM(jumlah) FROM counter_harian WHERE user_id=? AND tanggal>=? AND tanggal<? AND jenis='toilet'", (uid, start, end))
        tt = c.fetchone()[0] or 0
        c.execute("SELECT SUM(jumlah) FROM counter_harian WHERE user_id=? AND tanggal>=? AND tanggal<? AND jenis='rokok'", (uid, start, end))
        tr = c.fetchone()[0] or 0
        laporan += f"{nama} | Telat pagi: {tp} | Toilet: {tt}x | Rokok: {tr}x\n"
    conn.close()
    await update.message.reply_text(laporan)

async def daily_pulang_checker(app):
    while True:
        now = datetime.now()
        if now.hour == 22 and now.minute == 0:
            tanggal = now.strftime("%Y-%m-%d")
            for cid in get_all_chats():
                if not sudah_kirim_pulang_today(cid, tanggal):
                    await app.bot.send_message(cid, f"🎉 Pulang Kerja! 🎉\n{random.choice(UCAPAN_PULANG_LIST)}")
                    catat_kirim_pulang(cid, tanggal)
        await asyncio.sleep(60)

async def set_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Mulai"), BotCommand("absen_masuk", "Absen pagi"), BotCommand("istirahat_siang_mulai", "Mulai istirahat siang"),
        BotCommand("absen_istirahat_siang", "Kembali siang"), BotCommand("istirahat_sore_mulai", "Mulai istirahat sore"),
        BotCommand("absen_istirahat_sore", "Kembali sore"), BotCommand("toilet", "Izin toilet"), BotCommand("rokok", "Izin rokok"),
        BotCommand("selesai_toilet", "Selesai toilet"), BotCommand("selesai_rokok", "Selesai rokok"), BotCommand("pulang", "Pulang"),
        BotCommand("laporan_bulanan", "Laporan admin"), BotCommand("nyanyi", "Nyanyi @user"), BotCommand("reset_hari_ini", "Reset data")
    ])

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

    async def post_init(app):
        await restore_timers(app)
        await set_commands(app)
        asyncio.create_task(daily_pulang_checker(app))

    app.post_init = post_init
    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
