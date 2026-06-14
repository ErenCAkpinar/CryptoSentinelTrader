/// Crypto-specific anomaly detection engine.
///
/// Detects:
/// - Volume z-score spikes (whale accumulation, wash trading)
/// - Flash crash / pump (rapid price movement via rate-of-change)
/// - Liquidation cascade risk (aggressive selling + volume spike + rapid drop)

use std::collections::VecDeque;

// ── Result type ──────────────────────────────────────────────────────────────

pub struct AnomalyResult {
    pub is_anomaly: bool,
    /// Combined score in 0.0–1.0; higher = more abnormal.
    pub score: f64,
    /// "LOW", "MEDIUM", "HIGH", "CRITICAL"
    pub liquidation_cascade_risk: &'static str,
    /// Human-readable labels for every pattern that fired.
    pub patterns: Vec<&'static str>,
}

// ── Detector ─────────────────────────────────────────────────────────────────

pub struct AnomalyDetector {
    // ── Volume z-score ───────────────────────────────────────────────────────
    /// Rolling 5-minute volume buckets (up to 288 = 24 h).
    volume_buckets: VecDeque<f64>,

    // ── Flash crash / pump ───────────────────────────────────────────────────
    /// (timestamp_ms, price) snapshots; kept for the last 5 minutes.
    price_snapshots: VecDeque<(u64, f64)>,

    // ── Liquidation cascade inference ────────────────────────────────────────
    /// Recent buy/sell ratio readings (one per evaluation cycle).
    recent_buy_sell_ratios: VecDeque<f64>,

    // ── Configurable thresholds ──────────────────────────────────────────────
    /// z-score above which volume is considered anomalous.
    volume_z_threshold: f64,
    /// Percentage drop (positive number) in 30 s that counts as a flash crash.
    flash_crash_30s_pct: f64,
    /// Percentage drop in 2 min.
    flash_crash_2m_pct: f64,
}

impl AnomalyDetector {
    pub fn new() -> Self {
        Self {
            volume_buckets: VecDeque::with_capacity(288),
            price_snapshots: VecDeque::with_capacity(600), // ~5 min at 2/s
            recent_buy_sell_ratios: VecDeque::with_capacity(60),
            volume_z_threshold: 3.0,
            flash_crash_30s_pct: 3.0,
            flash_crash_2m_pct: 5.0,
        }
    }

    // ── Feed data ────────────────────────────────────────────────────────────

    /// Record a price snapshot (call once per evaluation cycle or per second).
    pub fn record_price(&mut self, timestamp_ms: u64, price: f64) {
        self.price_snapshots.push_back((timestamp_ms, price));
        // Keep last 5 minutes
        let cutoff = timestamp_ms.saturating_sub(300_000);
        while self.price_snapshots.front().map_or(false, |&(t, _)| t < cutoff) {
            self.price_snapshots.pop_front();
        }
    }

    /// Push the latest 5-minute volume bucket.
    pub fn record_volume_bucket(&mut self, volume: f64) {
        self.volume_buckets.push_back(volume);
        if self.volume_buckets.len() > 288 {
            self.volume_buckets.pop_front();
        }
    }

    /// Record the latest buy/sell ratio (0.0 = all sells, 1.0 = all buys).
    pub fn record_buy_sell_ratio(&mut self, ratio: f64) {
        self.recent_buy_sell_ratios.push_back(ratio);
        if self.recent_buy_sell_ratios.len() > 60 {
            self.recent_buy_sell_ratios.pop_front();
        }
    }

    // ── Evaluate ─────────────────────────────────────────────────────────────

    /// Run all detectors and return a combined result.
    pub fn evaluate(&self) -> AnomalyResult {
        let mut patterns: Vec<&'static str> = Vec::new();
        let mut scores: Vec<f64> = Vec::new();

        // 1. Volume z-score
        if let Some(z) = self.volume_z_score() {
            if z > self.volume_z_threshold {
                scores.push((z / 10.0).min(1.0)); // normalize: z=10 → 1.0
                if z > 5.0 {
                    patterns.push("VOLUME_EXTREME_SPIKE");
                } else {
                    patterns.push("VOLUME_SPIKE");
                }
            }
        }

        // 2. Flash crash / pump
        let (crash_pct, pump_pct) = self.rate_of_change();

        if crash_pct >= self.flash_crash_2m_pct {
            scores.push((crash_pct / 10.0).min(1.0));
            patterns.push("FLASH_CRASH");
        } else if crash_pct >= self.flash_crash_30s_pct {
            scores.push((crash_pct / 10.0).min(1.0));
            patterns.push("RAPID_DROP");
        }

        if pump_pct >= self.flash_crash_2m_pct {
            scores.push((pump_pct / 10.0).min(1.0));
            patterns.push("FLASH_PUMP");
        } else if pump_pct >= self.flash_crash_30s_pct {
            scores.push((pump_pct / 10.0).min(1.0));
            patterns.push("RAPID_PUMP");
        }

        // 3. Liquidation cascade risk
        let liq_risk = self.liquidation_cascade_risk(crash_pct);
        if liq_risk == "CRITICAL" {
            scores.push(1.0);
            patterns.push("LIQUIDATION_CASCADE");
        } else if liq_risk == "HIGH" {
            scores.push(0.7);
            patterns.push("LIQUIDATION_RISK_HIGH");
        }

        // Combined score: max of individual scores (worst signal dominates)
        let score = scores.iter().cloned().fold(0.0_f64, f64::max);
        let is_anomaly = !patterns.is_empty();

        AnomalyResult {
            is_anomaly,
            score,
            liquidation_cascade_risk: liq_risk,
            patterns,
        }
    }

    // ── Internal detectors ───────────────────────────────────────────────────

    /// Volume z-score of the most recent bucket relative to historical buckets.
    fn volume_z_score(&self) -> Option<f64> {
        if self.volume_buckets.len() < 12 {
            // Need at least 1 hour of data
            return None;
        }

        let n = self.volume_buckets.len();
        let current = *self.volume_buckets.back()?;

        // Mean and std of all but the last bucket
        let count = (n - 1) as f64;
        let sum: f64 = self.volume_buckets.iter().take(n - 1).sum();
        let mean = sum / count;
        let variance: f64 = self
            .volume_buckets
            .iter()
            .take(n - 1)
            .map(|v| (v - mean).powi(2))
            .sum::<f64>()
            / count;
        let std_dev = variance.sqrt();

        if std_dev < f64::EPSILON {
            return None; // no variation → z-score is meaningless
        }

        Some((current - mean) / std_dev)
    }

    /// Compute the worst drop% and worst pump% over the last 30 s and 2 min.
    /// Returns (max_drop_pct, max_pump_pct) as positive numbers.
    fn rate_of_change(&self) -> (f64, f64) {
        if self.price_snapshots.len() < 2 {
            return (0.0, 0.0);
        }

        let latest_ts = self.price_snapshots.back().unwrap().0;
        let latest_price = self.price_snapshots.back().unwrap().1;

        let mut max_drop_pct: f64 = 0.0;
        let mut max_pump_pct: f64 = 0.0;

        // Check against snapshots from 30 s ago and 2 min ago
        for &(ts, old_price) in self.price_snapshots.iter().rev() {
            let age_ms = latest_ts.saturating_sub(ts);
            if age_ms > 120_000 {
                break; // older than 2 min
            }
            if old_price <= 0.0 {
                continue;
            }

            let change_pct = (latest_price - old_price) / old_price * 100.0;

            // Within windows of interest (30s and 2min)
            if change_pct < 0.0 {
                max_drop_pct = max_drop_pct.max(change_pct.abs());
            } else {
                max_pump_pct = max_pump_pct.max(change_pct);
            }
        }

        (max_drop_pct, max_pump_pct)
    }

    /// Infer liquidation cascade risk from recent sell pressure + rapid price drop.
    ///
    /// Real liquidation data requires exchange API; this approximation uses:
    /// - Aggressive sell volume (buy_sell_ratio < 0.3)
    /// - Sustained selling (multiple consecutive low ratios)
    /// - Rapid price decline (crash_pct)
    fn liquidation_cascade_risk(&self, crash_pct: f64) -> &'static str {
        if self.recent_buy_sell_ratios.len() < 3 {
            return "LOW";
        }

        // Count how many of the last 5 readings show heavy selling
        let window: Vec<f64> = self
            .recent_buy_sell_ratios
            .iter()
            .rev()
            .take(5)
            .copied()
            .collect();
        let heavy_sell_count = window.iter().filter(|&&r| r < 0.3).count();
        let avg_ratio = window.iter().sum::<f64>() / window.len() as f64;

        if heavy_sell_count >= 3 && crash_pct >= 5.0 {
            "CRITICAL"
        } else if heavy_sell_count >= 2 && crash_pct >= 3.0 {
            "HIGH"
        } else if avg_ratio < 0.35 && crash_pct >= 1.5 {
            "MEDIUM"
        } else {
            "LOW"
        }
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_detector_with_volume(buckets: &[f64]) -> AnomalyDetector {
        let mut d = AnomalyDetector::new();
        for &v in buckets {
            d.record_volume_bucket(v);
        }
        d
    }

    // ── Volume z-score ───────────────────────────────────────────────────────

    #[test]
    fn no_anomaly_on_normal_volume() {
        // 23 normal buckets + 1 slightly elevated
        let mut buckets: Vec<f64> = vec![100.0; 23];
        buckets.push(120.0); // only 20% above average
        let d = make_detector_with_volume(&buckets);
        let r = d.evaluate();
        assert!(!r.is_anomaly, "normal volume should not be anomalous");
    }

    #[test]
    fn detects_volume_spike() {
        // Realistic: natural volume variation around 100, then a 6× spike
        let mut buckets: Vec<f64> = (0..23).map(|i| 90.0 + (i as f64 * 0.87) % 20.0).collect();
        buckets.push(600.0);
        let d = make_detector_with_volume(&buckets);
        let r = d.evaluate();
        assert!(r.is_anomaly, "volume spike should be detected");
        assert!(
            r.patterns.contains(&"VOLUME_EXTREME_SPIKE") || r.patterns.contains(&"VOLUME_SPIKE"),
            "expected volume pattern, got {:?}",
            r.patterns,
        );
    }

    #[test]
    fn insufficient_data_returns_no_anomaly() {
        let d = make_detector_with_volume(&[100.0; 5]);
        let r = d.evaluate();
        assert!(!r.is_anomaly);
    }

    // ── Flash crash / pump ───────────────────────────────────────────────────

    #[test]
    fn detects_flash_crash() {
        let mut d = AnomalyDetector::new();
        // Price was 100 at t=0, dropped to 94 at t=25s → 6% drop in 25s
        d.record_price(0, 100.0);
        d.record_price(25_000, 94.0);
        let r = d.evaluate();
        assert!(r.is_anomaly);
        assert!(
            r.patterns.contains(&"FLASH_CRASH") || r.patterns.contains(&"RAPID_DROP"),
            "expected crash pattern, got {:?}",
            r.patterns,
        );
    }

    #[test]
    fn detects_flash_pump() {
        let mut d = AnomalyDetector::new();
        d.record_price(0, 100.0);
        d.record_price(20_000, 108.0); // 8% pump in 20s
        let r = d.evaluate();
        assert!(r.is_anomaly);
        assert!(
            r.patterns.contains(&"FLASH_PUMP") || r.patterns.contains(&"RAPID_PUMP"),
            "expected pump pattern, got {:?}",
            r.patterns,
        );
    }

    #[test]
    fn no_crash_on_stable_price() {
        let mut d = AnomalyDetector::new();
        for i in 0..60 {
            d.record_price(i * 1000, 100.0 + (i as f64 * 0.01));
        }
        let r = d.evaluate();
        let has_crash = r.patterns.iter().any(|p| p.contains("CRASH") || p.contains("DROP"));
        assert!(!has_crash);
    }

    // ── Liquidation cascade ──────────────────────────────────────────────────

    #[test]
    fn detects_liquidation_cascade_critical() {
        let mut d = AnomalyDetector::new();
        // Heavy selling (buy_sell_ratio < 0.3) for 5 consecutive readings
        for _ in 0..5 {
            d.record_buy_sell_ratio(0.15);
        }
        // Combined with a 6% crash
        d.record_price(0, 100.0);
        d.record_price(30_000, 94.0);
        let r = d.evaluate();
        assert_eq!(r.liquidation_cascade_risk, "CRITICAL");
        assert!(r.patterns.contains(&"LIQUIDATION_CASCADE"));
    }

    #[test]
    fn low_risk_when_balanced_selling() {
        let mut d = AnomalyDetector::new();
        for _ in 0..5 {
            d.record_buy_sell_ratio(0.5); // balanced
        }
        d.record_price(0, 100.0);
        d.record_price(30_000, 99.0); // only 1% drop
        let r = d.evaluate();
        assert_eq!(r.liquidation_cascade_risk, "LOW");
    }

    // ── Score ────────────────────────────────────────────────────────────────

    #[test]
    fn score_bounded_0_to_1() {
        let mut d = AnomalyDetector::new();
        // Extreme everything
        let mut buckets: Vec<f64> = vec![100.0; 23];
        buckets.push(5000.0);
        for v in &buckets {
            d.record_volume_bucket(*v);
        }
        d.record_price(0, 100.0);
        d.record_price(10_000, 50.0); // 50% crash
        for _ in 0..5 {
            d.record_buy_sell_ratio(0.05);
        }
        let r = d.evaluate();
        assert!(r.score >= 0.0 && r.score <= 1.0, "score {} out of bounds", r.score);
    }
}
