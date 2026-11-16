import ast
import re
from datetime import date as date_type

from odoo import fields, models, _
from odoo.exceptions import UserError
from odoo.tools import date_utils, float_round
from odoo.tools.float_utils import float_is_zero

_AGG_TOKEN = re.compile(r'[A-Za-z_][\w]*\.[\w]+|[+\-()]|\d+(?:\.\d+)?')
_CODE_LABEL = re.compile(r'^([A-Za-z_][\w]*)\.([\w]+)$')


class AccountReport(models.Model):
    _inherit = 'account.report'

    def _fin_is_available_for(self, company):
        """Return True when this report applies to the company."""
        self.ensure_one()
        if self.availability_condition == 'always':
            return True
        if self.availability_condition == 'country':
            return bool(self.country_id) and self.country_id == company.account_fiscal_country_id
        if self.availability_condition == 'coa':
            return bool(self.chart_template) and self.chart_template == company.chart_template
        return False

    def _fin_get_report_for_company(self, company):
        """Return the chart variant, else country variant, else this report."""
        self.ensure_one()
        root = self.root_report_id or self
        variants = root.variant_report_ids.filtered(lambda report: report._fin_is_available_for(company))
        chart_match = variants.filtered(lambda report: report.availability_condition == 'coa')
        if chart_match:
            return chart_match.sorted(lambda report: (report.sequence, report.id))[:1]
        country_match = variants.filtered(lambda report: report.availability_condition == 'country')
        if country_match:
            return country_match.sorted(lambda report: (report.sequence, report.id))[:1]
        return root

    def _fin_iter_lines(self):
        """Yield report lines in parent-then-child order."""
        self.ensure_one()

        def walk(lines):
            for line in lines.sorted(lambda item: (item.sequence, item.id)):
                yield line
                yield from walk(line.children_ids)

        yield from walk(self.line_ids.filtered(lambda line: not line.parent_id))

    def _fin_default_dates(self, company, today=None):
        """Return (date_from, date_to) from the report opening filter and company fiscal year."""
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        fiscal_start, fiscal_end = date_utils.get_fiscal_year(
            today,
            day=company.fiscalyear_last_day,
            month=int(company.fiscalyear_last_month),
        )
        opening = self.default_opening_date_filter or 'this_month'
        if not self.filter_date_range:
            return fiscal_start, today
        if opening == 'this_year':
            return fiscal_start, min(today, fiscal_end)
        if opening == 'previous_year':
            previous = date_type(fiscal_start.year - 1, fiscal_start.month, min(fiscal_start.day, 28))
            fiscal_start, fiscal_end = date_utils.get_fiscal_year(
                previous,
                day=company.fiscalyear_last_day,
                month=int(company.fiscalyear_last_month),
            )
            return fiscal_start, fiscal_end
        if opening == 'this_quarter':
            quarter = (today.month - 1) // 3
            start_month = quarter * 3 + 1
            return today.replace(month=start_month, day=1), today
        if opening == 'previous_month':
            first = today.replace(day=1)
            date_to = date_type.fromordinal(first.toordinal() - 1)
            return date_to.replace(day=1), date_to
        if opening == 'today':
            return today, today
        return today.replace(day=1), today

    def _fin_aml_domain(self, options, date_scope='strict_range'):
        """Build the move-line domain for a date scope."""
        self.ensure_one()
        company_ids = options['company_ids']
        date_from = options['date_from']
        date_to = options['date_to']
        state_domain = [('parent_state', '=', 'posted')] if options.get('posted_only', True) else [
            ('parent_state', 'in', ('posted', 'draft')),
        ]

        if not self.filter_date_range and date_scope == 'strict_range':
            date_scope = 'from_beginning'

        if date_scope in ('from_fiscalyear', 'to_beginning_of_fiscalyear'):
            company_domains = []
            for company in self.env['res.company'].browse(company_ids):
                fiscal_start, _fiscal_end = date_utils.get_fiscal_year(
                    date_to,
                    day=company.fiscalyear_last_day,
                    month=int(company.fiscalyear_last_month),
                )
                piece = [('company_id', '=', company.id)] + state_domain
                if date_scope == 'from_fiscalyear':
                    piece += [('date', '>=', fiscal_start), ('date', '<=', date_to)]
                else:
                    piece.append(('date', '<', fiscal_start))
                company_domains.append(piece)
            domain = company_domains[0]
            for piece in company_domains[1:]:
                domain = ['|'] + domain + piece
            return domain

        domain = [('company_id', 'in', company_ids)] + state_domain
        if date_scope == 'from_beginning':
            domain.append(('date', '<=', date_to))
        elif date_scope == 'strict_range':
            domain += [('date', '>=', date_from), ('date', '<=', date_to)]
        elif date_scope == 'to_beginning_of_period':
            domain.append(('date', '<', date_from))
        else:
            raise UserError(_("Unsupported date scope %s.", date_scope))
        return domain

    def _fin_sum_balance(self, domain):
        totals = self.env['account.move.line']._read_group(domain, (), ['balance:sum'])
        if not totals:
            return 0.0
        return totals[0][0] or 0.0

    def _fin_eval_domain_expression(self, expression, options):
        formula = expression.formula
        try:
            domain = ast.literal_eval(formula)
        except (SyntaxError, ValueError) as error:
            raise UserError(_("Invalid domain on %(line)s: %(error)s", line=expression.report_line_id.name, error=error)) from error
        if not isinstance(domain, list):
            raise UserError(_("Domain formula on %s must be a list.", expression.report_line_id.name))
        amount = self._fin_sum_balance(self._fin_aml_domain(options, expression.date_scope) + domain)
        if expression.subformula == '-sum':
            amount = -amount
        return amount, domain

    def _fin_eval_aggregation(self, expression, values):
        tokens = _AGG_TOKEN.findall(expression.formula or '')
        if not tokens:
            return 0.0
        if expression.formula.strip() == 'sum_children':
            line = expression.report_line_id
            return sum(values.get(child.code, {}).get('balance', 0.0) for child in line.children_ids)

        def read_value(token):
            match = _CODE_LABEL.match(token)
            if match:
                code, label = match.groups()
                return values.get(code, {}).get(label, 0.0)
            return float(token)

        stack = []
        operators = []

        def apply_operator():
            operator = operators.pop()
            right = stack.pop()
            left = stack.pop()
            stack.append(left + right if operator == '+' else left - right)

        for token in tokens:
            if token in '+-':
                while operators and operators[-1] in '+-':
                    apply_operator()
                operators.append(token)
            elif token == '(':
                operators.append(token)
            elif token == ')':
                while operators and operators[-1] != '(':
                    apply_operator()
                if not operators:
                    raise UserError(_("Invalid aggregation formula on %s.", expression.report_line_id.name))
                operators.pop()
            else:
                stack.append(read_value(token))
        while operators:
            if operators[-1] == '(':
                raise UserError(_("Invalid aggregation formula on %s.", expression.report_line_id.name))
            apply_operator()
        return stack[0] if stack else 0.0

    def _fin_expression_ready(self, expression, values):
        if expression.engine == 'custom' and (expression.formula or '').strip() == 'cash_unreconciled':
            return all(code in values for code in ('CFO', 'CFI', 'CFF', 'CFOB', 'CFC'))
        if expression.engine != 'aggregation':
            return True
        if (expression.formula or '').strip() == 'sum_children':
            return all(child.code in values for child in expression.report_line_id.children_ids)
        for token in _AGG_TOKEN.findall(expression.formula or ''):
            match = _CODE_LABEL.match(token)
            if match and match.group(1) not in values:
                return False
        return True

    def _fin_compute_line_values(self, options):
        """Return {line_code: {'balance': amount, 'domain': domain|False}}."""
        self.ensure_one()
        values = {}
        pending = list(self.line_ids)
        for _iteration in range(len(self.line_ids) + 2):
            if not pending:
                return values
            next_pending = []
            progressed = False
            for line in pending:
                expressions = line.expression_ids.filtered(lambda expr: expr.label == 'balance')
                if expressions and not all(self._fin_expression_ready(expr, values) for expr in expressions):
                    next_pending.append(line)
                    continue
                if not expressions:
                    if any(child.code not in values for child in line.children_ids):
                        next_pending.append(line)
                        continue
                    amount = sum(values[child.code]['balance'] for child in line.children_ids)
                    values[line.code] = {'balance': self._fin_round(amount, options), 'domain': False}
                    progressed = True
                    continue
                amount = 0.0
                drill_domain = False
                for expression in expressions:
                    if expression.engine == 'domain':
                        part, drill_domain = self._fin_eval_domain_expression(expression, options)
                        amount += part
                    elif expression.engine == 'aggregation':
                        amount += self._fin_eval_aggregation(expression, values)
                    elif expression.engine == 'custom':
                        amount += self._fin_eval_custom(expression, options, values)
                    elif expression.engine == 'external':
                        amount += 0.0
                    else:
                        raise UserError(_("Engine %s is not supported on %s.", expression.engine, line.name))
                values[line.code] = {'balance': self._fin_round(amount, options), 'domain': drill_domain}
                progressed = True
            if not progressed:
                stuck = ', '.join(line.code or line.name for line in next_pending)
                raise UserError(_("Could not compute report lines: %s", stuck))
            pending = next_pending
        raise UserError(_("Could not compute the report."))

    def _fin_eval_custom(self, expression, options, values):
        formula = (expression.formula or '').strip()
        if formula.startswith('cash_change:'):
            account_types = [item.strip() for item in formula.split(':', 1)[1].split(',') if item.strip()]
            extra = [('account_id.account_type', 'in', account_types)]
            opening = self._fin_sum_balance(self._fin_aml_domain(options, 'to_beginning_of_period') + extra)
            closing = self._fin_sum_balance(self._fin_aml_domain(options, 'from_beginning') + extra)
            return opening - closing
        method_name = '_fin_custom_%s' % formula
        method = getattr(self, method_name, None)
        if not method:
            raise UserError(_("Missing custom method %s.", method_name))
        return method(expression, options, values)

    def _fin_round(self, amount, options):
        currency = options.get('currency')
        if currency:
            return currency.round(amount or 0.0)
        return float_round(amount or 0.0, precision_digits=2)

    def _fin_has_unposted(self, options):
        domain = [
            ('company_id', 'in', options['company_ids']),
            ('parent_state', '=', 'draft'),
            ('date', '<=', options['date_to']),
        ]
        if self.filter_date_range:
            domain.append(('date', '>=', options['date_from']))
        return bool(self.env['account.move.line'].search(domain, limit=1))

    def _fin_build_result_rows(self, options):
        """Return ordered display rows for the viewer."""
        self.ensure_one()
        values = self._fin_compute_line_values(options)
        rows = []
        for line in self._fin_iter_lines():
            data = values.get(line.code) or {'balance': 0.0, 'domain': False}
            balance = data['balance']
            if line.hide_if_zero and float_is_zero(balance, precision_digits=2):
                continue
            level = 0
            parent = line.parent_id
            while parent:
                level += 1
                parent = parent.parent_id
            rows.append({
                'name': line.name,
                'code': line.code,
                'level': level,
                'balance': balance,
                'is_section': bool(line.children_ids) or not data['domain'],
                'drill_domain': data['domain'],
                'line_id': line.id,
            })
        return rows

