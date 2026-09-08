"""Business labels and HTML primitives. No accounting formulas."""
import html
from decimal import Decimal


def esc(value):
    return html.escape(str(value) if value is not None else '', quote=True)


def currency(value):
    return f'{Decimal(value) / 100:,.2f}'


def field(name, label, value='', *, kind='text', required=False):
    if kind == 'hidden':
        return f'<input name="{esc(name)}" type="hidden" value="{esc(value)}">'
    return f'<label>{esc(label)}<input name="{esc(name)}" type="{kind}" value="{esc(value)}" {"required" if required else ""}></label>'


def select(name, label, choices, selected=''):
    options = ''.join(f'<option value="{esc(k)}" {"selected" if str(k)==str(selected) else ""}>{esc(v)}</option>' for k,v in choices)
    return f'<label>{esc(label)}<select name="{esc(name)}">{options}</select></label>'


def table(headers, rows):
    if not rows:
        return '<p class="muted">暂无记录</p>'
    return '<div class="table-wrap"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join(f'<td>{value}</td>' for value in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def business_name(row):
    if row['kind'] in ('orders','fulfillment'):
        return f'{row["data"]["order_id"]} / {row["data"]["line_id"]}'
    return row['business_key']
