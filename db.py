import aiosqlite
import random
import re
from config import DB_PATH

# Роли: pending -> agent -> mentor -> admin; banned — отклонён/забанен
ROLES = ("pending", "agent", "mentor", "admin", "banned")


def is_accepted_app_status(status: str | None) -> bool:
    status_lower = str(status or "").lower()
    negative_markers = ("не принят", "непринят", "отклон", "отказ", "слив", "отмен")
    return "принят" in status_lower and not any(marker in status_lower for marker in negative_markers)


def extract_candidate_info_from_text(text: str):
    source = text or ""

    def field_value(pattern: str) -> str:
        match = re.search(pattern, source, flags=re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else ""

    name = field_value(r"^1\)\s*(?:имя|фио)\s*:\s*(.+)$") or "Модель"
    phone_raw = field_value(r"^3\)\s*(?:номер|телефон)\s*:\s*(.+)$")
    username_raw = field_value(r"^4\)\s*(?:телеграм|tg)\s*:\s*(.+)$")

    phone_match = re.search(r"(\+?\d[\d\s\-()]{7,}\d)", phone_raw)
    phone = phone_match.group(1).strip() if phone_match else phone_raw

    username_match = re.search(r"@[A-Za-z0-9_]{3,}", username_raw)
    if username_match:
        username = username_match.group(0).lstrip("@")
    else:
        username = username_raw.replace("https://t.me/", "").replace("t.me/", "").lstrip("@")

    return name, phone, username


async def _table_exists(db, table: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    return await cur.fetchone() is not None


async def _table_columns(db, table: str) -> set[str]:
    if not await _table_exists(db, table):
        return set()
    cur = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


async def _add_column_if_missing(db, table: str, column: str, ddl: str):
    if column not in await _table_columns(db, table):
        await db.execute(ddl)


async def _generate_model_code(db) -> str:
    while True:
        model_code = str(random.randint(100000, 999999))
        cur = await db.execute("SELECT 1 FROM models WHERE model_code = ?", (model_code,))
        if not await cur.fetchone():
            return model_code


async def _preferred_model_code(db, preferred: int | str | None = None) -> str:
    if preferred is not None:
        code = str(preferred).strip()
        if code:
            cur = await db.execute("SELECT 1 FROM models WHERE model_code = ?", (code,))
            if not await cur.fetchone():
                return code
    return await _generate_model_code(db)


async def _ensure_model_codes(db):
    cur = await db.execute("SELECT id FROM models WHERE model_code IS NULL OR model_code = ''")
    for (model_pk,) in await cur.fetchall():
        await db.execute(
            "UPDATE models SET model_code = ? WHERE id = ?",
            (await _generate_model_code(db), model_pk),
        )


async def _migrate_interviews_into_models(db):
    if not await _table_exists(db, "interviews"):
        return

    model_columns = await _table_columns(db, "models")
    has_legacy_link = "interview_id" in model_columns

    cur = await db.execute("SELECT * FROM interviews ORDER BY id")
    for interview in await cur.fetchall():
        inv = dict(interview)
        linked_model = None
        if has_legacy_link:
            model_cur = await db.execute(
                "SELECT * FROM models WHERE interview_id = ? ORDER BY id LIMIT 1",
                (inv["id"],),
            )
            linked_model = await model_cur.fetchone()

        parsed_name, parsed_phone, parsed_username = extract_candidate_info_from_text(inv.get("text") or "")
        position = inv.get("position") or ""
        if not position:
            text_lower = (inv.get("text") or "").lower()
            if "позиция: оператор" in text_lower:
                position = "Оператор"
            else:
                position = "Модель"

        if linked_model:
            model = dict(linked_model)
            model_code = model.get("model_code") or await _preferred_model_code(db, inv["id"])
            name = model.get("name") if model.get("name") and model.get("name") != "Модель" else parsed_name
            phone = model.get("phone") or parsed_phone
            username = model.get("username") or parsed_username
            await db.execute(
                """
                UPDATE models
                SET model_code = ?, owner_tg_id = ?, name = ?, phone = ?, username = ?,
                    application_text = ?, application_status = ?, app_status = ?,
                    partner = ?, position = ?, report_sheet_url = ?,
                    sobes_date = ?, sobes_time = ?, confirm_sent = ?, reminder_6h_sent = ?
                WHERE id = ?
                """,
                (
                    model_code,
                    inv["tg_id"],
                    name,
                    phone,
                    username,
                    inv.get("text") or "",
                    inv.get("status") or "new",
                    inv.get("app_status") or "Не подтверждена",
                    inv.get("partner") or "",
                    position,
                    inv.get("report_sheet_url") or "",
                    inv.get("sobes_date") or "",
                    inv.get("sobes_time") or "",
                    inv.get("confirm_sent") or 0,
                    inv.get("reminder_6h_sent") or 0,
                    model["id"],
                ),
            )
        else:
            model_code = await _preferred_model_code(db, inv["id"])
            await db.execute(
                """
                INSERT INTO models (
                    model_code, owner_tg_id, name, phone, username, shifts, status,
                    created_at, application_text, application_status, app_status,
                    partner, position, report_sheet_url, sobes_date, sobes_time,
                    confirm_sent, reminder_6h_sent
                ) VALUES (?, ?, ?, ?, ?, 0, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_code,
                    inv["tg_id"],
                    parsed_name,
                    parsed_phone,
                    parsed_username,
                    inv.get("created_at"),
                    inv.get("text") or "",
                    inv.get("status") or "new",
                    inv.get("app_status") or "Не подтверждена",
                    inv.get("partner") or "",
                    position,
                    inv.get("report_sheet_url") or "",
                    inv.get("sobes_date") or "",
                    inv.get("sobes_time") or "",
                    inv.get("confirm_sent") or 0,
                    inv.get("reminder_6h_sent") or 0,
                ),
            )


async def _migrate_processed_shifts(db):
    columns = await _table_columns(db, "processed_shifts")
    if not columns:
        return
    if "interview_id" not in columns:
        return

    await db.execute("""
        CREATE TABLE IF NOT EXISTS processed_shifts_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            shift_date TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 12.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(model_id, shift_date)
        )
    """)
    model_columns = await _table_columns(db, "models")
    legacy_join = "m.interview_id = ps.interview_id" if "interview_id" in model_columns else "0"
    await db.execute(f"""
        INSERT OR IGNORE INTO processed_shifts_new (id, model_id, shift_date, amount, created_at)
        SELECT ps.id, m.id, ps.shift_date, ps.amount, ps.created_at
        FROM processed_shifts ps
        JOIN models m ON ({legacy_join}) OR m.model_code = CAST(ps.interview_id AS TEXT)
    """)
    await db.execute("DROP TABLE processed_shifts")
    await db.execute("ALTER TABLE processed_shifts_new RENAME TO processed_shifts")


async def _migrate_sheet_history(db):
    columns = await _table_columns(db, "sheet_history")
    if not columns:
        return

    if "model_code" not in columns:
        await db.execute("ALTER TABLE sheet_history ADD COLUMN model_code TEXT DEFAULT ''")
        columns.add("model_code")

    if "interview_id" in columns:
        model_columns = await _table_columns(db, "models")
        if "interview_id" in model_columns:
            await db.execute("""
                UPDATE sheet_history
                SET model_code = COALESCE(
                    (
                        SELECT m.model_code
                        FROM models m
                        WHERE CAST(m.interview_id AS TEXT) = CAST(sheet_history.interview_id AS TEXT)
                        LIMIT 1
                    ),
                    NULLIF(model_code, ''),
                    interview_id,
                    ''
                )
            """)
        else:
            await db.execute("""
                UPDATE sheet_history
                SET model_code = COALESCE(NULLIF(model_code, ''), interview_id, '')
            """)

        await db.execute("""
            CREATE TABLE sheet_history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_code TEXT DEFAULT '',
                model_name TEXT DEFAULT '',
                sheet_name TEXT DEFAULT '',
                col_title TEXT DEFAULT '',
                old_value TEXT DEFAULT '',
                new_value TEXT DEFAULT '',
                user_email TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            INSERT INTO sheet_history_new
                (id, model_code, model_name, sheet_name, col_title, old_value, new_value, user_email, created_at)
            SELECT id, model_code, model_name, sheet_name, col_title, old_value, new_value, user_email, created_at
            FROM sheet_history
        """)
        await db.execute("DROP TABLE sheet_history")
        await db.execute("ALTER TABLE sheet_history_new RENAME TO sheet_history")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            role TEXT NOT NULL DEFAULT 'pending',
            balance REAL NOT NULL DEFAULT 0,
            total_earned REAL NOT NULL DEFAULT 0,
            pending REAL NOT NULL DEFAULT 0,
            wallet TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_code TEXT UNIQUE,
            owner_tg_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            username TEXT DEFAULT '',
            shifts INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active', -- active / dropped
            application_text TEXT DEFAULT '',
            application_status TEXT DEFAULT 'done',
            app_status TEXT DEFAULT 'Активна',
            partner TEXT DEFAULT '',
            position TEXT DEFAULT 'Модель',
            report_sheet_url TEXT DEFAULT '',
            sobes_date TEXT DEFAULT '',
            sobes_time TEXT DEFAULT '',
            confirm_sent INTEGER DEFAULT 0,
            reminder_6h_sent INTEGER DEFAULT 0,
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
            model_id INTEGER NOT NULL,
            shift_date TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 12.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(model_id, shift_date)
        );
        CREATE TABLE IF NOT EXISTS sheet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_code TEXT DEFAULT '',
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
        for table, column, ddl in (
            ("models", "model_code", "ALTER TABLE models ADD COLUMN model_code TEXT"),
            ("models", "phone", "ALTER TABLE models ADD COLUMN phone TEXT DEFAULT ''"),
            ("models", "username", "ALTER TABLE models ADD COLUMN username TEXT DEFAULT ''"),
            ("models", "shifts", "ALTER TABLE models ADD COLUMN shifts INTEGER NOT NULL DEFAULT 0"),
            ("models", "application_text", "ALTER TABLE models ADD COLUMN application_text TEXT DEFAULT ''"),
            ("models", "application_status", "ALTER TABLE models ADD COLUMN application_status TEXT DEFAULT 'done'"),
            ("models", "app_status", "ALTER TABLE models ADD COLUMN app_status TEXT DEFAULT 'Активна'"),
            ("models", "partner", "ALTER TABLE models ADD COLUMN partner TEXT DEFAULT ''"),
            ("models", "position", "ALTER TABLE models ADD COLUMN position TEXT DEFAULT 'Модель'"),
            ("models", "report_sheet_url", "ALTER TABLE models ADD COLUMN report_sheet_url TEXT DEFAULT ''"),
            ("models", "sobes_date", "ALTER TABLE models ADD COLUMN sobes_date TEXT DEFAULT ''"),
            ("models", "sobes_time", "ALTER TABLE models ADD COLUMN sobes_time TEXT DEFAULT ''"),
            ("models", "confirm_sent", "ALTER TABLE models ADD COLUMN confirm_sent INTEGER DEFAULT 0"),
            ("models", "reminder_6h_sent", "ALTER TABLE models ADD COLUMN reminder_6h_sent INTEGER DEFAULT 0"),
            ("users", "onboarded", "ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 1"),
            ("users", "pending", "ALTER TABLE users ADD COLUMN pending REAL NOT NULL DEFAULT 0"),
            ("users", "total_earned", "ALTER TABLE users ADD COLUMN total_earned REAL NOT NULL DEFAULT 0"),
            ("users", "team_id", "ALTER TABLE users ADD COLUMN team_id INTEGER"),
            ("users", "agent_code", "ALTER TABLE users ADD COLUMN agent_code INTEGER"),
            ("partners", "chat_id", "ALTER TABLE partners ADD COLUMN chat_id TEXT DEFAULT ''"),
            ("partners", "sheet_url", "ALTER TABLE partners ADD COLUMN sheet_url TEXT DEFAULT ''"),
            ("partners", "topic_applications", "ALTER TABLE partners ADD COLUMN topic_applications INTEGER DEFAULT NULL"),
            ("partners", "topic_confirmations", "ALTER TABLE partners ADD COLUMN topic_confirmations INTEGER DEFAULT NULL"),
            ("partners", "topic_sobes", "ALTER TABLE partners ADD COLUMN topic_sobes INTEGER DEFAULT NULL"),
            ("partners", "topic_registration", "ALTER TABLE partners ADD COLUMN topic_registration INTEGER DEFAULT NULL"),
            ("partners", "topic_shift1", "ALTER TABLE partners ADD COLUMN topic_shift1 INTEGER DEFAULT NULL"),
            ("partners", "topic_shift2", "ALTER TABLE partners ADD COLUMN topic_shift2 INTEGER DEFAULT NULL"),
            ("partners", "topic_cancelled", "ALTER TABLE partners ADD COLUMN topic_cancelled INTEGER DEFAULT NULL"),
        ):
            await _add_column_if_missing(db, table, column, ddl)

        if await _table_exists(db, "interviews"):
            for column, ddl in (
                ("partner", "ALTER TABLE interviews ADD COLUMN partner TEXT DEFAULT ''"),
                ("app_status", "ALTER TABLE interviews ADD COLUMN app_status TEXT DEFAULT 'Не подтверждена'"),
                ("position", "ALTER TABLE interviews ADD COLUMN position TEXT DEFAULT ''"),
                ("report_sheet_url", "ALTER TABLE interviews ADD COLUMN report_sheet_url TEXT DEFAULT ''"),
                ("sobes_date", "ALTER TABLE interviews ADD COLUMN sobes_date TEXT DEFAULT ''"),
                ("sobes_time", "ALTER TABLE interviews ADD COLUMN sobes_time TEXT DEFAULT ''"),
                ("confirm_sent", "ALTER TABLE interviews ADD COLUMN confirm_sent INTEGER DEFAULT 0"),
                ("reminder_6h_sent", "ALTER TABLE interviews ADD COLUMN reminder_6h_sent INTEGER DEFAULT 0"),
            ):
                await _add_column_if_missing(db, "interviews", column, ddl)

        user_columns = {
            row[1] for row in await (await db.execute("PRAGMA table_info(users)")).fetchall()
        }
        if "team" in user_columns or "stars" in user_columns:
            await db.execute("ALTER TABLE users RENAME TO users_legacy")
            await db.execute("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    role TEXT NOT NULL DEFAULT 'pending',
                    balance REAL NOT NULL DEFAULT 0,
                    total_earned REAL NOT NULL DEFAULT 0,
                    pending REAL NOT NULL DEFAULT 0,
                    wallet TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    onboarded INTEGER NOT NULL DEFAULT 1,
                    team_id INTEGER,
                    agent_code INTEGER
                )
            """)
            await db.execute("""
                INSERT INTO users
                    (id, tg_id, username, full_name, role, balance, total_earned,
                     pending, wallet, created_at, onboarded, team_id, agent_code)
                SELECT id, tg_id, username, full_name, role, balance, total_earned,
                       pending, wallet, created_at, onboarded, team_id, agent_code
                FROM users_legacy
            """)
            await db.execute("DROP TABLE users_legacy")
        await db.execute("UPDATE users SET agent_code = 1000 + id WHERE agent_code IS NULL")
        await _ensure_model_codes(db)
        await _migrate_interviews_into_models(db)
        await _migrate_processed_shifts(db)
        await _migrate_sheet_history(db)

        model_columns = await _table_columns(db, "models")
        if "interview_id" in model_columns:
            try:
                await db.execute("ALTER TABLE models DROP COLUMN interview_id")
            except aiosqlite.OperationalError:
                pass
        await db.execute("DROP TABLE IF EXISTS interviews")
        await _ensure_model_codes(db)
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_models_model_code ON models(model_code)")

        await db.execute("UPDATE models SET app_status = 'Активна' WHERE app_status IS NULL OR app_status = ''")
        await db.execute("UPDATE models SET application_status = 'done' WHERE application_status IS NULL OR application_status = ''")
        # Для старых баз переносим накопленный текущий баланс в историю заработка.
        await db.execute(
            "UPDATE users SET total_earned = balance "
            "WHERE total_earned = 0 AND balance != 0"
        )
        await db.execute(
            "UPDATE models SET position='Модель' "
            "WHERE (position IS NULL OR position='') AND application_text LIKE 'Позиция: Модель%'")
        await db.execute(
            "UPDATE models SET position='Оператор' "
            "WHERE (position IS NULL OR position='') AND application_text LIKE 'Позиция: Оператор%'")
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
        conditions = ["m.owner_tg_id = ?"]
        params = [owner_tg_id]
        registered = (
            "(LOWER(COALESCE(m.app_status, '')) LIKE '%регистрац%' "
            "OR LOWER(COALESCE(m.app_status, '')) LIKE '%актив%')"
        )
        not_cancelled = (
            "LOWER(COALESCE(m.app_status, '')) NOT LIKE '%слив%' "
            "AND LOWER(COALESCE(m.app_status, '')) NOT LIKE '%отмен%' "
            "AND LOWER(COALESCE(m.app_status, '')) NOT LIKE '%отклон%' "
            "AND LOWER(COALESCE(m.app_status, '')) NOT LIKE '%отказ%'"
        )
        if status == "active":
            conditions.extend(["m.status = 'active'", registered, not_cancelled])
        elif status == "dropped":
            conditions.extend(["m.status = 'dropped'", "m.shifts >= 1"])
        query = f"SELECT m.* FROM models m WHERE {' AND '.join(conditions)} ORDER BY m.id"
        cur = await db.execute(query, params)
        return await cur.fetchall()


async def add_model(
    owner_tg_id: int,
    name: str,
    phone: str = "",
    username: str = "",
    app_status: str = "Активна",
    position: str = "Модель",
    application_text: str = "",
    partner: str = "",
    sobes_date: str = "",
    sobes_time: str = "",
    report_sheet_url: str = "",
    application_status: str = "done",
):
    async with aiosqlite.connect(DB_PATH) as db:
        while True:
            model_code = await _generate_model_code(db)
            try:
                await db.execute(
                    """
                    INSERT INTO models (
                        model_code, owner_tg_id, name, phone, username,
                        app_status, position, application_text, partner,
                        sobes_date, sobes_time, report_sheet_url, application_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        model_code,
                        owner_tg_id,
                        name or "Модель",
                        phone or "",
                        (username or "").lstrip("@"),
                        app_status,
                        position or "Модель",
                        application_text or "",
                        partner or "",
                        sobes_date or "",
                        sobes_time or "",
                        report_sheet_url or "",
                        application_status or "done",
                    ),
                )
                await db.commit()
                return model_code
            except aiosqlite.IntegrityError:
                await db.rollback()


async def add_model_application(
    owner_tg_id: int,
    name: str,
    phone: str,
    username: str,
    text: str,
    partner: str = "",
    position: str = "",
    sobes_date: str = "",
    sobes_time: str = "",
) -> str:
    return await add_model(
        owner_tg_id=owner_tg_id,
        name=name or "Модель",
        phone=phone,
        username=username,
        app_status="Не подтверждена",
        position=position or "Модель",
        application_text=text,
        partner=partner,
        sobes_date=sobes_date,
        sobes_time=sobes_time,
        application_status="new",
    )


async def get_model_by_id(model_id: int | str, owner_tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM models WHERE model_code = ? AND owner_tg_id = ?", (str(model_id), owner_tg_id)
        )
        return await cur.fetchone()


async def get_model_by_code(model_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models WHERE model_code = ?", (str(model_code),))
        return await cur.fetchone()


async def update_model_name(model_code: str, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models WHERE model_code = ?", (str(model_code),))
        model = await cur.fetchone()
        if not model:
            return None
        application_text = re.sub(
            r"(^\s*1\)\s*(?:имя|фио)\s*:\s*).*$",
            rf"\g<1>{name}",
            model["application_text"] or "",
            count=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        await db.execute(
            "UPDATE models SET name = ?, application_text = ? WHERE id = ?",
            (name, application_text, model["id"]),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM models WHERE id = ?", (model["id"],))
        return await cur.fetchone()


async def inc_shift(model_id: int | str, owner_tg_id: int, delta: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET shifts = MAX(shifts + ?, 0) WHERE model_code = ? AND owner_tg_id = ?",
            (delta, str(model_id), owner_tg_id),
        )
        await db.commit()


async def set_model_status(model_id: int | str, owner_tg_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET status = ? WHERE model_code = ? AND owner_tg_id = ?",
            (status, str(model_id), owner_tg_id),
        )
        await db.commit()


async def get_occupied_times(chosen_date: str, partner: str = "") -> set:
    """
    Возвращает множество занятых слотов времени (например {"17:00"}) на дату chosen_date.
    Слот освобождается, если у модели статус Слив/Отмена/Отклонена/Не принято.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT sobes_time, app_status, application_text FROM models "
            "WHERE (partner = ? OR ? = '') "
            "AND (sobes_date = ? OR application_text LIKE ?) "
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
            m = re.search(r"в\s+(\d{1,2}:\d{2})\s+МСК", r_dict.get("application_text") or "")
            if m:
                t = m.group(1)
                if len(t) == 4:
                    t = "0" + t
        if t:
            occupied.add(t)
    return occupied


async def update_model_app_status(model_code: str, app_status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "UPDATE models SET app_status = ? WHERE model_code = ?", (app_status, str(model_code))
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM models WHERE model_code = ?", (str(model_code),))
        return await cur.fetchone()


async def update_model_report_sheet(model_code: int | str, report_url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET report_sheet_url = ? WHERE model_code = ?",
            (report_url, str(model_code)),
        )
        await db.commit()


async def get_models_with_report_sheets():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM models WHERE report_sheet_url IS NOT NULL AND report_sheet_url != '' GROUP BY report_sheet_url"
        )
        return await cur.fetchall()


async def register_shift_payout(model_id: int, shift_date: str, amount: float = 12.0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models WHERE id = ?", (model_id,))
        model = await cur.fetchone()
        if not model:
            return None

        report_url = model["report_sheet_url"]
        if report_url:
            # Проверяем, не выписывался ли уже бонус за эту смену по этому же отчётнику
            check_cur = await db.execute("""
                SELECT ps.id FROM processed_shifts ps
                JOIN models m ON ps.model_id = m.id
                WHERE (m.report_sheet_url = ? OR ps.model_id = ?) AND ps.shift_date = ?
            """, (report_url, model_id, shift_date))
            if await check_cur.fetchone():
                return None

        try:
            await db.execute(
                "INSERT INTO processed_shifts (model_id, shift_date, amount) VALUES (?,?,?)",
                (model_id, shift_date, amount)
            )
        except aiosqlite.IntegrityError:
            return None

        agent_tg_id = model["owner_tg_id"]
        await db.execute(
            "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE tg_id = ?",
            (amount, amount, agent_tg_id)
        )
        await db.commit()

        cur_u = await db.execute("SELECT balance FROM users WHERE tg_id = ?", (agent_tg_id,))
        u_row = await cur_u.fetchone()
        new_balance = u_row["balance"] if u_row else 0.0

        return {
            "model_id": model_id,
            "model_code": model["model_code"],
            "agent_tg_id": agent_tg_id,
            "shift_date": shift_date,
            "amount": amount,
            "new_balance": new_balance,
            "partner": model["partner"] or "-",
            "text": model["application_text"] or ""
        }


async def get_model_processed_shifts_count(model_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM processed_shifts WHERE model_id = ?", (model_id,))
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
            "SELECT m.*, m.owner_tg_id AS tg_id, m.application_text AS text, "
            "u.username, u.full_name, u.id AS owner_no, u.agent_code AS owner_agent_code "
            "FROM models m "
            "LEFT JOIN users u ON u.tg_id = m.owner_tg_id "
            "WHERE m.application_status = 'new' ORDER BY m.created_at"
        )
        return await cur.fetchall()


async def close_interview(model_code: int | str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET application_status = 'done' WHERE model_code = ?",
            (str(model_code),),
        )
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
            """
            SELECT
                SUM(CASE WHEN status = 'active'
                    AND (LOWER(COALESCE(app_status, '')) LIKE '%регистрац%'
                         OR LOWER(COALESCE(app_status, '')) LIKE '%актив%')
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%слив%'
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%отмен%'
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%отклон%'
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%отказ%'
                    THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'active'
                    AND (LOWER(COALESCE(app_status, '')) LIKE '%регистрац%'
                         OR LOWER(COALESCE(app_status, '')) LIKE '%актив%')
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%слив%'
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%отмен%'
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%отклон%'
                    AND LOWER(COALESCE(app_status, '')) NOT LIKE '%отказ%'
                    THEN shifts ELSE 0 END),
                SUM(CASE WHEN status = 'dropped' AND shifts >= 1 THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'dropped' AND shifts >= 1 THEN shifts ELSE 0 END)
            FROM models
            WHERE owner_tg_id = ?
            """,
            (owner_tg_id,),
        )
        row = await cur.fetchone()
        return {
            "active": (row[0] or 0, row[1] or 0),
            "dropped": (row[2] or 0, row[3] or 0),
        }


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
            "WHERE u.team_id = ? AND ("
            "  (m.status = 'active' "
            "   AND (LOWER(COALESCE(m.app_status, '')) LIKE '%регистрац%' "
            "        OR LOWER(COALESCE(m.app_status, '')) LIKE '%актив%') "
            "   AND LOWER(COALESCE(m.app_status, '')) NOT LIKE '%слив%' "
            "   AND LOWER(COALESCE(m.app_status, '')) NOT LIKE '%отмен%' "
            "   AND LOWER(COALESCE(m.app_status, '')) NOT LIKE '%отклон%' "
            "   AND LOWER(COALESCE(m.app_status, '')) NOT LIKE '%отказ%') "
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


async def set_app_status(model_code: int | str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET app_status = ? WHERE model_code = ?", (status, str(model_code))
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
    """Разбивка моделей по статусам собеседования за период."""
    cond = "m.created_at >= ? AND m.created_at < ?"
    params: list = [dfrom, dto]
    join = ""
    if team_id is not None:
        join = "JOIN users u ON u.tg_id = m.owner_tg_id"
        cond += " AND u.team_id = ?"
        params.append(team_id)
    if tg_id is not None:
        cond += " AND m.owner_tg_id = ?"
        params.append(tg_id)
    if position is not None:
        cond += " AND m.position = ?"
        params.append(position)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"SELECT COALESCE(m.app_status, 'Не подтверждена'), COUNT(*) "
            f"FROM models m {join} WHERE {cond} "
            f"GROUP BY COALESCE(m.app_status, 'Не подтверждена')",
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
    """Топ агентов по моделям за период + регистрации. solo_only - только без команды"""
    solo_cond = "AND u.team_id IS NULL AND u.role = 'agent' " if solo_only else ""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT u.id AS agent_no, u.tg_id, u.full_name, u.username, "
            " COUNT(m.id) AS records, "
            " SUM(CASE WHEN m.app_status IN (?, ?) THEN 1 ELSE 0 END) AS regs "
            "FROM models m JOIN users u ON u.tg_id = m.owner_tg_id "
            "WHERE m.created_at >= ? AND m.created_at < ? "
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
            " COUNT(m.id) AS records, "
            " SUM(CASE WHEN m.app_status IN (?, ?) THEN 1 ELSE 0 END) AS regs "
            "FROM models m JOIN users u ON u.tg_id = m.owner_tg_id "
            "LEFT JOIN teams t ON t.id = u.team_id "
            "WHERE m.created_at >= ? AND m.created_at < ? "
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
            if str(m["model_code"]) == digits or (phone_digits and digits in phone_digits):
                result.append(m)
        else:
            name = (m["name"] or "").casefold()
            uname = (m["username"] or "").casefold()
            if q.casefold() in name or q.casefold() == uname:
                result.append(m)
        if len(result) >= limit:
            break
    return result


async def format_anketa_topic_message(model: dict, header_title: str) -> str:
    inv = dict(model)
    agent_id = inv.get("owner_tg_id") or inv.get("tg_id")
    agent_user = await get_user(agent_id)
    agent_code = get_agent_code(agent_user) or agent_id
    
    raw_text = inv.get("application_text") or inv.get("text") or ""
    
    msg = (
        f"{header_title}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🟢 Агент ID: <code>{agent_code}</code>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{raw_text}"
    )
    return msg


from datetime import datetime

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


async def mark_confirm_sent(model_code: int | str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET confirm_sent = 1 WHERE model_code = ?", (str(model_code),)
        )
        await db.commit()


async def get_unnotified_accepted_interviews():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM models "
            "WHERE (confirm_sent IS NULL OR confirm_sent = 0)"
        )
        rows = await cur.fetchall()
        return [row for row in rows if is_accepted_app_status(row["app_status"])]


async def get_today_interviews_grouped_by_agent(today_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM models "
            "WHERE (sobes_date = ? OR application_text LIKE ?) "
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
        agent_id = r_dict["owner_tg_id"]
        if agent_id not in by_agent:
            by_agent[agent_id] = []
        by_agent[agent_id].append(r_dict)
    return by_agent


# ---------- история изменений таблицы ----------

async def add_sheet_history_entry(
    model_code: str = "",
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
            INSERT INTO sheet_history (model_code, model_name, sheet_name, col_title, old_value, new_value, user_email)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (model_code, model_name, sheet_name, col_title, old_value, new_value, user_email)
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

async def set_onboarded(tg_id: int, value: int):
    """0 - агент ещё проходит вводные шаги (меню скрыто), 1 - прошёл"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET onboarded = ? WHERE tg_id = ?", (value, tg_id))
        await db.commit()


async def get_unanswered_accepted_interviews():
    """Принятые партнёром модели, по которым агент ещё не нажал «придёт/не придёт»
    и 6-часовое напоминание ещё не отправлялось"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM models "
            "WHERE (reminder_6h_sent IS NULL OR reminder_6h_sent = 0)"
        )
        rows = await cur.fetchall()
        return [row for row in rows if is_accepted_app_status(row["app_status"])]


async def mark_reminder_6h_sent(model_code: int | str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE models SET reminder_6h_sent = 1 WHERE model_code = ?", (str(model_code),)
        )
        await db.commit()


async def get_all_active_tg_ids():
    """Все пользователи с доступом (для рассылки)"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT tg_id FROM users WHERE role NOT IN ('pending', 'banned')"
        )
        return [r[0] for r in await cur.fetchall()]
