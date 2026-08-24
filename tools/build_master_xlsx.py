"""
Build the master workbook exports/GC1972_Territories.xlsx from scratch:
an 'All Territories' tab (the single hand-editable source of ID / Name /
Faction / Value / SC for all 87 land territories) plus 6 live faction
subtabs and an 'Unassigned' subtab that read from it via formulas.

This replaces the old rework_xlsx.py, which depended on a pre-existing
uploaded_edited.xlsx as the only place the 87-territory data existed.
Here that data comes from data/territories.json, so the xlsx is a pure
generated export/view -- nothing about game data lives only in the
spreadsheet.

Run from the repo root, before tools/build_setup_tab.py:
    python3 tools/build_master_xlsx.py
"""
import json

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule

OUT = 'exports/GC1972_Territories.xlsx'

FONT_NAME = 'Arial'
HEADER_FONT = Font(name=FONT_NAME, bold=True, color='FFFFFF', size=11)
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=14)
BODY_FONT = Font(name=FONT_NAME, size=11)
SC_FONT = Font(name=FONT_NAME, size=11, bold=True, color='9C6500')
TOTAL_FONT = Font(name=FONT_NAME, bold=True, size=11)
NOTE_FONT = Font(name=FONT_NAME, italic=True, size=9, color='808080')
THIN = Side(style='thin', color='D9D9D9')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center')
LEFT = Alignment(horizontal='left', vertical='center')

factions_data = json.load(open('data/factions.json'))['factions']
FACTION_ORDER = ['NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC']
FACTION_META = {fac: (info['name'], info['color'].lstrip('#')) for fac, info in factions_data.items()}
CAPACITY = 30          # rows of headroom per faction tab
UNASSIGNED_CAPACITY = 10

COLUMNS = ['ID', 'Name', 'Faction', 'Value', 'SC']
COL_WIDTHS = [8, 26, 12, 9, 8]

territories = json.load(open('data/territories.json'))['spaces']
land = sorted((s for s in territories if s['type'] == 'land'), key=lambda s: s['id'])

wb = Workbook()
wb.remove(wb.active)

# ------------------------------------------------------------------
# 1. "All Territories" -- the live master. One row per land territory,
#    plus 3 hidden helper columns the subtabs key off of so they can
#    look a row up without an array formula: Group (faction, or
#    UNASSIGNED), Rank (nth row in that group), Key (Group-Rank, a
#    unique lookup key per row).
# ------------------------------------------------------------------
master = wb.create_sheet('All Territories')

master['A1'] = 'Global Cataclysm: 1972 — All Land Territories'
master['A1'].font = TITLE_FONT
master.merge_cells('A1:E1')

header_row = 3
for i, col in enumerate(COLUMNS):
    c = master.cell(row=header_row, column=i + 1, value=col)
    c.font = HEADER_FONT
    c.fill = PatternFill('solid', fgColor='404040')
    c.alignment = CENTER
    c.border = BORDER
master['F3'] = 'Group'
master['G3'] = 'Rank'
master['H3'] = 'Key'
for c in ('F3', 'G3', 'H3'):
    master[c].font = NOTE_FONT

for i, w in enumerate(COL_WIDTHS):
    master.column_dimensions[get_column_letter(i + 1)].width = w
master.freeze_panes = f'A{header_row + 1}'

first_data_row = header_row + 1
for i, terr in enumerate(land):
    r = first_data_row + i
    master.cell(row=r, column=1, value=terr['id'])
    master.cell(row=r, column=2, value=terr['name'])
    master.cell(row=r, column=3, value=terr['faction'])
    master.cell(row=r, column=4, value=terr['value'])
    master.cell(row=r, column=5, value='Yes' if terr['strategic_center'] else None)
    for col_idx in range(1, 6):
        cell = master.cell(row=r, column=col_idx)
        cell.font = SC_FONT if terr['strategic_center'] else BODY_FONT
        cell.border = BORDER
        cell.alignment = CENTER if col_idx != 2 else LEFT
        if (r - first_data_row) % 2 == 1:
            cell.fill = PatternFill('solid', fgColor='F5F5F5')
    master.cell(row=r, column=6, value=f'=IF(C{r}="","UNASSIGNED",C{r})')
    master.cell(row=r, column=7, value=f'=COUNTIF($F$4:F{r},F{r})')
    master.cell(row=r, column=8, value=f'=F{r}&"-"&G{r}')
    for col in (6, 7, 8):
        master.cell(row=r, column=col).font = NOTE_FONT

last_data_row = first_data_row + len(land) - 1

for col in ('F', 'G', 'H'):
    master.column_dimensions[col].hidden = True

# Faction dropdown so a typo can't silently vanish from every subtab.
dv = DataValidation(type='list', formula1='"NAA,UE,UER,GPC,PAF,AAC"', allow_blank=True,
                     showDropDown=False, showErrorMessage=True,
                     errorTitle='Invalid faction', error='Use one of NAA, UE, UER, GPC, PAF, AAC (or leave blank for unassigned).')
master.add_data_validation(dv)
dv.add(f'C4:C{last_data_row + 50}')

total_row = last_data_row + 1
master.cell(row=total_row, column=1, value='Total').font = TOTAL_FONT
master.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
master.cell(row=total_row, column=1).alignment = CENTER
tot_cell = master.cell(row=total_row, column=4, value=f'=SUM(D{first_data_row}:D{last_data_row})')
tot_cell.font = TOTAL_FONT
for col in range(1, 6):
    master.cell(row=total_row, column=col).border = BORDER

note_row = total_row + 2
master.cell(row=note_row, column=1,
            value=('This sheet is the master list — edit ID / Name / Faction / Value / SC here. '
                   'The faction tabs (and Unassigned) read from it automatically and need no manual updates.'))
master.cell(row=note_row, column=1).font = NOTE_FONT
master.merge_cells(f'A{note_row}:E{note_row}')

# ------------------------------------------------------------------
# 2. Every faction tab (and Unassigned) is a live view: each row looks
#    up 'FACTION-<rank>' in the master's Key column and pulls that
#    row's five fields with INDEX. No data is stored here -- editing
#    the master is the only way to change what appears.
# ------------------------------------------------------------------

def build_tab(ws, group_label, accent_hex, title, capacity):
    ws['A1'] = title
    ws['A1'].font = TITLE_FONT
    ws.merge_cells('A1:E1')

    header_row = 3
    for i, col in enumerate(COLUMNS):
        c = ws.cell(row=header_row, column=i + 1, value=col)
        c.font = HEADER_FONT
        c.fill = PatternFill('solid', fgColor=accent_hex)
        c.alignment = CENTER
        c.border = BORDER
    for i, w in enumerate(COL_WIDTHS):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    ws.freeze_panes = f'A{header_row + 1}'

    first_body = header_row + 1
    for i in range(capacity):
        r = first_body + i
        key_formula = f'"{group_label}-"&{i + 1}'
        match_formula = f"IFERROR(MATCH({key_formula},'All Territories'!$H:$H,0),\"\")"
        ws.cell(row=r, column=7, value=f'={match_formula}')  # hidden helper: matched row #
        for col_idx, src_col in enumerate(['A', 'B', 'C', 'D', 'E'], start=1):
            if src_col == 'E':
                # SC column only ever holds "Yes" or blank in the master; a
                # plain INDEX on a blank source cell would show 0, not "".
                f = f"=IFERROR(IF(INDEX('All Territories'!$E:$E,$G{r})=\"Yes\",\"Yes\",\"\"),\"\")"
            else:
                f = (f"=IFERROR(INDEX('All Territories'!${src_col}:${src_col},$G{r}),\"\")")
            cell = ws.cell(row=r, column=col_idx, value=f)
            cell.alignment = CENTER if col_idx != 2 else LEFT
        for col in range(1, 6):
            cell = ws.cell(row=r, column=col)
            cell.font = BODY_FONT
    ws.column_dimensions['G'].hidden = True

    last_body = first_body + capacity - 1

    # conditional formatting: border + zebra fill only on populated rows;
    # bold/gold text on Strategic Center rows -- all driven off column A.
    rng = f'A{first_body}:E{last_body}'
    ws.conditional_formatting.add(rng, FormulaRule(
        formula=[f'$A{first_body}<>""'], stopIfTrue=False,
        border=Border(left=THIN, right=THIN, top=THIN, bottom=THIN)))
    ws.conditional_formatting.add(rng, FormulaRule(
        formula=[f'AND($A{first_body}<>"",MOD(ROW(),2)=0)'],
        fill=PatternFill('solid', fgColor='F5F5F5')))
    ws.conditional_formatting.add(rng, FormulaRule(
        formula=[f'$E{first_body}="Yes"'],
        font=Font(name=FONT_NAME, bold=True, color='9C6500')))

    total_r = last_body + 2
    ws.cell(row=total_r, column=1, value='Total').font = TOTAL_FONT
    ws.merge_cells(f'A{total_r}:C{total_r}')
    ws.cell(row=total_r, column=1).alignment = CENTER
    tot_cell = ws.cell(row=total_r, column=4, value=f'=SUM(D{first_body}:D{last_body})')
    tot_cell.font = TOTAL_FONT
    tot_cell.alignment = CENTER
    for col in range(1, 6):
        ws.cell(row=total_r, column=col).border = BORDER

    info_r = total_r + 1
    ws.cell(row=info_r, column=1, value='Territories').font = NOTE_FONT
    ws.merge_cells(f'A{info_r}:C{info_r}')
    # COUNTA would also count formula cells that evaluate to "" (still
    # "non-blank" to a spreadsheet engine); SUMPRODUCT on a <>"" test
    # correctly counts only rows with a real value.
    ws.cell(row=info_r, column=4, value=f'=SUMPRODUCT(--(A{first_body}:A{last_body}<>""))').font = NOTE_FONT
    ws.cell(row=info_r, column=4).alignment = CENTER

    sc_r = info_r + 1
    ws.cell(row=sc_r, column=1, value='Strategic Centers').font = NOTE_FONT
    ws.merge_cells(f'A{sc_r}:C{sc_r}')
    ws.cell(row=sc_r, column=4, value=f'=COUNTIF(E{first_body}:E{last_body},"Yes")').font = NOTE_FONT
    ws.cell(row=sc_r, column=4).alignment = CENTER

    src_r = sc_r + 2
    ws.cell(row=src_r, column=1,
            value="Live view of 'All Territories' -- edit data on that tab, not here.").font = NOTE_FONT
    ws.merge_cells(f'A{src_r}:E{src_r}')


for fac in FACTION_ORDER:
    full_name, accent = FACTION_META[fac]
    ws = wb.create_sheet(fac)
    build_tab(ws, fac, accent, f'{full_name} ({fac})', CAPACITY)

ws = wb.create_sheet('Unassigned')
build_tab(ws, 'UNASSIGNED', '808080', 'Unassigned Territories', UNASSIGNED_CAPACITY)

wb.save(OUT)
print('saved', OUT, 'sheets:', wb.sheetnames, '| land territories:', len(land))
