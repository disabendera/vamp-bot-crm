import aiosqlite
from config import DB_PATH

# Роли: pending -> agent -> mentor -> admin; banned — отклонён/забанен
ROLES = ("pending", "agent", "mentor", "admin", "banned")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            role TEXT NOT NULL DEFAULT 'pending',
            team TEXT DEFAULT '',
            balance REAL NOT NULL DEFAULT 0,
            total_earned REAL NOT NULL DEFAULT 0,
            pending REAL NOT NULL DEFAULT 0,
            stars INTEGER NOT NULL DEFAULT 0,
            wallet TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tg_id INTEGER NOT NULL,
            interview_id INTEGER,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            username TEXT DEFAULT '',
            shifts INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active', -- active / dropped
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new', -- new / done
            app_status TEXT DEFAULT 'Не подтверждена',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            leader_tg_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS agent_partners (
            agent_tg_id INTEGER NOT NULL,
            partner_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            PRIMARY KEY (agent_tg_id, partner_id)
        );
        CREATE TABLE IF NOT EXISTS agent_partner_rotation (
            agent_tg_id INTEGER PRIMARY KEY,
            next_index INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS processed_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id INTEGER NOT NULL,
            shift_date TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 12.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(interview_id, shift_date)
        );
        CREATE TABLE IF NOT EXISTS sheet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id TEXT DEFAULT '',
            model_name TEXT DEFAULT '',
            sheet_name TEXT DEFAULT '',
            col_title TEXT DEFAULT '',
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            user_email TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # миграция для баз, созданных старой версией
        for column, ddl in (
            ("interview_id", "ALTER TABLE models ADD COLUMN interview_id INTEGER"),
            ("phone", "ALTER TABLE models ADD COLUMN phone TEXT DEFAULT ''"),
            ("username", "ALTER TABLE models ADD COLUMN username TEXT DEFAULT ''"),
            ("shifts", "ALTER TABLE models ADD COLUMN shifts INTEGER NOT NULL DEFAULT 0"),
            ("pending", "ALTER TABLE users ADD COLUMN pending REAL NOT NULL DEFAULT 0"),
            ("total_earned", "ALTER TABLE users ADD COLUMN total_earned REAL NOT NULL DEFAULT 0"),
            ("partner", "ALTER TABLE interviews ADD COLUMN partner TEXT DEFAULT ''"),
            ("team_id", "ALTER TABLE users ADD COLUMN team_id INTEGER"),
            ("app_status", "ALTER TABLE interviews ADD COLUMN app_status TEXT DEFAULT 'Не подтверждена'"),
            ("position", "ALTER TABLE interviews ADD COLUMN position TEXT DEFAULT ''"),
            ("chat_id", "ALTER TABLE partners ADD COLUMN chat_id TEXT DEFAULT ''"),
            ("sheet_url", "ALTER TABLE partners ADD COLUMN sheet_url TEXT DEFAULT ''"),
            ("report_sheet_url", "ALTER TABLE interviews ADD COLUMN report_sheet_url TEXT DEFAULT ''"),
            ("topic_applications", "ALTER TABLE partners ADD COLUMN topic_applications INTEGER DEFAULT NULL"),
            ("topic_confirmations", "ALTER TABLE partners ADD COLUMN topic_confirmations INTEGER DEFAULT NULL"),
            ("topic_sobes", "ALTER TABLE partners ADD COLUMN topic_sobes INTEGER DEFAULT NULL"),
            ("topic_registration", "ALTER TABLE partners ADD COLUMN topic_registration INTEGER DEFAULT NULL"),
            ("topic_shift1", "ALTER TABLE partners ADD COLUMN topic_shift1 INTEGER DEFAULT NULL"),
            ("topic_shift2", "ALTER TABLE partners ADD COLUMN topic_shift2 INTEGER DEFAULT NULL"),
            ("topic_cancelled", "ALTER TABLE partners ADD COLUMN topic_cancelled INTEGER DEFAULT NULL"),
            ("sobes_date", "ALTER TABLE interviews ADD COLUMN sobes_date TEXT DEFAULT ''"),
            ("sobes_time", "ALTER TABLE interviews ADD COLUMN sobes_time TEXT DEFAULT ''"),
            ("confirm_sent", "ALTER TABLE interviews ADD COLUMN confirm_sent INTEGER DEFAULT 0"),
            ("agent_code", "ALTER TABLE users ADD COLUMN agent_code INTEGER"),
        ):
            try:
                await db.execute(ddl)
            except aiosqlite.OperationalError:
                pass  # колонка уже есть
        await db.execute("UPDATE users SET agent_code = 1000 + id WHERE agent_code IS NULL")
        # Для старых баз переносим накопленный текущий баланс в историю заработка.
        await db.execute(
            "UPDATE users SET total_earned = balance "
            "WHERE total_earned = 0 AND balance != 0"
        )
        await db.execute(
            "UPDATE interviews SET position='Модель' "
            "WHERE (position IS NULL OR position='') AND text LIKE 'Позиция: Модель%'")
        await db.execute(
            "UPDATE interviews SET position='Оператор' "
            "WHERE (position IS NULL OR position='') AND text LIKE 'Позиция: Оператор%'")
        await db.commit()



# ---------- users ----------

async def get_user(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        return await cur.fetchone()


async def create_user(tg_id: int, username: str, full_name: str, role: str = "pending"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, full_name, role) VALUES (?,?,?,?)",
            (tg_id, username or "", full_name or "", role),
        )
        await db.execute("UPDATE users SET agent_code = 1000 + id WHERE tg_id = ? AND agent_code IS NULL", (tg_id,))
        await db.commit()


async def set_role(tg_id: int, role: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET role = ? WHERE tg_id = ?", (role, tg_id))
        await db.commit()


async def set_wallet(tg_id: int, wallet: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET wallet = ? WHERE tg_id = ?", (wallet, tg_id))
        await db.commit()


async def add_balance(tg_id: int, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE tg_id = ?",
            (amount, amount, tg_id),
        )
        await db.commit()


async def mark_all_balances_paid() -> float:
    """Обнуляет текущие балансы после выплаты, сохраняя total_earned."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(SUM(balance), 0) FROM users WHERE role != 'banned'")
        total = (await cur.fetchone())[0]
        await db.execute("UPDATE users SET balance = 0 WHERE role != 'banned'")
        await db.commit()
        return float(total or 0)


async def get_users_by_role(role: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE role = ? ORDER BY created_at", (role,))
        return await cur.fetchall()


async def get_staff_ids():
    """tg_id всех наставников и админов — для уведомлений о заявках."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tg_id FROM users WHERE role IN ('mentor','admin')")
        return [r[0] for r in await cur.fetchall()]


async def get_mentor_ids():
    """tg_id всех наставников (без админа) — для уведомлений о новых заявках."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tg_id FROM users WHERE role = 'mentor'")
        return [r[0] for r in await cur.fetchall()]


# ---------- models ----------

async def get_models(owner_tg_id: int, status: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        conditions = ["m.owner_tg_id = ?", "m.interview_id IS NOT NULL"]
        params = [owner_tg_id]
        registered = (
            "(LOWER(COALESCE(i.app_status, '')) LIKE '%регистрац%' "
            "OR LOWER(COALESCE(i.app_status, '')) LIKE '%актив%')"
        )
        not_cancelled = (
            "LOWER(COALESCE(i.app_status, '')) NOT LIKE '%слив%' "
            "AND LOWER(COALESCE(i.app_status, '')) NOT LIKE '%отмен%' "
            "AND LOWER(COALESCE(i.app_status, '')) NOT LIKE '%отклон%' "
            "AND LOWER(COALESCE(i.app_status, '')) NOT LIKE '%отказ%'"
        )
        if status == "active":
            conditions.extend(["m.status = 'active'", registered, not_cancelled])
        elif status == "dropped":
            conditions.extend(["m.status = 'dropped'", "m.shifts >= 1"])
        query = (
            "SELECT m.* FROM models m "
            "JOIN interviews i ON i.id = m.interview_id "
            f"WHERE {' AND '.join(conditions)} ORDER BY m.id"
        )
        cur = await db.execute(query, params)
        return await cur.fetchall()


async def add_model(owner_tg_id: int, name: str, phone: str = "", username: str = "", interview_id: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO models (owner_tg_id, interview_id, name, phone, username) VALUES (?,?,?,?,?)",
            (owner_tg_id, interview_id, name, phone, username),
        )
        await db.commit()


async def get_model_by_id(model_id: int, owner_tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM models WHERE id = ? AND owner_tg_id = ?", (model_id, owner_tg_id)
        )
        return await cur.fetchone()


async def inc_shift(model_id: int, owner_tg_id: int, delta: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET shifts = MAX(shifts + ?, 0) WHERE id = ? AND owner_tg_id = ?",
            (delta, model_id, owner_tg_id),
        )
        await db.commit()


async def set_model_status(model_id: int, owner_tg_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET status = ? WHERE id = ? AND owner_tg_id = ?",
            (status, model_id, owner_tg_id),
        )
        await db.commit()


import random
import re

async def add_interview(tg_id: int, text: str, partner: str = "", position: str = "", sobes_date: str = "", sobes_time: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        while True:
            rand_id = random.randint(100000, 999999)
            try:
                await db.execute(
                    "INSERT INTO interviews (id, tg_id, text, partner, position, sobes_date, sobes_time) VALUES (?,?,?,?,?,?,?)",
                    (rand_id, tg_id, text, partner, position, sobes_date, sobes_time),
                )
                await db.commit()
                return rand_id
            except aiosqlite.IntegrityError:
                continue


async def get_occupied_times(chosen_date: str, partner: str = "") -> set:
    """
    Возвращает множество занятых слотов времени (например {"17:00"}) на дату chosen_date.
    Слот освобождается, если у заявки статус Слив/Отмена/Отклонена/Не принято.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT sobes_time, app_status, text FROM interviews "
            "WHERE (partner = ? OR ? = '') "
            "AND (sobes_date = ? OR text LIKE ?) "
            "AND (app_status IS NULL OR ( "
            "  LOWER(app_status) NOT LIKE '%слив%' "
            "  AND LOWER(app_status) NOT LIKE '%отмен%' "
            "  AND LOWER(app_status) NOT LIKE '%отклон%' "
            "  AND LOWER(app_status) NOT LIKE '%не принят%' "
            "  AND LOWER(app_status) NOT LIKE '%отказ%' "
            "))",
            (partner, partner, chosen_date, f"%Собес: {chosen_date}%")
        )
        rows = await cur.fetchall()

    occupied = set()
    for r in rows:
        r_dict = dict(r)
        t = r_dict.get("sobes_time")
        if not t:
            m = re.search(r"в\s+(\d{1,2}:\d{2})\s+МСК", r_dict.get("text") or "")
            if m:
                t = m.group(1)
                if len(t) == 4:
                    t = "0" + t
        if t:
            occupied.add(t)
    return occupied


async def get_interview(interview_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,))
        return await cur.fetchone()


async def update_interview_app_status(interview_id: int, app_status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "UPDATE interviews SET app_status = ? WHERE id = ?", (app_status, interview_id)
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,))
        return await cur.fetchone()


async def update_interview_report_sheet(interview_id: int | str, report_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if not str(interview_id).isdigit():
            return
        await db.execute(
            "UPDATE interviews SET report_sheet_url = ? WHERE id = ?",
            (report_url, int(interview_id))
        )
        await db.commit()


async def get_interviews_with_report_sheets():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM interviews WHERE report_sheet_url IS NOT NULL AND report_sheet_url != '' GROUP BY report_sheet_url"
        )
        return await cur.fetchall()


async def register_shift_payout(interview_id: int, shift_date: str, amount: float = 12.0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,))
        interview = await cur.fetchone()
        if not interview:
            return None

        report_url = interview["report_sheet_url"]
        if report_url:
            # Проверяем, не выписывался ли уже бонус за эту смену по этому же отчётнику
            check_cur = await db.execute("""
                SELECT ps.id FROM processed_shifts ps
                JOIN interviews i ON ps.interview_id = i.id
                WHERE (i.report_sheet_url = ? OR ps.interview_id = ?) AND ps.shift_date = ?
            """, (report_url, interview_id, shift_date))
            if await check_cur.fetchone():
                return None

        try:
            await db.execute(
                "INSERT INTO processed_shifts (interview_id, shift_date, amount) VALUES (?,?,?)",
                (interview_id, shift_date, amount)
            )
        except aiosqlite.IntegrityError:
            return None

        agent_tg_id = interview["tg_id"]
        await db.execute(
            "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE tg_id = ?",
            (amount, amount, agent_tg_id)
        )
        await db.commit()

        cur_u = await db.execute("SELECT balance FROM users WHERE tg_id = ?", (agent_tg_id,))
        u_row = await cur_u.fetchone()
        new_balance = u_row["balance"] if u_row else 0.0

        return {
            "interview_id": interview_id,
            "agent_tg_id": agent_tg_id,
            "shift_date": shift_date,
            "amount": amount,
            "new_balance": new_balance,
            "partner": interview["partner"] or "-",
            "text": interview["text"] or ""
        }


async def get_interview_processed_shifts_count(interview_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM processed_shifts WHERE interview_id = ?", (interview_id,))
        row = await cur.fetchone()
        return row[0] if row else 0


# ---------- partners ----------


async def get_partners():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM partners ORDER BY name")
        return await cur.fetchall()


async def get_partner(partner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM partners WHERE id = ?", (partner_id,))
        return await cur.fetchone()


async def get_partner_by_name(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM partners WHERE name = ?", (name,))
        return await cur.fetchone()


async def get_agent_partners(agent_tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT p.* FROM agent_partners ap JOIN partners p ON p.id = ap.partner_id "
            "WHERE ap.agent_tg_id = ? ORDER BY ap.sort_order, p.id",
            (agent_tg_id,),
        )
        return await cur.fetchall()


async def toggle_agent_partner(agent_tg_id: int, partner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM agent_partners WHERE agent_tg_id = ? AND partner_id = ?",
            (agent_tg_id, partner_id),
        )
        exists = await cur.fetchone()
        if exists:
            await db.execute(
                "DELETE FROM agent_partners WHERE agent_tg_id = ? AND partner_id = ?",
                (agent_tg_id, partner_id),
            )
        else:
            cur = await db.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM agent_partners WHERE agent_tg_id = ?",
                (agent_tg_id,),
            )
            sort_order = (await cur.fetchone())[0]
            await db.execute(
                "INSERT INTO agent_partners (agent_tg_id, partner_id, sort_order) VALUES (?,?,?)",
                (agent_tg_id, partner_id, sort_order),
            )
        await db.execute(
            "INSERT INTO agent_partner_rotation (agent_tg_id, next_index) VALUES (?, 0) "
            "ON CONFLICT(agent_tg_id) DO UPDATE SET next_index = 0",
            (agent_tg_id,),
        )
        await db.commit()


async def reserve_next_agent_partner(agent_tg_id: int):
    """Выбирает партнёра агента по кругу и сразу сдвигает указатель."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT p.* FROM agent_partners ap JOIN partners p ON p.id = ap.partner_id "
            "WHERE ap.agent_tg_id = ? ORDER BY ap.sort_order, p.id",
            (agent_tg_id,),
        )
        partners = await cur.fetchall()
        if not partners:
            await db.commit()
            return None
        cur = await db.execute(
            "SELECT next_index FROM agent_partner_rotation WHERE agent_tg_id = ?",
            (agent_tg_id,),
        )
        row = await cur.fetchone()
        index = (row[0] if row else 0) % len(partners)
        partner = partners[index]
        await db.execute(
            "INSERT INTO agent_partner_rotation (agent_tg_id, next_index) VALUES (?, ?) "
            "ON CONFLICT(agent_tg_id) DO UPDATE SET next_index = excluded.next_index",
            (agent_tg_id, (index + 1) % len(partners)),
        )
        await db.commit()
        return partner


import re


def parse_topic_input(val: str) -> tuple[str | None, int | None]:
    """
    Принимает либо ссылку вида https://t.me/c/1234567890/45 (или t.me/c/1234567890/45),
    либо просто ID топика (число, напр. 45).
    Возвращает (chat_id, topic_id).
    """
    if not val:
        return None, None
    val = val.strip()
    match = re.search(r"t\.me/c/(\d+)/(\d+)", val)
    if match:
        raw_chat_id = match.group(1)
        chat_id = f"-100{raw_chat_id}"
        topic_id = int(match.group(2))
        return chat_id, topic_id
    if val.isdigit():
        return None, int(val)
    return None, None


async def add_partner(name: str, chat_id: str = "", sheet_url: str = "",
                      topic_applications: int = None, topic_confirmations: int = None,
                      topic_registration: int = None, topic_shift1: int = None,
                      topic_shift2: int = None, topic_cancelled: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO partners (name, chat_id, sheet_url, topic_applications, topic_confirmations, topic_registration, topic_shift1, topic_shift2, topic_cancelled) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, chat_id, sheet_url, topic_applications, topic_confirmations, topic_registration, topic_shift1, topic_shift2, topic_cancelled),
        )
        await db.commit()
        return cur.lastrowid


async def update_partner_topic(partner_id: int, topic_key: str, topic_id: int | None, chat_id: str = None):
    valid_keys = {"topic_applications", "topic_confirmations", "topic_sobes", "topic_registration", "topic_shift1", "topic_shift2", "topic_cancelled"}
    if topic_key not in valid_keys:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        if chat_id:
            await db.execute(f"UPDATE partners SET {topic_key} = ?, chat_id = ? WHERE id = ?", (topic_id, chat_id, partner_id))
        else:
            await db.execute(f"UPDATE partners SET {topic_key} = ? WHERE id = ?", (topic_id, partner_id))
        await db.commit()


async def update_partner_info(partner_id: int, chat_id: str = None, sheet_url: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if chat_id is not None:
            await db.execute("UPDATE partners SET chat_id = ? WHERE id = ?", (chat_id, partner_id))
        if sheet_url is not None:
            await db.execute("UPDATE partners SET sheet_url = ? WHERE id = ?", (sheet_url, partner_id))
        await db.commit()


async def delete_partner(partner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM partners WHERE id = ?", (partner_id,))
        await db.commit()


async def get_new_interviews():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT i.*, u.username, u.full_name FROM interviews i "
            "LEFT JOIN users u ON u.tg_id = i.tg_id "
            "WHERE i.status = 'new' ORDER BY i.created_at"
        )
        return await cur.fetchall()


async def close_interview(interview_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE interviews SET status = 'done' WHERE id = ?", (interview_id,))
        await db.commit()


# ---------- materials ----------

async def add_material(title: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO materials (title, content) VALUES (?,?)", (title, content))
        await db.commit()


async def get_materials():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM materials ORDER BY id")
        return await cur.fetchall()


async def delete_material(material_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        await db.commit()


async def get_material(material_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
        return await cur.fetchone()


# ---------- settings ----------

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


# ---------- лидеры / команды / поиск ----------

async def refresh_user_info(tg_id: int, username: str, full_name: str):
    """Обновляет юзернейм и имя при каждом обращении — поиск найдёт и после смены ника."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET username = ?, full_name = ? WHERE tg_id = ?",
            (username or "", full_name or "", tg_id),
        )
        await db.commit()


def get_agent_code(user_obj) -> int | str:
    """Безопасно возвращает agent_code для пользователя (поддерживает sqlite3.Row, dict, None)."""
    if not user_obj:
        return ""
    u = dict(user_obj)
    if u.get("agent_code"):
        return u["agent_code"]
    if "id" in u and u["id"]:
        return 1000 + u["id"]
    return u.get("tg_id", "")


async def find_agent(query: str):
    """Поиск по внутреннему ID агента (agent_code / id), Telegram ID или @юзернейму."""
    q = query.strip().lstrip("@").lstrip("№").strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if q.isdigit():
            val = int(q)
            cur = await db.execute(
                "SELECT * FROM users WHERE agent_code = ? OR id = ? OR tg_id = ?",
                (val, val, val),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM users WHERE lower(username) = lower(?)", (q,)
            )
        return await cur.fetchone()


async def models_summary(owner_tg_id: int):
    """{'active': (кол-во, смен), 'dropped': (кол-во, смен)}"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(shifts),0) FROM models "
            "WHERE owner_tg_id = ? GROUP BY status", (owner_tg_id,)
        )
        return {row[0]: (row[1], row[2]) for row in await cur.fetchall()}


async def add_team(name: str, leader_tg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO teams (name, leader_tg_id) VALUES (?,?)", (name, leader_tg_id)
        )
        await db.commit()
        return cur.lastrowid


async def get_teams():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM teams ORDER BY name")
        return await cur.fetchall()


async def get_team(team_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
        return await cur.fetchone()


async def set_team(tg_id: int, team_id: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET team_id = ? WHERE tg_id = ?", (team_id, tg_id))
        await db.commit()


async def team_agents(team_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM users WHERE team_id = ? ORDER BY id", (team_id,)
        )
        return await cur.fetchall()


async def team_models(team_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT m.*, u.username AS owner_username, u.full_name AS owner_name, u.id AS owner_no "
            "FROM models m "
            "JOIN users u ON u.tg_id = m.owner_tg_id "
            "JOIN interviews i ON i.id = m.interview_id "
            "WHERE u.team_id = ? AND ("
            "  (m.status = 'active' "
            "   AND (LOWER(COALESCE(i.app_status, '')) LIKE '%регистрац%' "
            "        OR LOWER(COALESCE(i.app_status, '')) LIKE '%актив%') "
            "   AND LOWER(COALESCE(i.app_status, '')) NOT LIKE '%слив%' "
            "   AND LOWER(COALESCE(i.app_status, '')) NOT LIKE '%отмен%' "
            "   AND LOWER(COALESCE(i.app_status, '')) NOT LIKE '%отклон%' "
            "   AND LOWER(COALESCE(i.app_status, '')) NOT LIKE '%отказ%') "
            "  OR (m.status = 'dropped' AND m.shifts >= 1)"
            ") ORDER BY m.status, m.shifts DESC", (team_id,)
        )
        return await cur.fetchall()


# ---------- статусы заявок и аналитика ----------

APP_STATUSES = [
    "Отказ в назначении",
    "Отказ со стороны кандидата",
    "Отказ в работе",
    "Перенос",
    "Регистрация",
    "Не подтверждена",
    "Назначено",
    "Слив",
    "Активна",
]


async def set_app_status(interview_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE interviews SET app_status = ? WHERE id = ?", (status, interview_id)
        )
        await db.commit()


async def get_team_by_leader(leader_tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM teams WHERE leader_tg_id = ?", (leader_tg_id,)
        )
        return await cur.fetchone()


async def analytics_interviews(dfrom: str, dto: str,
                               team_id: int | None = None,
                               tg_id: int | None = None,
                               position: str | None = None) -> dict:
    """Разбивка заявок по статусам за период. Возвращает {статус: количество}."""
    cond = "i.created_at >= ? AND i.created_at < ?"
    params: list = [dfrom, dto]
    join = ""
    if team_id is not None:
        join = "JOIN users u ON u.tg_id = i.tg_id"
        cond += " AND u.team_id = ?"
        params.append(team_id)
    if tg_id is not None:
        cond += " AND i.tg_id = ?"
        params.append(tg_id)
    if position is not None:
        cond += " AND i.position = ?"
        params.append(position)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"SELECT COALESCE(i.app_status, 'Не подтверждена'), COUNT(*) "
            f"FROM interviews i {join} WHERE {cond} "
            f"GROUP BY COALESCE(i.app_status, 'Не подтверждена')",
            params,
        )
        return {row[0]: row[1] for row in await cur.fetchall()}


async def new_agents_count(team_id: int, dfrom: str, dto: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM users WHERE team_id = ? "
            "AND created_at >= ? AND created_at < ?",
            (team_id, dfrom, dto),
        )
        return (await cur.fetchone())[0]


async def delete_team(team_id: int):
    """Удаляет команду: агенты уходят в соло, лидер становится агентом"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT leader_tg_id FROM teams WHERE id = ?", (team_id,))
        row = await cur.fetchone()
        if row:
            await db.execute("UPDATE users SET role = 'agent' WHERE tg_id = ? AND role = 'leader'", (row[0],))
        await db.execute("UPDATE users SET team_id = NULL WHERE team_id = ?", (team_id,))
        await db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        await db.commit()
        return row[0] if row else None


# ---------- Топы (воронка): записи → регистрации → 1-я смена → 2-я смена ----------

REG_STATUSES = ("Регистрация", "Активна")  # статусы, которые считаем дошедшими до регистрации


async def top_agents(dfrom: str, dto: str, limit: int = 10, solo_only: bool = False):
    """Топ агентов по заявкам за период + регистрации. solo_only - только без команды"""
    solo_cond = "AND u.team_id IS NULL AND u.role = 'agent' " if solo_only else ""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT u.id AS agent_no, u.tg_id, u.full_name, u.username, "
            " COUNT(i.id) AS records, "
            " SUM(CASE WHEN i.app_status IN (?, ?) THEN 1 ELSE 0 END) AS regs "
            "FROM interviews i JOIN users u ON u.tg_id = i.tg_id "
            "WHERE i.created_at >= ? AND i.created_at < ? "
            + solo_cond +
            "GROUP BY u.tg_id ORDER BY records DESC LIMIT ?",
            (*REG_STATUSES, dfrom, dto, limit),
        )
        return await cur.fetchall()


async def top_teams(dfrom: str, dto: str, limit: int = 10):
    """Топ команд (соло-агенты объединяются в строку «Соло»)"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT COALESCE(t.name, 'Соло') AS team_name, u.team_id, "
            " COUNT(i.id) AS records, "
            " SUM(CASE WHEN i.app_status IN (?, ?) THEN 1 ELSE 0 END) AS regs "
            "FROM interviews i JOIN users u ON u.tg_id = i.tg_id "
            "LEFT JOIN teams t ON t.id = u.team_id "
            "WHERE i.created_at >= ? AND i.created_at < ? "
            "GROUP BY u.team_id ORDER BY records DESC LIMIT ?",
            (*REG_STATUSES, dfrom, dto, limit),
        )
        return await cur.fetchall()


async def shifts_funnel_by_agent(dfrom: str, dto: str) -> dict:
    """{tg_id: (моделей с 1+ сменой, моделей с 2+ сменами)} по моделям, созданным в период"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT owner_tg_id, "
            " SUM(CASE WHEN shifts >= 1 THEN 1 ELSE 0 END), "
            " SUM(CASE WHEN shifts >= 2 THEN 1 ELSE 0 END) "
            "FROM models WHERE created_at >= ? AND created_at < ? "
            "GROUP BY owner_tg_id",
            (dfrom, dto),
        )
        return {r[0]: (r[1], r[2]) for r in await cur.fetchall()}


async def shifts_funnel_by_team(dfrom: str, dto: str) -> dict:
    """{team_id|None: (1+ смена, 2+ смены)}"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT u.team_id, "
            " SUM(CASE WHEN m.shifts >= 1 THEN 1 ELSE 0 END), "
            " SUM(CASE WHEN m.shifts >= 2 THEN 1 ELSE 0 END) "
            "FROM models m JOIN users u ON u.tg_id = m.owner_tg_id "
            "WHERE m.created_at >= ? AND m.created_at < ? "
            "GROUP BY u.team_id",
            (dfrom, dto),
        )
        return {r[0]: (r[1], r[2]) for r in await cur.fetchall()}


async def update_material(material_id: int, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE materials SET content = ? WHERE id = ?", (content, material_id))
        await db.commit()


async def search_models(query: str, tg_id: int | None = None,
                        team_id: int | None = None, limit: int = 5):
    """Поиск моделей по спец-номеру, ФИО или телефону.
    tg_id - только модели агента; team_id - модели всей команды; иначе все.
    Фильтрация по имени - в Python: sqlite не понимает регистр кириллицы"""
    q = query.strip().lstrip("@")
    scope, params = "1=1", []
    if tg_id is not None:
        scope = "m.owner_tg_id = ?"
        params.append(tg_id)
    elif team_id is not None:
        scope = "u.team_id = ?"
        params.append(team_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT m.*, u.full_name AS owner_name, u.username AS owner_username, "
            f" u.id AS owner_no, u.tg_id AS owner_tg, u.team_id AS owner_team "
            f"FROM models m JOIN users u ON u.tg_id = m.owner_tg_id "
            f"WHERE {scope} ORDER BY m.id",
            params,
        )
        rows = await cur.fetchall()

    digits = q.replace("+", "").replace(" ", "").replace("-", "")
    result = []
    for m in rows:
        if digits.isdigit() and digits:
            phone_digits = (m["phone"] or "").replace("+", "").replace(" ", "").replace("-", "")
            if m["id"] == int(digits) or (phone_digits and digits in phone_digits):
                result.append(m)
        else:
            name = (m["name"] or "").casefold()
            uname = (m["username"] or "").casefold()
            if q.casefold() in name or q.casefold() == uname:
                result.append(m)
        if len(result) >= limit:
            break
    return result


async def format_anketa_topic_message(interview: dict, header_title: str) -> str:
    inv = dict(interview)
    agent_id = inv["tg_id"]
    agent_user = await get_user(agent_id)
    agent_code = get_agent_code(agent_user) or agent_id
    
    raw_text = inv.get("text") or ""
    
    msg = (
        f"{header_title}\n"
        f"👤 <b>Агент ID:</b> <code>{agent_code}</code>\n\n"
        f"{raw_text}"
    )
    return msg


from datetime import datetime, timezone, timedelta

def parse_interview_datetime(sobes_date: str, sobes_time: str, text: str = ""):
    try:
        if not sobes_date and text:
            m = re.search(r"(\d{2}\.\d{2}\.\d{2,4})", text)
            if m:
                sobes_date = m.group(1)
        if not sobes_time and text:
            m = re.search(r"в\s+(\d{1,2}:\d{2})", text)
            if m:
                sobes_time = m.group(1)
                
        if not sobes_date or not sobes_time:
            return None

        time_clean = re.search(r"(\d{1,2}:\d{2})", sobes_time)
        if not time_clean:
            return None
        time_str = time_clean.group(1)
        if len(time_str) == 4:
            time_str = "0" + time_str
            
        date_str = sobes_date.strip()
        dt_str = f"{date_str} {time_str}"
        try:
            return datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
        except ValueError:
            return datetime.strptime(dt_str, "%d.%m.%y %H:%M")
    except Exception:
        return None


async def mark_confirm_sent(interview_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE interviews SET confirm_sent = 1 WHERE id = ?", (interview_id,))
        await db.commit()


async def get_unnotified_accepted_interviews():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM interviews "
            "WHERE LOWER(app_status) LIKE '%принято%' "
            "AND (confirm_sent IS NULL OR confirm_sent = 0)"
        )
        return await cur.fetchall()


async def get_today_interviews_grouped_by_agent(today_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM interviews "
            "WHERE (sobes_date = ? OR text LIKE ?) "
            "AND (app_status IS NULL OR ( "
            "  LOWER(app_status) NOT LIKE '%слив%' "
            "  AND LOWER(app_status) NOT LIKE '%отмен%' "
            "  AND LOWER(app_status) NOT LIKE '%отклон%' "
            "  AND LOWER(app_status) NOT LIKE '%не принят%' "
            "  AND LOWER(app_status) NOT LIKE '%отказ%' "
            ")) ORDER BY sobes_time ASC",
            (today_str, f"%Собес: {today_str}%")
        )
        rows = await cur.fetchall()
        
    by_agent = {}
    for r in rows:
        r_dict = dict(r)
        agent_id = r_dict["tg_id"]
        if agent_id not in by_agent:
            by_agent[agent_id] = []
        by_agent[agent_id].append(r_dict)
    return by_agent


# ---------- история изменений таблицы ----------

async def add_sheet_history_entry(
    interview_id: str = "",
    model_name: str = "",
    sheet_name: str = "",
    col_title: str = "",
    old_value: str = "",
    new_value: str = "",
    user_email: str = ""
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO sheet_history (interview_id, model_name, sheet_name, col_title, old_value, new_value, user_email)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (interview_id, model_name, sheet_name, col_title, old_value, new_value, user_email)
        )
        await db.commit()


async def get_sheet_history(limit: int = 5000):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM sheet_history ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return await cur.fetchall()


async def get_main_sheet_history(limit: int = 5000):
    """Возвращает изменения только из листов основной таблицы партнёра."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM sheet_history "
            "WHERE lower(trim(sheet_name)) IN (?, ?) "
            "ORDER BY id DESC LIMIT ?",
            ("заявки", "запуски", limit),
        )
        return await cur.fetchall()

