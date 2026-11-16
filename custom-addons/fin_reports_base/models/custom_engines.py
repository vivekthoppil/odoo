from odoo import models
from odoo.tools import date_utils


class AccountReport(models.Model):
    _inherit = 'account.report'

    def _fin_custom_current_year_earnings(self, expression, options, values):
        """Income and expense of the current fiscal year, shown as equity earnings."""
        return self._fin_earnings_amount(options, current_year=True)

    def _fin_custom_previous_years_earnings(self, expression, options, values):
        """Income and expense before the current fiscal year, shown as prior earnings."""
        return self._fin_earnings_amount(options, current_year=False)

    def _fin_earnings_amount(self, options, current_year):
        income_types = [
            'income', 'income_other', 'expense', 'expense_direct_cost',
            'expense_depreciation', 'expense_other', 'equity_unaffected',
        ]
        total = 0.0
        date_to = options['date_to']
        for company in self.env['res.company'].browse(options['company_ids']):
            fiscal_start, _fiscal_end = date_utils.get_fiscal_year(
                date_to,
                day=company.fiscalyear_last_day,
                month=int(company.fiscalyear_last_month),
            )
            domain = [
                ('company_id', '=', company.id),
                ('account_id.account_type', 'in', income_types),
            ]
            if options.get('posted_only', True):
                domain.append(('parent_state', '=', 'posted'))
            else:
                domain.append(('parent_state', 'in', ('posted', 'draft')))
            if current_year:
                domain += [('date', '>=', fiscal_start), ('date', '<=', date_to)]
            else:
                domain.append(('date', '<', fiscal_start))
            total -= self._fin_sum_balance(domain)
        return total

    def _fin_custom_cash_flow_net_profit(self, expression, options, values):
        """Net profit from the generic Profit and Loss for the same options."""
        report = self.env.ref('fin_reports_generic.profit_and_loss')
        profit = report._fin_get_report_for_company(self.env['res.company'].browse(options['company_ids'][:1]))
        profit_values = profit._fin_compute_line_values(options)
        return profit_values.get('NEP', {}).get('balance', 0.0)

    def _fin_custom_cash_unreconciled(self, expression, options, values):
        """Gap between the change in cash and the classified cash-flow sections."""
        classified = (
            values.get('CFO', {}).get('balance', 0.0)
            + values.get('CFI', {}).get('balance', 0.0)
            + values.get('CFF', {}).get('balance', 0.0)
        )
        change_in_cash = values.get('CFC', {}).get('balance', 0.0) - values.get('CFOB', {}).get('balance', 0.0)
        return change_in_cash - classified
