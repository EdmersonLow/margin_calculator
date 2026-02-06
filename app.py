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
  (This matches Phillip's Account Details Report)

VBA FORMULAS:
- Usable Cash (O12) = Portfolio Value - IM + Net Amount
- Margin Call? (O8) = IF(Usable Cash < 0, "Yes", "No")
- Margin Call Amount (O9) = -(Portfolio Value - MM + Net Amount)
- Available Buy Limit (O15) = Net Amount + Credit Limit
- Buying Power (O18) = MIN(Usable Cash, Available Buy Limit)

NOTE: Small variance (~0.05%) from Phillip's exact values may occur due to
FX rate timing differences and rounding.

Run: streamlit run margin_calculator.py
"""

import streamlit as st
import pandas as pd
import numpy as np

# =============================================================================
# REFERENCE DATA (from REFERENCE sheet)
# =============================================================================

GRADES = {
    80: {'name': 'Grade S (80%)', 'im': 0.20, 'mm': 0.20, 'fm': 0.1304, 'sell': 5.000, 'deposit': 1.250, 'purchase': 5.000},
    70: {'name': 'Grade A (70%)', 'im': 0.30, 'mm': 0.30, 'fm': 0.2391, 'sell': 3.333, 'deposit': 1.429, 'purchase': 3.333},
    50: {'name': 'Grade B (50%)', 'im': 0.50, 'mm': 0.50, 'fm': 0.4565, 'sell': 2.000, 'deposit': 2.000, 'purchase': 2.000},
    30: {'name': 'Grade E (30%)', 'im': 0.70, 'mm': 0.70, 'fm': 0.6739, 'sell': 1.429, 'deposit': 3.333, 'purchase': 1.429},
    0:  {'name': 'Grade C (0%)', 'im': 1.00, 'mm': 1.00, 'fm': 1.0000, 'sell': 1.000, 'deposit': None, 'purchase': 1.000},
}

DEFAULT_FX = {'SGD': 1.0, 'USD': 1.3374, 'HKD': 0.1626}


def parse_number(val) -> float:
    # print("raw float number" + val)
    """Parse number from various formats including comma-separated and (negative) notation"""
    if pd.isna(val) or val is None or val == '' or val == '-':
        return 0.0
    val_str = str(val).replace(',', '').strip()
    if val_str.startswith('(') and val_str.endswith(')'):
        val_str = '-' + val_str[1:-1]
    # print("raw float number" + float(val_str))
    try:
        return float(val_str)
    except:
        return 0.0


def parse_grade(val) -> int:
    """Parse grade from percentage string like '80%' or decimal like 0.8"""
    if pd.isna(val) or val in [None, '', '-']:
        return 0
    try:
        val_str = str(val).replace('%', '').strip()
        num = float(val_str)
        return int(num) if num > 1 else int(num * 100)
    except:
        return 0


def get_grade_info(grade_pct: int) -> dict:
    """Get grade info for a given percentage, snapping to nearest grade"""
    levels = [80, 70, 50, 30, 0]
    closest = min(levels, key=lambda x: abs(x - grade_pct))
    return GRADES[closest]


def parse_scrip_positions(uploaded_file) -> tuple:
    """
    Parse ScripPositions.xlsx file
    
    Structure:
    - SG, HK, US sections: Equities (INCLUDE)
    - ZZ section: Bonds (INCLUDE) - use Previous Day Closing Price
    - After GRAND TOTAL: Unit Trusts (EXCLUDE)
    
    Returns:
        positions: List of position dicts
        currencies: Set of unique currencies
    """
    df = pd.read_excel(uploaded_file, header=None)
    
    # Find header row (contains "Company Name")
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
    grand_total_reached = False
    
    for i in range(header_idx + 1, len(df)):
        row = df.iloc[i]
        col0 = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ''
        
        # Stop at GRAND TOTAL - everything after is Unit Trusts
        if 'GRAND TOTAL' in col0.upper():
            grand_total_reached = True
            break
        
        # Detect section headers
        if col0 in ['SG', 'HK', 'US']:
            current_section = col0
            is_bond_section = False
            continue
        
        # ZZ marks the bond section
        if col0 == 'ZZ':
            current_section = 'ZZ'
            is_bond_section = True
            continue
        
        # Skip TOTAL rows and empty rows
        if 'TOTAL' in col0.upper() or not col0:
            continue
        
        # Parse position row
        name = col0
        grade = parse_grade(row.iloc[2])
        code = str(row.iloc[3]).strip() if pd.notna(row.iloc[3]) else ''
        qty_on_hand = parse_number(row.iloc[4])
        currency = str(row.iloc[6]).strip().upper() if pd.notna(row.iloc[6]) else 'SGD'
        prev_close = parse_number(row.iloc[8])
        print("current_price_raw  " + str(row.iloc[16]))
        current_price = parse_number(row.iloc[16]) if len(row) > 16 else 0
        print("current_price_after processing  " + str(current_price)) 
        # Unsettled positions
        unsettled_purch = parse_number(row.iloc[13]) if len(row) > 13 else 0
        unsettled_sales = parse_number(row.iloc[14]) if len(row) > 14 else 0
        
        # Calculate effective quantity
        # Total Quantity = Qty on Hand (a) + Unsettled Purchases (b) + Unsettled Sales (c)
        # Note: Unsettled Sales is negative
        effective_qty = qty_on_hand + unsettled_purch + unsettled_sales
        
        # Skip if net position is zero or negative
        if effective_qty <= 0:
            continue
        
        # Determine price to use
        # For MARGIN CALCULATION: Always use Previous Day Closing Price
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
            'price_used': price,  # Always prev_close for margin calculation
        })
    
    return positions, currencies


def calculate_margin(positions: list, net_amount: float, credit_limit: float, fx_rates: dict) -> dict:
    """
    Calculate margin status using VBA formulas
    
    Key formulas:
    - Usable Cash (O12) = PV - IM + Net Amount
    - Margin Call? (O8) = IF(Usable Cash < 0, "Yes", "No")  
    - Margin Call Amount (O9) = -(PV - MM + Net Amount)
    - Available Buy Limit (O15) = Net Amount + Credit Limit
    - Buying Power (O18) = MIN(Usable Cash, Available Buy Limit)
    """
    if not positions:
        usable_cash = net_amount
        return {
            'positions': [],
            'total_pv': 0,
            'total_im': 0,
            'total_mm': 0,
            'total_fm': 0,
            'usable_cash': usable_cash,
            'is_margin_call': usable_cash < 0,
            'margin_call_amount': max(0, -usable_cash),
            'available_buy_limit': net_amount + credit_limit,
            'buying_power': min(usable_cash, net_amount + credit_limit),
            'buying_power_no_margin': net_amount,
            'credit_capped': False,
            'mm_ratio': 0,
            'fm_ratio': 0,
            'lowest_pv_before_mc': 0,
            'max_drop_before_mc': 0,
            'lowest_pv_before_fs': 0,
            'max_drop_before_fs': 0,
        }
    
    total_pv = 0
    total_im = 0
    total_mm = 0
    total_fm = 0
    calc_positions = []
    for pos in positions:
        grade_info = get_grade_info(pos['grade'])
        fx = fx_rates.get(pos['currency'], 1.0)
        print("fx" + str(fx))
        mv_local = pos['effective_qty'] * pos['price_used']
        print("effective qty" + str(pos['effective_qty']))
        print("ystd price" + str(pos['current_price']))
        print("mv_local" + str(mv_local))
        mv_sgd = mv_local * fx
        print("market value based on yesterday price" + str(mv_sgd))
    
        im_sgd = mv_sgd * grade_info['im']
        mm_sgd = mv_sgd * grade_info['mm']
        fm_sgd = mv_sgd * grade_info['fm']
        
        total_pv += mv_sgd
        total_im += im_sgd
        total_mm += mm_sgd
        total_fm += fm_sgd
        
        calc_positions.append({
            **pos,
            'grade_name': grade_info['name'],
            'fx_rate': fx,
            'mv_local': mv_local,
            'mv_sgd': mv_sgd,
            'im_sgd': im_sgd,
            'mm_sgd': mm_sgd,
            'fm_sgd': fm_sgd,
        })
    
    usable_cash = total_pv - total_im + net_amount 
    is_margin_call = usable_cash < 0 
    margin_call_amount = -(total_pv - total_mm + net_amount) if is_margin_call else 0  
    available_buy_limit = net_amount + credit_limit  
    buying_power = min(usable_cash, available_buy_limit)  
    buying_power_no_margin = net_amount  
    credit_capped = not is_margin_call and (usable_cash > available_buy_limit)
    
    # Ratios
    mm_ratio = total_mm / total_pv if total_pv > 0 else 0  
    fm_ratio = total_fm / total_pv if total_pv > 0 else 0 
    
    # Lowest PV before Margin Call: -Net Amount / (1 - MM Ratio)
    if mm_ratio < 1 and net_amount < 0:
        lowest_pv_before_mc = -net_amount / (1 - mm_ratio) 
    else:
        lowest_pv_before_mc = 0
    
    # Max % drop before margin call
    if total_pv > 0 and lowest_pv_before_mc > 0:
        max_drop_before_mc = (total_pv - lowest_pv_before_mc) / total_pv  
    else:
        max_drop_before_mc = 0
    
    # Lowest PV before Force Sell: -Net Amount / (1 - FM Ratio)
    if fm_ratio < 1 and net_amount < 0:
        lowest_pv_before_fs = -net_amount / (1 - fm_ratio)  
    else:
        lowest_pv_before_fs = 0
    
    # Max % drop before force sell
    if total_pv > 0 and lowest_pv_before_fs > 0:
        max_drop_before_fs = (total_pv - lowest_pv_before_fs) / total_pv  
    else:
        max_drop_before_fs = 0
    
    return {
        'positions': calc_positions,
        'total_pv': total_pv,
        'total_im': total_im,
        'total_mm': total_mm,
        'total_fm': total_fm,
        'usable_cash': usable_cash,
        'is_margin_call': is_margin_call,
        'margin_call_amount': margin_call_amount,
        'available_buy_limit': available_buy_limit,
        'buying_power': buying_power,
        'buying_power_no_margin': buying_power_no_margin,
        'credit_capped': credit_capped,
        'mm_ratio': mm_ratio,
        'fm_ratio': fm_ratio,
        'lowest_pv_before_mc': lowest_pv_before_mc,
        'max_drop_before_mc': max_drop_before_mc,
        'lowest_pv_before_fs': lowest_pv_before_fs,
        'max_drop_before_fs': max_drop_before_fs,
    }


def simulate_purchase(calc: dict, purchases: dict, fx_rates: dict) -> dict:
    """
    Simulate purchasing shares and check if margin call would be triggered
    
    purchases: dict of {grade: market_value_in_sgd}
    """
    total_purchase = sum(purchases.values())
    
    # Calculate new IM required
    new_im = 0
    for grade, mv in purchases.items():
        grade_info = GRADES.get(grade, GRADES[0])
        new_im += mv * grade_info['im']
    
    new_total_im = calc['total_im'] + new_im
    new_total_pv = calc['total_pv'] + total_purchase
    
    # Recalculate usable cash after purchase
    # Net amount decreases by purchase amount
    new_net_amount = st.session_state.net_amount - total_purchase
    new_usable_cash = new_total_pv - new_total_im + new_net_amount
    
    is_margin_call = new_usable_cash < 0
    exceeds_credit = total_purchase > calc['available_buy_limit']
    
    return {
        'total_purchase': total_purchase,
        'new_im': new_im,
        'new_usable_cash': new_usable_cash,
        'is_margin_call': is_margin_call,
        'exceeds_credit': exceeds_credit,
    }


def simulate_transfer(calc: dict, transfers: list, fx_rates: dict) -> dict:
    """
    Simulate transferring shares out and check if margin call would be triggered
    
    transfers: list of {'position': pos_dict, 'qty': transfer_qty}
    """
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
        im_out = mv_out * grade_info['im']
        
        total_mv_out += mv_out
        total_im_out += im_out
    
    # After transfer: PV decreases, IM decreases
    new_total_pv = calc['total_pv'] - total_mv_out
    new_total_im = calc['total_im'] - total_im_out
    
    # Usable cash after transfer
    new_usable_cash = new_total_pv - new_total_im + st.session_state.net_amount
    
    is_margin_call = new_usable_cash < 0
    
    return {
        'total_mv_out': total_mv_out,
        'total_im_out': total_im_out,
        'new_usable_cash': new_usable_cash,
        'is_margin_call': is_margin_call,
    }


# =============================================================================
# STREAMLIT APP
# =============================================================================

def main():
    st.set_page_config(
        page_title="Phillip Margin Calculator",
        page_icon="📊",
        layout="wide"
    )
    
    st.title("📊 Phillip Securities Margin Calculator")
    st.caption("Cash Plus / Margin Account • Includes Equities + Bonds • Excludes Unit Trusts • Uses Prev Day Close Price")
    
    # Initialize session state
    if 'positions' not in st.session_state:
        st.session_state.positions = []
    if 'currencies' not in st.session_state:
        st.session_state.currencies = set()
    if 'fx_rates' not in st.session_state:
        st.session_state.fx_rates = DEFAULT_FX.copy()
    if 'net_amount' not in st.session_state:
        st.session_state.net_amount = 0.0
    if 'credit_limit' not in st.session_state:
        st.session_state.credit_limit = 100000.0
    
    # ==========================================================================
    # SIDEBAR - INPUTS
    # ==========================================================================
    with st.sidebar:
        st.header("📁 Upload File")
        
        uploaded = st.file_uploader(
            "ScripPositions.xlsx",
            type=['xlsx', 'xls'],
            help="Upload your ScripPositions file from POEMS"
        )
        
        if uploaded:
            positions, currencies = parse_scrip_positions(uploaded)
            if positions:
                st.session_state.positions = positions
                st.session_state.currencies = currencies
                st.success(f"✓ Loaded {len(positions)} positions")
                
                # Show breakdown
                equities = len([p for p in positions if p['type'] == 'Equity'])
                bonds = len([p for p in positions if p['type'] == 'Bond'])
                st.caption(f"Equities: {equities} | Bonds: {bonds}")
        
        st.divider()
        
        # ==========================================================================
        # NET AMOUNT INPUT
        # ==========================================================================
        st.subheader("💰 Net Amount (a) +/- (b)")
        
        net_direction = st.selectbox(
            "Credit or Debit?",
            options=["Credit", "Debit"],
            index=1 if st.session_state.net_amount < 0 else 0,
            help="Credit = positive balance, Debit = borrowed/owing"
        )
        
        net_value = st.number_input(
            "Amount (SGD)",
            value=abs(st.session_state.net_amount) if st.session_state.net_amount != 0 else 0.0,
            min_value=0.0,
            step=1000.0,
            format="%.2f"
        )
        
        # Calculate actual net amount
        if net_direction == "Credit":
            st.session_state.net_amount = net_value
        else:
            st.session_state.net_amount = -net_value
        
        st.caption(f"Net Amount: **${st.session_state.net_amount:,.2f}**")
        
        st.divider()
        
        # ==========================================================================
        # CREDIT LIMIT
        # ==========================================================================
        st.subheader("🏦 Credit Limit")
        
        st.session_state.credit_limit = st.number_input(
            "Credit Limit (SGD)",
            value=st.session_state.credit_limit,
            min_value=0.0,
            step=10000.0,
            format="%.2f"
        )
        
        st.divider()
        
        # ==========================================================================
        # FX RATES
        # ==========================================================================
        st.subheader("💱 FX Rates (to SGD)")
        
        base_currencies = []
        all_currencies = set(base_currencies) | st.session_state.currencies
        
        for curr in sorted(all_currencies):
            if curr == 'SGD':
                st.session_state.fx_rates['SGD'] = 1.0
                st.text("SGD/SGD: 1.0000 (fixed)")
            else:
                default_rate = st.session_state.fx_rates.get(curr, DEFAULT_FX.get(curr, 1.0))
                st.session_state.fx_rates[curr] = st.number_input(
                    f"{curr}/SGD",
                    value=default_rate,
                    min_value=0.0001,
                    step=0.0001,
                    format="%.4f",
                    key=f"fx_{curr}"
                )
    
    # ==========================================================================
    # MAIN CONTENT
    # ==========================================================================
    
    if not st.session_state.positions:
        st.info("👈 Upload your ScripPositions.xlsx file to begin")
        
        return
    
    # ==========================================================================
    # CALCULATE MARGIN
    # ==========================================================================
    calc = calculate_margin(
        st.session_state.positions,
        st.session_state.net_amount,
        st.session_state.credit_limit,
        st.session_state.fx_rates
    )
    
    # ==========================================================================
    # STATUS BANNER
    # ==========================================================================
    if calc['is_margin_call']:
        st.error(f"🚨 **MARGIN CALL** — Amount Required: **S${calc['margin_call_amount']:,.2f}**")
    elif calc['credit_capped']:
        st.warning(f"⚡ **CREDIT LIMIT CAPPED** — Max Purchase: **S${calc['available_buy_limit']:,.2f}**")
    else:
        st.success(f"✅ **NO MARGIN CALL** — Usable Cash: **S${calc['usable_cash']:,.2f}**")
    
    # ==========================================================================
    # ACCOUNT SUMMARY
    # ==========================================================================
    st.subheader("📊 Account Summary")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Portfolio Value", f"S${calc['total_pv']:,.2f}")
        st.metric("Initial Margin", f"S${calc['total_im']:,.2f}")
    
    with col2:
        st.metric("Maintenance Margin", f"S${calc['total_mm']:,.2f}")
        st.metric("Force-sell Margin", f"S${calc['total_fm']:,.2f}")
    
    with col3:
        st.metric("Usable Cash", f"S${calc['usable_cash']:,.2f}")
        st.metric("Buying Power", f"S${calc['buying_power']:,.2f}")
    
    # ==========================================================================
    # POSITIONS TABLE
    # ==========================================================================
    st.subheader("📋 Position Details")
    
    if calc['positions']:
        pos_df = pd.DataFrame(calc['positions'])
        
        display_df = pos_df[[
            'type', 'name', 'code', 'grade', 'effective_qty', 
            'prev_close', 'currency', 'mv_sgd', 'im_sgd', 'mm_sgd'
        ]].copy()
        
        display_df.columns = [
            'Type', 'Name', 'Code', 'Grade', 'Qty', 
            'Prev Close', 'Curr', 'MV (SGD)', 'IM (SGD)', 'MM (SGD)'
        ]
        
        display_df['Grade'] = display_df['Grade'].apply(lambda x: f"{x}%")
        
        st.dataframe(
            display_df.style.format({
                'Qty': '{:,.0f}',
                'Prev Close': '{:.4f}',
                'MV (SGD)': 'S${:,.2f}',
                'IM (SGD)': 'S${:,.2f}',
                'MM (SGD)': 'S${:,.2f}',
            }),
            use_container_width=True,
            hide_index=True
        )
    
    st.divider()
    
    # ==========================================================================
    # MARGIN CALL SCENARIOS
    # ==========================================================================
    
    if calc['is_margin_call']:
        # ==========================================================================
        # MARGIN CALL: SETTLEMENT OPTIONS
        # ==========================================================================
        st.subheader("🚨 Margin Call Settlement Options")
        
        tab_cash, tab_sell, tab_deposit = st.tabs(["💵 Deposit Cash", "📉 Sell Shares", "📈 Deposit Shares "])
        
        with tab_cash:
            st.info(f"Deposit **S${calc['margin_call_amount']:,.2f}** to clear margin call")
        
        with tab_sell:
            st.markdown("**Minimum Market Value of Shares to Sell (by Grade):**")
            
            sell_data = []
            for grade_pct, info in sorted(GRADES.items(), reverse=True):
                sell_amt = calc['margin_call_amount'] * info['sell']
                sell_data.append({
                    'Grade': info['name'],
                    'Multiplier': f"{info['sell']}x",
                    'Min Sell Amount': f"S${sell_amt:,.2f}"
                })
            
            st.dataframe(pd.DataFrame(sell_data), use_container_width=True, hide_index=True)
            
            st.divider()
            
            st.markdown("**🔧 Sell Simulator**")
            st.caption("Select positions to sell and verify if margin call would be fulfilled")
            
            # Create selection for selling
            sell_selections = []
            for i, pos in enumerate(calc['positions']):
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.text(f"{pos['name']} ({pos['code']}) - {pos['effective_qty']:,} shares @ {pos['current_price']:.4f}")
                with col_b:
                    qty_to_sell = st.number_input(
                        "Sell Qty",
                        min_value=0,
                        max_value=pos['effective_qty'],
                        value=0,
                        key=f"sell_{i}",
                        label_visibility="collapsed"
                    )
                    if qty_to_sell > 0:
                        sell_selections.append({'position': pos, 'qty': qty_to_sell})
            
            if sell_selections:
                total_sell_proceeds = 0
                total_im_released = 0
                
                for sel in sell_selections:
                    pos = sel['position']
                    qty = sel['qty']
                    grade_info = get_grade_info(pos['grade'])
                    fx = st.session_state.fx_rates.get(pos['currency'], 1.0)
                    
                    proceeds = qty * pos['current_price'] * fx
                    im_released = proceeds * grade_info['im']
                    effective_release = proceeds / grade_info['sell']  # How much MC it covers
                    
                    total_sell_proceeds += proceeds
                    total_im_released += im_released
                
                remaining_mc = calc['margin_call_amount'] - total_sell_proceeds + total_im_released
                
                if remaining_mc <= 0:
                    st.success(f"✅ Margin Call FULFILLED! Selling these shares releases S${total_sell_proceeds:,.2f}")
                else:
                    st.error(f"❌ Need to sell more! Remaining MC: S${remaining_mc:,.2f}")

        with tab_deposit:
            st.markdown("**Minimum Market Value of Shares to Deposit (by Grade):**")
            deposit_data = []
            for grade_pct, info in sorted(GRADES.items(), reverse=True):
                if info['deposit'] is None:
                    deposit_multiplier = "N/A"
                    deposit_amt = "Cannot deposit Grade C shares"
                else:
                    deposit_multiplier = f"{info['deposit']}x"
                    deposit_amt = f"S${info['deposit'] * calc['margin_call_amount']:,.2f}"
                
                deposit_data.append({
                    'Grade': info['name'],
                    'Multiplier': deposit_multiplier,
                    'Min Deposit Amount': deposit_amt
                })
            
            
            st.dataframe(pd.DataFrame(deposit_data), use_container_width=True, hide_index=True)
            
    else:
        # ==========================================================================
        # NO MARGIN CALL: PURCHASE CAPACITY & TRANSFER SIMULATOR
        # ==========================================================================
        
        # How far to margin call
        st.subheader("📉 Distance to Margin Call")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if calc['max_drop_before_mc'] > 0:
                st.metric(
                    "Max % Drop Before Margin Call",
                    f"{calc['max_drop_before_mc']*100:.2f}%"
                )
                st.metric(
                    "Lowest PV Before Margin Call",
                    f"S${calc['lowest_pv_before_mc']:,.2f}"
                )
            elif st.session_state.net_amount >= 0:
                st.info("💡 No margin call risk - Net Amount is positive")
            else:
                st.info("💡 Portfolio can drop significantly before margin call")
        
        with col2:
            if calc['max_drop_before_fs'] > 0:
                st.metric(
                    "Max % Drop Before Force Sell",
                    f"{calc['max_drop_before_fs']*100:.2f}%"
                )
                st.metric(
                    "Lowest PV Before Force Sell",
                    f"S${calc['lowest_pv_before_fs']:,.2f}"
                )
        
        st.divider()
        
        # ==========================================================================
        # PURCHASE CAPACITY
        # ==========================================================================
        st.subheader("💰 Purchase Capacity")
        
        if calc['credit_capped']:
            st.warning(f"⚠️ You are capped by credit limit. Max purchase: S${calc['available_buy_limit']:,.2f}")
        
        st.markdown("**Maximum Purchase by Grade (with current Usable Cash):**")
        
        base_cash = max(0, calc['usable_cash'])
        purchase_data = []
        for grade_pct, info in sorted(GRADES.items(), reverse=True):
            max_buy = base_cash * info['purchase']
            purchase_data.append({
                'Grade': info['name'],
                'Multiplier': f"{info['purchase']}x",
                'Max Purchase': f"S${max_buy:,.2f}"
            })
        
        st.dataframe(pd.DataFrame(purchase_data), use_container_width=True, hide_index=True)
        
        st.divider()
        
        # ==========================================================================
        # PURCHASE SIMULATOR
        # ==========================================================================
        st.subheader("🛒 Purchase Simulator")
        st.caption("Enter market value of shares you wish to purchase for each grade")
        
        purchase_inputs = {}
        col1, col2 = st.columns(2)
        
        with col1:
            for grade_pct in [80, 70, 50]:
                info = GRADES[grade_pct]
                purchase_inputs[grade_pct] = st.number_input(
                    f"{info['name']} (SGD)",
                    min_value=0.0,
                    step=1000.0,
                    format="%.2f",
                    key=f"purchase_{grade_pct}"
                )
        
        with col2:
            for grade_pct in [30, 0]:
                info = GRADES[grade_pct]
                purchase_inputs[grade_pct] = st.number_input(
                    f"{info['name']} (SGD)",
                    min_value=0.0,
                    step=1000.0,
                    format="%.2f",
                    key=f"purchase_{grade_pct}"
                )
        
        if st.button("Calculate Purchase", type="primary"):
            result = simulate_purchase(calc, purchase_inputs, st.session_state.fx_rates)
            
            if result['total_purchase'] == 0:
                st.info("Enter purchase amounts to simulate")
            elif result['is_margin_call']:
                st.error(f"❌ Cannot Buy - Margin Call will be triggered!")
                st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")
            elif result['exceeds_credit']:
                st.warning(f"⚠️ You have sufficient buying power but will exceed Credit Limit!")
                st.caption(f"Total purchase: S${result['total_purchase']:,.2f} > Limit: S${calc['available_buy_limit']:,.2f}")
            else:
                st.success(f"✅ You can purchase these shares!")
                st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")
        
        st.divider()
        
        # ==========================================================================
        # TRANSFER OUT SIMULATOR
        # ==========================================================================
        st.subheader("📤 Transfer Out Simulator")
        st.caption("Check if transferring shares out will trigger margin call")
        
        transfer_selections = []
        
        for i, pos in enumerate(calc['positions']):
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.text(f"{pos['name']} ({pos['code']}) - {pos['effective_qty']:,} shares")
            with col_b:
                qty_to_transfer = st.number_input(
                    "Transfer Qty",
                    min_value=0,
                    max_value=pos['effective_qty'],
                    value=0,
                    key=f"transfer_{i}",
                    label_visibility="collapsed"
                )
                if qty_to_transfer > 0:
                    transfer_selections.append({'position': pos, 'qty': qty_to_transfer})
        
        if st.button("Check Transfer", type="secondary"):
            if not transfer_selections:
                st.info("Select shares to transfer out")
            else:
                result = simulate_transfer(calc, transfer_selections, st.session_state.fx_rates)
                
                if result['is_margin_call']:
                    st.error(f"❌ Cannot Transfer - Margin Call will be triggered!")
                    st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")
                else:
                    st.success(f"✅ Safe to Transfer!")
                    st.caption(f"New Usable Cash would be: S${result['new_usable_cash']:,.2f}")
    


if __name__ == "__main__":
    main()