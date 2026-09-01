import datetime as dt
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import requests
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from tkcalendar import DateEntry

from db import Database
from services import FinanceService
from ui_dialogs import manage_bank_accounts_dialog, manage_budgets_dialog, manage_categories_dialog

# ── Design tokens ──────────────────────────────────────────────
COLOR_INCOME   = "#27ae60"   # green
COLOR_EXPENSE  = "#e74c3c"   # red
COLOR_PRIMARY  = "#2c3e50"   # dark blue-grey (header bg)
COLOR_ACCENT   = "#3498db"   # blue
COLOR_BG_LIGHT = "#f0f4f8"   # light grey background
COLOR_BG_CARD  = "#ffffff"   # white card
COLOR_TEXT     = "#2d3748"   # dark text
COLOR_MUTED    = "#718096"   # muted text
FONT_HEADER    = ("Segoe UI", 14, "bold")
FONT_LABEL     = ("Segoe UI", 10)
FONT_MONO      = ("Consolas", 10)


logger = logging.getLogger(__name__)


class FinanceApp:
	def __init__(self, root: tk.Tk, db: Database) -> None:
		self.root = root
		self.root.title("Система управління особистими фінансами")
		
		self.db = db
		self.service = FinanceService(self.db)
		
		# Завантаження налаштувань
		saved_geometry = self.db.get_setting("window_geometry", "1200x750")
		self.root.geometry(saved_geometry)
		saved_currency = self.db.get_setting("currency", "UAH")
		saved_theme = self.db.get_setting("theme", "light")
		
		# Styling
		self.style = ttk.Style(self.root)
		self.dark_mode = (saved_theme == "dark")
		self._apply_theme(saved_theme)
		
		self.style.configure("TButton", padding=(10, 6))
		self.style.configure("Budget.TButton", padding=(8, 4))
		self.style.configure("TLabel", padding=(2, 2))
		self.style.configure("Summary.TLabel", font=("Segoe UI", 10, "bold"))
		self.style.configure("Treeview", rowheight=26)
		self.style.map("TButton", relief=[("pressed", "sunken"), ("active", "raised")])

		self.selected_transaction_id: int | None = None
		self.currency_var = tk.StringVar(value=saved_currency)
		self.currency_symbols: dict[str, str] = {"UAH": "грн", "USD": "$", "EUR": "€", "PLN": "zł"}
		# Conversion rates to UAH (1 unit of currency = X UAH)
		# Завантаження збережених курсів
		self.rates_to_uah: dict[str, float] = {"UAH": 1.0, "USD": 40.0, "EUR": 43.0, "PLN": 10.0}
		for cur in ["USD", "EUR", "PLN"]:
			saved_rate = self.db.get_setting(f"rate_{cur}", "")
			if saved_rate:
				try:
					self.rates_to_uah[cur] = float(saved_rate)
				except Exception:
					pass
		# Localized type mapping (UI <-> DB)
		self.type_display_values = ["Дохід", "Витрата"]
		self.type_display_to_db = {"Дохід": "income", "Витрата": "expense", "Витрати": "expense"}
		self.type_db_to_display = {v: k for k, v in self.type_display_to_db.items()}
		
		# Збереження розміру вікна при закритті
		self.root.bind("<Configure>", self._on_window_configure)
		self.root.protocol("WM_DELETE_WINDOW", self._on_close)

		self._build_ui()
		self._load_categories()
		self._set_default_filters()
		self._setup_hotkeys()
		self.refresh()
		# Автооновлення курсів валют у фоні (не блокує UI)
		self.update_rates_from_api_async(manual=False)

	def _build_ui(self) -> None:
		container = ttk.Frame(self.root, padding=10)
		container.pack(fill=tk.BOTH, expand=True)

		# Filters and summary top bar (2 рядки, щоб елементи не обрізались)
		header = tk.Frame(container, bg=COLOR_PRIMARY, padx=12, pady=8)
		header.pack(fill=tk.X)
		tk.Label(
			header,
			text=" Фінансовий помічник",
			bg=COLOR_PRIMARY,
			fg="white",
			font=FONT_HEADER,
		).pack(side=tk.LEFT)
		top = ttk.Frame(container)
		top.pack(fill=tk.X, pady=(6, 0))
		top_row_1 = ttk.Frame(top)
		top_row_1.pack(fill=tk.X)
		top_row_2 = ttk.Frame(top)
		top_row_2.pack(fill=tk.X, pady=(6, 0))
		self.month_var = tk.StringVar()
		self.year_var = tk.StringVar()
		ttk.Label(top_row_1, text="Місяць:").pack(side=tk.LEFT)
		self.month_cb = ttk.Combobox(top_row_1, textvariable=self.month_var, width=5, state="readonly", values=[f"{m:02d}" for m in range(1, 13)])
		self.month_cb.pack(side=tk.LEFT, padx=(5, 10))
		ttk.Label(top_row_1, text="Рік:").pack(side=tk.LEFT)
		years = [str(y) for y in range(dt.date.today().year - 5, dt.date.today().year + 6)]
		self.year_cb = ttk.Combobox(top_row_1, textvariable=self.year_var, width=7, state="readonly", values=years)
		self.year_cb.pack(side=tk.LEFT, padx=(5, 10))
		self.apply_filter_btn = ttk.Button(top_row_1, text="Застосувати", command=self.refresh)
		self.apply_filter_btn.pack(side=tk.LEFT)

		cards_frame = ttk.Frame(container)
		cards_frame.pack(fill=tk.X, pady=(6, 0))

		def make_card(parent, title, var, color):
			f = tk.Frame(parent, bg=color, padx=14, pady=8, relief="flat", bd=0)
			f.pack(side=tk.LEFT, padx=6, fill=tk.Y)
			tk.Label(f, text=title, bg=color, fg="white",
					 font=("Segoe UI", 8)).pack(anchor=tk.W)
			tk.Label(f, textvariable=var, bg=color, fg="white",
					 font=("Segoe UI", 13, "bold")).pack(anchor=tk.W)
			return f

		self.income_summary_var = tk.StringVar(value="—")
		self.expense_summary_var = tk.StringVar(value="—")
		self.balance_summary_var = tk.StringVar(value="—")

		make_card(cards_frame, "Доходи ▲", self.income_summary_var, "#27ae60")
		make_card(cards_frame, "Витрати ▼", self.expense_summary_var, "#e74c3c")
		make_card(cards_frame, "Баланс", self.balance_summary_var, "#2980b9")

		# Currency selector
		curr_frame = ttk.Frame(top_row_2)
		curr_frame.pack(side=tk.RIGHT)
		ttk.Label(curr_frame, text="Валюта:").pack(side=tk.LEFT)
		self.currency_cb = ttk.Combobox(curr_frame, textvariable=self.currency_var, state="readonly", width=7, values=["UAH", "USD", "EUR", "PLN"])
		self.currency_cb.pack(side=tk.LEFT, padx=(5, 10))
		self.currency_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

		# Rates controls
		rate_btn = ttk.Button(curr_frame, text="Курси...", command=self.edit_rates)
		rate_btn.pack(side=tk.LEFT)
		update_rates_btn = ttk.Button(curr_frame, text="Оновити курси", command=lambda: self.update_rates_from_api_async(manual=True))
		update_rates_btn.pack(side=tk.LEFT, padx=(4, 0))

		# Меню кнопок
		menu_frame = ttk.Frame(top_row_2)
		menu_frame.pack(side=tk.LEFT)
		manage_cats_btn = ttk.Button(menu_frame, text="Категорії...", command=self.manage_categories)
		manage_cats_btn.pack(side=tk.LEFT, padx=2)
		budget_btn = ttk.Button(menu_frame, text="Бюджети...", command=self.manage_budgets)
		budget_btn.pack(side=tk.LEFT, padx=2)
		bank_btn = ttk.Button(menu_frame, text="Банківські рахунки...", command=self.manage_bank_accounts)
		bank_btn.pack(side=tk.LEFT, padx=2)
		stats_btn = ttk.Button(menu_frame, text="Статистика...", command=self.show_statistics)
		stats_btn.pack(side=tk.LEFT, padx=2)
		search_btn = ttk.Button(menu_frame, text="Пошук...", command=self.show_search)
		search_btn.pack(side=tk.LEFT, padx=2)
		settings_btn = ttk.Button(menu_frame, text="Налаштування...", command=self.show_settings)
		settings_btn.pack(side=tk.LEFT, padx=2)

		# Split left form and right data/chart
		content = ttk.PanedWindow(container, orient=tk.HORIZONTAL)
		content.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

		form_frame = ttk.Frame(content)
		form_frame.configure(width=300)
		content.add(form_frame, weight=1)

		data_frame = ttk.PanedWindow(content, orient=tk.VERTICAL)
		content.add(data_frame, weight=3)

		# Form controls
		self.date_var = tk.StringVar()
		self.type_var = tk.StringVar(value="Витрата")
		self.amount_var = tk.StringVar()
		self.category_var = tk.StringVar()
		self.note_var = tk.StringVar()

		lf = ttk.LabelFrame(form_frame, text="➕ Нова операція")
		lf.pack(fill=tk.X)
		row1 = ttk.Frame(lf)
		row1.pack(fill=tk.X, padx=10, pady=6)
		ttk.Label(row1, text="Дата (РРРР-ММ-ДД)").pack(side=tk.LEFT)
		self.date_entry = DateEntry(
			row1,
			textvariable=self.date_var,
			width=12,
			date_pattern="yyyy-mm-dd",
			background="#3498db",
			foreground="white",
			borderwidth=1,
		)
		self.date_entry.pack(side=tk.LEFT, padx=(8, 0))
		ttk.Label(row1, text="Тип").pack(side=tk.LEFT, padx=(16, 0))
		self.type_cb = ttk.Combobox(row1, textvariable=self.type_var, state="readonly", width=10, values=self.type_display_values)
		self.type_cb.pack(side=tk.LEFT, padx=(8, 0))

		row2 = ttk.Frame(lf)
		row2.pack(fill=tk.X, padx=10, pady=6)
		ttk.Label(row2, text="Сума").pack(side=tk.LEFT)
		self.amount_entry = ttk.Entry(row2, textvariable=self.amount_var, width=16)
		vcmd = (self.root.register(self._validate_decimal_input), "%P")
		self.amount_entry.configure(validate="key", validatecommand=vcmd)
		self.amount_entry.pack(side=tk.LEFT, padx=(8, 0))
		ttk.Label(row2, text="Категорія").pack(side=tk.LEFT, padx=(16, 0))
		self.category_cb = ttk.Combobox(row2, textvariable=self.category_var, state="readonly")
		self.category_cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

		row3 = ttk.Frame(lf)
		row3.pack(fill=tk.X, padx=10, pady=6)
		ttk.Label(row3, text="Примітка").pack(side=tk.LEFT)
		self.note_entry = ttk.Entry(row3, textvariable=self.note_var)
		self.note_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

		btns = ttk.Frame(form_frame)
		btns.pack(fill=tk.X, pady=(10, 0))
		self.add_btn = ttk.Button(btns, text="Додати", command=self.add_transaction)
		self.add_btn.pack(side=tk.LEFT)
		self.update_btn = ttk.Button(btns, text="Оновити", command=self.update_transaction)
		self.update_btn.pack(side=tk.LEFT, padx=8)
		self.delete_btn = ttk.Button(btns, text="Видалити", command=self.delete_transaction)
		self.delete_btn.pack(side=tk.LEFT)
		self.clear_btn = ttk.Button(btns, text="Очистити", command=self.clear_form)
		self.clear_btn.pack(side=tk.LEFT, padx=8)

		# Table
		table_container = ttk.Frame(data_frame)
		data_frame.add(table_container, weight=2)
		columns = ("id", "date", "type", "amount", "category", "note")
		# Поле швидкого пошуку над таблицею
		search_bar = ttk.Frame(table_container)
		search_bar.pack(fill=tk.X, pady=(0, 4))
		ttk.Label(search_bar, text="Швидкий пошук:").pack(side=tk.LEFT)
		self.quick_search_var = tk.StringVar()
		quick_search_entry = ttk.Entry(search_bar, textvariable=self.quick_search_var, width=30)
		quick_search_entry.pack(side=tk.LEFT, padx=(5, 0))
		quick_search_entry.bind("<KeyRelease>", lambda _e: self._apply_quick_filter())

		self.tree = ttk.Treeview(table_container, columns=columns, show="headings", height=12)
		col_widths = {"id": 40, "date": 95, "type": 80, "amount": 110, "category": 130, "note": 260}
		for col, text in zip(columns, ["ID", "Дата", "Тип", "Сума", "Категорія", "Примітка"]):
			self.tree.heading(col, text=text)
			self.tree.column(col, width=col_widths.get(col, 100), anchor=tk.W)
		self.tree.column("id", stretch=False)
		self.tree.column("type", anchor=tk.CENTER)
		self.tree.column("amount", anchor=tk.E)
		self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		scroll = ttk.Scrollbar(table_container, orient=tk.VERTICAL, command=self.tree.yview)
		scroll.pack(side=tk.RIGHT, fill=tk.Y)
		self.tree.configure(yscrollcommand=scroll.set)
		self.tree.bind("<<TreeviewSelect>>", self.on_select_row)

		# Stripe rows
		self.tree.tag_configure("income", foreground=COLOR_INCOME, font=FONT_LABEL)
		self.tree.tag_configure("expense", foreground=COLOR_EXPENSE, font=FONT_LABEL)
		self.tree.tag_configure("oddrow", background="#f7f9fc")
		self.tree.tag_configure("evenrow", background="#ffffff")

		# Chart
		chart_container = ttk.Frame(data_frame)
		data_frame.add(chart_container, weight=1)
		self.figure = Figure(figsize=(5, 3), dpi=100)
		self.ax = self.figure.add_subplot(111)
		self.ax.set_title("Витрати за місяць за категоріями")
		self.ax.set_ylabel("Сума")
		self.canvas = FigureCanvasTkAgg(self.figure, master=chart_container)
		self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

	def edit_rates(self) -> None:
		win = tk.Toplevel(self.root)
		win.title("Курси валют (1 одиниця у валюті = X грн)")
		frm = ttk.Frame(win, padding=10)
		frm.pack(fill=tk.BOTH, expand=True)
		entries: dict[str, tk.Entry] = {}
		row = 0
		for cur in ["USD", "EUR", "PLN"]:
			ttk.Label(frm, text=cur).grid(row=row, column=0, sticky=tk.W, padx=5, pady=5)
			val = tk.StringVar(value=str(self.rates_to_uah.get(cur, 1.0)))
			e = ttk.Entry(frm, textvariable=val, width=10)
			vcmd = (self.root.register(self._validate_decimal_input), "%P")
			e.configure(validate="key", validatecommand=vcmd)
			e.grid(row=row, column=1, padx=5, pady=5)
			entries[cur] = e
			row += 1
		def save() -> None:
			for cur, e in entries.items():
				try:
					v = float(e.get())
					if v <= 0:
						continue
					self.rates_to_uah[cur] = v
					self.db.set_setting(f"rate_{cur}", str(v))
				except Exception:
					logger.warning("Невірний курс для %s у діалозі редагування", cur, exc_info=True)
			self.refresh()
			win.destroy()
		btns = ttk.Frame(frm)
		btns.grid(row=row, column=0, columnspan=2, pady=(10, 0))
		ttk.Button(btns, text="Зберегти", command=save).pack(side=tk.LEFT)
		ttk.Button(btns, text="Скасувати", command=win.destroy).pack(side=tk.LEFT, padx=8)

	def _set_default_filters(self) -> None:
		today = dt.date.today()
		self.month_var.set(f"{today.month:02d}")
		self.year_var.set(str(today.year))
		self.date_var.set(today.strftime("%Y-%m-%d"))

	def _load_categories(self) -> None:
		cats = self.db.list_categories()
		self._categories = [(c["id"], c["name"]) for c in cats]
		self.category_cb["values"] = [name for _id, name in self._categories]
		if self._categories:
			self.category_var.set(self._categories[0][1])

	def _validate_date(self, date_str: str | None = None) -> bool:
		value = self.date_var.get() if date_str is None else date_str
		ok = self.service.validate_date(value)
		if not ok:
			logger.warning("Невірний формат дати: %s", value)
		return ok

	def _parse_amount(self) -> float | None:
		amount = self.service.parse_amount(self.amount_var.get())
		if amount is None:
			logger.warning("Невірний формат суми: %s", self.amount_var.get())
		return amount

	def _currency_symbol(self) -> str:
		return self.currency_symbols.get(self.currency_var.get(), "")

	def _rate_to_uah(self, currency: str | None = None) -> float:
		cur = currency or self.currency_var.get()
		return self.service.rate_to_uah(self.rates_to_uah, cur)

	def _convert_from_uah(self, amount_uah: float) -> float:
		return self.service.convert_from_uah(amount_uah, self.rates_to_uah, self.currency_var.get())

	def _convert_to_uah(self, amount_in_selected: float) -> float:
		return self.service.convert_to_uah(amount_in_selected, self.rates_to_uah, self.currency_var.get())

	def _format_amount_from_uah(self, amount_uah: float) -> str:
		return self.service.format_amount_from_uah(
			amount_uah,
			self.rates_to_uah,
			self.currency_var.get(),
			self.currency_symbols,
		)

	def _category_name_to_id(self, name: str) -> int | None:
		return self.service.category_name_to_id(self._categories, name)

	def clear_form(self) -> None:
		self.selected_transaction_id = None
		self.type_var.set("Витрата")
		self.amount_var.set("")
		self.note_var.set("")
		self._set_default_filters()
		if self._categories:
			self.category_var.set(self._categories[0][1])

	def _validate_decimal_input(self, proposed: str) -> bool:
		"""Дозволяє лише цифри та крапку в полях вводу."""
		if proposed == "":
			return True
		allowed = set("0123456789.")
		if any(ch not in allowed for ch in proposed):
			return False
		# лише одна крапка
		return proposed.count(".") <= 1

	def update_rates_from_api_async(self, manual: bool = True) -> None:
		"""Запускає оновлення курсів у окремому потоці, без фрізів UI."""
		def worker() -> None:
			try:
				new_rates = self._fetch_rates_from_api()
			except Exception as exc:
				logger.exception("Не вдалося отримати курси валют з API")
				if manual:
					self.root.after(
						0,
						lambda: messagebox.showerror(
							"Помилка мережі",
							f"Не вдалося оновити курси валют.\nДеталі: {exc}",
						),
					)
				return

			def apply() -> None:
				updated = 0
				for ccy, rate in new_rates.items():
					self.rates_to_uah[ccy] = rate
					self.db.set_setting(f"rate_{ccy}", str(rate))
					updated += 1
				self.refresh()
				if manual:
					messagebox.showinfo("Курси оновлено", f"Оновлено курси валют: {updated}")

			self.root.after(0, apply)

		threading.Thread(target=worker, daemon=True).start()

	def _fetch_rates_from_api(self) -> dict[str, float]:
		"""
		Оновлює курси валют (USD, EUR, PLN) через публічне API.
		Використовує курси продажу UAH, щоб отримати співвідношення 1 одиниця валюти = X грн.
		"""
		url = "https://api.privatbank.ua/p24api/pubinfo?json&exchange&coursid=5"
		resp = requests.get(url, timeout=5)
		resp.raise_for_status()
		data = resp.json()

		rates: dict[str, float] = {}
		for item in data:
			ccy = item.get("ccy")
			base_ccy = item.get("base_ccy")
			if base_ccy != "UAH":
				continue
			if ccy in ("USD", "EUR", "PLN"):
				rate = float(item.get("sale") or item.get("buy"))
				if rate > 0:
					rates[ccy] = rate

		if not rates:
			raise RuntimeError("API не повернуло курсів USD/EUR/PLN")

		return rates

	def add_transaction(self) -> None:
		if not self._validate_date(None):
			messagebox.showerror("Невірна дата", "Використовуйте формат РРРР-ММ-ДД")
			return
		amount = self._parse_amount()
		if amount is None or amount <= 0:
			messagebox.showerror("Невірна сума", "Сума має бути додатним числом")
			return
		cat_id = self._category_name_to_id(self.category_var.get())
		# Map display type to DB value
		tx_type = self.type_display_to_db.get(self.type_var.get(), "expense")
		amount_uah = self._convert_to_uah(amount)
		
		# Перевірка бюджету для витрат – винесена в сервіс
		if tx_type == "expense":
			try:
				date_obj = dt.datetime.strptime(self.date_var.get(), "%Y-%m-%d")
				month = date_obj.month
				year = date_obj.year
				check = self.service.check_budget_before_expense(cat_id, amount_uah, year, month)
				if check.over_budget:
					if not messagebox.askyesno(
						"Перевищення бюджету",
						f"Ця транзакція перевищить бюджет на {check.percent_after - 100:.1f}%.\nПродовжити?",
					):
						return
				elif check.near_limit:
					messagebox.showwarning(
						"Наближення до ліміту",
						f"Витрати досягнуть {check.percent_after:.1f}% від бюджету",
					)
			except Exception:
				logger.exception("Помилка під час перевірки бюджету перед витратою")
		
		self.db.add_transaction(
			self.date_var.get(), tx_type, amount_uah, cat_id, self.note_var.get().strip()
		)
		self.refresh()
		self.clear_form()

	def on_select_row(self, _evt) -> None:
		selected = self.tree.selection()
		if not selected:
			return
		item = self.tree.item(selected[0])
		values = item.get("values", [])
		if not values:
			return
		self.selected_transaction_id = int(values[0])
		self.date_var.set(values[1])
		self.type_var.set(values[2])
		self.amount_var.set(str(values[3]))
		self.category_var.set(values[4] or "")
		self.note_var.set(values[5] or "")

	def update_transaction(self) -> None:
		if self.selected_transaction_id is None:
			messagebox.showwarning("Нічого не вибрано", "Виберіть операцію для оновлення")
			return
		if not self._validate_date(None):
			messagebox.showerror("Невірна дата", "Використовуйте формат РРРР-ММ-ДД")
			return
		amount = self._parse_amount()
		if amount is None or amount <= 0:
			messagebox.showerror("Невірна сума", "Сума має бути додатним числом")
			return
		cat_id = self._category_name_to_id(self.category_var.get())
		tx_type = self.type_display_to_db.get(self.type_var.get(), "expense")
		amount_uah = self._convert_to_uah(amount)
		self.db.update_transaction(
			self.selected_transaction_id,
			self.date_var.get(),
			tx_type,
			amount_uah,
			cat_id,
			self.note_var.get().strip(),
		)
		self.refresh()

	def delete_transaction(self) -> None:
		if self.selected_transaction_id is None:
			messagebox.showwarning("Нічого не вибрано", "Виберіть операцію для видалення")
			return
		if not messagebox.askyesno("Підтвердження", "Видалити вибрану операцію?"):
			return
		self.db.delete_transaction(self.selected_transaction_id)
		self.refresh()
		self.clear_form()

	def refresh(self) -> None:
		month = int(self.month_var.get()) if self.month_var.get() else None
		year = int(self.year_var.get()) if self.year_var.get() else None
		# Reload table
		for row_id in self.tree.get_children():
			self.tree.delete(row_id)
		rows = self.db.list_transactions(month, year)
		# Зберігаємо сирі рядки для швидкого фільтрування
		self._all_rows = list(rows)
		for idx, r in enumerate(self._all_rows):
			amt = self._format_amount_from_uah(float(r["amount"]))
			type_tag = "income" if r["type"] == "income" else "expense"
			tag = "evenrow" if idx % 2 == 0 else "oddrow"
			display_type = self.type_db_to_display.get(r["type"], r["type"])  # локалізація типу
			self.tree.insert("", tk.END, values=(r["id"], r["date"], display_type, amt, r["category"], r["note"]), tags=(tag, type_tag))
		s = self.db.summary(month, year)
		total_income = s["income"]
		total_expense = s["expense"]
		sym = self._currency_symbol()
		self.income_summary_var.set(f"{self._convert_from_uah(total_income):.2f} {sym}")
		self.expense_summary_var.set(f"{self._convert_from_uah(total_expense):.2f} {sym}")
		balance = total_income - total_expense
		self.balance_summary_var.set(f"{self._convert_from_uah(balance):.2f} {sym}")
		# Update chart
		self._update_chart(month, year)

	def _apply_quick_filter(self) -> None:
		"""Фільтрація поточної таблиці в режимі реального часу по тексту."""
		query = (self.quick_search_var.get() or "").strip().lower()
		for row_id in self.tree.get_children():
			self.tree.delete(row_id)

		if not getattr(self, "_all_rows", None):
			return

		def match(row: dict) -> bool:
			if not query:
				return True
			in_note = (row["note"] or "").lower()
			in_cat = (row["category"] or "").lower()
			return query in in_note or query in in_cat

		filtered = [r for r in self._all_rows if match(r)]
		for idx, r in enumerate(filtered):
			amt = self._format_amount_from_uah(float(r["amount"]))
			type_tag = "income" if r["type"] == "income" else "expense"
			tag = "evenrow" if idx % 2 == 0 else "oddrow"
			display_type = self.type_db_to_display.get(r["type"], r["type"])
			self.tree.insert(
				"",
				tk.END,
				values=(r["id"], r["date"], display_type, amt, r["category"], r["note"]),
				tags=(tag, type_tag),
			)

	def _update_chart(self, month: int | None, year: int | None) -> None:
		self.ax.clear()
		self.ax.set_title("Витрати за місяць за категоріями")
		sym = self._currency_symbol()
		self.ax.set_ylabel(f"Сума ({sym})" if sym else "Сума")
		data = self.db.expenses_by_category(month, year)
		if data:
			cats = [c for c, _ in data]
			vals = [self._convert_from_uah(v) for _, v in data]
			x = range(len(cats))
			self.ax.bar(x, vals, color="#1976d2")
			self.ax.set_xticks(list(x))
			self.ax.set_xticklabels(cats, rotation=30, ha="right")
			self.ax.grid(axis="y", linestyle=":", alpha=0.5)
			# Add value labels
			for i, v in enumerate(vals):
				self.ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
		else:
			self.ax.text(0.5, 0.5, "Немає витрат", ha="center", va="center")
		self.ax.set_facecolor("#f8fafc")
		self.figure.patch.set_facecolor("#f8fafc")
		self.ax.spines["top"].set_visible(False)
		self.ax.spines["right"].set_visible(False)
		self.ax.tick_params(colors=COLOR_TEXT)
		self.ax.title.set_color(COLOR_TEXT)
		self.canvas.draw_idle()

	def manage_categories(self) -> None:
		manage_categories_dialog(self)

	def _apply_theme(self, theme: str) -> None:
		"""Застосовує тему (light/dark)"""
		try:
			if theme == "dark":
				self.style.theme_use("clam")
				self.root.configure(bg="#2b2b2b")
				self.style.configure("TFrame", background="#2b2b2b")
				self.style.configure("TLabel", background="#2b2b2b", foreground="#ffffff")
				self.style.configure("TButton", background="#404040", foreground="#ffffff")
				self.style.map("TButton", background=[("active", "#505050")])
				self.dark_mode = True
			else:
				self.style.theme_use("clam")
				self.root.configure(bg="SystemButtonFace")
				self.style.configure("TFrame", background="SystemButtonFace")
				self.style.configure("TLabel", background="SystemButtonFace", foreground="SystemButtonText")
				self.style.configure("TButton", background="SystemButtonFace", foreground="SystemButtonText")
				self.dark_mode = False
		except Exception:
			logger.exception("Не вдалося застосувати тему '%s'", theme)

	def _on_window_configure(self, event: tk.Event) -> None:
		"""Зберігає розмір вікна"""
		if event.widget == self.root:
			geometry = self.root.geometry()
			self.db.set_setting("window_geometry", geometry)

	def _on_close(self) -> None:
		try:
			geom = self.root.geometry()
			self.db.set_setting("window_geometry", geom)
			self.db.close()
		except Exception:
			logger.exception("Помилка при закритті застосунку")
		finally:
			self.root.destroy()

	def _setup_hotkeys(self) -> None:
		"""Налаштування гарячих клавіш"""
		self.root.bind("<Control-n>", lambda e: self.add_transaction())
		self.root.bind("<Control-s>", lambda e: self.show_search())
		self.root.bind("<Control-b>", lambda e: self.manage_budgets())
		self.root.bind("<Control-r>", lambda e: self.refresh())
		self.root.bind("<Delete>", lambda e: self.delete_transaction())
		self.root.bind("<Control-d>", lambda e: self.delete_transaction())
		self.root.bind("<Escape>", lambda e: self.clear_form())

	def manage_budgets(self) -> None:
		manage_budgets_dialog(self)

	def manage_bank_accounts(self) -> None:
		manage_bank_accounts_dialog(self)

	def show_search(self) -> None:
		win = tk.Toplevel(self.root)
		win.title("Пошук та фільтри")
		win.geometry("500x400")
		frame = ttk.Frame(win, padding=10)
		frame.pack(fill=tk.BOTH, expand=True)
		ttk.Label(frame, text="Пошук за приміткою/категорією:").pack(anchor=tk.W)
		search_var = tk.StringVar()
		ttk.Entry(frame, textvariable=search_var).pack(fill=tk.X, pady=(0, 10))
		ttk.Label(frame, text="Категорія:").pack(anchor=tk.W)
		cat_filter_var = tk.StringVar(value="Всі")
		cat_filter_cb = ttk.Combobox(frame, textvariable=cat_filter_var, state="readonly", values=["Всі"] + [name for _, name in self._categories])
		cat_filter_cb.pack(fill=tk.X, pady=(0, 10))
		ttk.Label(frame, text="Діапазон дат:").pack(anchor=tk.W)
		date_frame = ttk.Frame(frame)
		date_frame.pack(fill=tk.X, pady=(0, 10))
		ttk.Label(date_frame, text="Від:").pack(side=tk.LEFT)
		date_from_var = tk.StringVar()
		ttk.Entry(date_frame, textvariable=date_from_var, width=12).pack(side=tk.LEFT, padx=5)
		ttk.Label(date_frame, text="До:").pack(side=tk.LEFT)
		date_to_var = tk.StringVar()
		ttk.Entry(date_frame, textvariable=date_to_var, width=12).pack(side=tk.LEFT, padx=5)
		ttk.Label(frame, text="Діапазон сум:").pack(anchor=tk.W)
		amount_frame = ttk.Frame(frame)
		amount_frame.pack(fill=tk.X, pady=(0, 10))
		ttk.Label(amount_frame, text="Від:").pack(side=tk.LEFT)
		min_amount_var = tk.StringVar()
		ttk.Entry(amount_frame, textvariable=min_amount_var, width=12).pack(side=tk.LEFT, padx=5)
		ttk.Label(amount_frame, text="До:").pack(side=tk.LEFT)
		max_amount_var = tk.StringVar()
		ttk.Entry(amount_frame, textvariable=max_amount_var, width=12).pack(side=tk.LEFT, padx=5)
		result_frame = ttk.Frame(frame)
		result_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
		columns = ("date", "type", "amount", "category", "note")
		tree = ttk.Treeview(result_frame, columns=columns, show="headings", height=10)
		for col, text in zip(columns, ["Дата", "Тип", "Сума", "Категорія", "Примітка"]):
			tree.heading(col, text=text)
			tree.column(col, width=100)
		tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		scroll = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=tree.yview)
		scroll.pack(side=tk.RIGHT, fill=tk.Y)
		tree.configure(yscrollcommand=scroll.set)
		def do_search() -> None:
			for row_id in tree.get_children():
				tree.delete(row_id)
			search_text = search_var.get().strip() if search_var.get() else None
			cat_name = cat_filter_var.get()
			cat_id = None if cat_name == "Всі" else self._category_name_to_id(cat_name)
			date_from = date_from_var.get().strip() if date_from_var.get() else None
			date_to = date_to_var.get().strip() if date_to_var.get() else None
			try:
				min_amount = float(min_amount_var.get()) if min_amount_var.get() else None
				if min_amount:
					min_amount = self._convert_to_uah(min_amount)
			except Exception:
				min_amount = None
			try:
				max_amount = float(max_amount_var.get()) if max_amount_var.get() else None
				if max_amount:
					max_amount = self._convert_to_uah(max_amount)
			except Exception:
				max_amount = None
			month = int(self.month_var.get()) if self.month_var.get() else None
			year = int(self.year_var.get()) if self.year_var.get() else None
			results = self.db.list_transactions_filtered(month, year, search_text, cat_id, min_amount, max_amount, date_from, date_to)
			for idx, r in enumerate(results):
				amt = self._format_amount_from_uah(float(r["amount"]))
				display_type = self.type_db_to_display.get(r["type"], r["type"])
				tag = "evenrow" if idx % 2 == 0 else "oddrow"
				tree.insert("", tk.END, values=(r["date"], display_type, amt, r["category"] or "", r["note"] or ""), tags=(tag,))
		ttk.Button(frame, text="Пошук", command=do_search).pack(pady=5)
		do_search()

	def show_statistics(self) -> None:
		win = tk.Toplevel(self.root)
		win.title("Статистика та аналіз")
		win.geometry("900x700")
		notebook = ttk.Notebook(win)
		notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
		avg_frame = ttk.Frame(notebook)
		notebook.add(avg_frame, text="Середні витрати")
		avg_tree = ttk.Treeview(avg_frame, columns=("category", "avg"), show="headings", height=15)
		avg_tree.heading("category", text="Категорія")
		avg_tree.heading("avg", text="Середня сума")
		avg_tree.column("category", width=200)
		avg_tree.column("avg", width=150)
		avg_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
		avg_data = self.db.average_expenses_by_category(3)
		sym = self._currency_symbol()
		for cat, avg in avg_data:
			avg_val = self._convert_from_uah(avg)
			avg_tree.insert("", tk.END, values=(cat, f"{avg_val:.2f} {sym}"))
		trend_frame = ttk.Frame(notebook)
		notebook.add(trend_frame, text="Тренди")
		trend_fig = Figure(figsize=(8, 4), dpi=100)
		trend_ax = trend_fig.add_subplot(111)
		trend_data = self.db.expense_trends(6)
		if trend_data:
			months = [m for m, _ in trend_data]
			amounts = [self._convert_from_uah(a) for _, a in trend_data]
			trend_ax.plot(months, amounts, marker='o')
			trend_ax.set_title("Тренди витрат за останні 6 місяців")
			trend_ax.set_ylabel(f"Сума ({sym})")
			trend_ax.set_xlabel("Місяць")
			trend_ax.grid(True, alpha=0.3)
			trend_ax.tick_params(axis='x', rotation=45)
		trend_ax.set_facecolor("#f8fafc")
		trend_fig.patch.set_facecolor("#f8fafc")
		trend_ax.spines["top"].set_visible(False)
		trend_ax.spines["right"].set_visible(False)
		trend_ax.tick_params(colors=COLOR_TEXT)
		trend_ax.title.set_color(COLOR_TEXT)
		trend_canvas = FigureCanvasTkAgg(trend_fig, master=trend_frame)
		trend_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
		# Cash Flow (Доходи vs Витрати)
		cashflow_frame = ttk.Frame(notebook)
		notebook.add(cashflow_frame, text="Cash Flow")
		cf_fig = Figure(figsize=(8, 4), dpi=100)
		cf_ax = cf_fig.add_subplot(111)
		cf_data = self.service.cash_flow_by_month(6)
		if cf_data:
			months_cf = [m for m, _, _ in cf_data]
			incomes = [self._convert_from_uah(inc) for _, inc, _ in cf_data]
			expenses = [self._convert_from_uah(exp) for _, _, exp in cf_data]
			x = range(len(months_cf))
			width = 0.35
			cf_ax.bar([i - width / 2 for i in x], incomes, width=width, label="Дохід", color="#2ecc71")
			cf_ax.bar([i + width / 2 for i in x], expenses, width=width, label="Витрати", color="#e74c3c")
			cf_ax.set_xticks(list(x))
			cf_ax.set_xticklabels(months_cf, rotation=45, ha="right")
			cf_ax.set_title("Cash Flow (Дохід vs Витрати)")
			cf_ax.set_ylabel(f"Сума ({sym})")
			cf_ax.grid(axis="y", alpha=0.3)
			cf_ax.legend()
		cf_ax.set_facecolor("#f8fafc")
		cf_fig.patch.set_facecolor("#f8fafc")
		cf_ax.spines["top"].set_visible(False)
		cf_ax.spines["right"].set_visible(False)
		cf_ax.tick_params(colors=COLOR_TEXT)
		cf_ax.title.set_color(COLOR_TEXT)
		cf_canvas = FigureCanvasTkAgg(cf_fig, master=cashflow_frame)
		cf_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

		# Депозити / банківські рахунки
		deposits_frame = ttk.Frame(notebook)
		notebook.add(deposits_frame, text="Депозити")
		dep_top = ttk.Frame(deposits_frame)
		dep_top.pack(fill=tk.X, padx=10, pady=(10, 5))
		ttk.Label(dep_top, text="Банківські рахунки та депозити").pack(side=tk.LEFT)
		ttk.Button(dep_top, text="Додати рахунок", command=self.manage_bank_accounts).pack(side=tk.RIGHT)

		dep_columns = ("name", "balance", "rate", "monthly_profit", "created_at")
		dep_tree = ttk.Treeview(deposits_frame, columns=dep_columns, show="headings", height=14)
		for col, text in zip(dep_columns, ["Назва", "Баланс", "Річний %", "Прибуток/міс", "Створено"]):
			dep_tree.heading(col, text=text)
			dep_tree.column(col, width=160, anchor=tk.W)
		dep_tree.column("rate", width=90, stretch=False)
		dep_tree.column("monthly_profit", width=120, stretch=False)
		dep_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

		def reload_deposits() -> None:
			for row_id in dep_tree.get_children():
				dep_tree.delete(row_id)
			accounts = self.db.list_bank_accounts()
			for acc in accounts:
				bal = self._convert_from_uah(float(acc["balance"] or 0.0))
				rate = float(acc["interest_rate"] or 0.0)
				monthly_profit = bal * rate / 100.0 / 12.0
				dep_tree.insert(
					"",
					tk.END,
					values=(
						acc["name"],
						f"{bal:.2f} {sym}",
						f"{rate:.2f}%",
						f"{monthly_profit:.2f} {sym}",
						acc["created_at"],
					),
				)

		reload_deposits()

		pie_frame = ttk.Frame(notebook)
		notebook.add(pie_frame, text="Розподіл витрат")
		pie_fig = Figure(figsize=(6, 6), dpi=100)
		pie_ax = pie_fig.add_subplot(111)
		month = int(self.month_var.get()) if self.month_var.get() else None
		year = int(self.year_var.get()) if self.year_var.get() else None
		pie_data = self.db.expenses_by_category(month, year)
		if pie_data:
			cats = [c for c, _ in pie_data]
			vals = [self._convert_from_uah(v) for _, v in pie_data]
			pie_ax.pie(vals, labels=cats, autopct='%1.1f%%', startangle=90)
			pie_ax.set_title("Відсотковий розподіл витрат")
		pie_canvas = FigureCanvasTkAgg(pie_fig, master=pie_frame)
		pie_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

	def show_settings(self) -> None:
		win = tk.Toplevel(self.root)
		win.title("Налаштування")
		win.geometry("400x500")
		frame = ttk.Frame(win, padding=10)
		frame.pack(fill=tk.BOTH, expand=True)
		ttk.Label(frame, text="Тема:").pack(anchor=tk.W, pady=(0, 5))
		theme_var = tk.StringVar(value="dark" if self.dark_mode else "light")
		theme_frame = ttk.Frame(frame)
		theme_frame.pack(fill=tk.X, pady=(0, 15))
		ttk.Radiobutton(theme_frame, text="Світла", variable=theme_var, value="light").pack(side=tk.LEFT, padx=5)
		ttk.Radiobutton(theme_frame, text="Темна", variable=theme_var, value="dark").pack(side=tk.LEFT, padx=5)
		ttk.Label(frame, text="Пароль (захист даних):").pack(anchor=tk.W, pady=(0, 5))
		password_frame = ttk.Frame(frame)
		password_frame.pack(fill=tk.X, pady=(0, 15))
		password_var = tk.StringVar()
		password_entry = ttk.Entry(password_frame, textvariable=password_var, show="*", width=20)
		password_entry.pack(side=tk.LEFT, padx=5)
		saved_password = self.db.get_setting("password", "")
		if saved_password:
			password_var.set("********")
		def set_password() -> None:
			if password_var.get() and password_var.get() != "********":
				self.db.set_setting("password", password_var.get())
				messagebox.showinfo("Успіх", "Пароль встановлено")
			else:
				messagebox.showwarning("Помилка", "Введіть новий пароль")
		ttk.Button(password_frame, text="Встановити", command=set_password).pack(side=tk.LEFT, padx=5)
		def save_settings() -> None:
			theme = theme_var.get()
			self.db.set_setting("theme", theme)
			self._apply_theme(theme)
			self.db.set_setting("currency", self.currency_var.get())
			messagebox.showinfo("Успіх", "Налаштування збережено")
			win.destroy()
			self.refresh()
		ttk.Button(frame, text="Зберегти", command=save_settings).pack(pady=20)


