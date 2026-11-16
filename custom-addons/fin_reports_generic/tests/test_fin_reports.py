from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestFinReports(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.profit = cls.env.ref('fin_reports_generic.profit_and_loss')
        cls.balance = cls.env.ref('fin_reports_generic.balance_sheet')
        cls.cash = cls.env.ref('fin_reports_generic.cash_flow')
        cls.date_from = fields.Date.from_string('2026-01-01')
        cls.date_to = fields.Date.from_string('2026-12-31')

    def _options(self):
        return {
            'date_from': self.date_from,
            'date_to': self.date_to,
            'company_ids': self.env.company.ids,
            'posted_only': True,
            'currency': self.env.company.currency_id,
        }

    def _post(self, entry_date, rows):
        self.env['account.move'].create({
            'move_type': 'entry',
            'date': entry_date,
            'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [
                Command.create({
                    'name': 'fin test',
                    'account_id': account.id,
                    'partner_id': partner.id if partner else False,
                    'debit': debit,
                    'credit': credit,
                })
                for account, debit, credit, partner in rows
            ],
        }).action_post()

    def test_profit_balance_and_cash(self):
        options = self._options()
        before_p = self.profit._fin_compute_line_values(options)
        before_b = self.balance._fin_compute_line_values(options)
        before_c = self.cash._fin_compute_line_values(options)
        bank = self.company_data['default_journal_bank'].default_account_id
        receivable = self.company_data['default_account_receivable']
        payable = self.company_data['default_account_payable']
        income = self.company_data['default_account_revenue']
        expense = self.company_data['default_account_expense']
        self._post('2026-03-15', [
            (receivable, 1000.0, 0.0, self.partner_a),
            (income, 0.0, 1000.0, False),
        ])
        self._post('2026-03-16', [
            (expense, 200.0, 0.0, False),
            (payable, 0.0, 200.0, self.partner_a),
        ])
        self._post('2026-03-20', [
            (bank, 400.0, 0.0, False),
            (receivable, 0.0, 400.0, self.partner_a),
        ])
        self._post('2026-03-21', [
            (payable, 50.0, 0.0, self.partner_a),
            (bank, 0.0, 50.0, False),
        ])
        profit = self.profit._fin_compute_line_values(options)
        balance = self.balance._fin_compute_line_values(options)
        cash = self.cash._fin_compute_line_values(options)
        self.assertAlmostEqual(profit['NEP']['balance'] - before_p['NEP']['balance'], 800.0, places=2)
        self.assertAlmostEqual(balance['CYE']['balance'] - before_b['CYE']['balance'], 800.0, places=2)
        self.assertAlmostEqual(balance['ASSETS']['balance'], balance['LEQ']['balance'], places=2)
        self.assertAlmostEqual(
            cash['CFC']['balance'] - cash['CFOB']['balance'],
            cash['TOTAL']['balance'],
            places=2,
        )
        self.assertAlmostEqual(cash['CFC']['balance'] - before_c['CFC']['balance'], 350.0, places=2)

    def test_variant_selection(self):
        company = self.env.company
        self.assertEqual(self.balance._fin_get_report_for_company(company), self.balance)
        schedule3 = self.env.ref('l10n_in_fin_reports_sch3.balance_sheet_in_sch3', raise_if_not_found=False)
        if not schedule3:
            self.skipTest('India Schedule III module is not installed')
        company.chart_template = 'in_sch3'
        self.assertEqual(self.balance._fin_get_report_for_company(company), schedule3)
