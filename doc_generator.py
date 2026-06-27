import os
import json
from copy import copy
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import config


def generate_document(doc_type, data, package_id):
    generators = {
        'ks2': _generate_ks2,
        'ks3': _generate_ks3,
        'invoice': _generate_invoice,
        'raw_material_report': _generate_raw_material_report,
    }
    gen = generators.get(doc_type)
    if not gen:
        return None
    return gen(data, package_id)


def _ensure_dir(package_id):
    folder = os.path.join(config.PACKAGES_FOLDER, str(package_id))
    os.makedirs(folder, exist_ok=True)
    return folder


def _generate_ks2(data, package_id):
    folder = _ensure_dir(package_id)
    filename = f'ks2_{package_id}.xlsx'
    filepath = os.path.join(folder, filename)

    wb = Workbook()
    ws = wb.active
    ws.title = 'КС-2'

    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hf = Font(bold=True, size=10)
    sf = Font(size=9)
    lf = Font(bold=True, size=9)

    ws.column_dimensions['A'].width = 5
    ws.column_dimensions['B'].width = 10
    ws.column_dimensions['C'].width = 35
    ws.column_dimensions['D'].width = 12
    ws.column_dimensions['E'].width = 8
    ws.column_dimensions['F'].width = 12
    ws.column_dimensions['G'].width = 14
    ws.column_dimensions['H'].width = 16

    ws.merge_cells('A1:H1')
    ws['A1'] = 'АКТ О ПРИЁМКЕ ВЫПОЛНЕННЫХ РАБОТ'
    ws['A1'].font = Font(bold=True, size=13)
    ws['A1'].alignment = Alignment(horizontal='center')

    ws.merge_cells('A2:H2')
    act_num = data.get('act_number', '')
    act_date = data.get('act_date', '')
    ws['A2'] = f'№ {act_num} от {act_date}'
    ws['A2'].font = Font(size=10)
    ws['A2'].alignment = Alignment(horizontal='center')

    row = 4
    period = ''
    if data.get('period_from') or data.get('period_to'):
        period = f"с {data.get('period_from', '')} по {data.get('period_to', '')}"

    header_lines = [
        ('Инвестор:', f"{data.get('investor_name', '')}  ИНН {data.get('investor_inn', '')}  КПП {data.get('investor_kpp', '')}"),
        ('', f"Адрес: {data.get('investor_address', '')}  ОКПО: {data.get('investor_okpo', '')}  Тел: {data.get('investor_phone', '')}"),
        ('Заказчик (Генподрядчик):', f"{data.get('customer_name', '')}  ИНН {data.get('customer_inn', '')}  КПП {data.get('customer_kpp', '')}"),
        ('', f"Адрес: {data.get('customer_address', '')}  ОКПО: {data.get('customer_okpo', '')}"),
        ('Подрядчик (Субподрядчик):', f"{data.get('contractor_name', '')}  ИНН {data.get('contractor_inn', '')}  КПП {data.get('contractor_kpp', '')}"),
        ('', f"Адрес: {data.get('contractor_address', '')}  ОКПО: {data.get('contractor_okpo', '')}"),
        ('Стройка:', data.get('construction_name', '')),
        ('Адрес стройки:', data.get('construction_address', '')),
        ('Объект:', data.get('object_name', '')),
        ('Договор №:', f"{data.get('contract_number', '')} от {data.get('contract_date', '')}"),
        ('Сметная стоимость:', f"{data.get('smeta_cost', '')} руб."),
        ('Отчётный период:', period),
    ]
    for label, val in header_lines:
        ws[f'A{row}'] = label
        ws[f'A{row}'].font = lf
        ws.merge_cells(f'B{row}:H{row}')
        ws[f'B{row}'] = val
        ws[f'B{row}'].font = sf
        row += 1

    row += 1
    headers = ['№ п/п', '№ по смете', 'Наименование работ', '№ расценки', 'Ед.', 'Кол-во', 'Цена (₽)', 'Сумма (₽)']
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=i + 1, value=h)
        cell.font = hf
        cell.border = border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    row += 1
    items = data.get('items', [])
    total = 0
    for idx, item in enumerate(items, 1):
        qty = _to_float(item.get('quantity', 0))
        price = _to_float(item.get('price', 0))
        amount = qty * price
        total += amount

        ws.cell(row=row, column=1, value=idx).border = border
        ws.cell(row=row, column=2, value=item.get('smeta_num', '')).border = border
        ws.cell(row=row, column=3, value=item.get('name', '')).border = border
        ws.cell(row=row, column=4, value=item.get('rate_num', '')).border = border
        ws.cell(row=row, column=5, value=item.get('unit', '')).border = border
        c = ws.cell(row=row, column=6, value=qty); c.border = border; c.number_format = '#,##0.00'
        c = ws.cell(row=row, column=7, value=price); c.border = border; c.number_format = '#,##0.00'
        c = ws.cell(row=row, column=8, value=amount); c.border = border; c.number_format = '#,##0.00'
        row += 1

    ws.merge_cells(f'A{row}:G{row}')
    ws[f'A{row}'] = 'Итого:'
    ws[f'A{row}'].font = hf
    ws[f'A{row}'].alignment = Alignment(horizontal='right')
    ws[f'A{row}'].border = border
    c = ws.cell(row=row, column=8, value=total); c.font = hf; c.border = border; c.number_format = '#,##0.00'

    vat_mode = data.get('vat_mode', 'none')
    vat_rate = _to_float(data.get('vat_rate', 20))
    row += 1

    if vat_mode == 'on_top':
        vat_amount = total * vat_rate / 100
        grand_total = total + vat_amount
    elif vat_mode == 'included':
        vat_amount = total * vat_rate / (100 + vat_rate)
        grand_total = total
    else:
        vat_amount = 0
        grand_total = total

    if vat_mode != 'none':
        ws.merge_cells(f'A{row}:G{row}')
        ws[f'A{row}'] = f'НДС {int(vat_rate)}%:'
        ws[f'A{row}'].alignment = Alignment(horizontal='right')
        c = ws.cell(row=row, column=8, value=round(vat_amount, 2)); c.number_format = '#,##0.00'
        row += 1

    ws.merge_cells(f'A{row}:G{row}')
    ws[f'A{row}'] = 'Всего с НДС:' if vat_mode != 'none' else 'Всего:'
    ws[f'A{row}'].font = Font(bold=True, size=11)
    ws[f'A{row}'].alignment = Alignment(horizontal='right')
    c = ws.cell(row=row, column=8, value=round(grand_total, 2))
    c.font = Font(bold=True, size=11); c.number_format = '#,##0.00'

    row += 2
    con_pos = data.get('contractor_rep_position', '')
    con_name = data.get('contractor_rep_name', '')
    cust_pos = data.get('customer_rep_position', '')
    cust_name = data.get('customer_rep_name', '')
    ws[f'A{row}'] = f'Сдал: {con_pos}'
    ws[f'E{row}'] = f'Принял: {cust_pos}'
    row += 1
    ws[f'A{row}'] = f'__________ {con_name}'
    ws[f'E{row}'] = f'__________ {cust_name}'

    wb.save(filepath)
    return filename


def _generate_ks3(data, package_id):
    folder = _ensure_dir(package_id)
    filename = f'ks3_{package_id}.xlsx'
    filepath = os.path.join(folder, filename)

    wb = Workbook()
    ws = wb.active
    ws.title = 'КС-3'

    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    lf = Font(bold=True, size=9)
    sf = Font(size=9)

    ws.column_dimensions['A'].width = 5
    ws.column_dimensions['B'].width = 8
    ws.column_dimensions['C'].width = 32
    ws.column_dimensions['D'].width = 18
    ws.column_dimensions['E'].width = 18
    ws.column_dimensions['F'].width = 18

    ws.merge_cells('A1:F1')
    ws['A1'] = 'СПРАВКА О СТОИМОСТИ ВЫПОЛНЕННЫХ РАБОТ И ЗАТРАТ'
    ws['A1'].font = Font(bold=True, size=13)
    ws['A1'].alignment = Alignment(horizontal='center')

    doc_num = data.get('doc_number', '')
    doc_date = data.get('doc_date', '')
    ws.merge_cells('A2:F2')
    ws['A2'] = f'№ {doc_num} от {doc_date}'
    ws['A2'].font = Font(size=10)
    ws['A2'].alignment = Alignment(horizontal='center')

    row = 4
    period = ''
    if data.get('period_from') or data.get('period_to'):
        period = f"с {data.get('period_from', '')} по {data.get('period_to', '')}"

    header_lines = [
        ('Заказчик (Генподрядчик):', f"{data.get('customer_name', '')}  ИНН {data.get('customer_inn', '')}  КПП {data.get('customer_kpp', '')}"),
        ('', f"Адрес: {data.get('customer_address', '')}  ОКПО: {data.get('customer_okpo', '')}"),
        ('Подрядчик (Субподрядчик):', f"{data.get('contractor_name', '')}  ИНН {data.get('contractor_inn', '')}  КПП {data.get('contractor_kpp', '')}"),
        ('', f"Адрес: {data.get('contractor_address', '')}  ОКПО: {data.get('contractor_okpo', '')}"),
        ('Стройка:', data.get('construction_name', '')),
        ('Адрес стройки:', data.get('construction_address', '')),
        ('Договор №:', f"{data.get('contract_number', '')} от {data.get('contract_date', '')}"),
        ('Сметная стоимость:', f"{data.get('smeta_cost', '')} руб."),
        ('Отчётный период:', period),
    ]
    for label, val in header_lines:
        ws[f'A{row}'] = label
        ws[f'A{row}'].font = lf
        ws.merge_cells(f'B{row}:F{row}')
        ws[f'B{row}'] = val
        ws[f'B{row}'].font = sf
        row += 1

    row += 1
    for i, h in enumerate(['№ п/п', 'Код', 'Наименование работ и затрат',
                            'Стоимость с начала работ (₽)', 'Стоимость с начала года (₽)',
                            'В т.ч. за отчётный период (₽)']):
        cell = ws.cell(row=row, column=i + 1, value=h)
        cell.font = Font(bold=True, size=9)
        cell.border = border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    row += 1
    items = data.get('items', [])
    total_cumulative = 0
    total_year = 0
    total_period = 0
    for idx, item in enumerate(items, 1):
        cum = _to_float(item.get('cumulative', 0))
        year = _to_float(item.get('year_amount', 0))
        period = _to_float(item.get('period_amount', 0))
        total_cumulative += cum
        total_year += year
        total_period += period
        ws.cell(row=row, column=1, value=idx).border = border
        ws.cell(row=row, column=2, value=item.get('code', '')).border = border
        ws.cell(row=row, column=3, value=item.get('name', '')).border = border
        c = ws.cell(row=row, column=4, value=cum); c.border = border; c.number_format = '#,##0.00'
        c = ws.cell(row=row, column=5, value=year); c.border = border; c.number_format = '#,##0.00'
        c = ws.cell(row=row, column=6, value=period); c.border = border; c.number_format = '#,##0.00'
        row += 1

    hf = Font(bold=True, size=10)
    ws.merge_cells(f'A{row}:C{row}')
    ws[f'A{row}'] = 'Итого:'
    ws[f'A{row}'].font = hf; ws[f'A{row}'].alignment = Alignment(horizontal='right')
    for col, val in [(4, total_cumulative), (5, total_year), (6, total_period)]:
        c = ws.cell(row=row, column=col, value=val)
        c.font = hf; c.border = border; c.number_format = '#,##0.00'

    vat_mode = data.get('vat_mode', 'none')
    vat_rate = _to_float(data.get('vat_rate', 20))
    row += 1
    if vat_mode == 'on_top':
        vat_p = total_period * vat_rate / 100
        grand_p = total_period + vat_p
    elif vat_mode == 'included':
        vat_p = total_period * vat_rate / (100 + vat_rate)
        grand_p = total_period
    else:
        vat_p = 0
        grand_p = total_period

    if vat_mode != 'none':
        ws.merge_cells(f'A{row}:C{row}')
        ws[f'A{row}'] = f'НДС {int(vat_rate)}%:'
        ws[f'A{row}'].alignment = Alignment(horizontal='right')
        c = ws.cell(row=row, column=6, value=round(vat_p, 2)); c.number_format = '#,##0.00'
        row += 1

    ws.merge_cells(f'A{row}:C{row}')
    ws[f'A{row}'] = 'Всего с НДС:' if vat_mode != 'none' else 'Всего:'
    ws[f'A{row}'].font = Font(bold=True, size=11)
    ws[f'A{row}'].alignment = Alignment(horizontal='right')
    c = ws.cell(row=row, column=6, value=round(grand_p, 2))
    c.font = Font(bold=True, size=11); c.number_format = '#,##0.00'

    row += 2
    ws[f'A{row}'] = 'Заказчик (Генподрядчик):'
    ws[f'D{row}'] = 'Подрядчик (Субподрядчик):'

    wb.save(filepath)
    return filename


def _generate_invoice(data, package_id):
    folder = _ensure_dir(package_id)
    filename = f'invoice_{package_id}.xlsx'
    filepath = os.path.join(folder, filename)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Счёт-фактура'

    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    lf = Font(bold=True, size=9)
    sf = Font(size=9)

    for c, w in [('A', 5), ('B', 30), ('C', 7), ('D', 10), ('E', 12),
                 ('F', 14), ('G', 8), ('H', 12), ('I', 14)]:
        ws.column_dimensions[c].width = w

    doc_num = data.get('doc_number', '')
    doc_date = data.get('doc_date', '')
    ws.merge_cells('A1:I1')
    ws['A1'] = f'СЧЁТ-ФАКТУРА № {doc_num} от {doc_date}'
    ws['A1'].font = Font(bold=True, size=12)
    ws['A1'].alignment = Alignment(horizontal='center')

    corr = data.get('correction_number', '')
    if corr:
        ws.merge_cells('A2:I2')
        ws['A2'] = f'Исправление № {corr} от {data.get("correction_date", "")}'
        ws['A2'].font = sf
        ws['A2'].alignment = Alignment(horizontal='center')

    row = 4
    header_lines = [
        ('Продавец:', f"{data.get('contractor_name', '')}"),
        ('Адрес:', data.get('contractor_address', '')),
        ('ИНН/КПП продавца:', f"{data.get('contractor_inn', '')}/{data.get('contractor_kpp', '')}"),
        ('Грузоотправитель:', data.get('shipper', '') or 'он же'),
        ('Грузополучатель:', data.get('consignee', '') or 'он же'),
        ('К платёжно-расчётному документу:', data.get('payment_doc', '') or '—'),
        ('Покупатель:', data.get('customer_name', '')),
        ('Адрес:', data.get('customer_address', '')),
        ('ИНН/КПП покупателя:', f"{data.get('customer_inn', '')}/{data.get('customer_kpp', '')}"),
        ('Валюта:', data.get('currency', 'Российский рубль, 643')),
    ]
    for label, val in header_lines:
        ws[f'A{row}'] = label
        ws[f'A{row}'].font = lf
        ws.merge_cells(f'B{row}:I{row}')
        ws[f'B{row}'] = val
        ws[f'B{row}'].font = sf
        row += 1

    row += 1
    headers = ['№', 'Наименование товара\n(работ, услуг)', 'Ед.', 'Кол-во',
               'Цена (₽)', 'Стоимость\nбез налога', 'Ставка\nНДС',
               'Сумма\nналога', 'Стоимость\nс налогом']
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=i + 1, value=h)
        cell.font = Font(bold=True, size=8)
        cell.border = border
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    row += 1
    items = data.get('items', [])
    vat_mode = data.get('vat_mode', 'on_top')
    vat_rate = _to_float(data.get('vat_rate', 20))
    total_no_vat = 0
    total_vat = 0
    total_with_vat = 0

    for idx, item in enumerate(items, 1):
        qty = _to_float(item.get('quantity', 0))
        price = _to_float(item.get('price', 0))
        base = qty * price

        if vat_mode == 'on_top':
            line_vat = base * vat_rate / 100
            amount_no_vat = base
            amount_with_vat = base + line_vat
        elif vat_mode == 'included':
            line_vat = base * vat_rate / (100 + vat_rate)
            amount_no_vat = base - line_vat
            amount_with_vat = base
        else:
            line_vat = 0
            amount_no_vat = base
            amount_with_vat = base

        total_no_vat += amount_no_vat
        total_vat += line_vat
        total_with_vat += amount_with_vat

        vat_label = 'Без НДС' if vat_mode == 'none' else f'{int(vat_rate)}%'

        ws.cell(row=row, column=1, value=idx).border = border
        ws.cell(row=row, column=2, value=item.get('name', '')).border = border
        ws.cell(row=row, column=3, value=item.get('unit', '')).border = border
        c = ws.cell(row=row, column=4, value=qty); c.border = border; c.number_format = '#,##0.00'
        c = ws.cell(row=row, column=5, value=price); c.border = border; c.number_format = '#,##0.00'
        c = ws.cell(row=row, column=6, value=round(amount_no_vat, 2)); c.border = border; c.number_format = '#,##0.00'
        ws.cell(row=row, column=7, value=vat_label).border = border
        c = ws.cell(row=row, column=8, value=round(line_vat, 2)); c.border = border; c.number_format = '#,##0.00'
        c = ws.cell(row=row, column=9, value=round(amount_with_vat, 2)); c.border = border; c.number_format = '#,##0.00'
        row += 1

    hf = Font(bold=True, size=10)
    ws.merge_cells(f'A{row}:E{row}')
    ws[f'A{row}'] = 'Всего к оплате:'
    ws[f'A{row}'].font = hf
    ws[f'A{row}'].alignment = Alignment(horizontal='right')
    for col, val in [(6, round(total_no_vat, 2)), (8, round(total_vat, 2)), (9, round(total_with_vat, 2))]:
        c = ws.cell(row=row, column=col, value=val)
        c.font = hf; c.border = border; c.number_format = '#,##0.00'

    row += 2
    head = data.get('contractor_head', '')
    accountant = data.get('contractor_accountant', '')
    ws[f'A{row}'] = f'Руководитель организации: __________ {head}'
    ws[f'A{row}'].font = sf
    row += 1
    ws[f'A{row}'] = f'Главный бухгалтер: __________ {accountant}'
    ws[f'A{row}'].font = sf

    wb.save(filepath)
    return filename


def _generate_raw_material_report(data, package_id):
    folder = _ensure_dir(package_id)
    filename = f'raw_material_{package_id}.xlsx'
    filepath = os.path.join(folder, filename)

    BLANK = os.path.join(config.BASE_DIR, 'templates', 'forms', 'otchet_davalchesky_blank.xlsx')

    if os.path.exists(BLANK):
        wb = load_workbook(BLANK)
        ws = wb.active
        return _fill_blank_template(ws, wb, data, filepath, filename)

    # Fallback if blank not found
    wb = Workbook()
    ws = wb.active
    ws.title = 'Давальческий'
    ws['A1'] = 'ОТЧЁТ (бланк не найден — упрощённый формат)'
    items = data.get('items', [])
    row = 3
    for idx, item in enumerate(items, 1):
        ws.cell(row=row, column=1, value=idx)
        ws.cell(row=row, column=2, value=item.get('name', ''))
        row += 1
    wb.save(filepath)
    return filename


def _fill_blank_template(ws, wb, data, filepath, filename):
    # Blank structure (verified):
    # C2: title (keep)
    # A4:I4 merged — объект (строка 1)
    # A5:I5 merged — объект (строка 2, кадастр)
    # Row 6-13: свободные строки для шапки (не объединены)
    #   6: дата, 8: генподрядчик, 10: подрядчик, 12: договор
    # Row 14: заголовки таблицы
    # Rows 15-35: данные (A:D merged)
    # Row 39: C=Генподрядчик, G=Подрядчик (подписи)
    # Row 51: C, G = __________ /Ф.И.О./

    sf = Font(size=8)

    # Row 4 (merged A4:I4): наименование объекта
    ws['A4'] = f"Объект: {data.get('object_name', '')}"
    ws['A4'].font = sf

    # Row 5 (merged A5:I5): кадастровый номер
    ws['A5'] = f"Кадастровый номер: {data.get('cadastral_number', '')}"
    ws['A5'].font = sf

    # Row 6: дата
    ws['I6'] = data.get('doc_date', '')
    ws['I6'].font = sf

    # Row 8: генподрядчик
    ws['A8'] = f"Генподрядчик: {data.get('customer_name', '')}  ИНН {data.get('customer_inn', '')} КПП {data.get('customer_kpp', '')}"
    ws['A8'].font = sf

    # Row 10: подрядчик
    ws['A10'] = f"Подрядчик: {data.get('contractor_name', '')}  ИНН {data.get('contractor_inn', '')} КПП {data.get('contractor_kpp', '')}"
    ws['A10'].font = sf

    # Row 12: договор
    ws['A12'] = f"Договор: № {data.get('contract_number', '')} от {data.get('contract_date', '')}"
    ws['A12'].font = sf

    # Fill data rows starting at row 15
    items = data.get('items', [])
    DATA_START = 15
    SIGN_ROW_OFFSET = 39 - 35  # signatures are at row 39, data ends at 35 in blank

    # If more items than available rows (15-35 = 21 rows), insert rows
    available = 35 - DATA_START + 1  # 21 rows
    needed = len(items)

    if needed > available:
        extra = needed - available
        ws.insert_rows(DATA_START + available, extra)

    for idx, item in enumerate(items):
        r = DATA_START + idx
        beg = _to_float(item.get('beginning', 0))
        issued = _to_float(item.get('issued', 0))
        certified = _to_float(item.get('certified', 0))
        remainder = beg + issued - certified

        # Unmerge A:D for this row if merged, then re-merge
        for mg in list(ws.merged_cells.ranges):
            if mg.min_row == r and mg.min_col == 1 and mg.max_col == 4:
                ws.unmerge_cells(str(mg))
                break

        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        c = ws.cell(row=r, column=1, value=item.get('name', ''))
        c.font = sf

        ws.cell(row=r, column=5, value=item.get('unit', '')).font = sf
        c = ws.cell(row=r, column=6, value=beg); c.font = sf; c.number_format = '#,##0.00'
        c = ws.cell(row=r, column=7, value=issued); c.font = sf; c.number_format = '#,##0.00'
        c = ws.cell(row=r, column=8, value=certified); c.font = sf; c.number_format = '#,##0.00'
        c = ws.cell(row=r, column=9, value=remainder); c.font = sf; c.number_format = '#,##0.00'

    # Fill signatures
    sign_base = DATA_START + max(needed, available) + SIGN_ROW_OFFSET
    # Find the actual signature rows (look for 'Генподрядчик' text)
    for r in range(1, ws.max_row + 1):
        v = ws.cell(row=r, column=3).value
        if v and 'Генподрядчик' in str(v):
            break
    else:
        r = sign_base

    # Write rep names near signature lines
    cust_rep = data.get('customer_rep', '')
    con_rep = data.get('contractor_rep', '')
    # Find the /Ф.И.О./ row
    for sr in range(r, ws.max_row + 1):
        v = ws.cell(row=sr, column=3).value
        if v and 'Ф.И.О.' in str(v):
            ws.cell(row=sr, column=3, value=f'__________ /{cust_rep}/').font = sf
            break
    for sr in range(r, ws.max_row + 1):
        v = ws.cell(row=sr, column=7).value
        if v and 'Ф.И.О.' in str(v):
            ws.cell(row=sr, column=7, value=f'__________ /{con_rep}/').font = sf
            break

    wb.save(filepath)
    return filename


def _to_float(val):
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0
