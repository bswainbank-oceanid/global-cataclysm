"""
Add/replace the 'Initial Setup' tab on exports/GC1972_Territories.xlsx: the
per-faction starting-unit purchase plan, promoted-units picks, a live
stacking-cap check, and territory coverage, all built from
data/scenarios/starting_setup_200ipc.json + data/units.json +
data/factions.json + derived/faction_territory_profile.json.

This is a straight port of the legacy add_setup_tab200_v4.py -- same
tables, same formulas, same business rules (full-budget spend, the
value+3(+2 if SC) stacking cap that excludes naval units and
carrier-escorted aircraft, coastal-only naval purchase & sea-zone
deployment, no-shared-sea-zone, carrier-must-have-escort,
mandatory-infantry-at-foreign-border, and the promotion effect). Only the
data sources changed: JSON files under data/ and derived/ instead of a
bundle of ad hoc, hand-maintained scripts.

Run from the repo root, after tools/build_master_xlsx.py:
    python3 tools/build_setup_tab.py
"""
import json

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import FormulaRule
from openpyxl.utils import get_column_letter

SRC = 'exports/GC1972_Territories.xlsx'
OUT = 'exports/GC1972_Territories.xlsx'

FONT_NAME = 'Arial'
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=16)
SUBTITLE_FONT = Font(name=FONT_NAME, italic=True, size=10, color='808080')
H1_FONT = Font(name=FONT_NAME, bold=True, size=12)
H2_FONT = Font(name=FONT_NAME, bold=True, italic=True, size=10, color='808080')
HEADER_FONT = Font(name=FONT_NAME, bold=True, color='FFFFFF', size=10)
BODY_FONT = Font(name=FONT_NAME, size=10)
SC_FONT = Font(name=FONT_NAME, size=10, bold=True, color='9C6500')
TOTAL_FONT = Font(name=FONT_NAME, bold=True, size=10)
NOTE_FONT = Font(name=FONT_NAME, italic=True, size=9, color='808080')
THIN = Side(style='thin', color='D9D9D9')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center')
LEFT = Alignment(horizontal='left', vertical='center')

factions_data = json.load(open('data/factions.json'))['factions']
FACTION_ORDER = ['NAA', 'UE', 'UER', 'GPC', 'PAF', 'AAC']
FACTION_META = {fac: (info['name'], info['color'].lstrip('#')) for fac, info in factions_data.items()}
SUBTAB_CAPACITY = 30      # matches tools/build_master_xlsx.py's per-faction subtab capacity
NAVAL_UNITS = ['Aircraft Carrier', 'Submarine', 'Cruiser']
LAND_UNITS = ['Infantry', 'Mechanized Infantry', 'Armor']
AIR_UNITS = ['Fighter', 'Bomber']

units_data = json.load(open('data/units.json'))['units']
UNIT_COSTS = {name: {'type': info['category'], 'cost': info['cost'], 'sc_cost': info['sc_cost']}
              for name, info in units_data.items() if info['purchasable']}
UNIT_STATS = {name: {'die': info['attack_die'], 'defense': info['defense'], 'hp': info['hp']}
              for name, info in units_data.items()}

rules = json.load(open('data/rules.json'))['setup']
BUDGET = rules['starting_unit_budget_ipc']

scenario = json.load(open('data/scenarios/starting_setup_200ipc.json'))
# reshape list-of-dicts (the portable JSON shape) back into the
# positional (tid, [[unit, qty], ...]) shape the rendering logic below
# was written against.
DESIGN = {
    fac: [(entry['territory_id'], [[u['unit'], u['qty']] for u in entry['units']]) for entry in entries]
    for fac, entries in scenario['purchases'].items()
}
PROMOTIONS = {
    fac: [(entry['territory_id'], entry['unit']) for entry in entries]
    for fac, entries in scenario['promotions'].items()
}
NAVAL_OVERRIDES = scenario['naval_deploy_overrides']  # fac -> {tid(str): {unit: zone_name}}
CARRIER_ESCORTS = scenario['carrier_escorts']         # fac -> [{carrier_tid, aircraft_tid, unit, qty}]

profile = json.load(open('derived/faction_territory_profile.json'))
prof_by_fac_id = {fac: {r['id']: r for r in rows} for fac, rows in profile.items()}

DIE_STEPS = ['D6', 'D8', 'D10', 'D12']

def promoted_die(die):
    i = DIE_STEPS.index(die)
    return DIE_STEPS[min(i + 1, len(DIE_STEPS) - 1)]

def promoted_defense(defense):
    return min(defense + 1, 10)

def promoted_hp(hp):
    return hp + 1

wb = load_workbook(SRC, data_only=False)
if 'Initial Setup' in wb.sheetnames:
    del wb['Initial Setup']
ws = wb.create_sheet('Initial Setup')

# ---- Title ----
ws['A1'] = 'Global Cataclysm: 1972 — Initial Force Setup'
ws['A1'].font = TITLE_FONT
ws.merge_cells('A1:I1')
ws['A2'] = (f'Every faction spends the full {BUDGET} IPC on starting units. A territory may start with up to its '
            '(value + 3) land/air units, plus 2 more at a Strategic Center (the SC production bonus); units riding '
            'a carrier don’t count against that cap either. Naval units are bought at a coastal territory and '
            'begin in their own sea zone — no two factions deploy naval units to the same sea zone. Every Aircraft '
            'Carrier has at least 1 escorting Fighter/Bomber that deploys aboard it, in the same sea zone. Every '
            'territory bordering a foreign power has at least 1 infantry. Each faction also promotes 3 units at '
            'setup — a promoted unit permanently steps up one attack-die size (max D12), gains +1 defense (max 10), '
            'and gains +1 HP. Territory / Faction / SC / cost / cap columns below are LIVE — edit ‘All '
            'Territories’ and they recalc automatically. Only the Territory ID / Unit / Qty columns, the '
            'carrier escort assignments, and the Promoted Units picks are the fixed setup plan.')
ws['A2'].font = SUBTITLE_FONT
ws.merge_cells('A2:I2')

# ---- Unit cost reference table (rows 6-13; referenced by every faction's formulas below) ----
r = 4
ws.cell(row=r, column=1, value='Unit Cost Reference').font = H1_FONT
r += 1
ref_cols = ['Unit', 'Category', 'Cost', 'Cost at Strategic Center']
for i, col in enumerate(ref_cols):
    c = ws.cell(row=r, column=i + 1, value=col)
    c.font = HEADER_FONT
    c.fill = PatternFill('solid', fgColor='404040')
    c.alignment = CENTER
    c.border = BORDER
r += 1
REF_FIRST = r
unit_order = ['Infantry', 'Mechanized Infantry', 'Armor', 'Fighter', 'Bomber',
              'Aircraft Carrier', 'Submarine', 'Cruiser']
for unit in unit_order:
    info = UNIT_COSTS[unit]
    ws.cell(row=r, column=1, value=unit).font = BODY_FONT
    ws.cell(row=r, column=2, value=info['type']).font = BODY_FONT
    ws.cell(row=r, column=3, value=info['cost']).font = BODY_FONT
    ws.cell(row=r, column=4, value=info['sc_cost']).font = BODY_FONT
    for col in range(1, 5):
        cell = ws.cell(row=r, column=col)
        cell.border = BORDER
        cell.alignment = CENTER if col != 1 else LEFT
        if r % 2 == 0:
            cell.fill = PatternFill('solid', fgColor='F5F5F5')
    r += 1
REF_LAST = r - 1
REF_RANGE = f'$A${REF_FIRST}:$D${REF_LAST}'

# ---- Faction summary table (built after per-faction blocks so it can point at their Total cells) ----
summary_row_of = {}
r += 2
summary_title_row = r
ws.cell(row=r, column=1, value='Faction Summary').font = H1_FONT
r += 1
summary_header_row = r
summary_cols = ['Faction', 'Name', 'IPC Spent', 'IPC Unspent', 'Land Units', 'Air Units', 'Naval Units', 'Total Units', 'Promotions']
for i, col in enumerate(summary_cols):
    c = ws.cell(row=r, column=i + 1, value=col)
    c.font = HEADER_FONT
    c.fill = PatternFill('solid', fgColor='404040')
    c.alignment = CENTER
    c.border = BORDER
r += 1
for fac in FACTION_ORDER:
    summary_row_of[fac] = r
    full_name, accent = FACTION_META[fac]
    c = ws.cell(row=r, column=1, value=fac)
    c.font = Font(name=FONT_NAME, bold=True, color='FFFFFF', size=10)
    c.fill = PatternFill('solid', fgColor=accent)
    c.alignment = CENTER
    ws.cell(row=r, column=2, value=full_name).font = BODY_FONT
    ws.cell(row=r, column=2).alignment = LEFT
    for col in range(1, 10):
        ws.cell(row=r, column=col).border = BORDER
    r += 1
summary_last_row = r - 1

# placeholders for the detail blocks; filled in the loop below, then the
# summary row formulas are patched in afterward once we know each block's
# Total-row cell and purchase-row range.
detail_cols = ['Territory ID', 'Territory', 'Faction', 'SC?', 'Unit', 'Qty', 'Unit Cost', 'Line Cost', 'Deploys To']
col_widths = [12, 22, 9, 6, 20, 6, 10, 10, 22, 30]
for i, w in enumerate(col_widths):
    ws.column_dimensions[get_column_letter(i + 1)].width = w

block_info = {}  # fac -> dict with row refs needed for the summary table & cap-check table

r += 2
for fac in FACTION_ORDER:
    full_name, accent = FACTION_META[fac]
    entries = DESIGN[fac]

    r += 1
    ws.cell(row=r, column=1, value=f'{full_name} ({fac})').font = Font(name=FONT_NAME, bold=True, color='FFFFFF', size=12)
    ws.cell(row=r, column=1).fill = PatternFill('solid', fgColor=accent)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
    r += 1

    header_row = r
    for i, col in enumerate(detail_cols):
        c = ws.cell(row=r, column=i + 1, value=col)
        c.font = HEADER_FONT
        c.fill = PatternFill('solid', fgColor=accent)
        c.alignment = CENTER
        c.border = BORDER
    r += 1

    def naval_zone(fac, tid, unit):
        return NAVAL_OVERRIDES.get(fac, {}).get(str(tid), {}).get(unit) \
            or prof_by_fac_id[fac][tid]['sea_zone_name'] or '?'

    # flatten (territory, unit, qty) rows, SC territories first for readability.
    # deploy_override is None for land/air units stationed on their territory
    # (Deploys To stays a live formula); it's a literal sea-zone string for
    # naval units, and for carrier-escorted aircraft it's the carrier's own
    # sea zone (the aircraft rides the carrier rather than basing on land).
    flat = []
    for tid, units in entries:
        is_sc = prof_by_fac_id[fac][tid]['sc']
        for unit, qty in units:
            escort_qty = 0
            for esc in CARRIER_ESCORTS.get(fac, []):
                if esc['aircraft_tid'] == tid and esc['unit'] == unit:
                    escort_qty = esc['qty']
                    carrier_zone = naval_zone(fac, esc['carrier_tid'], 'Aircraft Carrier')
                    flat.append((tid, is_sc, unit, escort_qty, f'{carrier_zone} (aboard Aircraft Carrier)'))
            remaining = qty - escort_qty
            if remaining > 0:
                if unit in NAVAL_UNITS:
                    flat.append((tid, is_sc, unit, remaining, naval_zone(fac, tid, unit)))
                else:
                    flat.append((tid, is_sc, unit, remaining, None))
    flat.sort(key=lambda x: (not x[1], x[0]))

    first_body = r
    for tid, is_sc, unit, qty, deploy_override in flat:
        ws.cell(row=r, column=1, value=tid)
        ws.cell(row=r, column=2, value=(f"=IFERROR(INDEX('All Territories'!$B:$B,"
                                         f"MATCH($A{r},'All Territories'!$A:$A,0)),\"?\")"))
        ws.cell(row=r, column=3, value=(f"=IFERROR(INDEX('All Territories'!$C:$C,"
                                         f"MATCH($A{r},'All Territories'!$A:$A,0)),\"?\")"))
        ws.cell(row=r, column=4, value=(f"=IFERROR(IF(INDEX('All Territories'!$E:$E,"
                                         f"MATCH($A{r},'All Territories'!$A:$A,0))=\"Yes\",\"Yes\",\"\"),\"\")"))
        ws.cell(row=r, column=5, value=unit)
        ws.cell(row=r, column=6, value=qty)
        ws.cell(row=r, column=7, value=f'=IF($D{r}="Yes",VLOOKUP($E{r},{REF_RANGE},4,0),VLOOKUP($E{r},{REF_RANGE},3,0))')
        ws.cell(row=r, column=8, value=f'=$F{r}*$G{r}')
        if deploy_override is not None:
            ws.cell(row=r, column=9, value=deploy_override)
        else:
            ws.cell(row=r, column=9, value=f'=$B{r}')
        for col in range(1, 10):
            c = ws.cell(row=r, column=col)
            c.font = SC_FONT if is_sc else BODY_FONT
            c.border = BORDER
            c.alignment = CENTER if col not in (2, 9) else LEFT
            if (r - first_body) % 2 == 1:
                c.fill = PatternFill('solid', fgColor='F5F5F5')
        r += 1
    last_body = r - 1

    total_row = r
    ws.cell(row=r, column=1, value='Total').font = TOTAL_FONT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
    ws.cell(row=r, column=1).alignment = CENTER
    tot_cell = ws.cell(row=r, column=8, value=f'=SUM(H{first_body}:H{last_body})')
    tot_cell.font = TOTAL_FONT
    for col in range(1, 10):
        ws.cell(row=r, column=col).border = BORDER
    r += 1

    # flag (live) any purchase row whose territory no longer belongs to this
    # faction on 'All Territories' -- can't be auto-relocated, but it won't
    # go unnoticed.
    ws.conditional_formatting.add(
        f'A{first_body}:I{last_body}',
        FormulaRule(formula=[f'$C{first_body}<>"{fac}"'], stopIfTrue=False,
                    font=Font(name=FONT_NAME, size=10, bold=True, color='CC0000'))
    )

    r += 1
    ws.cell(row=r, column=1, value='Mismatched-faction rows above (highlighted in red) mean that territory has '
                                    'since been reassigned on ‘All Territories’ and this purchase needs '
                                    'a manual re-check.').font = NOTE_FONT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
    r += 2

    # ---- promoted units (fixed picks — 3 per faction) ----
    qty_by_tid_unit = {}
    for tid, _, unit, qty, _ in flat:
        qty_by_tid_unit[(tid, unit)] = qty_by_tid_unit.get((tid, unit), 0) + qty
    ws.cell(row=r, column=1, value=f'Promoted Units — {fac} (3 promotions granted at setup)').font = H2_FONT
    r += 1
    promo_header_row = r
    promo_cols = ['Territory ID', 'Territory', 'Unit', 'Base Attack Die', 'Base Defense', 'Base HP',
                  'Promoted Attack Die', 'Promoted Defense', 'Promoted HP', 'Notes']
    for i, col in enumerate(promo_cols):
        c = ws.cell(row=r, column=i + 1, value=col)
        c.font = Font(name=FONT_NAME, bold=True, size=9, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='6E6E6E')
        c.alignment = CENTER
        c.border = BORDER
    r += 1
    promo_first = r
    for tid, unit in PROMOTIONS[fac]:
        stats = UNIT_STATS[unit]
        base_die, base_def, base_hp = stats['die'], stats['defense'], stats['hp']
        promo_die, promo_def, promo_hp = promoted_die(base_die), promoted_defense(base_def), promoted_hp(base_hp)
        qty_here = qty_by_tid_unit.get((tid, unit), 0)
        notes = f'1 of {qty_here} {unit} at this territory'
        ws.cell(row=r, column=1, value=tid)
        ws.cell(row=r, column=2, value=(f"=IFERROR(INDEX('All Territories'!$B:$B,"
                                         f"MATCH($A{r},'All Territories'!$A:$A,0)),\"?\")"))
        ws.cell(row=r, column=3, value=unit)
        ws.cell(row=r, column=4, value=base_die)
        ws.cell(row=r, column=5, value=base_def)
        ws.cell(row=r, column=6, value=base_hp)
        ws.cell(row=r, column=7, value=promo_die)
        ws.cell(row=r, column=8, value=promo_def)
        ws.cell(row=r, column=9, value=promo_hp)
        ws.cell(row=r, column=10, value=notes)
        for col in range(1, 11):
            c = ws.cell(row=r, column=col)
            c.font = Font(name=FONT_NAME, size=9, bold=True, color='2E7D32')
            c.border = BORDER
            c.alignment = CENTER if col != 10 else LEFT
            if (r - promo_first) % 2 == 1:
                c.fill = PatternFill('solid', fgColor='EAF6EC')
        r += 1
    promo_last = r - 1
    r += 2

    # ---- per-territory stacking-cap check (live) ----
    ws.cell(row=r, column=1, value=f'Stacking Cap Check — {fac}').font = H2_FONT
    r += 1
    cap_header_row = r
    cap_cols = ['Territory ID', 'Territory', 'Value', 'SC?', 'Cap (value+3, +2 more if SC)', 'Land/Air Deployed', 'Status']
    for i, col in enumerate(cap_cols):
        c = ws.cell(row=r, column=i + 1, value=col)
        c.font = Font(name=FONT_NAME, bold=True, size=9, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='6E6E6E')
        c.alignment = CENTER
        c.border = BORDER
    r += 1
    unique_ids = sorted({tid for tid, _, _, _, _ in flat})
    cap_first = r
    for tid in unique_ids:
        ws.cell(row=r, column=1, value=tid)
        ws.cell(row=r, column=2, value=(f"=IFERROR(INDEX('All Territories'!$B:$B,"
                                         f"MATCH($A{r},'All Territories'!$A:$A,0)),\"?\")"))
        ws.cell(row=r, column=3, value=(f"=IFERROR(INDEX('All Territories'!$D:$D,"
                                         f"MATCH($A{r},'All Territories'!$A:$A,0)),0)"))
        ws.cell(row=r, column=4, value=(f"=IFERROR(IF(INDEX('All Territories'!$E:$E,"
                                         f"MATCH($A{r},'All Territories'!$A:$A,0))=\"Yes\",\"Yes\",\"\"),\"\")"))
        ws.cell(row=r, column=5, value=f'=$C{r}+3+IF($D{r}="Yes",2,0)')
        # Only count units actually stationed on this territory (Deploys To = its
        # own name). This naturally excludes naval units (deploy to a sea zone)
        # AND carrier-based aircraft (deploy to the carrier's sea zone too),
        # without needing to enumerate unit types.
        ws.cell(row=r, column=6, value=(f'=SUMIFS($F${first_body}:$F${last_body},$A${first_body}:$A${last_body},$A{r},'
                                         f'$I${first_body}:$I${last_body},$B{r})'))
        ws.cell(row=r, column=7, value=f'=IF($F{r}>$E{r},"OVER CAP","OK")')
        for col in range(1, 8):
            c = ws.cell(row=r, column=col)
            c.font = BODY_FONT
            c.border = BORDER
            c.alignment = CENTER if col != 2 else LEFT
            if (r - cap_first) % 2 == 1:
                c.fill = PatternFill('solid', fgColor='F5F5F5')
        r += 1
    cap_last = r - 1
    if cap_last >= cap_first:
        ws.conditional_formatting.add(
            f'A{cap_first}:G{cap_last}',
            FormulaRule(formula=[f'$G{cap_first}="OVER CAP"'], stopIfTrue=False,
                        font=Font(name=FONT_NAME, size=9, bold=True, color='CC0000'))
        )
    r += 2

    # ---- territory coverage: every territory this faction currently holds
    # (mirrors the live faction subtab), flagging which ones got no units ----
    ws.cell(row=r, column=1, value=f'Territory Coverage — {fac} (live from the ‘{fac}’ tab)').font = H2_FONT
    r += 1
    cov_header_row = r
    cov_cols = ['Territory ID', 'Territory', 'Value', 'SC?', 'Has Initial Units?']
    for i, col in enumerate(cov_cols):
        c = ws.cell(row=r, column=i + 1, value=col)
        c.font = Font(name=FONT_NAME, bold=True, size=9, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='6E6E6E')
        c.alignment = CENTER
        c.border = BORDER
    r += 1
    cov_first = r
    for i in range(SUBTAB_CAPACITY):
        sub_row = 4 + i
        ws.cell(row=r, column=1, value=f"='{fac}'!A{sub_row}")
        ws.cell(row=r, column=2, value=f"='{fac}'!B{sub_row}")
        ws.cell(row=r, column=3, value=f"='{fac}'!D{sub_row}")
        ws.cell(row=r, column=4, value=f"='{fac}'!E{sub_row}")
        ws.cell(row=r, column=5, value=(f'=IF($A{r}="","",IF(COUNTIF($A${first_body}:$A${last_body},$A{r})>0,'
                                         f'"Yes","No"))'))
        for col in range(1, 6):
            c = ws.cell(row=r, column=col)
            c.font = BODY_FONT
            c.border = BORDER
            c.alignment = CENTER if col != 2 else LEFT
            if (r - cov_first) % 2 == 1:
                c.fill = PatternFill('solid', fgColor='F5F5F5')
        r += 1
    cov_last = r - 1
    ws.conditional_formatting.add(
        f'A{cov_first}:E{cov_last}',
        FormulaRule(formula=[f'$E{cov_first}="No"'], stopIfTrue=False,
                    fill=PatternFill('solid', fgColor='FFF3CD'))
    )

    block_info[fac] = {'total_cell': f'H{total_row}', 'first_body': first_body, 'last_body': last_body,
                        'promo_first': promo_first, 'promo_last': promo_last}
    r += 2

# ---- patch the faction summary rows with live formulas now that we know each block's cells ----
for fac in FACTION_ORDER:
    sr = summary_row_of[fac]
    info = block_info[fac]
    spent_cell = ws.cell(row=sr, column=3, value=f"={info['total_cell']}")
    spent_cell.font = BODY_FONT
    spent_cell.alignment = CENTER
    unspent_cell = ws.cell(row=sr, column=4, value=f'={BUDGET}-C{sr}')
    unspent_cell.font = BODY_FONT
    unspent_cell.alignment = CENTER
    fb, lb = info['first_body'], info['last_body']

    def sumifs_units(units):
        parts = [f'SUMIFS($F${fb}:$F${lb},$E${fb}:$E${lb},"{u}")' for u in units]
        return '+'.join(parts)

    land_cell = ws.cell(row=sr, column=5, value=f'={sumifs_units(LAND_UNITS)}')
    air_cell = ws.cell(row=sr, column=6, value=f'={sumifs_units(AIR_UNITS)}')
    sea_cell = ws.cell(row=sr, column=7, value=f'={sumifs_units(NAVAL_UNITS)}')
    for cell in (land_cell, air_cell, sea_cell):
        cell.font = BODY_FONT
        cell.alignment = CENTER
    total_units_cell = ws.cell(row=sr, column=8, value=f'=E{sr}+F{sr}+G{sr}')
    total_units_cell.font = BODY_FONT
    total_units_cell.alignment = CENTER
    pf, pl = info['promo_first'], info['promo_last']
    promo_cell = ws.cell(row=sr, column=9, value=f'=SUMPRODUCT(--($A${pf}:$A${pl}<>""))')
    promo_cell.font = BODY_FONT
    promo_cell.alignment = CENTER

ws.freeze_panes = 'A4'
wb.save(OUT)
print('saved', OUT, 'sheets:', wb.sheetnames)
