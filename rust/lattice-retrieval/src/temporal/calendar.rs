use lattice_core::graph_write::clock::{civil_from_days, iso_seconds_for};
use lattice_core::pytext::days_from_civil;

pub const DAY_SECS: f64 = 86_400.0;

pub fn day_index(epoch: f64) -> i64 {
    (epoch / DAY_SECS).floor() as i64
}

pub fn day_start(days: i64) -> f64 {
    days as f64 * DAY_SECS
}

pub fn today_start(now: f64) -> f64 {
    day_start(day_index(now))
}

pub fn week_start(now: f64) -> f64 {
    let days = day_index(now);
    let weekday_from_monday = (days + 3).rem_euclid(7);
    day_start(days - weekday_from_monday)
}

pub fn month_start_offset(now: f64, months_back: i64) -> f64 {
    let (year, month, _) = civil_from_days(day_index(now));
    let total = year * 12 + (month as i64 - 1) - months_back;
    let (y, m) = (total.div_euclid(12), total.rem_euclid(12) + 1);
    day_start(days_from_civil(y, m as u32, 1))
}

pub fn year_start_offset(now: f64, years_back: i64) -> f64 {
    let (year, _, _) = civil_from_days(day_index(now));
    day_start(days_from_civil(year - years_back, 1, 1))
}

pub fn date_epoch(year: i64, month: u32, day: u32) -> Option<f64> {
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) || !(1900..=9999).contains(&year) {
        return None;
    }
    let days = days_from_civil(year, month, day);
    let (y, m, d) = civil_from_days(days);
    (y == year && m == month && d == day).then(|| day_start(days))
}

pub fn next_month_epoch(year: i64, month: u32) -> Option<f64> {
    if month == 12 {
        date_epoch(year + 1, 1, 1)
    } else {
        date_epoch(year, month + 1, 1)
    }
}

pub fn iso(epoch: f64) -> String {
    iso_seconds_for(epoch.floor() as i64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_core::parse_iso;

    fn at(stamp: &str) -> f64 {
        parse_iso(Some(stamp)).unwrap()
    }

    #[test]
    fn weeks_start_on_monday_and_months_roll_across_years() {
        let thursday = at("2026-11-26T15:30:00");
        assert_eq!(iso(today_start(thursday)), "2026-11-26T00:00:00");
        assert_eq!(iso(week_start(thursday)), "2026-11-23T00:00:00");
        assert_eq!(
            iso(week_start(at("2026-11-23T00:00:00"))),
            "2026-11-23T00:00:00"
        );
        assert_eq!(
            iso(week_start(at("2026-11-29T23:59:59"))),
            "2026-11-23T00:00:00"
        );
        assert_eq!(iso(month_start_offset(thursday, 0)), "2026-11-01T00:00:00");
        assert_eq!(
            iso(month_start_offset(at("2026-01-10T00:00:00"), 1)),
            "2025-12-01T00:00:00"
        );
        assert_eq!(iso(year_start_offset(thursday, 1)), "2025-01-01T00:00:00");
        assert_eq!(date_epoch(2026, 2, 30), None);
        assert_eq!(date_epoch(2026, 13, 1), None);
        assert_eq!(date_epoch(1200, 1, 1), None);
        assert_eq!(
            iso(next_month_epoch(2026, 12).unwrap()),
            "2027-01-01T00:00:00"
        );
        assert_eq!(
            iso(next_month_epoch(2026, 3).unwrap()),
            "2026-04-01T00:00:00"
        );
    }
}
