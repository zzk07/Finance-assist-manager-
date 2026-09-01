"""Бізнес-логіка між UI та базою даних для фінансового менеджера."""

import logging
from dataclasses import dataclass
from datetime import datetime

from db import Database

logger = logging.getLogger(__name__)


@dataclass
class BudgetCheckResult:
    allowed: bool
    percent_after: float
    over_budget: bool
    near_limit: bool


class FinanceService:
    """
    Сервісний шар між UI та базою даних.

    Виносить більш складну бізнес-логіку з Tkinter‑класу, щоб спростити ui.py.
    """

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # ---------- Загальні утиліти / обчислення ----------

    def validate_date(self, date_str: str, fmt: str = "%Y-%m-%d") -> bool:
        """Повертає True якщо рядок відповідає формату дати."""
        try:
            datetime.strptime(date_str, fmt)
            return True
        except Exception:
            return False

    def parse_amount(self, raw_value: str) -> float | None:
        """Повертає float або None якщо рядок не є числом."""
        try:
            return float(raw_value)
        except Exception:
            return None

    def rate_to_uah(self, rates_to_uah: dict[str, float], currency: str) -> float:
        return float(rates_to_uah.get(currency, 1.0)) or 1.0

    def convert_from_uah(self, amount_uah: float, rates_to_uah: dict[str, float], currency: str) -> float:
        """Конвертує суму з гривень в обрану валюту."""
        rate = self.rate_to_uah(rates_to_uah, currency)
        return amount_uah if rate == 1.0 else amount_uah / rate

    def convert_to_uah(self, amount_in_selected: float, rates_to_uah: dict[str, float], currency: str) -> float:
        """Конвертує суму з обраної валюти у гривні."""
        rate = self.rate_to_uah(rates_to_uah, currency)
        return amount_in_selected if rate == 1.0 else amount_in_selected * rate

    def format_amount_from_uah(
        self,
        amount_uah: float,
        rates_to_uah: dict[str, float],
        currency: str,
        currency_symbols: dict[str, str],
    ) -> str:
        converted = self.convert_from_uah(amount_uah, rates_to_uah, currency)
        symbol = currency_symbols.get(currency, "")
        return f"{converted:.2f} {symbol}" if symbol else f"{converted:.2f}"

    def category_name_to_id(self, categories: list[tuple[int, str]], name: str) -> int | None:
        for cid, cname in categories:
            if cname == name:
                return cid
        return None

    # ---------- Транзакції та бюджети ----------

    def check_budget_before_expense(
        self,
        category_id: int | None,
        amount_uah: float,
        year: int,
        month: int,
    ) -> BudgetCheckResult:
        """
        Перевіряє бюджет для витрати і повертає інформацію,
        чи перевищиться/наблизиться ліміт.
        """
        try:
            progress = self.db.get_budget_progress(category_id, month, year)
        except Exception:
            logger.exception("Не вдалося отримати прогрес бюджету")
            return BudgetCheckResult(True, 0.0, False, False)

        budget = progress["budget"]
        if budget <= 0:
            return BudgetCheckResult(True, 0.0, False, False)

        new_spent = progress["spent"] + amount_uah
        percent_after = (new_spent / budget * 100) if budget > 0 else 0.0
        over_budget = percent_after > 100
        near_limit = 80 < percent_after <= 100

        return BudgetCheckResult(
            allowed=not over_budget,
            percent_after=percent_after,
            over_budget=over_budget,
            near_limit=near_limit,
        )

    def get_budget_progress(self, category_id: int | None, month: int, year: int) -> dict[str, float]:
        """Безпечна обгортка над Database.get_budget_progress з логуванням помилок."""
        try:
            return self.db.get_budget_progress(category_id, month, year)
        except Exception:
            logger.exception(
                "Помилка отримання прогресу бюджету (category_id=%s, month=%s, year=%s)",
                category_id,
                month,
                year,
            )
            return {"budget": 0.0, "spent": 0.0, "remaining": 0.0, "percent": 0.0}

    # ---------- Статистика / аналітика ----------

    def cash_flow_by_month(self, months: int = 6) -> list[tuple[str, float, float]]:
        """
        Повертає список (місяць, дохід, витрати) для останніх N місяців.
        Логіка агрегації живе в Database.cash_flow_by_month, тут лише делегація.
        """
        try:
            return self.db.cash_flow_by_month(months)
        except AttributeError:
            # fallback, якщо стара версія Database без методу
            logger.exception("Метод cash_flow_by_month відсутній у Database")
            return []
        except Exception:
            logger.exception("Помилка при отриманні Cash Flow з бази")
            return []


