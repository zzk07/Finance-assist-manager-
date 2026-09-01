import datetime as dt
import logging
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


logger = logging.getLogger(__name__)


def manage_categories_dialog(app) -> None:
    win = tk.Toplevel(app.root)
    win.title("Категорії")
    win.geometry("360x420")
    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    lst = tk.Listbox(frame)
    lst.pack(fill=tk.BOTH, expand=True)

    def reload_list() -> None:
        lst.delete(0, tk.END)
        for cid, name in app._categories:
            lst.insert(tk.END, f"{cid}: {name}")

    def add_cat() -> None:
        name = simpledialog.askstring("Додати категорію", "Назва:", parent=win)
        if not name:
            return
        app.db.add_category(name.strip())
        app._load_categories()
        reload_list()

    def del_cat() -> None:
        idx = lst.curselection()
        if not idx:
            return
        item = lst.get(idx[0])
        cid = int(item.split(":", 1)[0])
        if not messagebox.askyesno("Підтвердження", f"Видалити категорію {item}?"):
            return
        app.db.delete_category(cid)
        app._load_categories()
        reload_list()

    btns = ttk.Frame(frame)
    btns.pack(fill=tk.X, pady=(8, 0))
    ttk.Button(btns, text="Додати", command=add_cat).pack(side=tk.LEFT)
    ttk.Button(btns, text="Видалити", command=del_cat).pack(side=tk.LEFT, padx=8)

    reload_list()


def manage_budgets_dialog(app) -> None:
    win = tk.Toplevel(app.root)
    win.title("Управління бюджетами")
    win.geometry("600x500")
    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    filter_frame = ttk.Frame(frame)
    filter_frame.pack(fill=tk.X, pady=(0, 10))
    left_filter = ttk.Frame(filter_frame)
    left_filter.pack(side=tk.LEFT)
    right_btns = ttk.Frame(filter_frame)
    right_btns.pack(side=tk.RIGHT)

    ttk.Label(left_filter, text="Місяць:").pack(side=tk.LEFT)
    bud_month_var = tk.StringVar(value=app.month_var.get())
    bud_month_cb = ttk.Combobox(
        left_filter,
        textvariable=bud_month_var,
        width=5,
        state="readonly",
        values=[f"{m:02d}" for m in range(1, 13)],
    )
    bud_month_cb.pack(side=tk.LEFT, padx=5)

    ttk.Label(left_filter, text="Рік:").pack(side=tk.LEFT, padx=(10, 0))
    bud_year_var = tk.StringVar(value=app.year_var.get())
    years = [str(y) for y in range(dt.date.today().year - 2, dt.date.today().year + 3)]
    bud_year_cb = ttk.Combobox(left_filter, textvariable=bud_year_var, width=7, state="readonly", values=years)
    bud_year_cb.pack(side=tk.LEFT, padx=5)

    columns = ("category", "budget", "spent", "remaining", "percent")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=15)
    for col, text in zip(columns, ["Категорія", "Бюджет", "Витрачено", "Залишилось", "%"]):
        tree.heading(col, text=text)
        tree.column(col, width=100)
    tree.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

    def reload_budgets() -> None:
        for row_id in tree.get_children():
            tree.delete(row_id)
        month = int(bud_month_var.get()) if bud_month_var.get() else None
        year = int(bud_year_var.get()) if bud_year_var.get() else None
        budgets = app.db.get_all_budgets(month, year)
        for bud in budgets:
            cat_id = bud["category_id"]
            cat_name = bud["category"] or "Всі категорії"
            progress = app.service.get_budget_progress(cat_id, bud["month"], bud["year"])
            sym = app._currency_symbol()
            budget_val = app._convert_from_uah(progress["budget"])
            spent_val = app._convert_from_uah(progress["spent"])
            remaining_val = app._convert_from_uah(progress["remaining"])
            percent_val = progress["percent"]
            color = "red" if percent_val > 100 else ("orange" if percent_val > 80 else "green")
            tree.insert(
                "",
                tk.END,
                iid=str(bud["id"]),
                values=(
                    cat_name,
                    f"{budget_val:.2f} {sym}",
                    f"{spent_val:.2f} {sym}",
                    f"{remaining_val:.2f} {sym}",
                    f"{percent_val:.1f}%",
                ),
                tags=(color,),
            )
        tree.tag_configure("red", foreground="red")
        tree.tag_configure("orange", foreground="orange")
        tree.tag_configure("green", foreground="green")

    def set_budget() -> None:
        dialog = tk.Toplevel(win)
        dialog.title("Встановити бюджет")
        dialog.geometry("340x220")
        f = ttk.Frame(dialog, padding=10)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(f, text="Категорія (або 'Всі категорії'):").pack(anchor=tk.W)
        cat_var = tk.StringVar()
        cat_cb = ttk.Combobox(
            f,
            textvariable=cat_var,
            state="readonly",
            values=["Всі категорії"] + [name for _, name in app._categories],
        )
        cat_cb.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(f, text=f"Сума бюджету ({app.currency_var.get()}):").pack(anchor=tk.W)
        amount_var = tk.StringVar()
        amount_entry = ttk.Entry(f, textvariable=amount_var)
        vcmd = (app.root.register(app._validate_decimal_input), "%P")
        amount_entry.configure(validate="key", validatecommand=vcmd)
        amount_entry.pack(fill=tk.X, pady=(0, 10))

        def save_budget() -> None:
            try:
                raw = (amount_var.get() or "").strip()
                if not raw:
                    messagebox.showwarning("Помилка", "Вкажіть суму бюджету")
                    return
                amount = float(raw)
                if amount <= 0:
                    messagebox.showwarning("Помилка", "Сума має бути додатною")
                    return
                cat_name = cat_var.get()
                cat_id = None if cat_name == "Всі категорії" else app._category_name_to_id(cat_name)
                month = int(bud_month_var.get())
                year = int(bud_year_var.get())
                amount_uah = app._convert_to_uah(amount)
                app.db.set_budget(cat_id, month, year, amount_uah)
                reload_budgets()
                dialog.destroy()
                app.refresh()
            except Exception as e:
                messagebox.showerror("Помилка", f"Невірні дані: {e}")

        ttk.Button(f, text="Зберегти", command=save_budget).pack(pady=5)
        ttk.Button(f, text="Скасувати", command=dialog.destroy).pack()
        if cat_cb["values"]:
            cat_var.set(cat_cb["values"][0])
        amount_entry.focus_set()

    def delete_budget() -> None:
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("Нічого не вибрано", "Виберіть бюджет для видалення")
            return
        budget_id = int(selected[0])
        if not messagebox.askyesno("Підтвердження", "Видалити вибраний бюджет?"):
            return
        try:
            app.db.delete_budget(budget_id)
            reload_budgets()
            app.refresh()
        except Exception:
            logger.exception("Не вдалося видалити бюджет id=%s", budget_id)
            messagebox.showerror("Помилка", "Не вдалося видалити бюджет")

    bud_month_cb.bind("<<ComboboxSelected>>", lambda _e: reload_budgets())
    bud_year_cb.bind("<<ComboboxSelected>>", lambda _e: reload_budgets())

    ttk.Button(
        right_btns,
        text="Встановити бюджет",
        style="Budget.TButton",
        width=18,
        command=set_budget,
    ).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        right_btns,
        text="Видалити",
        style="Budget.TButton",
        width=12,
        command=delete_budget,
    ).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        right_btns,
        text="Оновити",
        style="Budget.TButton",
        width=12,
        command=reload_budgets,
    ).pack(side=tk.LEFT, padx=5)

    reload_budgets()
    month = int(bud_month_var.get()) if bud_month_var.get() else None
    year = int(bud_year_var.get()) if bud_year_var.get() else None
    if month and year:
        budgets = app.db.get_all_budgets(month, year)
        over_budget = []
        for bud in budgets:
            progress = app.service.get_budget_progress(bud["category_id"], bud["month"], bud["year"])
            if progress["percent"] > 100:
                cat_name = bud["category"] or "Всі категорії"
                over_budget.append(f"{cat_name}: {progress['percent']:.1f}%")
        if over_budget:
            messagebox.showwarning(
                "Перевищення бюджету",
                "Наступні категорії перевищили бюджет:\n" + "\n".join(over_budget),
            )


def manage_bank_accounts_dialog(app) -> None:
    win = tk.Toplevel(app.root)
    win.title("Банківські рахунки")
    win.geometry("650x500")

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    form = ttk.LabelFrame(frame, text="Новий рахунок")
    form.pack(fill=tk.X, pady=(0, 10))

    name_var = tk.StringVar()
    amount_var = tk.StringVar()
    rate_var = tk.StringVar()

    row1 = ttk.Frame(form)
    row1.pack(fill=tk.X, padx=10, pady=4)
    ttk.Label(row1, text="Назва банку / рахунку:").pack(side=tk.LEFT)
    ttk.Entry(row1, textvariable=name_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

    row2 = ttk.Frame(form)
    row2.pack(fill=tk.X, padx=10, pady=4)
    ttk.Label(row2, text="Початкова сума:").pack(side=tk.LEFT)
    vcmd = (app.root.register(app._validate_decimal_input), "%P")
    ttk.Entry(row2, textvariable=amount_var, width=12, validate="key", validatecommand=vcmd).pack(side=tk.LEFT, padx=(5, 15))
    ttk.Label(row2, text="Річний %:").pack(side=tk.LEFT)
    ttk.Entry(row2, textvariable=rate_var, width=8, validate="key", validatecommand=vcmd).pack(side=tk.LEFT, padx=(5, 0))

    btn_row = ttk.Frame(form)
    btn_row.pack(fill=tk.X, padx=10, pady=(4, 4))

    columns = ("id", "name", "balance", "rate", "monthly_profit", "created_at")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=15)
    for col, text in zip(columns, ["ID", "Назва", "Баланс", "Річний %", "Прибуток/міс", "Створено"]):
        tree.heading(col, text=text)
        width = 60 if col in ("id", "rate") else 140
        tree.column(col, width=width, anchor=tk.W)
    tree.column("id", width=40, stretch=False)
    tree.pack(fill=tk.BOTH, expand=True, pady=(5, 10))

    scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    tree.configure(yscrollcommand=scroll.set)

    def reload_accounts() -> None:
        for row_id in tree.get_children():
            tree.delete(row_id)
        accounts = app.db.list_bank_accounts()
        sym = app._currency_symbol()
        for acc in accounts:
            bal = app._convert_from_uah(float(acc["balance"] or 0.0))
            rate = float(acc["interest_rate"] or 0.0)
            monthly_profit = bal * rate / 100.0 / 12.0
            tree.insert(
                "",
                tk.END,
                values=(
                    acc["id"],
                    acc["name"],
                    f"{bal:.2f} {sym}",
                    f"{rate:.2f}%",
                    f"{monthly_profit:.2f} {sym}",
                    acc["created_at"],
                ),
            )

    def add_account() -> None:
        try:
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("Помилка", "Вкажіть назву рахунку")
                return
            amount = float(amount_var.get())
            if amount <= 0:
                messagebox.showwarning("Помилка", "Сума має бути додатною")
                return
            rate = float(rate_var.get())
            amount_uah = app._convert_to_uah(amount)
            app.db.add_bank_account(name, amount_uah, rate)
            reload_accounts()
            app.refresh()
            name_var.set("")
            amount_var.set("")
            rate_var.set("")
        except Exception as e:
            messagebox.showerror("Помилка", f"Невірні дані: {e}")

    def delete_account() -> None:
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Нічого не вибрано", "Виберіть рахунок для видалення")
            return
        item = tree.item(sel[0])
        values = item.get("values", [])
        if not values:
            return
        acc_id = int(values[0])
        if not messagebox.askyesno("Підтвердження", "Видалити вибраний рахунок?"):
            return
        app.db.delete_bank_account(acc_id)
        reload_accounts()
        app.refresh()

    def accrue_interest() -> None:
        updated = app.db.accrue_monthly_interest()
        reload_accounts()
        app.refresh()
        messagebox.showinfo("Нарахування відсотків", f"Оновлено {updated} рахунків(и)")

    ttk.Button(btn_row, text="Створити рахунок", command=add_account).pack(side=tk.LEFT)
    ttk.Button(btn_row, text="Видалити вибраний", command=delete_account).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_row, text="Нарахувати щомісячний відсоток", command=accrue_interest).pack(side=tk.LEFT, padx=5)

    reload_accounts()
