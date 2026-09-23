"""
Phillip Securities Margin Calculator
=====================================
INPUTS:
- ScripPositions.xlsx file upload
- Net Amount (a) +/- (b) - manual input
- Credit Limit - manual input  
- FX Rates - manual input

RULES:
- INCLUDE: Equities (SG, HK, US) and Bonds (ZZ section)
- EXCLUDE: Unit Trusts (section after GRAND TOTAL)
- Include Outstanding Purchases (unsettled buys) in quantity
- Exclude positions where Unsettled Sales = -Qty (net position = 0)
- Use Previous Day Closing Price for ALL margin calculations
- V Account: detect special financing where grade ≠ actual collateral

LIVE WHAT-IF:
- Position table is editable: change Grade / Price per counter and the whole dashboard recalculates
- New Order Simulator: add buy / sell orders (qty × price ± brokerage). Buys deduct cash from
  Net Amount and add a pending position (consuming buying power); sells reduce the holding and
  add the net proceeds back to cash

VBA FORMULAS:
- Usable Cash (O12) = Portfolio Value - IM + Net Amount
- Margin Call? (O8) = IF(Usable Cash < 0, "Yes", "No")
- Margin Call Amount (O9) = -(Portfolio Value - MM + Net Amount)
- Available Buy Limit (O15) = Net Amount + Credit LimitR
- Buying Power (O18) = MIN(Usable Cash, Available Buy Limit)

Run: streamlit run margin_app.py
"""

from re import S
import streamlit as st
import pandas as pd

# =============================================================================
# REFERENCE DATA (from REFERENCE sheet)
# =============================================================================

GRADES = {
    80: {'name': 'Grade S (80%)', 'im': 0.20, 'mm': 0.20, 'fm': 0.1304, 'sell': 5.000, 'deposit': 1.250, 'purchase': 5.000},
    70: {'name': 'Grade A (70%)', 'im': 0.30, 'mm': 0.30, 'fm': 0.2391, 'sell': 3.333, 'deposit': 1.429, 'purchase': 3.333},
    50: {'name': 'Grade B (50%)', 'im': 0.50, 'mm': 0.50, 'fm': 0.4565, 'sell': 2.000, 'deposit': 2.000, 'purchase': 2.000},
    30: {'name': 'Grade E (30%)', 'im': 0.70, 'mm': 0.70, 'fm': 0.6739, 'sell': 1.429, 'deposit': 3.333, 'purchase': 1.429},
    0:  {'name': 'Grade C (0%)',  'im': 1.00, 'mm': 1.00, 'fm': 1.0000, 'sell': 1.000, 'deposit': None, 'purchase': 1.000},
}

# Financing % by grade (= 1 - IM rate)
GRADE_FINANCING = {80: 0.80, 70: 0.70, 50: 0.50, 30: 0.30, 0: 0.00}

DEFAULT_FX = {'SGD': 1.0, 'USD': 1.3374, 'HKD': 0.1626}
LETTER_GRADE_MAP = {'S': 80, 'A': 70, 'B': 50, 'E': 30, 'C': 0}


def parse_number(val) -> float:
    if pd.isna(val) or val is None or val == '' or val == '-':
        return 0.0
    val_str = str(val).replace(',', '').strip()
    if val_str.startswith('(') and val_str.endswith(')'):
        val_str = '-' + val_str[1:-1]
    try:
        return float(val_str)
    except:
        return 0.0


def parse_grade(val) -> int:
    if pd.isna(val) or val in [None, '', '-']:
        return 0
    val_str = str(val).strip().upper()
    if val_str in LETTER_GRADE_MAP:
        return LETTER_GRADE_MAP[val_str]
    try:
        val_str = str(val).replace('%', '').strip()
        num = float(val_str)
        return int(num) if num > 1 else int(num * 100)
    except:
        return 0


def get_grade_info(grade_pct: int) -> dict:
    levels = [80, 70, 50, 30, 0]
    closest = min(levels, key=lambda x: abs(x - grade_pct))
    return GRADES[closest]


def get_nearest_grade(grade_pct: int) -> int:
    levels = [80, 70, 50, 30, 0]
    return min(levels, key=lambda x: abs(x - grade_pct))


def detect_special_financing(grade_pct: int, prev_close: float, total_qty: int,
                             collateral_file: float = 0, im_file: float = 0,
                             is_v_account: bool = False) -> dict:
    """
    Exact check for special financing.
    
    V Account:      Grade% × Prev_Close × Qty ≠ Collateral
    Margin/CashPlus: (1 - Grade%) × Prev_Close × Qty ≠ IM
    """
    nearest = get_nearest_grade(grade_pct)
    financing_pct = GRADE_FINANCING[nearest]  # e.g. 80% for Grade S
    im_rate = 1 - financing_pct               # e.g. 20% for Grade S
    mv_local = prev_close * total_qty

    if is_v_account:
        expected = financing_pct * mv_local       # expected collateral
        actual = collateral_file
    else:
        expected = im_rate * mv_local             # expected IM
        actual = im_file
    
    # Calculate actual financing percentage
    actual_financing_pct = 0.0
    if mv_local > 0:
        if is_v_account:
            actual_financing_pct = round((actual / mv_local)*100,0)
            print("actual financing pct " ,str(actual_financing_pct))  
            print("actual financing pct without rounding" ,str(actual / mv_local))          # collateral / MV
                    # collateral / MV
        else:
            print(1 - (actual / mv_local))

            actual_financing_pct = round((1 - (actual / mv_local))*100,0)   # 1 - (IM / MV)
    
    # Compare financing percentages with tolerance
    print("expected financing pct ", str(financing_pct))
    expected_financing_pct = round(financing_pct, 0)  # Same expected financing % for both account types, rounded to integer
    tolerance = 1.0  # 1 unit tolerance for integer percentages
    diff = abs(expected_financing_pct - actual_financing_pct)
    
    # Check for tiny absolute difference to handle cases like $24,605.00 vs $24,604.99
    absolute_diff = abs(expected - actual)
    absolute_tolerance = 1.0  # $1 tolerance for nearly identical values
    
    # Not special if the absolute difference is very small
    is_special = (diff > tolerance) and (absolute_diff > absolute_tolerance) and actual > 0


    return {
        'is_special': is_special,
        'grade_shown': nearest,
        'expected_financing': financing_pct,
        'actual_financing': actual_financing_pct,
        'expected_value': expected,
        'actual_value': actual,
        'check_type': 'collateral' if is_v_account else 'IM',
    }

# =============================================================================
# LIVE WHAT-IF HELPERS (grade / price overrides + new orders)
# =============================================================================

GRADE_LEVELS = [80, 70, 50, 30, 0]
GRADE_OPTION_NAMES = [GRADES[g]['name'] for g in GRADE_LEVELS]
GRADE_NAME_TO_PCT = {GRADES[g]['name']: g for g in GRADE_LEVELS}


def apply_overrides(base_positions: list, overrides: dict) -> list:
    """
    Return a copy of the parsed positions with live grade / price overrides applied.

    overrides: {position_index: {'grade': int, 'price': float}}
    - A grade override means "treat this counter as that grade", so the counter is valued
      with the normal grade-based IM/MM/FM (special financing is switched off for it).
    - A price override replaces the Prev Day Close used for margin (stress % still applies on top).
    """
    result = []
    for i, base in enumerate(base_positions):
        pos = dict(base)
        pos['file_grade'] = base['grade']
        pos['file_price'] = base['prev_close']
        pos['grade_overridden'] = False
        pos['price_overridden'] = False
        ov = overrides.get(i, {})
        if 'grade' in ov and get_nearest_grade(ov['grade']) != get_nearest_grade(base['grade']):
            pos['grade'] = ov['grade']
            pos['grade_overridden'] = True
            pos['is_special_financing'] = False
        if 'price' in ov and ov['price'] > 0 and abs(ov['price'] - base['prev_close']) > 1e-9:
            pos['price_used'] = ov['price']
            pos['price_overridden'] = True
        result.append(pos)
    return result


def order_cost(order: dict, fx_rates: dict) -> dict:
    """
    Cash effect of an order. Brokerage = max(order value × %, minimum).
    BUY : cash out = value + fee   -> net_cash_sgd > 0
    SELL: cash in  = value - fee   -> net_cash_sgd < 0 (cash comes back)
    """
    fx = fx_rates.get(order['currency'], DEFAULT_FX.get(order['currency'], 1.0))
    value_local = order['qty'] * order['avg_price']
    fee_local = max(value_local * order['fee_pct'] / 100.0, order['min_fee'])
    is_sell = order.get('side') == 'SELL'
    net_local = -(value_local - fee_local) if is_sell else (value_local + fee_local)
    return {
        'fx': fx,
        'value_local': value_local,
        'fee_local': fee_local,
        'value_sgd': value_local * fx,
        'fee_sgd': fee_local * fx,
        'total_sgd': abs(net_local) * fx,   # gross cash moved: out for buys, in for sells
        'net_cash_sgd': net_local * fx,     # signed: +out (buy) / -in (sell)
    }


def apply_sell_orders(file_positions: list, orders: list) -> list:
    """
    Reduce holdings by pending SELL orders (matched by position index). Quantity never goes below 0.
    Rows are kept (with qty 0 if fully sold) so table row indices stay stable.
    """
    sold = {}
    for o in orders:
        if o.get('side') == 'SELL':
            sold[o['pos_index']] = sold.get(o['pos_index'], 0) + int(o['qty'])
    if not sold:
        return file_positions
    result = []
    for i, p in enumerate(file_positions):
        q = dict(p)
        if i in sold and q['effective_qty'] > 0:
            original = q['effective_qty']
            remaining = max(0, original - sold[i])
            q['effective_qty'] = remaining
            q['pending_sell_qty'] = original - remaining
            # collateral / IM in the file covers the full holding: scale it pro-rata with what is left
            q['margin_col_value'] = q['margin_col_value'] * remaining / original
        result.append(q)
    return result


def order_to_position(order: dict) -> dict:
    """
    Represent a pending buy order as a portfolio position so it flows through calculate_margin.
    Valued at its mark price (defaults to the avg/fill price; editable in the position table).
    """
    mark = order.get('mark_price') or order['avg_price']
    return {
        'section': 'ORDER', 'type': 'Order',
        'name': order['name'], 'code': order['code'],
        'grade': order['grade'],
        'qty_on_hand': 0, 'unsettled_purch': int(order['qty']), 'unsettled_sales': 0,
        'effective_qty': int(order['qty']),
        'currency': order['currency'],
        'prev_close': mark, 'current_price': mark, 'price_used': mark,
        'margin_col_value': 0.0,
        'is_special_financing': False,
        'actual_financing_pct': 0, 'expected_financing_pct': 0,
        'is_order': True, 'order_id': order['id'],
        'file_grade': order['grade'], 'file_price': order['avg_price'],
        'grade_overridden': False,
        'price_overridden': abs(mark - order['avg_price']) > 1e-9,
    }


def new_order_id() -> int:
    st.session_state.order_seq += 1
    return st.session_state.order_seq


def parse_scrip_positions(uploaded_file, is_v_account: bool = False) -> tuple:
    """
    Parse ScripPositions.xlsx
    
    Returns:
        positions: list of position dicts
        currencies: set of unique currencies
    """
    df = pd.read_excel(uploaded_file, header=None)
    
    header_idx = None
    for i, row in df.iterrows():
        row_str = ' '.join(str(v) for v in row.values if pd.notna(v))
        if 'Company Name' in row_str and 'Stock Code' in row_str:
            header_idx = i
            break
    
    if header_idx is None:
        st.error("Could not find header row in ScripPositions file")
        return [], set()
    
    positions = []
    currencies = set()
    current_section = None
    is_bond_section = False
    
    for i in range(header_idx + 1, len(df)):
        row = df.iloc[i]
        col0 = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ''
        
        if 'GRAND TOTAL' in col0.upper():
            break
        
        if col0 in ['SG', 'HK', 'US']:
            current_section = col0
            is_bond_section = False
            continue
        
        if col0 == 'ZZ':
            current_section = 'ZZ'
            is_bond_section = True
            continue
        
        if 'TOTAL' in col0.upper() or not col0:
            continue
        
        name = col0
        grade = parse_grade(row.iloc[2])
        code = str(row.iloc[3]).strip() if pd.notna(row.iloc[3]) else ''
        qty_on_hand = parse_number(row.iloc[4])
        currency = str(row.iloc[6]).strip().upper() if pd.notna(row.iloc[6]) else 'SGD'
        prev_close = parse_number(row.iloc[8])
        current_price = parse_number(row.iloc[16]) if len(row) > 16 else 0
        unsettled_purch = parse_number(row.iloc[13]) if len(row) > 13 else 0
        unsettled_sales = parse_number(row.iloc[14]) if len(row) > 14 else 0
        
        # Parse: one column, one variable
        margin_col_value = parse_number(row.iloc[11]) if len(row) > 11 else 0
        if is_v_account:
            effective_qty = qty_on_hand
        else:
            effective_qty = qty_on_hand + unsettled_purch + unsettled_sales
    
        
        if effective_qty <= 0:
            continue
        special_info = None
        # mv = current_price * effective_qty 
        
        print(is_bond_section)
        
      
        if effective_qty  :
                special_info = detect_special_financing(
            grade_pct=grade,
            prev_close=prev_close, 
            total_qty=int(effective_qty),
            collateral_file=margin_col_value if is_v_account else 0,
            im_file=margin_col_value if not is_v_account else 0,
            is_v_account=is_v_account,
        )
        
        price = prev_close
        if price <= 0:
            continue

        currencies.add(currency)

        #NEW FEATURE
        if is_bond_section:
            special_info['is_special'] = False


        positions.append({
            'section': current_section,
            'type': 'Bond' if is_bond_section else 'Equity',
            'name': name,
            'code': code,
            'grade': grade,
            'qty_on_hand': int(qty_on_hand),
            'unsettled_purch': int(unsettled_purch),
            'unsettled_sales': int(unsettled_sales),
            'effective_qty': int(effective_qty),
            'currency': currency,
            'prev_close': prev_close,
            'current_price': current_price if current_price > 0 else prev_close,
            'price_used': price,
            'margin_col_value': margin_col_value,
            'is_special_financing': special_info['is_special'],
            'actual_financing_pct': special_info.get('actual_financing', 0),
            'expected_financing_pct': special_info.get('expected_financing', 0),
        })
    
    return positions, currencies


def calculate_margin(price_change_pct: float, positions: list, net_amount: float, credit_limit: float,
                     fx_rates: dict, is_v_account: bool = False) -> dict:
    """
    Calculate margin status using VBA formulas.
    
    For V Account with special financing:
    - Normal positions: use grade-based IM/MM/FM
    - Special financing positions: use file's collateral to derive actual IM
    """
    if not positions:
        usable_cash = net_amount
        return {
            'positions': [], 'special_positions': [],
            'net_amount': net_amount,
            'total_pv': 0, 'total_im': 0, 'total_mm': 0, 'total_fm': 0,
            'usable_cash': usable_cash,
            'is_margin_call': usable_cash < 0,
            'margin_call_amount': max(0, -usable_cash),
            'available_buy_limit': net_amount + credit_limit,
            'buying_power': min(usable_cash, net_amount + credit_limit),
            'buying_power_no_margin': net_amount,
            'credit_capped': False,
            'mm_ratio': 0, 'fm_ratio': 0,
            'lowest_pv_before_mc': 0, 'max_drop_before_mc': 0,
            'lowest_pv_before_fs': 0, 'max_drop_before_fs': 0,
        }
    
    total_pv = 0
    total_im = 0
    total_mm = 0
    total_fm = 0
    calc_positions = []
    special_positions = []
    
    for pos in positions:
        fx = fx_rates.get(pos['currency'], 1.0)
        adjusted_price = pos['price_used'] * (1 + price_change_pct / 100)
        mv_local = pos['effective_qty'] * adjusted_price
        mv_sgd = mv_local * fx
        
        if pos['is_special_financing']:
            # --- SPECIAL FINANCING: use file's collateral ---
            collateral_sgd = pos['margin_col_value'] * fx
            actual_fin = pos['actual_financing_pct']
            
            # IM = MV - Collateral (i.e., im_rate = 1 - actual_financing)
            if is_v_account:
                im_sgd = mv_sgd - (pos['margin_col_value'] * fx)
            else:
                im_sgd = pos['margin_col_value'] * fx
            mm_sgd = im_sgd  # MM = IM for special (conservative)
            
            # FM: derive proportionally. If actual financing maps to a known grade, use it.
            # Otherwise approximate: fm_rate ≈ im_rate * (fm/im ratio of nearest grade)
            grade_info = get_grade_info(pos['grade'])
            if grade_info['im'] > 0:
                fm_ratio_factor = grade_info['fm'] / grade_info['im']
            else:
                fm_ratio_factor = 1.0
            fm_sgd = mv_sgd  * fm_ratio_factor
            
            special_positions.append({
                **pos,
                'grade_name': f"Special ({actual_fin:.0f}%)",
                'fx_rate': fx,
                'mv_local': mv_local,
                'mv_sgd': mv_sgd,
                'im_sgd': im_sgd,
                'mm_sgd': mm_sgd,
                'fm_sgd': fm_sgd,
                'collateral_sgd': collateral_sgd,
            })
            
            calc_pos = {
                **pos,
                'grade_name': f"Special ({actual_fin:.0f}%)",
                'fx_rate': fx,
                'mv_local': mv_local,
                'mv_sgd': mv_sgd,
                'im_sgd': im_sgd,
                'mm_sgd': mm_sgd,
                'fm_sgd': fm_sgd,
            }
        else:
            # --- NORMAL: use grade-based calculation ---
            grade_info = get_grade_info(pos['grade'])
            im_sgd = mv_sgd * grade_info['im']
            mm_sgd = mv_sgd * grade_info['mm']
            fm_sgd = mv_sgd * grade_info['fm']
            
            calc_pos = {
                **pos,
                'grade_name': grade_info['name'],
                'fx_rate': fx,
                'mv_local': mv_local,
                'mv_sgd': mv_sgd,
                'im_sgd': im_sgd,
                'mm_sgd': mm_sgd,
                'fm_sgd': fm_sgd,
            }
        
        total_pv += mv_sgd
        total_im += im_sgd
        total_mm += mm_sgd
        total_fm += fm_sgd
        calc_positions.append(calc_pos)
    
    usable_cash = total_pv - total_im + net_amount
    is_margin_call = usable_cash < 0
    margin_call_amount = -(total_pv - total_mm + net_amount) if is_margin_call else 0
    available_buy_limit = net_amount + credit_limit
    buying_power = min(usable_cash, available_buy_limit)
    buying_power_no_margin = net_amount
    credit_capped = not is_margin_call and (usable_cash > available_buy_limit)
    
    mm_ratio = total_mm / total_pv if total_pv > 0 else 0
    fm_ratio = total_fm / total_pv if total_pv > 0 else 0
    lowest_pv_before_mc = -net_amount / (1 - mm_ratio) if mm_ratio < 1 else 0
    max_drop_before_mc = (total_pv - lowest_pv_before_mc) / total_pv if total_pv > 0 and lowest_pv_before_mc > 0 else 0
    lowest_pv_before_fs = -net_amount / (1 - fm_ratio) if fm_ratio < 1 else 0
    max_drop_before_fs = (total_pv - lowest_pv_before_fs) / total_pv if total_pv > 0 and lowest_pv_before_fs > 0 else 0
    
    return {
        'positions': calc_positions,
        'net_amount': net_amount,
        'special_positions': special_positions,
        'total_pv': total_pv, 'total_im': total_im,
        'total_mm': total_mm, 'total_fm': total_fm,
        'usable_cash': usable_cash,
        'is_margin_call': is_margin_call,
        'margin_call_amount': margin_call_amount,
        'available_buy_limit': available_buy_limit,
        'buying_power': buying_power,
        'buying_power_no_margin': buying_power_no_margin,
        'credit_capped': credit_capped,
        'mm_ratio': mm_ratio, 'fm_ratio': fm_ratio,
        'lowest_pv_before_mc': lowest_pv_before_mc,
        'max_drop_before_mc': max_drop_before_mc,
        'lowest_pv_before_fs': lowest_pv_before_fs,
        'max_drop_before_fs': max_drop_before_fs,
    }


def simulate_purchase(calc: dict, purchases: dict, fx_rates: dict) -> dict:
    total_purchase = sum(purchases.values())
    new_im = 0
    for grade, mv in purchases.items():
        grade_info = GRADES.get(grade, GRADES[0])
        new_im += mv * grade_info['im']
    
    new_total_im = calc['total_im'] + new_im
    new_total_pv = calc['total_pv'] + total_purchase
    new_net_amount = calc['net_amount'] - total_purchase
    new_usable_cash = new_total_pv - new_total_im + new_net_amount
    
    return {
        'total_purchase': total_purchase,
        'new_im': new_im,
        'new_usable_cash': new_usable_cash,
        'is_margin_call': new_usable_cash < 0,
        'exceeds_credit': total_purchase > calc['available_buy_limit'],
    }


def simulate_transfer(calc: dict, transfers: list, fx_rates: dict) -> dict:
    total_mv_out = 0
    total_im_out = 0
    
    for transfer in transfers:
        pos = transfer['position']
        qty = transfer['qty']
        if qty <= 0 or qty > pos['effective_qty']:
            continue
        grade_info = get_grade_info(pos['grade'])
        fx = fx_rates.get(pos['currency'], 1.0)
        mv_out = qty * pos['current_price'] * fx
        total_mv_out += mv_out
        total_im_out += mv_out * grade_info['im']
    
    new_total_pv = calc['total_pv'] - total_mv_out
    new_total_im = calc['total_im'] - total_im_out
    new_usable_cash = new_total_pv - new_total_im + calc['net_amount']
    
    return {
        'total_mv_out': total_mv_out,
        'total_im_out': total_im_out,
        'new_usable_cash': new_usable_cash,
        'is_margin_call': new_usable_cash < 0,
    }


# =============================================================================
# STREAMLIT APP
# =============================================================================

def main():
    st.set_page_config(page_title="Phillip Margin Calculator", page_icon="📊", layout="wide")
    
    st.title("📊 Phillip Securities Margin Calculator")
    st.caption("Cash Plus / Margin Account and Share Financing Account • Includes Equities + Bonds • Excludes Unit Trusts • Uses Prev Day Close Price")
    
    # Initialize session state
    for key, default in [
        ('price_change_pct', 0.0),
        ('positions', []), ('currencies', set()),
        ('fx_rates', DEFAULT_FX.copy()),
        ('net_amount', 0.0), ('credit_limit', 100000.0),
        ('is_v_account', False),
        # live what-if state
        ('base_positions', []), ('overrides', {}), ('editor_version', 0), ('file_sig', None),
        ('orders', []), ('order_seq', 0),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # Clear the new-order form after an order was added (must happen before the widgets render)
    if st.session_state.pop('_clear_order_form', False):
        st.session_state['order_name'] = ''
        st.session_state['order_code'] = ''
        st.session_state['order_qty'] = 0
        st.session_state['order_price'] = 0.0
        for k in [k for k in st.session_state.keys() if str(k).startswith('order_sell_qty_')]:
            st.session_state[k] = 0
    
    # ==========================================================================
    # SIDEBAR
    # ==========================================================================
    with st.sidebar:
        
        st.header("📁 Upload File")
        uploaded = st.file_uploader("ScripPositions.xlsx", type=['xlsx', 'xls'],
                                    help="Upload your ScripPositions file from POEMS")
        account_type = st.radio(
            "Account Type",
            options=["Margin / CashPlus", "V Account"],
            index=0,
            help="V Account: uses Qty on Hand only + detects special financing."
        )
        is_v_account = (account_type == "V Account")
        st.session_state.is_v_account = is_v_account
        
        if uploaded:
            # Parse only when a new file (or account type) arrives, so live overrides survive reruns
            file_sig = (uploaded.file_id, is_v_account)
            if file_sig != st.session_state.file_sig:
                positions, currencies = parse_scrip_positions(uploaded, is_v_account)
                if positions:
                    st.session_state.base_positions = positions
                    st.session_state.positions = positions
                    st.session_state.currencies = currencies
                    st.session_state.overrides = {}          # new file -> drop live overrides
                    st.session_state.editor_version += 1
                    st.session_state.file_sig = file_sig
            positions = st.session_state.base_positions
            if positions:
                equities = len([p for p in positions if p['type'] == 'Equity'])
                bonds = len([p for p in positions if p['type'] == 'Bond'])
                special_count = len([p for p in positions if p.get('is_special_financing')])
                st.success(f"✓ Loaded {len(positions)} positions")
                st.caption(f"Equities: {equities} | Bonds: {bonds}")
                if special_count > 0:
                    st.warning(f"⚡ {special_count} special financing counter(s) detected")
        
        st.divider()
        
        st.subheader("💰 Net Amount (a) +/- (b)")
        net_direction = st.selectbox("Credit or Debit?", options=["Credit", "Debit"],
                                     index=1 if st.session_state.net_amount < 0 else 0)
        net_value = st.number_input("Amount (SGD)",
                                    value=abs(st.session_state.net_amount) if st.session_state.net_amount != 0 else 0.0,
                                    min_value=0.0, step=1000.0, format="%.2f")
        st.session_state.net_amount = net_value if net_direction == "Credit" else -net_value
        st.caption(f"Net Amount: **${st.session_state.net_amount:,.2f}**")
        
        st.divider()
        
        st.subheader("🏦 Credit Limit")
        st.session_state.credit_limit = st.number_input(
            "Credit Limit (SGD)", value=st.session_state.credit_limit,
            min_value=0.0, step=10000.0, format="%.2f")
        
        st.divider()
        
        st.subheader("💱 FX Rates (to SGD)")
        all_currencies = set(st.session_state.currencies) | {o['currency'] for o in st.session_state.orders}
        for curr in sorted(all_currencies):
            if curr == 'SGD':
                st.text("SGD/SGD: 1.0000 (fixed)")
            else:
                default_rate = st.session_state.fx_rates.get(curr, DEFAULT_FX.get(curr, 1.0))
                st.session_state.fx_rates[curr] = st.number_input(
                    f"{curr}/SGD", value=default_rate,
                    min_value=0.0001, step=0.0001, format="%.7f", key=f"fx_{curr}")
        st.divider()

        st.subheader("⚡ Stress Test")
        st.caption(f"Current: **{st.session_state.price_change_pct:+.0f}%**")

        col_down, col_up = st.columns(2)
        with col_down:
            st.markdown("**📉 Drop**")
            for pct in [-5, -10, -20]:
                if st.button(f"{pct}%", key=f"stress_{pct}", width="stretch"):
                    st.session_state.price_change_pct = float(pct)
        with col_up:
            st.markdown("**📈 Rise**")
            for pct in [5, 10, 20]:
                if st.button(f"+{pct}%", key=f"stress_{pct}", width="stretch"):
                    st.session_state.price_change_pct = float(pct)

        if st.button("🔄 Reset to 0%", width="stretch"):
            st.session_state.price_change_pct = 0.0
            
        color = "#ef4444" if st.session_state.price_change_pct < 0 else "#22c55e" if st.session_state.price_change_pct > 0 else "#64748b"
        st.markdown(f"<p style='font-size:28px; font-weight:bold; text-align:center; color:{color};'>{st.session_state.price_change_pct:+.0f}%</p>", unsafe_allow_html=True)

    # ==========================================================================
    # MAIN CONTENT
    # ==========================================================================
    if not st.session_state.base_positions:
        st.info("👈 Upload your ScripPositions.xlsx file to begin")
        return

    # --- Live what-if: absorb Grade / Price edits made in the position table on the last run ---
    buy_orders = [o for o in st.session_state.orders if o.get('side', 'BUY') != 'SELL']
    editor_key = f"pos_editor_{st.session_state.editor_version}"
    editor_state = st.session_state.get(editor_key)
    if editor_state and editor_state.get('edited_rows'):
        n_file = len(st.session_state.base_positions)
        for row_idx, changes in editor_state['edited_rows'].items():
            i = int(row_idx)
            new_grade = GRADE_NAME_TO_PCT.get(changes['Grade']) if 'Grade' in changes else None
            new_price = changes.get('Price')
            new_price = float(new_price) if new_price is not None and float(new_price) > 0 else None
            if i < n_file:
                ov = st.session_state.overrides.setdefault(i, {})
                if new_grade is not None:
                    ov['grade'] = new_grade
                if new_price is not None:
                    ov['price'] = new_price
            elif i - n_file < len(buy_orders):
                order = buy_orders[i - n_file]
                if new_grade is not None:
                    order['grade'] = new_grade
                if new_price is not None:
                    order['mark_price'] = new_price
        st.session_state.editor_version += 1  # edits are baked in; render a fresh editor

    base_with_overrides = apply_overrides(st.session_state.base_positions, st.session_state.overrides)
    file_positions = apply_sell_orders(base_with_overrides, st.session_state.orders)   # pending sells reduce holdings
    st.session_state.positions = file_positions
    order_positions = [order_to_position(o) for o in buy_orders]                        # pending buys add positions
    order_costs = [order_cost(o, st.session_state.fx_rates) for o in st.session_state.orders]
    net_order_cash = sum(c['net_cash_sgd'] for c in order_costs)   # +cash out (buys) / -cash in (sells)
    effective_net = st.session_state.net_amount - net_order_cash

    calc = calculate_margin(
        st.session_state.price_change_pct,
        file_positions + order_positions,
        effective_net,
        st.session_state.credit_limit,
        st.session_state.fx_rates,
        st.session_state.is_v_account,
    )
    # Same account without the pending orders (for before / after comparisons)
    calc_no_orders = calc if not st.session_state.orders else calculate_margin(
        st.session_state.price_change_pct,
        base_with_overrides,
        st.session_state.net_amount,
        st.session_state.credit_limit,
        st.session_state.fx_rates,
        st.session_state.is_v_account,
    )
    # Baseline straight from the uploaded file: no overrides, no orders, 0% stress (drives the deltas in the summary)
    calc_file = calculate_margin(
        0.0,
        st.session_state.base_positions,
        st.session_state.net_amount,
        st.session_state.credit_limit,
        st.session_state.fx_rates,
        st.session_state.is_v_account,
    )

    def _delta(now: float, base: float):
        """Delta label for st.metric vs. the uploaded file; None hides the arrow when nothing changed."""
        return f"{now - base:+,.2f} vs file" if abs(now - base) > 0.005 else None

    # STATUS BANNER
    if calc['is_margin_call']:
        st.error(f"🚨 **MARGIN CALL** — Amount Required: **S${calc['margin_call_amount']:,.2f}**")
    elif calc['credit_capped']:
        st.warning(f"⚡ **CREDIT LIMIT CAPPED** — Max Purchase: **S${calc['available_buy_limit']:,.2f}**")
    else:
        st.success(f"✅ **NO MARGIN CALL** — Available Cash (w/o Margin): **S${calc['usable_cash']:,.2f}**")
    
    # ACCOUNT SUMMARY
    st.subheader("📊 Account Summary")
    col1, col3 = st.columns(2)
    with col1:
        st.metric("Portfolio Value", f"S${calc['total_pv']:,.2f}",
                  delta=_delta(calc['total_pv'], calc_file['total_pv']))
        st.metric("Initial Margin", f"S${calc['total_im']:,.2f}",
                  delta=_delta(calc['total_im'], calc_file['total_im']), delta_color="inverse")
    with col3:
        st.metric("Available Cash (w/o Margin)", f"S${calc['usable_cash']:,.2f}",
                  delta=_delta(calc['usable_cash'], calc_file['usable_cash']),
                  help="Usable Cash = Portfolio Value − Initial Margin + Net Amount")
        st.metric("Available Cash / Buying Power (with Margin)", f"S${calc['buying_power']:,.2f}",
                  delta=_delta(calc['buying_power'], calc_file['buying_power']),
                  help="Buying Power = MIN(Usable Cash, Net Amount + Credit Limit). "
                       "When Usable Cash is above the credit limit, this stays pinned at the limit "
                       "and price / grade changes will not move it.")
        if calc['credit_capped']:
            st.caption(
                f"🔒 Pinned at the credit limit (Net Amount + Credit Limit = S\\${calc['available_buy_limit']:,.2f}). "
                f"Usable Cash is S\\${calc['usable_cash']:,.2f}, so price / grade changes show up there, not here. "
                f"Raise the Credit Limit in the sidebar to see the full effect."
            )
    if st.session_state.orders:
        cash_word = "net cash out" if net_order_cash >= 0 else "net cash in"
        st.caption(
            f"🧾 Includes **{len(st.session_state.orders)} pending order(s)** with {cash_word} of "
            f"**S\\${abs(net_order_cash):,.2f}** (incl. brokerage). Net Amount used: **S\\${effective_net:,.2f}** "
            f"(was S\\${st.session_state.net_amount:,.2f})."   # backslash-dollar stops markdown treating $...$ as LaTeX
        )
    
    # POSITIONS TABLE (editable — live what-if on Grade / Price)
    st.subheader("📋 Position Details")
    st.caption(
        "✏️ **Live what-if:** double-click a **Grade** or **Price** cell, type the new value, then press **Enter** "
        "(or Tab, or click outside the table) to apply — the summary above shows the change vs. the uploaded file. "
        "Price is the Prev Day Close used for margin (the stress-test % is applied on top). "
        "Overriding a grade treats the counter as that grade (special financing is switched off for it). "
        "For **Order** rows, Price is the mark price used for valuation; the fill price stays in the order."
    )
    if calc['positions']:
        rows = []
        for cp in calc['positions']:
            if cp.get('is_order'):
                note = "🧾 pending order" + (" · re-marked" if cp.get('price_overridden') else "")
            else:
                flags = []
                if cp.get('grade_overridden'):
                    flags.append(f"grade {cp['file_grade']}% → {cp['grade']}%")
                if cp.get('price_overridden'):
                    flags.append(f"price {cp['file_price']:.4f} → {cp['price_used']:.4f}")
                if cp.get('is_special_financing'):
                    flags.append("⚡ special financing")
                if cp.get('pending_sell_qty'):
                    flags.append(f"🧾 pending sell −{cp['pending_sell_qty']:,}")
                note = " · ".join(flags)
            rows.append({
                'Type': cp['type'],
                'Name': cp['name'],
                'Code': cp['code'],
                'Curr': cp['currency'],
                'Qty': f"{cp['effective_qty']:,}",
                'Grade': GRADES[get_nearest_grade(cp['grade'])]['name'],
                'Price': float(cp['price_used']),
                'Financing': cp['grade_name'],
                'MV (SGD)': f"S${cp['mv_sgd']:,.2f}",
                'IM (SGD)': f"S${cp['im_sgd']:,.2f}",
                'Notes': note,
            })
        editor_key = f"pos_editor_{st.session_state.editor_version}"
        st.data_editor(
            pd.DataFrame(rows),
            key=editor_key,
            hide_index=True,
            num_rows="fixed",
            column_config={
                'Type': st.column_config.TextColumn('Type', width='small'),
                'Name': st.column_config.TextColumn('Name', width='medium'),
                'Code': st.column_config.TextColumn('Code', width='small'),
                'Curr': st.column_config.TextColumn('Curr', width='small'),
                'Qty': st.column_config.TextColumn('Qty', width='small'),
                'Grade': st.column_config.SelectboxColumn(
                    '✏️ Grade', options=GRADE_OPTION_NAMES, required=True,
                    help='Change to simulate a re-grading of this counter'),
                'Price': st.column_config.NumberColumn(
                    '✏️ Price', min_value=0.0, step=0.0001, format='%.4f', required=True,
                    help='Price used for margin (local currency, before stress-test %). Edit to simulate a price move.'),
                'Financing': st.column_config.TextColumn('Grade/Financing'),
                'MV (SGD)': st.column_config.TextColumn('MV (SGD)'),
                'IM (SGD)': st.column_config.TextColumn('IM (SGD)'),
                'Notes': st.column_config.TextColumn('Overrides / Notes', width='large'),
            },
            disabled=['Type', 'Name', 'Code', 'Curr', 'Qty', 'Financing', 'MV (SGD)', 'IM (SGD)', 'Notes'],
        )
        n_overridden = sum(1 for p in file_positions if p['grade_overridden'] or p['price_overridden'])
        n_remarked = sum(1 for o in st.session_state.orders if o.get('mark_price'))
        if n_overridden or n_remarked:
            c_reset, c_info = st.columns([1, 4])
            with c_reset:
                if st.button("↩️ Reset to file values", key="reset_overrides"):
                    st.session_state.overrides = {}
                    for o in st.session_state.orders:
                        o.pop('mark_price', None)
                    st.session_state.editor_version += 1
                    st.rerun()
            with c_info:
                st.caption(
                    f"{n_overridden} counter(s) overridden"
                    + (f", {n_remarked} order(s) re-marked" if n_remarked else "")
                    + " — values differ from the uploaded file."
                )

    # ==========================================================================
    # NEW ORDER SIMULATOR (pending buy orders consume cash / buying power)
    # ==========================================================================
    st.divider()
    st.subheader("🧾 New Order Simulator")
    st.caption(
        "Add a **buy** or **sell** order to see how it changes cash / buying power. "
        "**Buy:** cash out (qty × avg price + brokerage) is deducted from Net Amount and the shares are added as a "
        "pending **Order** position. **Sell:** the shares come off the existing holding and the proceeds less "
        "brokerage are added back to Net Amount. The status banner, Usable Cash and Buying Power above already "
        "include every order added here."
    )

    side = st.radio("Order Side", ["Buy", "Sell"], horizontal=True, key="order_side",
                    help="Buy: cash out, shares added as a pending position. "
                         "Sell: shares removed from an existing holding, proceeds less brokerage added back to cash.")
    is_sell = (side == "Sell")
    draft = None

    if is_sell:
        pending_sold = {}
        for o in st.session_state.orders:
            if o.get('side') == 'SELL':
                pending_sold[o['pos_index']] = pending_sold.get(o['pos_index'], 0) + int(o['qty'])
        sellable = [i for i, p in enumerate(base_with_overrides)
                    if p['effective_qty'] - pending_sold.get(i, 0) > 0]
        if not sellable:
            st.info("No holdings left to sell.")
        else:
            def _sell_label(i):
                p = base_with_overrides[i]
                return f"{p['name']} ({p['code']}) — {p['effective_qty'] - pending_sold.get(i, 0):,} available"
            sel_idx = st.selectbox("Counter to sell", sellable, format_func=_sell_label, key="order_sell_pos")
            sel_pos = base_with_overrides[sel_idx]
            available = sel_pos['effective_qty'] - pending_sold.get(sel_idx, 0)
            o_grade = get_nearest_grade(sel_pos['grade'])
            sc1, sc2, sc3, sc4 = st.columns(4)
            with sc1:
                o_qty = st.number_input("Quantity", min_value=0, max_value=int(available), step=100, value=0,
                                        key=f"order_sell_qty_{sel_idx}")
            with sc2:
                o_price = st.number_input("Sell Price (local ccy)", min_value=0.0, step=0.01,
                                          value=float(sel_pos['current_price']), format="%.4f",
                                          key=f"order_sell_price_{sel_idx}")
            with sc3:
                o_fee_pct = st.number_input("Brokerage (%)", min_value=0.0, step=0.01, value=0.28,
                                            format="%.3f", key="order_sell_fee_pct",
                                            help="Brokerage as a % of order value")
            with sc4:
                o_min_fee = st.number_input("Min Brokerage (local ccy)", min_value=0.0, step=1.0, value=0.0,
                                            format="%.2f", key="order_sell_min_fee",
                                            help="Fee = max(order value × %, minimum). Set % to 0 for a flat fee.")
            st.caption(
                f"{GRADES[o_grade]['name']} · {sel_pos['currency']} · holding {sel_pos['effective_qty']:,} shares"
                + (f" · {pending_sold[sel_idx]:,} already in pending sells" if pending_sold.get(sel_idx) else "")
            )
            draft = {
                'id': 0, 'side': 'SELL', 'pos_index': sel_idx,
                'name': sel_pos['name'], 'code': sel_pos['code'], 'currency': sel_pos['currency'],
                'grade': o_grade, 'qty': int(o_qty), 'avg_price': float(o_price),
                'fee_pct': float(o_fee_pct), 'min_fee': float(o_min_fee),
            }
    else:
        currency_options = sorted({'SGD', 'USD', 'HKD'} | set(st.session_state.currencies))
        oc1, oc2, oc3, oc4 = st.columns([2.5, 1.5, 1, 1.6])
        with oc1:
            o_name = st.text_input("Counter Name", key="order_name", placeholder="e.g. DBS GROUP HOLDINGS")
        with oc2:
            o_code = st.text_input("Stock Code / Ticker", key="order_code", placeholder="e.g. D05")
        with oc3:
            o_curr = st.selectbox("Currency", currency_options,
                                  index=currency_options.index('SGD') if 'SGD' in currency_options else 0,
                                  key="order_curr")
        with oc4:
            o_grade_name = st.selectbox("Grade", GRADE_OPTION_NAMES, index=1, key="order_grade")
        oc5, oc6, oc7, oc8 = st.columns(4)
        with oc5:
            o_qty = st.number_input("Quantity", min_value=0, step=100, value=0, key="order_qty")
        with oc6:
            o_price = st.number_input("Avg Price (local ccy)", min_value=0.0, step=0.01, value=0.0,
                                      format="%.4f", key="order_price")
        with oc7:
            o_fee_pct = st.number_input("Brokerage (%)", min_value=0.0, step=0.01, value=0.28,
                                        format="%.3f", key="order_fee_pct",
                                        help="Brokerage as a % of order value")
        with oc8:
            o_min_fee = st.number_input("Min Brokerage (local ccy)", min_value=0.0, step=1.0, value=0.0,
                                        format="%.2f", key="order_min_fee",
                                        help="Fee = max(order value × %, minimum). Set % to 0 for a flat fee.")
        draft = {
            'id': 0, 'side': 'BUY', 'pos_index': None,
            'name': o_name.strip() or 'NEW ORDER', 'code': o_code.strip().upper(),
            'currency': o_curr, 'grade': GRADE_NAME_TO_PCT[o_grade_name],
            'qty': int(o_qty), 'avg_price': float(o_price),
            'fee_pct': float(o_fee_pct), 'min_fee': float(o_min_fee),
        }

    if draft is not None and draft['qty'] > 0 and draft['avg_price'] > 0:
        d_cost = order_cost(draft, st.session_state.fx_rates)
        d_grade = GRADES[draft['grade']]
        d_im = d_cost['value_sgd'] * d_grade['im']
        if is_sell:
            preview_positions = apply_sell_orders(base_with_overrides, st.session_state.orders + [draft]) + order_positions
        else:
            preview_positions = file_positions + order_positions + [order_to_position(draft)]
        calc_after = calculate_margin(
            st.session_state.price_change_pct,
            preview_positions,
            effective_net - d_cost['net_cash_sgd'],
            st.session_state.credit_limit,
            st.session_state.fx_rates,
            st.session_state.is_v_account,
        )
        st.markdown("**Order Preview:**")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Order Value", f"S${d_cost['value_sgd']:,.2f}",
                  help=f"{draft['currency']} {d_cost['value_local']:,.2f} × FX {d_cost['fx']:.4f}")
        p2.metric("Brokerage", f"S${d_cost['fee_sgd']:,.2f}",
                  help=f"{draft['currency']} {d_cost['fee_local']:,.2f}")
        if is_sell:
            net_in = -d_cost['net_cash_sgd']   # positive = cash comes in, negative = brokerage exceeds proceeds
            p3.metric("Net Cash In" if net_in >= 0 else "Net Cash OUT",
                      f"S${abs(net_in):,.2f}" if net_in >= 0 else f"-S${abs(net_in):,.2f}",
                      help="Sell proceeds less brokerage. Negative when the brokerage is larger than the proceeds.")
            p4.metric("IM Released", f"S${d_im:,.2f}",
                      help=f"{d_grade['name']}: IM {d_grade['im']*100:.0f}% of the shares sold")
        else:
            p3.metric("Total Cash Out", f"S${d_cost['total_sgd']:,.2f}", help="Order value + brokerage")
            p4.metric("IM Required", f"S${d_im:,.2f}",
                      help=f"{d_grade['name']}: IM {d_grade['im']*100:.0f}% of order value")
        q1, q2, q3 = st.columns(3)
        q1.metric("Usable Cash after", f"S${calc_after['usable_cash']:,.2f}",
                  delta=f"{calc_after['usable_cash'] - calc['usable_cash']:,.2f}",
                  help="Buy: drops by IM + brokerage. Sell: rises by IM released less brokerage.")
        q2.metric("Buying Power after", f"S${calc_after['buying_power']:,.2f}",
                  delta=f"{calc_after['buying_power'] - calc['buying_power']:,.2f}")
        q3.metric("Available Buy Limit after", f"S${calc_after['available_buy_limit']:,.2f}",
                  delta=f"{calc_after['available_buy_limit'] - calc['available_buy_limit']:,.2f}",
                  help="Net Amount + Credit Limit, after the order's cash movement")

        if calc_after['is_margin_call']:
            st.error(f"❌ This order would leave the account in a **MARGIN CALL** of "
                     f"S${calc_after['margin_call_amount']:,.2f}")
        elif not is_sell and d_cost['total_sgd'] > calc['available_buy_limit']:
            st.warning(f"⚠️ Sufficient margin, but the order exceeds the **Credit Limit** "
                       f"(max purchase S${calc['available_buy_limit']:,.2f})")
        elif is_sell and d_cost['fee_sgd'] > d_cost['value_sgd']:
            st.warning(f"⚠️ Brokerage (S\\${d_cost['fee_sgd']:,.2f}) is larger than the sell proceeds "
                       f"(S\\${d_cost['value_sgd']:,.2f}) — this sell takes S\\${d_cost['fee_sgd'] - d_cost['value_sgd']:,.2f} "
                       f"out of the account. Check the Min Brokerage.")
        elif is_sell and calc_after['usable_cash'] < calc['usable_cash']:
            st.warning(f"⚠️ Brokerage exceeds the margin released — usable cash drops by "
                       f"S${calc['usable_cash'] - calc_after['usable_cash']:,.2f}")
        elif is_sell:
            st.success("✅ Sell order frees up cash and margin")
        else:
            st.success("✅ Order is within buying power and credit limit")

        if st.button("➕ Add Order to Portfolio", type="primary", key="add_order"):
            draft['id'] = new_order_id()
            st.session_state.orders.append(draft)
            if draft['currency'] not in st.session_state.fx_rates:
                st.session_state.fx_rates[draft['currency']] = DEFAULT_FX.get(draft['currency'], 1.0)
            st.session_state.editor_version += 1
            st.session_state._clear_order_form = True
            st.rerun()
    elif draft is not None:
        st.info("Enter a quantity and price to preview the order.")

    if st.session_state.orders:
        n_file = len(file_positions)
        st.markdown(f"**Pending Orders ({len(st.session_state.orders)}):**")
        widths = [0.7, 2.6, 1.4, 1, 1.2, 1.3, 1.1, 1.5, 1.5, 0.6]
        for col, title in zip(st.columns(widths), ["Side", "Counter", "Grade", "Qty", "Price", "Value (SGD)",
                                                   "Fee (SGD)", "Net Cash (SGD)", "IM Added / (Released)", ""]):
            col.markdown(f"**{title}**")
        remove_idx = None
        total_order_im = 0.0
        buy_j = 0
        for k, (o, c) in enumerate(zip(st.session_state.orders, order_costs)):
            is_sell_o = o.get('side') == 'SELL'
            if is_sell_o:
                im_k = -(c['value_sgd'] * GRADES[o['grade']]['im'])
            else:
                im_k = calc['positions'][n_file + buy_j]['im_sgd']
                buy_j += 1
            total_order_im += im_k
            r = st.columns(widths)
            r[0].text("SELL" if is_sell_o else "BUY")
            r[1].text(f"{o['name']} ({o['code']})" if o['code'] else o['name'])
            r[2].text(GRADES[o['grade']]['name'])
            r[3].text(f"{o['qty']:,}")
            r[4].text(f"{o['currency']} {o['avg_price']:.4f}")
            r[5].text(f"S${c['value_sgd']:,.2f}")
            r[6].text(f"S${c['fee_sgd']:,.2f}")
            r[7].text(f"S${c['total_sgd']:,.2f} {'in' if c['net_cash_sgd'] < 0 else 'out'}")
            r[8].text(f"(S${-im_k:,.2f})" if im_k < 0 else f"S${im_k:,.2f}")
            if r[9].button("🗑️", key=f"rm_order_{o['id']}", help="Remove this order"):
                remove_idx = k
        if remove_idx is not None:
            st.session_state.orders.pop(remove_idx)
            st.session_state.editor_version += 1
            st.rerun()

        st.markdown("**Impact of all pending orders (vs. without them):**")
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("Net Cash Out" if net_order_cash >= 0 else "Net Cash In", f"S${abs(net_order_cash):,.2f}",
                  help="Buys: value + brokerage out. Sells: value − brokerage in.")
        i2.metric("Net IM Change", f"S${total_order_im:,.2f}", help="IM added by buys less IM released by sells")
        i3.metric("Usable Cash", f"S${calc['usable_cash']:,.2f}",
                  delta=f"{calc['usable_cash'] - calc_no_orders['usable_cash']:,.2f}")
        i4.metric("Buying Power", f"S${calc['buying_power']:,.2f}",
                  delta=f"{calc['buying_power'] - calc_no_orders['buying_power']:,.2f}")
        if st.button("🗑️ Clear all orders", key="clear_orders"):
            st.session_state.orders = []
            st.session_state.editor_version += 1
            st.rerun()

# ==========================================================================
    # SPECIAL FINANCING TAB (V Account only)
    # ==========================================================================
    if calc['special_positions']:
        st.divider()
        st.subheader("⚡ Special Financing Counters")
       
        
        special_data = []
        for sp in calc['special_positions']:
            expected_grade = get_nearest_grade(sp['grade'])
            expected_fin = GRADE_FINANCING[expected_grade]
            actual_fin = sp['actual_financing_pct']
            
            special_data.append({
                'Counter': sp['name'],
                'Code': sp['code'],
                'Grade (Shown)': f"{sp['grade']}% ({GRADES[expected_grade]['name']})",
                'Expected Financing': f"{expected_fin*100:.0f}%",
                'Actual Financing': f"{actual_fin:.0f}%",
                'MV (Local)': f"{sp['currency']} {sp['mv_local']:,.2f}",
                'Expected Collateral': f"{sp['currency']} {sp['mv_local'] * expected_fin:,.2f}",
                'Actual Collateral': f"{sp['currency']} {sp['margin_col_value']:,.2f}",
            })
        
        st.dataframe(
            pd.DataFrame(special_data).style.map(
                lambda _: 'background-color: #fff3cd', subset=pd.IndexSlice[:, :]
            ),
            width="stretch", hide_index=True
        )
        
        # Impact summary
        total_col_diff = abs(sum(
            sp['mv_local'] * GRADE_FINANCING[get_nearest_grade(sp['grade'])] * st.session_state.fx_rates.get(sp['currency'] , 1.0) - sp['collateral_sgd'] 
            for sp in calc['special_positions']
        ))
    
        st.info (
            f"💡 **Impact**: Special financing reduces the collateral the client needs to maintain the position."
            f"Without it, based on the counter's grading, the client would need an additional **S${total_col_diff:,.2f}** in collateral, and the margin call would be higher."
        )
    
    st.divider()
    
    # ==========================================================================
    # MARGIN CALL vs NO MARGIN CALL SECTIONS
    # ==========================================================================
    
    if calc['is_margin_call']:
        # ==================================================================
        # MARGIN CALL: 4 TABS
        # ==================================================================
        st.subheader("🚨 Margin Call Settlement Options")
        
        tab_cash, tab_sell, tab_deposit, tab_combined = st.tabs([
            "💵 Deposit Cash",
            "📉 Sell Shares",
            "📈 Deposit Shares",
            "🔀 Combined (Cash + Sell)",
        ])
        
        # --- TAB 1: Deposit Cash ---
        with tab_cash:
            st.info(f"Deposit **S${calc['margin_call_amount']:,.2f}** to clear margin call")
        
        # --- TAB 2: Sell Shares ---
        with tab_sell:
            st.markdown("**Minimum Market Value of Shares to Sell (by Grade):**")
            sell_ref = []
            for grade_pct, info in sorted(GRADES.items(), reverse=True):
                sell_amt = calc['margin_call_amount'] * info['sell']
                sell_ref.append({'Grade': info['name'], 'Multiplier': f"{info['sell']}x",
                                 'Min Sell Amount': f"S${sell_amt:,.2f}"})
            st.dataframe(pd.DataFrame(sell_ref), width="stretch", hide_index=True)
            
            st.divider()
            st.markdown("**🔧 Sell Simulator**")
            st.caption("Prices are in **local currency** — edit to simulate different sell prices")
            
            sell_selections = []
            for i, pos in enumerate(calc['positions']):
                col_a, col_b, col_c = st.columns([3, 1.5, 1.5])
                with col_a:
                    label = f"{pos['name']} ({pos['code']}) - {pos['effective_qty']:,} shares"
                    if pos.get('is_special_financing'):
                        label += " ⚡"
                    st.text(label)
                    st.caption(f"{pos['currency']} | {pos['grade_name']}")
                with col_b:
                    sell_price = st.number_input(
                        f"Price ({pos['currency']})",
                        min_value=0.0,
                        value=float(pos['current_price']),
                        step=0.01, format="%.4f",
                        key=f"sell_price_{i}",
                        help=f"Sell price in {pos['currency']}")
                with col_c:
                    qty_to_sell = st.number_input(
                        "Sell Qty", min_value=0, max_value=pos['effective_qty'],
                        value=0, key=f"sell_{i}", label_visibility="collapsed")
                    if qty_to_sell > 0:
                        sell_selections.append({'position': pos, 'qty': qty_to_sell,
                                                'sell_price': sell_price})
            
            st.caption("Columns: Name | Sell Price (local currency) | Qty to Sell")
            
            if sell_selections:
                # total_sell_proceeds = 0
                # total_im_released = 0
                # if sell_selections:
                total_pv_sold = 0
                total_im_sold = 0
                total_mm_sold = 0
                
                for sel in sell_selections:
                    pos = sel['position']
                    qty = sel['qty']
                    price = sel['sell_price']
                    grade_info = get_grade_info(pos['grade'])
                    fx = st.session_state.fx_rates.get(pos['currency'], 1.0)
                    sell_mv_sgd = qty * price * fx
                    total_pv_sold += sell_mv_sgd
                    total_im_sold += sell_mv_sgd * grade_info['im']
                    total_mm_sold += sell_mv_sgd * grade_info['mm']
                
                # VBA formulas: shares leave portfolio, cash comes in
                new_pv = calc['total_pv'] - total_pv_sold
                new_im = calc['total_im'] - total_im_sold
                new_mm = calc['total_mm'] - total_mm_sold
                new_net = calc['net_amount'] + total_pv_sold  # sell proceeds add to cash
                new_usable_cash = new_pv - new_im + new_net            # O12
                new_mc_amount = -(new_pv - new_mm + new_net) if new_usable_cash < 0 else 0  # O9
                
                remaining_mc = new_mc_amount
                if remaining_mc <= 0:
                    st.success(f"✅ **Margin Call FULFILLED!** Client now has usable cash of **S${new_usable_cash:,.2f}**")
                else:
                    st.error(f"❌ Need to sell more! Remaining MC: S${remaining_mc:,.2f}")
                # for sel in sell_selections:
                #     pos = sel['position']
                #     qty = sel['qty']
                #     price = sel['sell_price']
                #     grade_info = get_grade_info(pos['grade'])
                #     fx = st.session_state.fx_rates.get(pos['currency'], 1.0)
                #     proceeds = qty * price * fx
                #     im_released = proceeds * (1 - grade_info['im'])
                #     total_sell_proceeds += proceeds
                #     total_im_released += im_released
                # #TODO: HELP TO CALCULATE THIS TODO
                # new_usable_cash 
                 
            # new_pv = calc['total_pv'] - total_sell_sgd
            # new_im = calc['total_im'] - total_im_released
            # new_mm = calc['total_mm'] - total_mm_released
            # new_net = st.session_state.net_amount + total_sell_sgd + cash_deposit
            # new_usable_cash = new_pv + new_net -new_im
            # new_mc_amount = -(new_pv - new_mm + new_net) if new_usable_cash < 0 else 0
            
                # remaining_mc = calc['margin_call_amount'] - total_sell_proceeds + total_im_released
                 
                
                
        
        # --- TAB 3: Deposit Shares ---
        with tab_deposit:
            st.markdown("**Minimum Market Value of Shares to Deposit (by Grade):**")
            dep_data = []
            for grade_pct, info in sorted(GRADES.items(), reverse=True):
                if info['deposit'] is None:
                    dep_data.append({'Grade': info['name'], 'Multiplier': 'N/A',
                                     'Min Deposit': 'Cannot deposit Grade C shares'})
                else:
                    dep_data.append({
                        'Grade': info['name'], 'Multiplier': f"{info['deposit']}x",
                        'Min Deposit': f"S${info['deposit'] * calc['margin_call_amount']:,.2f}"})
            st.dataframe(pd.DataFrame(dep_data), width="stretch", hide_index=True)
        
        # --- TAB 4: Combined Cash + Sell ---
        with tab_combined:
            st.markdown("**Combine cash deposit and share sales to settle the margin call**")
            st.caption("Useful when you want to partially pay cash and partially sell shares")
            
            cash_deposit = st.number_input(
                "💵 Cash Deposit (SGD)", min_value=0.0,
                max_value=float(calc['margin_call_amount'] * 2),
                value=0.0, step=1000.0, format="%.2f", key="combined_cash",
                help="Cash amount to deposit")
            
            st.markdown("**📉 Shares to Sell:**")
            st.caption("Prices are in **local currency**")
            
            ch1, ch2, ch3, ch4 = st.columns([3, 1, 1.5, 1.5])
            ch1.markdown("**Name**")
            ch2.markdown("**Curr**")
            ch3.markdown("**Sell Price**")
            ch4.markdown("**Qty to Sell**")
            
            combined_sell_items = []
            for i, pos in enumerate(calc['positions']):
                col_name, col_curr, col_price, col_qty = st.columns([3, 1, 1.5, 1.5])
                with col_name:
                    label = f"{pos['name']} ({pos['code']})"
                    if pos.get('is_special_financing'):
                        label += " ⚡"
                    st.text(label)
                    st.caption(f"{pos['effective_qty']:,} shares | {pos['grade_name']}")
                with col_curr:
                    st.text(pos['currency'])
                with col_price:
                    comb_sell_price = st.number_input(
                        f"Price ({pos['currency']})", min_value=0.0,
                        value=float(pos['current_price']),
                        step=0.01, format="%.4f",
                        key=f"comb_price_{i}", label_visibility="collapsed")
                with col_qty:
                    comb_qty = st.number_input(
                        "Qty", min_value=0, max_value=pos['effective_qty'],
                        value=0, key=f"comb_qty_{i}", label_visibility="collapsed")
                    if comb_qty > 0:
                        combined_sell_items.append({'position': pos, 'qty': comb_qty,
                                                    'sell_price': comb_sell_price})
            
            total_sell_sgd = 0.0
            total_im_released = 0.0
            total_mm_released = 0.0
            sell_breakdown = []
            
            for item in combined_sell_items:
                pos = item['position']
                qty = item['qty']
                price = item['sell_price']
                fx = st.session_state.fx_rates.get(pos['currency'], 1.0)
                grade_info = get_grade_info(pos['grade'])
                
                proceeds_local = qty * price
                proceeds_sgd = proceeds_local * fx
                print(proceeds_sgd)
                im_rel = proceeds_sgd * (grade_info['im'])
                print(im_rel)
                mm_rel = proceeds_sgd * (grade_info['im'])
                
                total_sell_sgd += im_rel
                total_im_released += im_rel
                total_mm_released += mm_rel
                
                sell_breakdown.append({
                    'Counter': f"{pos['name']} ({pos['code']})",
                    'Qty': f"{qty:,}",
                    'Sell Price': f"{pos['currency']} {price:.4f}",
                    'Grade Info': f"{grade_info["name"]}",
                    'Settlement (SGD)': f"S${im_rel:,.2f}",
                })
            
            if sell_breakdown:
                st.markdown("**Sell Breakdown:**")
                st.dataframe(pd.DataFrame(sell_breakdown), width="stretch",
                             hide_index=True)
            
            new_pv = calc['total_pv'] - total_sell_sgd
            new_im = calc['total_im'] - total_im_released
            new_mm = calc['total_mm'] - total_mm_released
            new_net = calc['net_amount'] + total_sell_sgd + cash_deposit
            new_usable_cash = new_pv + new_net -new_im
            new_mc_amount = -(new_pv - new_mm + new_net) if new_usable_cash < 0 else 0
            
            st.divider()
            st.markdown("### 📊 Combined Settlement Summary")
            s1, s2, s3 = st.columns(3)
            s1.metric("Cash Deposit", f"S${cash_deposit:,.2f}")
            s2.metric("Sell Proceeds", f"S${total_sell_sgd:,.2f}")
            s3.metric("Total Settlement", f"S${cash_deposit + total_sell_sgd:,.2f}")
            
            if new_usable_cash >= 0:
                st.success(f"✅ **Margin Call FULFILLED!** Client now has usable cash of **S${new_usable_cash:,.2f}**")
            else:
                st.error(f"❌ **Margin Call NOT fulfilled.** Remaining MC Amount: S${new_mc_amount:,.2f}")
    
    else:
        # ==================================================================
        # NO MARGIN CALL
        # ==================================================================
        is_special_financing = any(p.get('is_special_financing') for p in calc['positions'])
        if is_special_financing:
            st.subheader("💡 Disclaimer for TR, Check with Back Office for more details on Margin Call / Force Selling!")
        
            st.divider()

        else: 
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("📉 Distance to Margin Call")
                if calc['net_amount'] >= 0:
                    st.info("💡 No margin call possible — Net Amount is positive")
                elif calc['mm_ratio'] >= 1:
                    st.info("💡 Portfolio is 100% Grade C — no margin call as long as Net Amount is positive")
                else:
                    st.markdown(f"""
                        <p style='font-size:16px; font-weight:bold; margin-bottom:0;'>Max % Drop Before Margin Call</p>
                        <p style='font-size:36px; font-weight:bold; margin-top:0; margin-bottom:0;'>{calc['max_drop_before_mc']*100:.2f}%</p>
                        <p style='font-size:13px; font-style:italic; color:gray; margin-top:0;'>(Max % current portfolio can drop before margin call)</p>
                        <p style='font-size:16px; font-weight:bold; margin-bottom:0;'>Lowest Portfolio Value Before Margin Call</p>
                        <p style='font-size:36px; font-weight:bold; margin-top:0; margin-bottom:0;'>S${calc['lowest_pv_before_mc']:,.2f}</p>
                        <p style='font-size:13px; font-style:italic; color:gray; margin-top:0;'>(The lowest value the portfolio can reach before margin call)</p>
                    """, unsafe_allow_html=True)
            
            with col2:
                st.subheader("📉 Distance to Force Sell")
                if calc['net_amount'] >= 0:
                    st.info("💡 No force sell possible — Net Amount is positive")
                elif calc['fm_ratio'] >= 1:
                    st.info("💡 Portfolio is 100% Grade C — no force sell as long as Net Amount is positive")
                else:
                    st.markdown(f"""
                        <p style='font-size:16px; font-weight:bold; margin-bottom:0;'>Max % Drop Before Force Sell</p>
                        <p style='font-size:36px; font-weight:bold; margin-top:0; margin-bottom:0;'>{calc['max_drop_before_fs']*100:.2f}%</p>
                        <p style='font-size:13px; font-style:italic; color:gray; margin-top:0;'>(Max % current portfolio can drop before force sell)</p>
                        <p style='font-size:16px; font-weight:bold; margin-bottom:0;'>Lowest Portfolio Value Before Force Sell</p>
                        <p style='font-size:36px; font-weight:bold; margin-top:0; margin-bottom:0;'>S${calc['lowest_pv_before_fs']:,.2f}</p>
                        <p style='font-size:13px; font-style:italic; color:gray; margin-top:0;'>(The lowest value the portfolio can reach before force sell)</p>
                    """, unsafe_allow_html=True)
            
            st.divider()
        
        # PURCHASE CAPACITY
        st.subheader("💰 Purchase Capacity")
        if calc['credit_capped']:
            st.warning(f"⚠️ You are capped by credit limit. Max purchase: S${calc['available_buy_limit']:,.2f}")
        
        st.markdown("**Maximum Purchase by Grade (with Current Available Cash):**")
        base_cash = max(0, calc['usable_cash'])
        purchase_data = []
        for grade_pct, info in sorted(GRADES.items(), reverse=True):
            max_buy = base_cash * info['purchase']
            purchase_data.append({'Grade': info['name'], 'Multiplier': f"{info['purchase']}x",
                                  'Max Purchase': f"S${max_buy:,.2f}"})
        st.dataframe(pd.DataFrame(purchase_data), width="stretch", hide_index=True)
        
        st.divider()
        
        # PURCHASE SIMULATOR
        st.subheader("🛒 Purchase Simulator")
        st.caption("Enter market value of shares you wish to purchase for each grade")
        
        purchase_inputs = {}
        col1, col2 = st.columns(2)
        with col1:
            for grade_pct in [80, 70, 50]:
                info = GRADES[grade_pct]
                purchase_inputs[grade_pct] = st.number_input(
                    f"{info['name']} (SGD)", min_value=0.0, step=1000.0,
                    format="%.2f", key=f"purchase_{grade_pct}")
        with col2:
            for grade_pct in [30, 0]:
                info = GRADES[grade_pct]
                purchase_inputs[grade_pct] = st.number_input(
                    f"{info['name']} (SGD)", min_value=0.0, step=1000.0,
                    format="%.2f", key=f"purchase_{grade_pct}")
        
        if st.button("Calculate Purchase", type="primary"):
            result = simulate_purchase(calc, purchase_inputs, st.session_state.fx_rates)
            if result['total_purchase'] == 0:
                st.info("Enter purchase amounts to simulate")
            elif result['is_margin_call']:
                st.error("❌ Cannot Buy - Margin Call will be triggered!")
                st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")
            elif result['exceeds_credit']:
                st.warning("⚠️ You have sufficient buying power but will exceed Credit Limit!")
                st.caption(f"Total purchase: S\\${result['total_purchase']:,.2f} > Limit: S\\${calc['available_buy_limit']:,.2f}")
            else:
                st.success("✅ You can purchase these shares!")
                st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")
        
        st.divider()
        
        # TRANSFER OUT SIMULATOR
        st.subheader("📤 Transfer Out Simulator")
        st.caption("Check if transferring shares out will trigger margin call")
        
        transfer_selections = []
        for i, pos in enumerate(calc['positions']):
            col_a, col_b = st.columns([3, 1])
            with col_a:
                label = f"{pos['name']} ({pos['code']}) - {pos['effective_qty']:,} shares"
                if pos.get('is_special_financing'):
                    label += " ⚡"
                st.text(label)
            with col_b:
                qty_to_transfer = st.number_input(
                    "Transfer Qty", min_value=0, max_value=pos['effective_qty'],
                    value=0, key=f"transfer_{i}", label_visibility="collapsed")
                if qty_to_transfer > 0:
                    transfer_selections.append({'position': pos, 'qty': qty_to_transfer})
        
        if st.button("Check Transfer", type="secondary"):
            if not transfer_selections:
                st.info("Select shares to transfer out")
            else:
                result = simulate_transfer(calc, transfer_selections, st.session_state.fx_rates)
                if result['is_margin_call']:
                    st.error("❌ Cannot Transfer - Margin Call will be triggered!")
                    st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")
                else:
                    st.success("✅ Safe to Transfer!")
                    st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")


if __name__ == "__main__":
    main()
