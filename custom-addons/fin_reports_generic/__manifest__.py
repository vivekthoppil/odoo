{
    'name': 'Financial Reports - Generic Statements',
    'version': '20.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Generic Profit and Loss, Balance Sheet, and Cash Flow',
    'license': 'LGPL-3',
    'depends': ['fin_reports_base'],
    'data': [
        'data/account_report_profit_and_loss.xml',
        'data/account_report_balance_sheet.xml',
        'data/account_report_cash_flow.xml',
        'views/fin_report_menus.xml',
    ],
    'installable': True,
    'application': False,
}
