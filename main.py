import logging
import os
import shutil
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from pathlib import Path

from db import Database
from ui import FinanceApp

DB_FILE_NAME = "finance.db"
SUN_VALLEY_TCL = "sun-valley.tcl"


def setup_logging() -> None:
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
	)


def create_db_backup(max_copies: int = 3) -> None:
	"""Створює резервну копію файлу БД та залишає лише останні max_copies."""
	db_path = Path(DB_FILE_NAME)
	if not db_path.exists():
		return

	backup_dir = db_path.parent / "backups"
	backup_dir.mkdir(exist_ok=True)

	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
	backup_path = backup_dir / f"{db_path.stem}_{timestamp}{db_path.suffix}"

	try:
		shutil.copy2(db_path, backup_path)
		logging.info("Створено бекап бази даних: %s", backup_path)
	except Exception:
		logging.exception("Не вдалося створити бекап бази даних")
		return

	# Ротація – залишаємо тільки останні max_copies копій
	backups = sorted(backup_dir.glob(f"{db_path.stem}_*{db_path.suffix}"))
	if len(backups) > max_copies:
		for old in backups[:-max_copies]:
			try:
				os.remove(old)
				logging.info("Видалено старий бекап: %s", old)
			except Exception:
				logging.exception("Не вдалося видалити старий бекап: %s", old)


def authenticate_user(db: Database) -> int | None:
	"""Показує стартове вікно авторизації/реєстрації."""
	auth_root = tk.Tk()
	auth_root.title("Вхід до системи")
	auth_root.geometry("420x220")
	auth_root.resizable(False, False)

	result = {"user_id": None}
	mode_var = tk.StringVar(value="login")
	username_var = tk.StringVar()
	password_var = tk.StringVar()

	container = tk.Frame(auth_root, padx=14, pady=14)
	container.pack(fill=tk.BOTH, expand=True)

	header = tk.Label(container, text="Оберіть дію", font=("Segoe UI", 11, "bold"))
	header.pack(anchor=tk.W, pady=(0, 8))

	top_btns = tk.Frame(container)
	top_btns.pack(fill=tk.X, pady=(0, 10))

	def set_mode(mode: str) -> None:
		mode_var.set(mode)
		if mode == "register":
			header.config(text="Реєстрація нового користувача")
			submit_btn.config(text="Зареєструвати")
		else:
			header.config(text="Вхід користувача")
			submit_btn.config(text="Увійти")
		password_var.set("")

	tk.Button(top_btns, text="Новий користувач", command=lambda: set_mode("register")).pack(side=tk.LEFT)
	tk.Button(top_btns, text="Увійти", command=lambda: set_mode("login")).pack(side=tk.LEFT, padx=8)

	fields = tk.Frame(container)
	fields.pack(fill=tk.X)
	tk.Label(fields, text="Логін:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
	tk.Entry(fields, textvariable=username_var, width=32).grid(row=0, column=1, sticky="ew", pady=4)
	tk.Label(fields, text="Пароль:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
	tk.Entry(fields, textvariable=password_var, width=32, show="*").grid(row=1, column=1, sticky="ew", pady=4)
	fields.columnconfigure(1, weight=1)

	def submit() -> None:
		username = username_var.get().strip()
		password = password_var.get()
		if not username or not password:
			messagebox.showwarning("Помилка", "Заповніть логін та пароль.", parent=auth_root)
			return

		if mode_var.get() == "register":
			if db.user_exists(username):
				messagebox.showerror("Помилка", "Такий користувач вже існує.", parent=auth_root)
				return
			if not db.create_user(username, password):
				messagebox.showerror("Помилка", "Не вдалося створити користувача.", parent=auth_root)
				return
			messagebox.showinfo("Успіх", "Користувача створено. Тепер увійдіть.", parent=auth_root)
			set_mode("login")
			return

		if db.validate_user(username, password):
			result["user_id"] = db.get_user_id(username)
			auth_root.destroy()
		else:
			messagebox.showerror("Помилка", "Невірний логін або пароль.", parent=auth_root)

	actions = tk.Frame(container)
	actions.pack(fill=tk.X, pady=(12, 0))
	submit_btn = tk.Button(actions, text="Увійти", command=submit)
	submit_btn.pack(side=tk.LEFT)
	tk.Button(actions, text="Скасувати", command=auth_root.destroy).pack(side=tk.LEFT, padx=8)

	set_mode("login")
	auth_root.mainloop()
	return result["user_id"]


def main() -> None:
	setup_logging()
	create_db_backup()
	db_check = Database(DB_FILE_NAME)
	user_id = authenticate_user(db_check)
	if not user_id:
		db_check.close()
		return
	db_check.set_current_user(user_id)
	try:
		applied = db_check.process_monthly_interest()
		logging.info("process_monthly_interest: застосовано до %s рахунків", applied)
	except Exception:
		logging.exception("Не вдалося виконати process_monthly_interest")

	root = tk.Tk()
	try:
		tcl_path = Path(__file__).resolve().parent / SUN_VALLEY_TCL
		if tcl_path.exists():
			root.call("source", str(tcl_path))
			root.call("set_theme", "light")
		else:
			logging.warning("Файл теми sun-valley не знайдено: %s", tcl_path)
	except Exception:
		logging.exception("Не вдалося застосувати тему sun-valley")
	FinanceApp(root, db=db_check)
	root.mainloop()


if __name__ == "__main__":
	main()


