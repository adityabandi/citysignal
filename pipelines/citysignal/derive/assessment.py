"""Dated country assessments from explicit economic relationships.

No fitted model or aggregate score. Source variants of the same concept are
selected once; overlapping totals and components remain supporting evidence.
"""
from ..framework.record import period_end, period_shift

FAMILY = {
    'macro_gdp':'national_accounts', 'oecd_consumption':'national_accounts',
    'oecd_investment':'national_accounts', 'macro_employment':'national_accounts',
    'macro_industry':'industrial_production', 'macro_retail':'retail_volume',
    'macro_unemployment':'labour_force_survey', 'macro_inflation':'consumer_prices',
    'bis_cpi':'consumer_prices', 'bis_policy_rate':'policy_rates',
    'bis_euro_policy_rate':'policy_rates', 'oecd_cli':'leading_composite',
    'oecd_bond_yield':'sovereign_yields', 'oecd_equities':'equity_prices',
    'oecd_fx':'exchange_rates', 'oecd_euro_fx':'exchange_rates',
}


def family(metric):
    return FAMILY.get(metric['id'], metric['source_id'])


def historical_position(metric):
    """Same-series rank against preceding five years, excluding current point."""
    points = metric['series']
    end = period_end(metric['period'])
    values = [p['value'] for p in points if p['period'] < metric['period']
              and 0 < (end - period_end(p['period'])).days <= 5 * 366]
    minimum = 260 if metric['cadence'] == 'daily' else 12 if metric['cadence'] == 'quarterly' else 24
    if len(values) < minimum or metric['cadence'] == 'annual':
        return None
    v = metric['value']
    rank = 100 * (sum(x < v for x in values) + .5 * sum(x == v for x in values)) / len(values)
    return {'percentile':round(rank, 1), 'observations':len(values), 'window':'preceding 5 years',
            'basis':'within-country, same series and transformation'}


def assess_country(metrics, as_of):
    cards = {m['id']:m for m in metrics if m['freshness'] == 'current' and m['quality'] != 'suspect'}
    def get(*keys):
        return next((cards[k] for k in keys if k in cards), None)
    def evidence(items):
        return [{'metric_id':m['id'], 'period':m['period'], 'family':family(m)} for m in items if m]
    def direction(v, threshold=.05):
        return 'up' if v > threshold else 'down' if v < -threshold else 'flat'
    def breadth(items, change=False, inverse=False):
        values = [(-1 if inverse else 1) * m['change' if change else 'value'] for m in items
                  if m and m.get('change' if change else 'value') is not None]
        signs = {direction(v) for v in values} - {'flat'}
        return next(iter(signs)) if len(signs) == 1 else 'mixed' if len(signs) > 1 else 'flat' if values else 'missing'
    def pillar(key, label, state, names, items, detail):
        return {'id':key, 'label':label, 'direction':state,
                'headline':names.get(state, 'No current observations'),
                'detail':detail, 'evidence':evidence(items)}

    gdp, industry, cli = get('macro_gdp'), get('macro_industry'), get('oecd_cli')
    retail, consumption, investment = get('macro_retail'), get('oecd_consumption'), get('oecd_investment')
    unemployment, new, total = get('macro_unemployment'), get('hiring_new'), get('hiring_total')
    cpi = get('bis_cpi', 'macro_inflation')
    policy = get('bis_policy_rate', 'bis_euro_policy_rate')
    bond, fx, equities = get('oecd_bond_yield'), get('oecd_fx', 'oecd_euro_fx'), get('oecd_equities')
    exports, imports = get('shipping_exports'), get('shipping_imports')
    pillars = [
        pillar('growth', 'Growth', breadth([gdp, industry]),
            dict(up='Output expanding', down='Output contracting', mixed='Uneven output', flat='Output broadly flat'),
            [gdp, industry, cli, investment, get('de_truck_mileage')],
            'GDP: quarterly real growth. Industry: annual volume growth. Leading indicators and freight provide separate cycle context.'),
        pillar('demand', 'Household demand', breadth([retail, consumption]),
            dict(up='Demand expanding', down='Demand contracting', mixed='Uneven demand', flat='Demand broadly flat'),
            [retail, consumption, get('in_card_value'), get('in_upi_value')],
            'Retail and household consumption measure real activity. Payment values are nominal and include changes in payment adoption.'),
        pillar('labour', 'Labour', breadth([unemployment], change=True, inverse=True) if unemployment and unemployment['change'] is not None else breadth([new], change=True),
            dict(up='Unemployment falling', down='Unemployment rising', flat='Unemployment broadly steady') if unemployment and unemployment['change'] is not None else dict(up='New hiring rising', down='New hiring falling', flat='New hiring broadly steady'),
            [unemployment, new, total, get('us_withheld_tax')],
            'Direction follows the three-month change in unemployment; new-posting momentum is used where unemployment is absent. Tax receipts add nominal wage-bill context.'),
        pillar('inflation', 'Inflation', breadth([cpi], change=True),
            dict(up='Inflation rising', down='Inflation easing', flat='Inflation broadly steady'),
            [cpi], 'Change in annual inflation over three months. National CPI takes precedence; HICP remains a separate series.'),
        pillar('policy', 'Financial conditions', breadth([bond or policy], change=True),
            dict(up='Bond yields rising', down='Bond yields falling', flat='Bond yields broadly steady') if bond else dict(up='Policy rate rising', down='Policy rate falling', flat='Policy rate unchanged'),
            [bond, policy, fx, equities, get('uk_supplier_payment_days'), get('uk_supplier_overdue')],
            'Headline follows the three-month change in long-term government yields, or policy rates where yields are absent. FX and equity changes use monthly averages. Corporate payment conditions remain separate.'),
        pillar('trade', 'Trade', breadth([exports, imports]),
            dict(up='Seaborne trade growing', down='Seaborne trade falling', mixed='Diverging trade flows', flat='Seaborne trade broadly flat'),
            [exports, imports, get('shipping_container_exports'), get('shipping_tanker_exports'), get('in_bank_toll_volume')],
            'Physical tonnage at covered ports, including transshipment. Totals and cargo components are one source family.'),
    ]
    relationships = []
    def relation(title, detail, items):
        relationships.append({'title':title, 'detail':detail, 'evidence':evidence(items)})
    if gdp and cli and cli['change'] is not None:
        if 0 <= (period_end(cli['period']) - period_end(gdp['period'])).days <= 180:
            if gdp['value'] > 0 and cli['change'] < -.1:
                relation('Growth and leading momentum diverge', 'Positive quarterly GDP alongside a falling leading index: the completed quarter and forward cycle signal describe different horizons.', [gdp, cli])
            elif gdp['value'] < 0 and cli['change'] > .1:
                relation('Leading momentum improves after weaker GDP', 'The leading index is rising despite a negative completed quarter; follow subsequent production and demand releases.', [gdp, cli])
    if industry and cli and industry['value'] < 0 and cli['change'] is not None and cli['change'] > .1:
        if abs((period_end(cli['period']) - period_end(industry['period'])).days) <= 62:
            relation('Leading index improves ahead of production', 'Industrial output remains below its year-earlier level while the leading index rises.', [industry, cli])
    if new and total:
        av = {p['period']:p['value'] for p in new['series']}
        bv = {p['period']:p['value'] for p in total['series']}
        common = sorted(av.keys() & bv.keys())
        p = common[-1] if common else None
        prior = period_shift(p, -28) if p else None
        if p and p == new['period'] == total['period'] and av.get(prior, 0) > 0 and bv.get(prior, 0) > 0:
            a, b = 100*(av[p]/av[prior]-1), 100*(bv[p]/bv[prior]-1)
            if a * b < 0:
                relation('New hiring and posting stock diverge', f'New postings {a:+.1f}% versus total postings {b:+.1f}% over the same 28-day window. Both come from Indeed; this is a flow–stock divergence within one source.', [new, total])
    container, tanker = get('shipping_container_exports'), get('shipping_tanker_exports')
    if container and tanker and container['period'] == tanker['period'] and container['value'] * tanker['value'] < 0:
        relation('Cargo composition diverges', 'Container and tanker exports have opposite annual growth signs. Total tonnage combines different cargo cycles; growth contributions require tonnage weights.', [exports, container, tanker])
    if cpi and cpi['change'] is not None and gdp and gdp['value'] > 0 and cpi['change'] < -.1:
        relation('Positive output growth with easing inflation', 'Latest GDP growth is positive and annual inflation has fallen over three months. Observation windows differ.', [gdp, cpi])
    if cpi and bond and cpi['period'] == bond['period'] and cpi['change'] is not None and bond['change'] is not None:
        if cpi['change'] < -.1 and bond['change'] > .1:
            relation('Inflation eases while bond yields rise', 'Over matching three-month windows, annual inflation falls but long-term nominal yields rise. Policy expectations, term premia and supply can explain the divergence.', [cpi, bond])

    channels = []
    if cpi:
        channels.append({'label':'Rates & FX', 'text':'Compare inflation and policy momentum with government yields and currency moves. These monthly prices describe market conditions; they do not measure release surprises or intraday reactions.', 'metrics':[m['id'] for m in [cpi, policy, bond, fx] if m]})
    if industry or exports:
        channels.append({'label':'Industrials & exporters', 'text':'Production, freight and container volumes track demand exposure. Destination mix, inventories and exchange rates affect revenue translation.', 'metrics':[m['id'] for m in [industry, get('de_truck_mileage'), container or exports] if m]})
    if retail or consumption:
        channels.append({'label':'Consumer sectors', 'text':'Real household demand and hiring momentum inform consumer-sector exposure; payment values add nominal spending context.', 'metrics':[m['id'] for m in [retail or consumption, new] if m]})
    if get('uk_supplier_payment_days'):
        channels.append({'label':'Corporate credit', 'text':'Supplier-payment delays describe working-capital pressure within the reporting-company panel; changes in company membership also affect the median.', 'metrics':['uk_supplier_payment_days','uk_supplier_overdue']})
    families = sorted({family(m) for m in cards.values() if m['cadence'] != 'annual' and m['signal_type'] != 'derived'})
    return {'as_of':as_of, 'pillars':pillars, 'relationships':relationships, 'channels':channels,
            'families':families, 'current_series':len(cards),
            'method':'Dated observations grouped by economic role. One source variant per concept. No aggregate score; electricity and overlapping cargo/hiring components do not add votes to output. Assessments use current observations and separate levels, momentum and horizons.'}
