{
    'name': 'Financial Reports',
    'version': '20.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Statement report engine and viewer for Community accounting',
    'description': """
Evaluates Community account.report definitions and opens an interactive
statement page under Invoicing → Reporting → Statement Reports.
    """,
    'author': 'Custom',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/ir.access.csv',
        'views/fin_report_viewer_views.xml',
        'report/fin_report_templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'fin_reports_base/static/src/scss/fin_report.scss',
        ],
    },
    'installable': True,
    'application': False,
}
