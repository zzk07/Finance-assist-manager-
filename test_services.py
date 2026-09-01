import unittest
from unittest.mock import MagicMock
from services import FinanceService


class TestFinanceService(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.service = FinanceService(self.db)

    def test_validate_date_valid(self):
        self.assertTrue(self.service.validate_date("2024-01-15"))

    def test_validate_date_invalid(self):
        self.assertFalse(self.service.validate_date("15-01-2024"))
        self.assertFalse(self.service.validate_date("not-a-date"))

    def test_parse_amount_valid(self):
        self.assertEqual(self.service.parse_amount("123.45"), 123.45)

    def test_parse_amount_invalid(self):
        self.assertIsNone(self.service.parse_amount("abc"))

    def test_convert_to_uah(self):
        rates = {"USD": 40.0, "UAH": 1.0}
        result = self.service.convert_to_uah(10.0, rates, "USD")
        self.assertAlmostEqual(result, 400.0)

    def test_convert_from_uah(self):
        rates = {"USD": 40.0, "UAH": 1.0}
        result = self.service.convert_from_uah(400.0, rates, "USD")
        self.assertAlmostEqual(result, 10.0)

    def test_category_name_to_id_found(self):
        cats = [(1, "Їжа"), (2, "Транспорт")]
        self.assertEqual(self.service.category_name_to_id(cats, "Їжа"), 1)

    def test_category_name_to_id_not_found(self):
        cats = [(1, "Їжа")]
        self.assertIsNone(self.service.category_name_to_id(cats, "Невідома"))

    def test_check_budget_no_budget(self):
        self.db.get_budget_progress.return_value = {
            "budget": 0, "spent": 0, "remaining": 0, "percent": 0
        }
        result = self.service.check_budget_before_expense(1, 100.0, 2024, 1)
        self.assertTrue(result.allowed)
        self.assertFalse(result.over_budget)

    def test_check_budget_over_limit(self):
        self.db.get_budget_progress.return_value = {
            "budget": 100, "spent": 95, "remaining": 5, "percent": 95
        }
        result = self.service.check_budget_before_expense(1, 50.0, 2024, 1)
        self.assertTrue(result.over_budget)
        self.assertFalse(result.allowed)


if __name__ == "__main__":
    unittest.main()
