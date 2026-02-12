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

VBA FORMULAS:
- Usable Cash (O12) = Portfolio Value - IM + Net Amount
- Margin Call? (O8) = IF(Usable Cash < 0, "Yes", "No")
- Margin Call Amount (O9) = -(Portfolio Value - MM + Net Amount)
- Available Buy Limit (O15) = Net Amount + Credit Limit
- Buying Power (O18) = MIN(Usable Cash, Available Buy Limit)

Run: streamlit run margin_app.py
"""

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

    is_special = round(expected, 2) != round(actual, 2) and actual > 0

    # Derive actual financing %
    if mv_local > 0:
        if is_v_account:
            actual_financing = actual / mv_local          # collateral / MV
        else:
            actual_financing = 1 - (actual / mv_local)    # 1 - (IM / MV)
    else:
        actual_financing = 0.0

    return {
        'is_special': is_special,
        'grade_shown': nearest,
        'expected_financing': financing_pct,
        'actual_financing': actual_financing,
        'expected_value': expected,
        'actual_value': actual,
        'check_type': 'collateral' if is_v_account else 'IM',
    }

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
        
        # Detect: pass same value to both params
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
                'grade_name': f"Special ({actual_fin*100:.0f}%)",
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
                'grade_name': f"Special ({actual_fin*100:.0f}%)",
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
    new_net_amount = st.session_state.net_amount - total_purchase
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
    new_usable_cash = new_total_pv - new_total_im + st.session_state.net_amount
    
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
    ]:
        if key not in st.session_state:
            st.session_state[key] = default
    
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
            positions, currencies = parse_scrip_positions(uploaded, is_v_account)
            if positions:
                st.session_state.positions = positions
                st.session_state.currencies = currencies
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
        all_currencies = st.session_state.currencies
        for curr in sorted(all_currencies):
            if curr == 'SGD':
                st.text("SGD/SGD: 1.0000 (fixed)")
            else:
                default_rate = st.session_state.fx_rates.get(curr, DEFAULT_FX.get(curr, 1.0))
                st.session_state.fx_rates[curr] = st.number_input(
                    f"{curr}/SGD", value=default_rate,
                    min_value=0.0001, step=0.0001, format="%.4f", key=f"fx_{curr}")
        st.divider()

        st.subheader("⚡ Stress Test")
        st.caption(f"Current: **{st.session_state.price_change_pct:+.0f}%**")

        col_down, col_up = st.columns(2)
        with col_down:
            st.markdown("**📉 Drop**")
            for pct in [-5, -10, -20]:
                if st.button(f"{pct}%", key=f"stress_{pct}", use_container_width=True):
                    st.session_state.price_change_pct = float(pct)
        with col_up:
            st.markdown("**📈 Rise**")
            for pct in [5, 10, 20]:
                if st.button(f"+{pct}%", key=f"stress_{pct}", use_container_width=True):
                    st.session_state.price_change_pct = float(pct)

        if st.button("🔄 Reset to 0%", use_container_width=True):
            st.session_state.price_change_pct = 0.0
            
        color = "#ef4444" if st.session_state.price_change_pct < 0 else "#22c55e" if st.session_state.price_change_pct > 0 else "#64748b"
        st.markdown(f"<p style='font-size:28px; font-weight:bold; text-align:center; color:{color};'>{st.session_state.price_change_pct:+.0f}%</p>", unsafe_allow_html=True)

    # ==========================================================================
    # MAIN CONTENT
    # ==========================================================================
    if not st.session_state.positions:
        st.info("👈 Upload your ScripPositions.xlsx file to begin")
        return
    
    calc = calculate_margin(
        st.session_state.price_change_pct,
        st.session_state.positions,
        st.session_state.net_amount,
        st.session_state.credit_limit,
        st.session_state.fx_rates,
        st.session_state.is_v_account,
    )
    
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
        st.metric("Portfolio Value", f"S${calc['total_pv']:,.2f}")
        st.metric("Initial Margin", f"S${calc['total_im']:,.2f}")
    with col3:
        st.metric("Available Cash (w/o Margin)", f"S${calc['usable_cash']:,.2f}")
        st.metric("Available Cash / Buying Power (with Margin)", f"S${calc['buying_power']:,.2f}")
    
    # POSITIONS TABLE
    st.subheader("📋 Position Details")
    if calc['positions']:
        pos_df = pd.DataFrame(calc['positions'])
        display_df = pos_df[['type', 'name', 'code', 'grade', 'grade_name',
                             'effective_qty', 'prev_close', 'currency', 'mv_sgd']].copy()
        display_df.columns = ['Type', 'Name', 'Code', 'Grade', 'Grade/Financing',
                              'Qty', 'Prev Close', 'Curr', 'MV (SGD)']
        display_df['Grade'] = display_df['Grade'].apply(lambda x: f"{x}%")

        # Highlight special financing rows
        def highlight_special(row):
            if 'Special' in str(row['Grade/Financing']):
                return ['background-color: #fff3cd'] * len(row)
            return [''] * len(row)

        st.dataframe(
            display_df.style
                .format({'Qty': '{:,.0f}', 'Prev Close': '{:.4f}', 'MV (SGD)': 'S${:,.2f}'})
                .apply(highlight_special, axis=1),
            use_container_width=True, hide_index=True
        )
    
    # ==========================================================================
    # SPECIAL FINANCING TAB (V Account only)
    # ==========================================================================
    if calc['special_positions']:
        st.divider()
        st.subheader("⚡ Special Financing Counters")
        st.caption(
            "These positions have a **Grade C** classification but carry non-zero collateral "
            "in Phillip's system. The app uses the **file's actual collateral** instead of "
            "the grade-based calculation for accuracy."
        )
        
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
                'Actual Financing': f"{actual_fin*100:.0f}%",
                'MV (Local)': f"{sp['currency']} {sp['mv_local']:,.2f}",
                'Expected Collateral': f"S${sp['mv_local'] * expected_fin:,.2f}",
                'Actual Collateral': f"S${sp['margin_col_value']:,.2f}",
            })
        
        st.dataframe(
            pd.DataFrame(special_data).style.applymap(
                lambda _: 'background-color: #fff3cd', subset=pd.IndexSlice[:, :]
            ),
            use_container_width=True, hide_index=True
        )
        
        # Impact summary
        total_col_diff = sum(
            sp['collateral_sgd'] - sp['mv_local'] * GRADE_FINANCING[get_nearest_grade(sp['grade'])] * st.session_state.fx_rates.get(sp['currency'], 1.0)
            for sp in calc['special_positions']
        )
        st.info(
            f"💡 **Impact**: Special financing adds **S${total_col_diff:,.2f}** more collateral "
            f"than the grade alone would give. Without this adjustment, the margin call would "
            f"be overstated."
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
            st.dataframe(pd.DataFrame(sell_ref), use_container_width=True, hide_index=True)
            
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
                total_sell_proceeds = 0
                total_im_released = 0
                
                for sel in sell_selections:
                    pos = sel['position']
                    qty = sel['qty']
                    price = sel['sell_price']
                    grade_info = get_grade_info(pos['grade'])
                    fx = st.session_state.fx_rates.get(pos['currency'], 1.0)
                    proceeds = qty * price * fx
                    im_released = proceeds * (1 - grade_info['im'])
                    total_sell_proceeds += proceeds
                    total_im_released += im_released
                
                remaining_mc = calc['margin_call_amount'] - total_sell_proceeds + total_im_released
                
                if remaining_mc <= 0:
                    st.success(f"✅ Margin Call FULFILLED! Selling releases S${total_sell_proceeds:,.2f}")
                else:
                    st.error(f"❌ Need to sell more! Remaining MC: S${remaining_mc:,.2f}")
        
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
            st.dataframe(pd.DataFrame(dep_data), use_container_width=True, hide_index=True)
        
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
                    'Proceeds (SGD)': f"S${im_rel:,.2f}",
                })
            
            if sell_breakdown:
                st.markdown("**Sell Breakdown:**")
                st.dataframe(pd.DataFrame(sell_breakdown), use_container_width=True,
                             hide_index=True)
            
            new_pv = calc['total_pv'] - total_sell_sgd
            new_im = calc['total_im'] - total_im_released
            new_mm = calc['total_mm'] - total_mm_released
            new_net = st.session_state.net_amount + total_sell_sgd + cash_deposit
            new_usable_cash = new_pv + new_net -new_im
            new_mc_amount = -(new_pv - new_mm + new_net) if new_usable_cash < 0 else 0
            
            st.divider()
            st.markdown("### 📊 Combined Settlement Summary")
            s1, s2, s3 = st.columns(3)
            s1.metric("Cash Deposit", f"S${cash_deposit:,.2f}")
            s2.metric("Sell Proceeds", f"S${total_sell_sgd:,.2f}")
            s3.metric("Total Settlement", f"S${cash_deposit + total_sell_sgd:,.2f}")
            
            if new_usable_cash >= 0:
                st.success(f"✅ **Margin Call FULFILLED!** New Usable Cash: S${new_usable_cash:,.2f}")
            else:
                st.error(f"❌ **Margin Call NOT fulfilled.** Remaining MC Amount: S${new_mc_amount:,.2f}")
                st.caption(f"New Usable Cash: S${new_usable_cash:,.2f}")
    
    else:
        # ==================================================================
        # NO MARGIN CALL
        # ==================================================================
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("📉 Distance to Margin Call")
            if st.session_state.net_amount >= 0:
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
            if st.session_state.net_amount >= 0:
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
        st.dataframe(pd.DataFrame(purchase_data), use_container_width=True, hide_index=True)
        
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
                st.caption(f"Total purchase: S${result['total_purchase']:,.2f} > Limit: S${calc['available_buy_limit']:,.2f}")
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