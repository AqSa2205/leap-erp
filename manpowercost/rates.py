"""Deriving a charge-out rate from a cost. Pure functions, no ORM writes.

Same shape as pmo/progress.py and procurement/budget_status.py: the numbers
live here so they can be tested without a request, a sheet, or a database.

The chain mirrors the `REAL COST` sheet these rates came from, which built an
hourly rate as:

    salary + benefits
      + overhead       (labelled 10%, computed 5%)
      + profit         (labelled 25%, computed 20%)
      / billable months (labelled 10.5, computed 11)
      / hours per month (176)

Every one of those three labels disagreed with its formula. The point of
putting the chain here is that the steps are named, returned, and shown, so a
rate can be traced instead of taken on trust.
"""

from decimal import Decimal

# Rates are money-per-hour and get multiplied by thousands of hours, so they
# keep more precision than the 2dp used for the amounts themselves.
RATE_PRECISION = Decimal('0.0001')
MONEY = Decimal('0.01')


def _d(value):
    if value is None:
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def derive_charge_rate(monthly_cost, basis):
    """Build an hourly charge-out rate from a monthly cost.

    Returns every intermediate step, not just the answer, so the screen can
    show the build-up. A single opaque number is what made the source sheets
    impossible to check.

    Returns a dict with:
        monthly_cost, yearly_cost, overhead, profit, yearly_total,
        monthly_invoice, hourly_rate, daily_rate
    """
    monthly_cost = _d(monthly_cost)
    if basis is None:
        return {
            'monthly_cost': monthly_cost,
            'yearly_cost': monthly_cost * 12,
            'overhead': Decimal('0'),
            'profit': Decimal('0'),
            'yearly_total': monthly_cost * 12,
            'monthly_invoice': Decimal('0'),
            'hourly_rate': Decimal('0'),
            'daily_rate': Decimal('0'),
            'basis': None,
        }

    yearly_cost = monthly_cost * 12

    # Overhead and profit are both taken on the base cost, not compounded.
    # That is what the source sheet does (S=R*5%, T=R*20%, U=R+S+T), and
    # compounding them here would quietly raise every price.
    overhead = yearly_cost * _d(basis.overhead_pct) / Decimal('100')
    profit = yearly_cost * _d(basis.profit_pct) / Decimal('100')
    yearly_total = yearly_cost + overhead + profit

    billable_months = _d(basis.billable_months)
    monthly_invoice = (yearly_total / billable_months
                       if billable_months else Decimal('0'))

    hours_per_month = _d(basis.hours_per_month)
    hourly_rate = (monthly_invoice / hours_per_month
                   if hours_per_month else Decimal('0'))

    working_days = _d(basis.working_days_per_month)
    daily_rate = (monthly_invoice / working_days
                  if working_days else Decimal('0'))

    return {
        'monthly_cost': monthly_cost,
        'yearly_cost': yearly_cost,
        'overhead': overhead,
        'profit': profit,
        'yearly_total': yearly_total,
        'monthly_invoice': monthly_invoice,
        'hourly_rate': hourly_rate,
        'daily_rate': daily_rate,
        'basis': basis,
    }


def effective_hourly_rate(charge_rate):
    """The rate actually used: the manual override when set, else derived.

    Kept in one place because "which rate applies" is the question every
    caller asks, and answering it differently in two of them is how a quoted
    price and a reported margin come to disagree.
    """
    if charge_rate is None:
        return Decimal('0')
    if charge_rate.manual_rate is not None:
        return _d(charge_rate.manual_rate)
    return derive_charge_rate(charge_rate.monthly_cost,
                              charge_rate.basis)['hourly_rate']


def margin_pct(hourly_charge, hourly_cost):
    """Margin on the sell price, as a percentage.

    (charge - cost) / charge. Zero charge returns 0 rather than dividing -
    a rate that has not been set yet is not an infinite loss.
    """
    hourly_charge = _d(hourly_charge)
    hourly_cost = _d(hourly_cost)
    if not hourly_charge:
        return Decimal('0')
    return (hourly_charge - hourly_cost) / hourly_charge * Decimal('100')


def hourly_cost_from_monthly(monthly_cost, basis):
    """Cost per hour on a given basis.

    Deliberately divides the *monthly* cost by the *monthly* hours. The app
    this replaces divided a yearly figure by 2920 (365 calendar days x 8h),
    which bills weekends and holidays as working time and understates the
    hourly cost by about a third.
    """
    monthly_cost = _d(monthly_cost)
    if basis is None or not _d(basis.hours_per_month):
        return Decimal('0')
    return monthly_cost / _d(basis.hours_per_month)


def sheet_summary(sheet):
    """Totals for one cost sheet, plus the lines that cannot be totalled.

    `incomplete` is returned alongside the totals on purpose. Both source
    workbooks produced a confident total that had silently dropped people or
    salaries out of it; a caller that wants the total gets the caveat in the
    same breath.
    """
    basis = sheet.effective_basis
    lines = list(sheet.lines.all())
    monthly = sum((line.monthly_cost for line in lines), Decimal('0'))
    incomplete = [line for line in lines if not line.gross_salary]
    return {
        'basis': basis,
        'line_count': len(lines),
        'monthly_total': monthly,
        'yearly_total': monthly * 12,
        'hourly_total': hourly_cost_from_monthly(monthly, basis),
        'incomplete': incomplete,
        'incomplete_count': len(incomplete),
    }
