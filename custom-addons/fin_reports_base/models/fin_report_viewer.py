from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tools.safe_eval import safe_eval


class FinReportViewer(models.TransientModel):
    _name = 'fin.report.viewer'
    _description = 'Financial Statement'
    _rec_name = 'name'

    name = fields.Char(compute='_compute_name', store=True)
    report_id = fields.Many2one('account.report', required=True)
    root_report_id = fields.Many2one('account.report')
    company_id = fields.Many2one('res.company', compute='_compute_company_currency')
    currency_id = fields.Many2one('res.currency', compute='_compute_company_currency')
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    comparison_date_from = fields.Date()
    comparison_date_to = fields.Date()
    use_comparison = fields.Boolean(string='Comparison')
    posted_only = fields.Boolean(string='Posted Entries', default=True)
    warning_unposted = fields.Char(readonly=True)
    available_report_ids = fields.Many2many('account.report', compute='_compute_available_reports')
    line_ids = fields.One2many('fin.report.viewer.line', 'viewer_id', string='Lines')

    @api.depends('report_id', 'report_id.name')
    def _compute_name(self):
        for viewer in self:
            viewer.name = viewer.report_id.display_name or viewer.report_id.name or _('Financial Statement')

    @api.depends_context('allowed_company_ids')
    def _compute_company_currency(self):
        for viewer in self:
            viewer.company_id = self.env.company
            viewer.currency_id = self.env.company.currency_id

    @api.depends('root_report_id', 'report_id')
    @api.depends_context('allowed_company_ids')
    def _compute_available_reports(self):
        for viewer in self:
            root = viewer.root_report_id or viewer.report_id.root_report_id or viewer.report_id
            reports = root | root.variant_report_ids.filtered(
                lambda report: report._fin_is_available_for(self.env.company)
            )
            viewer.available_report_ids = reports

    @api.model
    def action_open_report(self, xmlid):
        """Open the statement page for a root report xml id."""
        root = self.env.ref(xmlid)
        report = root._fin_get_report_for_company(self.env.company)
        date_from, date_to = report._fin_default_dates(self.env.company)
        comparison_to = date_from - timedelta(days=1)
        comparison_from = comparison_to - timedelta(days=(date_to - date_from).days)
        viewer = self.create({
            'report_id': report.id,
            'root_report_id': root.id,
            'date_from': date_from,
            'date_to': date_to,
            'comparison_date_from': comparison_from,
            'comparison_date_to': comparison_to,
        })
        viewer._compute_lines()
        return viewer._action_open()

    def _options(self, date_from=None, date_to=None):
        self.ensure_one()
        return {
            'date_from': date_from or self.date_from,
            'date_to': date_to or self.date_to,
            'company_ids': self.env.companies.ids,
            'posted_only': self.posted_only,
            'currency': self.env.company.currency_id,
        }

    def _compute_lines(self):
        for viewer in self:
            report = viewer.report_id._fin_get_report_for_company(self.env.company)
            if report != viewer.report_id:
                viewer.report_id = report
            options = viewer._options()
            rows = report._fin_build_result_rows(options)
            comparison_map = {}
            if viewer.use_comparison and viewer.comparison_date_from and viewer.comparison_date_to:
                comparison_rows = report._fin_build_result_rows(viewer._options(
                    viewer.comparison_date_from, viewer.comparison_date_to,
                ))
                comparison_map = {row['code']: row['balance'] for row in comparison_rows}
            commands = [Command.clear()]
            sequence = 10
            for row in rows:
                comparison = comparison_map.get(row['code'], 0.0) if comparison_map else 0.0
                commands.append(Command.create({
                    'sequence': sequence,
                    'name': row['name'],
                    'code': row['code'],
                    'level': row['level'],
                    'balance': row['balance'],
                    'comparison': comparison if viewer.use_comparison else 0.0,
                    'is_section': row['is_section'],
                    'report_line_id': row['line_id'],
                    'drill_domain': repr(row['drill_domain']) if row['drill_domain'] else False,
                }))
                sequence += 10
            warning = False
            if report._fin_has_unposted(options):
                warning = _("There are unposted Journal Entries prior or included in this period.")
            viewer.write({
                'line_ids': commands,
                'warning_unposted': warning,
            })

    def _action_open(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name or self.report_id.name,
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {'create': False},
        }

    def action_refresh(self):
        self.ensure_one()
        self._compute_lines()
        return self._action_open()

    def action_print_pdf(self):
        self.ensure_one()
        self._compute_lines()
        return self.env.ref('fin_reports_base.action_report_fin_statement').report_action(self)

class FinReportViewerLine(models.TransientModel):
    _name = 'fin.report.viewer.line'
    _description = 'Financial Statement Line'
    _order = 'sequence, id'

    viewer_id = fields.Many2one('fin.report.viewer', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='viewer_id.currency_id')
    sequence = fields.Integer()
    name = fields.Char(required=True)
    code = fields.Char()
    level = fields.Integer()
    display_label = fields.Char(compute='_compute_display_label')
    balance = fields.Monetary()
    comparison = fields.Monetary()
    is_section = fields.Boolean()
    report_line_id = fields.Many2one('account.report.line')
    drill_domain = fields.Char()

    @api.depends('name', 'level')
    def _compute_display_label(self):
        for line in self:
            line.display_label = ('\u00a0' * (line.level * 4)) + (line.name or '')

    def action_open_items(self):
        self.ensure_one()
        if not self.drill_domain:
            raise UserError(_("This line has no journal items to open."))
        domain = safe_eval(self.drill_domain)
        viewer = self.viewer_id
        options = viewer._options()
        expression = self.report_line_id.expression_ids.filtered(lambda expr: expr.label == 'balance' and expr.engine == 'domain')[:1]
        date_scope = expression.date_scope if expression else 'strict_range'
        full_domain = viewer.report_id._fin_aml_domain(options, date_scope) + domain
        return {
            'type': 'ir.actions.act_window',
            'name': _('Journal Items'),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'domain': full_domain,
        }
