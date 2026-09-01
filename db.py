import calendar
import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta
from hashlib import sha256


DB_FILE = "finance.db"
logger = logging.getLogger(__name__)


class Database:
	def __init__(self, db_path: str = DB_FILE) -> None:
		self.db_path = db_path
		self.current_user_id: int | None = None
		self._lock = threading.RLock()
		self._conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
		self._conn.row_factory = sqlite3.Row
		try:
			self._conn.execute("PRAGMA busy_timeout = 5000")
			self._conn.execute("PRAGMA journal_mode=WAL")
		except Exception:
			logger.debug("Не вдалося ініціалізувати PRAGMA налаштування", exc_info=True)
		self._ensure_db()

	def _connect(self) -> sqlite3.Connection:
		"""
		Повертає спільне з'єднання з БД.
		Більше не створює нове підключення на кожен запит.
		"""
		return self._conn

	def close(self) -> None:
		with self._lock:
			try:
				self._conn.close()
			except Exception:
				logger.debug("Не вдалося коректно закрити з'єднання БД", exc_info=True)

	def __del__(self) -> None:
		try:
			self.close()
		except Exception:
			logger.debug("Помилка під час __del__ для Database", exc_info=True)

	def _ensure_db(self) -> None:
		with self._lock:
			conn = self._connect()
			cur = conn.cursor()

			# Видаляємо застарілу таблицю періодичних транзакцій, якщо вона ще існує
			try:
				cur.execute("DROP TABLE IF EXISTS recurring_transactions")
			except Exception:
				logger.debug("Не вдалося видалити таблицю recurring_transactions", exc_info=True)
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS categories (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					name TEXT UNIQUE NOT NULL
				)
				"""
			)
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS transactions (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					date TEXT NOT NULL,
					type TEXT CHECK(type IN ('income','expense')) NOT NULL,
					amount REAL NOT NULL,
					category_id INTEGER,
					note TEXT,
					FOREIGN KEY(category_id) REFERENCES categories(id)
				)
				"""
			)
			# Бюджети на категорію/місяць
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS budgets (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					category_id INTEGER,
					month INTEGER NOT NULL,
					year INTEGER NOT NULL,
					amount REAL NOT NULL,
					FOREIGN KEY(category_id) REFERENCES categories(id),
					UNIQUE(category_id, month, year)
				)
				"""
			)
			# Періодичні транзакції
			# Налаштування
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS settings (
					key TEXT PRIMARY KEY,
					value TEXT NOT NULL
				)
				"""
			)
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS users (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					username TEXT UNIQUE NOT NULL,
					password_hash TEXT NOT NULL
				)
				"""
			)
			# Нагадування
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS reminders (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					title TEXT NOT NULL,
					message TEXT,
					day_of_month INTEGER NOT NULL,
					category_id INTEGER,
					active INTEGER DEFAULT 1,
					FOREIGN KEY(category_id) REFERENCES categories(id)
				)
				"""
			)
			# Банківські рахунки / депозити
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS bank_accounts (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					name TEXT NOT NULL,
					balance REAL NOT NULL,
					interest_rate REAL NOT NULL,
					created_at TEXT NOT NULL,
					last_interest_date TEXT
				)
				"""
			)

			# Міграція: додаємо last_interest_date для існуючих БД
			try:
				cols = [r[1] for r in cur.execute("PRAGMA table_info(bank_accounts)").fetchall()]
				if "last_interest_date" not in cols:
					cur.execute("ALTER TABLE bank_accounts ADD COLUMN last_interest_date TEXT")
			except Exception:
				logger.debug("Не вдалося виконати міграцію bank_accounts.last_interest_date", exc_info=True)

			# Індекси для частих запитів (фільтри по даті/категорії/типу)
			cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_type_date ON transactions(type, date)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_budgets_period ON budgets(year, month)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_budgets_category_period ON budgets(category_id, year, month)")

			conn.commit()
			self._ensure_user_scoped_schema(conn)
			cur.execute("SELECT COUNT(*) as c FROM categories WHERE user_id = 0")
			if cur.fetchone()[0] == 0:
				default_cats = [
					("Їжа", 0),
					("Транспорт", 0),
					("Покупки", 0),
					("Комунальні послуги", 0),
					("Здоров'я", 0),
					("Розваги", 0),
					("Інше", 0),
				]
				cur.executemany("INSERT INTO categories(name, user_id) VALUES (?,?)", default_cats)
				conn.commit()
			else:
				# Attempt to localize previously seeded English defaults to Ukrainian
				self._localize_existing_defaults(conn)

			# Міграція безпеки: якщо пароль збережено plain text — хешуємо
			try:
				pw_row = cur.execute("SELECT value FROM settings WHERE user_id = 0 AND key = 'password'").fetchone()
				if pw_row:
					val = pw_row[0] or ""
					if val and not val.startswith("sha256$"):
						hashed = "sha256$" + sha256(val.encode("utf-8")).hexdigest()
						cur.execute("UPDATE settings SET value = ? WHERE user_id = 0 AND key = 'password'", (hashed,))
						conn.commit()
			except Exception:
				logger.exception("Не вдалося виконати міграцію plain text пароля")

	def set_current_user(self, user_id: int) -> None:
		self.current_user_id = int(user_id)

	def _uid(self) -> int:
		if self.current_user_id is None:
			raise RuntimeError("Поточний користувач не встановлений")
		return self.current_user_id

	def _ensure_user_scoped_schema(self, conn: sqlite3.Connection) -> None:
		"""Додає user_id до таблиць і індекси для ізоляції даних по користувачах."""
		cur = conn.cursor()
		tables_to_scope = {
			"categories": "INTEGER NOT NULL DEFAULT 0",
			"transactions": "INTEGER NOT NULL DEFAULT 0",
			"budgets": "INTEGER NOT NULL DEFAULT 0",
			"settings": "INTEGER NOT NULL DEFAULT 0",
			"reminders": "INTEGER NOT NULL DEFAULT 0",
			"bank_accounts": "INTEGER NOT NULL DEFAULT 0",
		}
		for table, col_def in tables_to_scope.items():
			try:
				cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
				if "user_id" not in cols:
					cur.execute(f"ALTER TABLE {table} ADD COLUMN user_id {col_def}")
			except Exception:
				logger.debug("Не вдалося додати user_id до %s", table, exc_info=True)

		# Перебудова таблиць з глобальними унікальними ключами в user-scoped формат.
		# categories: UNIQUE(name) -> UNIQUE(user_id, name)
		cur.executescript(
			"""
			CREATE TABLE IF NOT EXISTS categories_new (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				name TEXT NOT NULL,
				user_id INTEGER NOT NULL DEFAULT 0,
				UNIQUE(user_id, name)
			);
			INSERT OR IGNORE INTO categories_new(id, name, user_id)
			SELECT id, name, COALESCE(user_id, 0) FROM categories;
			DROP TABLE categories;
			ALTER TABLE categories_new RENAME TO categories;
			"""
		)

		# settings: PRIMARY KEY(key) -> PRIMARY KEY(user_id, key)
		cur.executescript(
			"""
			CREATE TABLE IF NOT EXISTS settings_new (
				user_id INTEGER NOT NULL DEFAULT 0,
				key TEXT NOT NULL,
				value TEXT NOT NULL,
				PRIMARY KEY(user_id, key)
			);
			INSERT OR IGNORE INTO settings_new(user_id, key, value)
			SELECT COALESCE(user_id, 0), key, value FROM settings;
			DROP TABLE settings;
			ALTER TABLE settings_new RENAME TO settings;
			"""
		)

		# budgets: UNIQUE(category_id, month, year) -> UNIQUE(user_id, category_id, month, year)
		cur.executescript(
			"""
			CREATE TABLE IF NOT EXISTS budgets_new (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				category_id INTEGER,
				month INTEGER NOT NULL,
				year INTEGER NOT NULL,
				amount REAL NOT NULL,
				user_id INTEGER NOT NULL DEFAULT 0,
				FOREIGN KEY(category_id) REFERENCES categories(id),
				UNIQUE(user_id, category_id, month, year)
			);
			INSERT OR IGNORE INTO budgets_new(id, category_id, month, year, amount, user_id)
			SELECT id, category_id, month, year, amount, COALESCE(user_id, 0) FROM budgets;
			DROP TABLE budgets;
			ALTER TABLE budgets_new RENAME TO budgets;
			"""
		)

		# Унікальність тепер в межах користувача
		cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_user_name ON categories(user_id, name)")
		cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_settings_user_key ON settings(user_id, key)")
		cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_budgets_user_cat_period ON budgets(user_id, category_id, month, year)")
		cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date)")
		cur.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user_active ON reminders(user_id, active)")
		cur.execute("CREATE INDEX IF NOT EXISTS idx_bank_accounts_user_id ON bank_accounts(user_id)")
		conn.commit()

	def _localize_existing_defaults(self, conn: sqlite3.Connection) -> None:
		mapping = {
			"Food": "Їжа",
			"Transport": "Транспорт",
			"Shopping": "Покупки",
			"Utilities": "Комунальні послуги",
			"Health": "Здоров'я",
			"Entertainment": "Розваги",
			"Other": "Інше",
		}
		cur = conn.cursor()
		for en, ua in mapping.items():
			# If English name exists and Ukrainian does not, rename
			row_en = cur.execute("SELECT id FROM categories WHERE name = ?", (en,)).fetchone()
			if not row_en:
				continue
			row_ua = cur.execute("SELECT id FROM categories WHERE name = ?", (ua,)).fetchone()
			if row_ua:
				continue
			cur.execute("UPDATE categories SET name = ? WHERE id = ?", (ua, row_en[0]))
		conn.commit()

	def list_categories(self) -> list[sqlite3.Row]:
		uid = self._uid()
		with self._connect() as conn:
			return conn.execute("SELECT id, name FROM categories WHERE user_id = ? ORDER BY name", (uid,)).fetchall()

	def add_category(self, name: str) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute("INSERT OR IGNORE INTO categories(name, user_id) VALUES (?,?)", (name, uid))
			conn.commit()

	def delete_category(self, category_id: int) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (category_id, uid))
			conn.commit()

	def add_transaction(self, date_str: str, tx_type: str, amount: float, category_id: int | None, note: str) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute(
				"INSERT INTO transactions(date, type, amount, category_id, note, user_id) VALUES (?,?,?,?,?,?)",
				(date_str, tx_type, amount, category_id, note, uid),
			)
			conn.commit()

	def update_transaction(self, tx_id: int, date_str: str, tx_type: str, amount: float, category_id: int | None, note: str) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute(
				"UPDATE transactions SET date=?, type=?, amount=?, category_id=?, note=? WHERE id=? AND user_id=?",
				(date_str, tx_type, amount, category_id, note, tx_id, uid),
			)
			conn.commit()

	def delete_transaction(self, tx_id: int) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (tx_id, uid))
			conn.commit()

	def list_transactions(self, month: int | None, year: int | None) -> list[sqlite3.Row]:
		query = [
			"SELECT t.id, t.date, t.type, t.amount, t.category_id, c.name AS category, t.note",
			"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id",
		]
		params: list = []
		clauses: list[str] = ["t.user_id = ?"]
		params.append(self._uid())
		if year:
			clauses.append("strftime('%Y', t.date) = ?")
			params.append(f"{year:04d}")
		if month:
			clauses.append("strftime('%m', t.date) = ?")
			params.append(f"{month:02d}")
		if clauses:
			query.append("WHERE " + " AND ".join(clauses))
		query.append("ORDER BY t.date DESC, t.id DESC")
		sql = "\n".join(query)
		with self._connect() as conn:
			return conn.execute(sql, params).fetchall()

	def expenses_by_category(self, month: int | None, year: int | None) -> list[tuple[str, float]]:
		query = [
			"SELECT COALESCE(c.name, 'Uncategorized') as category, SUM(t.amount) as total",
			"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id",
			"WHERE t.type = 'expense' AND t.user_id = ?",
		]
		params: list = [self._uid()]
		if year:
			query.append("AND strftime('%Y', t.date) = ?")
			params.append(f"{year:04d}")
		if month:
			query.append("AND strftime('%m', t.date) = ?")
			params.append(f"{month:02d}")
		query.append("GROUP BY category ORDER BY total DESC")
		sql = "\n".join(query)
		with self._connect() as conn:
			rows = conn.execute(sql, params).fetchall()
		return [(r[0], float(r[1]) if r[1] is not None else 0.0) for r in rows]

	def summary(self, month: int | None, year: int | None) -> dict[str, float]:
		query = [
			"SELECT",
			"SUM(CASE WHEN type='income' THEN amount ELSE 0 END) as income,",
			"SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) as expense",
			"FROM transactions",
		]
		params: list = []
		clauses: list[str] = ["user_id = ?"]
		params.append(self._uid())
		if year:
			clauses.append("strftime('%Y', date) = ?")
			params.append(f"{year:04d}")
		if month:
			clauses.append("strftime('%m', date) = ?")
			params.append(f"{month:02d}")
		if clauses:
			query.append("WHERE " + " AND ".join(clauses))
		sql = "\n".join(query)
		with self._connect() as conn:
			row = conn.execute(sql, params).fetchone()
			income = float(row[0] or 0.0)
			expense = float(row[1] or 0.0)
			return {"income": income, "expense": expense, "balance": income - expense}

	# Бюджети
	def set_budget(self, category_id: int | None, month: int, year: int, amount: float) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute(
				"INSERT OR REPLACE INTO budgets(user_id, category_id, month, year, amount) VALUES (?,?,?,?,?)",
				(uid, category_id, month, year, amount)
			)
			conn.commit()

	def get_budget(self, category_id: int | None, month: int, year: int) -> float | None:
		uid = self._uid()
		with self._connect() as conn:
			row = conn.execute(
				"SELECT amount FROM budgets WHERE user_id = ? AND category_id IS ? AND month = ? AND year = ?",
				(uid, category_id, month, year)
			).fetchone()
			return float(row[0]) if row else None

	def get_all_budgets(self, month: int | None, year: int | None) -> list[sqlite3.Row]:
		query = [
			"SELECT b.id, b.category_id, c.name AS category, b.month, b.year, b.amount",
			"FROM budgets b LEFT JOIN categories c ON c.id = b.category_id"
		]
		params: list = []
		clauses: list[str] = ["b.user_id = ?"]
		params.append(self._uid())
		if year:
			clauses.append("b.year = ?")
			params.append(year)
		if month:
			clauses.append("b.month = ?")
			params.append(month)
		if clauses:
			query.append("WHERE " + " AND ".join(clauses))
		query.append("ORDER BY b.year DESC, b.month DESC, c.name")
		sql = "\n".join(query)
		with self._connect() as conn:
			return conn.execute(sql, params).fetchall()

	def delete_budget(self, budget_id: int) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute("DELETE FROM budgets WHERE id = ? AND user_id = ?", (budget_id, uid))
			conn.commit()

	def get_budget_progress(self, category_id: int | None, month: int, year: int) -> dict[str, float]:
		"""Повертає бюджет, витрати та відсоток використання"""
		budget = self.get_budget(category_id, month, year)
		if budget is None:
			return {"budget": 0.0, "spent": 0.0, "remaining": 0.0, "percent": 0.0}
		
		# Отримуємо витрати за категорію
		query = [
			"SELECT SUM(amount) as total",
			"FROM transactions",
			"WHERE user_id = ? AND type = 'expense' AND strftime('%Y', date) = ? AND strftime('%m', date) = ?"
		]
		params = [self._uid(), f"{year:04d}", f"{month:02d}"]
		if category_id is not None:
			query.append("AND category_id = ?")
			params.append(category_id)
		else:
			query.append("AND category_id IS NULL")
		
		sql = "\n".join(query)
		with self._connect() as conn:
			row = conn.execute(sql, params).fetchone()
			spent = float(row[0] or 0.0) if row[0] else 0.0
		
		remaining = budget - spent
		percent = (spent / budget * 100) if budget > 0 else 0.0
		return {"budget": budget, "spent": spent, "remaining": remaining, "percent": percent}

	# Банківські рахунки / депозити

	def add_bank_account(self, name: str, balance: float, interest_rate: float) -> None:
		created_at = datetime.now().strftime("%Y-%m-%d")
		uid = self._uid()
		with self._connect() as conn:
			conn.execute(
				"INSERT INTO bank_accounts(name, balance, interest_rate, created_at, last_interest_date, user_id) VALUES (?,?,?,?,?,?)",
				(name, balance, interest_rate, created_at, created_at, uid),
			)
			conn.commit()

	def list_bank_accounts(self) -> list[sqlite3.Row]:
		uid = self._uid()
		with self._connect() as conn:
			return conn.execute(
				"SELECT id, name, balance, interest_rate, created_at, last_interest_date FROM bank_accounts WHERE user_id = ? ORDER BY id",
				(uid,),
			).fetchall()

	def delete_bank_account(self, account_id: int) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute("DELETE FROM bank_accounts WHERE id = ? AND user_id = ?", (account_id, uid))
			conn.commit()

	def accrue_monthly_interest(self, account_id: int | None = None) -> int:
		"""
		Нараховує щомісячні відсотки для всіх рахунків або одного (якщо задано account_id).
		Формула: balance += balance * interest_rate / 100 / 12
		Повертає кількість оновлених рахунків.
		"""
		updated = 0
		uid = self._uid()
		with self._connect() as conn:
			if account_id is None:
				rows = conn.execute(
					"SELECT id, balance, interest_rate FROM bank_accounts WHERE user_id = ?",
					(uid,),
				).fetchall()
			else:
				rows = conn.execute(
					"SELECT id, balance, interest_rate FROM bank_accounts WHERE id = ? AND user_id = ?",
					(account_id, uid),
				).fetchall()

			for row in rows:
				acc_id = int(row["id"])
				balance = float(row["balance"] or 0.0)
				rate = float(row["interest_rate"] or 0.0)
				if rate == 0 or balance == 0:
					continue
				interest = balance * rate / 100.0 / 12.0
				new_balance = balance + interest
				conn.execute(
					"UPDATE bank_accounts SET balance = ? WHERE id = ? AND user_id = ?",
					(new_balance, acc_id, uid),
				)
				updated += 1

			conn.commit()

		return updated

	def process_monthly_interest(self) -> int:
		"""
		Нараховує складні відсотки для всіх рахунків, якщо пройшов щонайменше місяць
		від `last_interest_date`. Оновлює `last_interest_date` відповідно.
		Повертає кількість рахунків, для яких було застосовано нарахування.
		"""

		def parse(d: str | None) -> date | None:
			if not d:
				return None
			try:
				return datetime.strptime(d, "%Y-%m-%d").date()
			except Exception:
				return None

		def add_months(d: date, n: int) -> date:
			y = d.year + (d.month - 1 + n) // 12
			m = (d.month - 1 + n) % 12 + 1
			# clamp day

			last_day = calendar.monthrange(y, m)[1]
			day = min(d.day, last_day)
			return date(y, m, day)

		applied = 0
		today = date.today()

		with self._connect() as conn:
			rows = conn.execute(
				"SELECT id, balance, interest_rate, created_at, last_interest_date FROM bank_accounts WHERE user_id = ?",
				(self._uid(),),
			).fetchall()

			for row in rows:
				acc_id = int(row["id"])
				balance = float(row["balance"] or 0.0)
				rate = float(row["interest_rate"] or 0.0)
				last_date = parse(row["last_interest_date"]) or parse(row["created_at"])
				if last_date is None:
					# якщо зовсім зламана дата — ініціалізуємо, але без нарахування
					conn.execute(
						"UPDATE bank_accounts SET last_interest_date = ? WHERE id = ? AND user_id = ?",
						(today.strftime("%Y-%m-%d"), acc_id, self._uid()),
					)
					continue

				month_diff = (today.year - last_date.year) * 12 + (today.month - last_date.month)
				if month_diff <= 0:
					continue
				# повний місяць вважаємо пройденим лише після того ж дня місяця
				if today.day < last_date.day:
					month_diff -= 1
				if month_diff <= 0:
					continue

				if balance > 0 and rate != 0:
					for _ in range(month_diff):
						balance += balance * rate / 100.0 / 12.0
					new_last = add_months(last_date, month_diff)
					conn.execute(
						"UPDATE bank_accounts SET balance = ?, last_interest_date = ? WHERE id = ? AND user_id = ?",
						(balance, new_last.strftime("%Y-%m-%d"), acc_id, self._uid()),
					)
					applied += 1
				else:
					# навіть якщо 0% або 0 баланс — просто рухаємо дату вперед
					new_last = add_months(last_date, month_diff)
					conn.execute(
						"UPDATE bank_accounts SET last_interest_date = ? WHERE id = ? AND user_id = ?",
						(new_last.strftime("%Y-%m-%d"), acc_id, self._uid()),
					)

			conn.commit()

		return applied

	# Налаштування
	def get_setting(self, key: str, default: str = "") -> str:
		uid = self._uid()
		with self._connect() as conn:
			row = conn.execute("SELECT value FROM settings WHERE user_id = ? AND key = ?", (uid, key)).fetchone()
			return row[0] if row else default

	def set_setting(self, key: str, value: str) -> None:
		uid = self._uid()
		stored_value = value
		if key == "password" and value and not value.startswith("sha256$"):
			stored_value = "sha256$" + sha256(value.encode("utf-8")).hexdigest()
		with self._connect() as conn:
			conn.execute("INSERT OR REPLACE INTO settings(user_id, key, value) VALUES (?,?,?)", (uid, key, stored_value))
			conn.commit()

	def user_exists(self, username: str) -> bool:
		with self._connect() as conn:
			row = conn.execute(
				"SELECT 1 FROM users WHERE username = ? LIMIT 1",
				(username.strip(),),
			).fetchone()
			return row is not None

	def create_user(self, username: str, password: str) -> bool:
		u = username.strip()
		if not u or not password:
			return False
		p_hash = "sha256$" + sha256(password.encode("utf-8")).hexdigest()
		try:
			with self._connect() as conn:
				cur = conn.execute(
					"INSERT INTO users(username, password_hash) VALUES (?, ?)",
					(u, p_hash),
				)
				new_user_id = int(cur.lastrowid)
				default_cats = [
					("Їжа", new_user_id),
					("Транспорт", new_user_id),
					("Покупки", new_user_id),
					("Комунальні послуги", new_user_id),
					("Здоров'я", new_user_id),
					("Розваги", new_user_id),
					("Інше", new_user_id),
				]
				conn.executemany("INSERT OR IGNORE INTO categories(name, user_id) VALUES (?,?)", default_cats)
				conn.commit()
				return True
		except Exception:
			logger.exception("Не вдалося створити користувача '%s'", u)
			return False

	def validate_user(self, username: str, password: str) -> bool:
		u = username.strip()
		if not u or password is None:
			return False
		p_hash = "sha256$" + sha256(password.encode("utf-8")).hexdigest()
		with self._connect() as conn:
			row = conn.execute(
				"SELECT password_hash FROM users WHERE username = ?",
				(u,),
			).fetchone()
			if not row:
				return False
			return row[0] == p_hash

	def get_user_id(self, username: str) -> int | None:
		with self._connect() as conn:
			row = conn.execute("SELECT id FROM users WHERE username = ?", (username.strip(),)).fetchone()
			return int(row[0]) if row else None

	# Нагадування
	def add_reminder(self, title: str, message: str, day_of_month: int, category_id: int | None) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute(
				"INSERT INTO reminders(title, message, day_of_month, category_id, user_id) VALUES (?,?,?,?,?)",
				(title, message, day_of_month, category_id, uid)
			)
			conn.commit()

	def list_reminders(self) -> list[sqlite3.Row]:
		uid = self._uid()
		with self._connect() as conn:
			return conn.execute(
				"""SELECT r.id, r.title, r.message, r.day_of_month, r.category_id, c.name AS category, r.active
				   FROM reminders r LEFT JOIN categories c ON c.id = r.category_id
				   WHERE r.active = 1 AND r.user_id = ?
				   ORDER BY r.day_of_month""",
				(uid,),
			).fetchall()

	def delete_reminder(self, reminder_id: int) -> None:
		uid = self._uid()
		with self._connect() as conn:
			conn.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, uid))
			conn.commit()

	# Розширені запити для статистики
	def list_transactions_filtered(self, month: int | None, year: int | None, 
								   search_text: str | None = None, 
								   category_id: int | None = None,
								   min_amount: float | None = None,
								   max_amount: float | None = None,
								   date_from: str | None = None,
								   date_to: str | None = None) -> list[sqlite3.Row]:
		"""Розширений пошук транзакцій з фільтрами"""
		query = [
			"SELECT t.id, t.date, t.type, t.amount, t.category_id, c.name AS category, t.note",
			"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id",
		]
		params: list = []
		clauses: list[str] = ["t.user_id = ?"]
		params.append(self._uid())
		
		if year:
			clauses.append("strftime('%Y', t.date) = ?")
			params.append(f"{year:04d}")
		if month:
			clauses.append("strftime('%m', t.date) = ?")
			params.append(f"{month:02d}")
		if category_id is not None:
			clauses.append("t.category_id = ?")
			params.append(category_id)
		if search_text:
			clauses.append("(t.note LIKE ? OR c.name LIKE ?)")
			search_pattern = f"%{search_text}%"
			params.extend([search_pattern, search_pattern])
		if min_amount is not None:
			clauses.append("t.amount >= ?")
			params.append(min_amount)
		if max_amount is not None:
			clauses.append("t.amount <= ?")
			params.append(max_amount)
		if date_from:
			clauses.append("t.date >= ?")
			params.append(date_from)
		if date_to:
			clauses.append("t.date <= ?")
			params.append(date_to)
		
		if clauses:
			query.append("WHERE " + " AND ".join(clauses))
		query.append("ORDER BY t.date DESC, t.id DESC")
		sql = "\n".join(query)
		with self._connect() as conn:
			return conn.execute(sql, params).fetchall()

	def average_expenses_by_category(self, months: int = 3) -> list[tuple[str, float]]:
		"""Середні витрати за категоріями за останні N місяців"""
		today = date.today()
		date_from = (today - timedelta(days=months * 30)).strftime("%Y-%m-%d")
		
		with self._connect() as conn:
			rows = conn.execute(
				"""SELECT COALESCE(c.name, 'Uncategorized') as category, AVG(t.amount) as avg_amount
				   FROM transactions t 
				   LEFT JOIN categories c ON c.id = t.category_id
				   WHERE t.type = 'expense' AND t.date >= ? AND t.user_id = ?
				   GROUP BY category
				   ORDER BY avg_amount DESC""",
				(date_from, self._uid())
			).fetchall()
		return [(r[0], float(r[1] or 0.0)) for r in rows]

	def expense_trends(self, months: int = 6) -> list[tuple[str, float]]:
		"""Тренди витрат по місяцях"""
		today = date.today()
		date_from = (today - timedelta(days=months * 30)).strftime("%Y-%m-%d")
		
		with self._connect() as conn:
			rows = conn.execute(
				"""SELECT strftime('%Y-%m', date) as month, SUM(amount) as total
				   FROM transactions
				   WHERE type = 'expense' AND date >= ? AND user_id = ?
				   GROUP BY month
				   ORDER BY month""",
				(date_from, self._uid())
			).fetchall()
		return [(r[0], float(r[1] or 0.0)) for r in rows]

	def cash_flow_by_month(self, months: int = 6) -> list[tuple[str, float, float]]:
		"""Повертає (місяць, дохід, витрати) для останніх N місяців."""
		today = date.today()
		date_from = (today - timedelta(days=months * 30)).strftime("%Y-%m-%d")

		with self._connect() as conn:
			rows = conn.execute(
				"""
				SELECT
					strftime('%Y-%m', date) as month,
					SUM(CASE WHEN type='income' THEN amount ELSE 0 END) as income,
					SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) as expense
				FROM transactions
				WHERE date >= ? AND user_id = ?
				GROUP BY month
				ORDER BY month
				""",
				(date_from, self._uid()),
			).fetchall()

		return [
			(r[0], float(r[1] or 0.0), float(r[2] or 0.0))
			for r in rows
		]

	def total_bank_balance(self) -> float:
		"""Повертає сумарний баланс по всіх банківських рахунках."""
		uid = self._uid()
		with self._connect() as conn:
			row = conn.execute("SELECT SUM(balance) FROM bank_accounts WHERE user_id = ?", (uid,)).fetchone()
			return float(row[0] or 0.0)


