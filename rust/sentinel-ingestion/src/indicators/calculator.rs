/// Technical indicator calculation library
/// All functions are stateless (pure) except VwapCalculator which maintains session state.

// ── Return types ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct MacdResult {
    pub value: f64,
    pub signal: f64,
    pub histogram: f64,
}

#[derive(Debug, Clone)]
pub struct BollingerResult {
    pub upper: f64,
    pub middle: f64,
    pub lower: f64,
    /// Where current price sits within the band, 0 = at lower, 100 = at upper
    pub position_pct: f64,
}

// ── Internal helper ───────────────────────────────────────────────────────────

/// Computes a full EMA series starting from index `period-1`.
/// Returns a Vec whose length is `prices.len() - period + 1`.
fn ema_series(prices: &[f64], period: usize) -> Vec<f64> {
    if prices.len() < period {
        return vec![];
    }
    let multiplier = 2.0 / (period as f64 + 1.0);
    let mut ema = prices[..period].iter().sum::<f64>() / period as f64;
    let mut result = Vec::with_capacity(prices.len() - period + 1);
    result.push(ema);
    for price in &prices[period..] {
        ema = (price - ema) * multiplier + ema;
        result.push(ema);
    }
    result
}

// ── MACD (12, 26, 9) ─────────────────────────────────────────────────────────

/// MACD(12, 26, 9): EMA12 − EMA26 with a 9-period EMA signal line.
/// Requires at least 35 prices for a meaningful result; returns zeroes otherwise.
pub fn calculate_macd(prices: &[f64]) -> MacdResult {
    let neutral = MacdResult { value: 0.0, signal: 0.0, histogram: 0.0 };
    if prices.len() < 35 {
        return neutral;
    }

    let ema12 = ema_series(prices, 12); // length = n - 11
    let ema26 = ema_series(prices, 26); // length = n - 25

    // ema12 has 14 extra elements at the front compared to ema26
    let offset = 26 - 12; // 14
    if ema12.len() <= offset || ema26.is_empty() {
        return neutral;
    }

    // MACD line: ema12[i+14] − ema26[i]
    let macd_series: Vec<f64> = ema26
        .iter()
        .enumerate()
        .map(|(i, e26)| ema12[i + offset] - e26)
        .collect();

    let macd_value = *macd_series.last().unwrap_or(&0.0);

    let signal_line = if macd_series.len() >= 9 {
        *ema_series(&macd_series, 9).last().unwrap_or(&0.0)
    } else {
        macd_value
    };

    MacdResult {
        value: macd_value,
        signal: signal_line,
        histogram: macd_value - signal_line,
    }
}

// ── Bollinger Bands (20, 2) ───────────────────────────────────────────────────

/// Bollinger Bands with configurable period and standard-deviation multiplier.
/// Typical usage: `calculate_bollinger_bands(prices, 20, 2.0)`.
pub fn calculate_bollinger_bands(
    prices: &[f64],
    period: usize,
    std_dev_mult: f64,
) -> BollingerResult {
    let current = prices.last().copied().unwrap_or(0.0);
    let neutral = BollingerResult {
        upper: current,
        middle: current,
        lower: current,
        position_pct: 50.0,
    };

    if prices.len() < period {
        return neutral;
    }

    let window = &prices[prices.len() - period..];
    let sma = window.iter().sum::<f64>() / period as f64;
    let variance = window.iter().map(|p| (p - sma).powi(2)).sum::<f64>() / period as f64;
    let std_dev = variance.sqrt();

    let upper = sma + std_dev_mult * std_dev;
    let lower = sma - std_dev_mult * std_dev;

    let position_pct = if (upper - lower).abs() > f64::EPSILON {
        ((current - lower) / (upper - lower) * 100.0).clamp(0.0, 100.0)
    } else {
        50.0
    };

    BollingerResult { upper, middle: sma, lower, position_pct }
}

// ── ATR (14) ─────────────────────────────────────────────────────────────────

/// Average True Range over `period` candles.
/// `candles` is a slice of `(high, low, close)` tuples ordered oldest→newest.
/// Requires at least 2 candles; returns 0.0 otherwise.
pub fn calculate_atr(candles: &[(f64, f64, f64)], period: usize) -> f64 {
    if candles.len() < 2 {
        return 0.0;
    }

    // True Range for each candle (using previous close)
    let true_ranges: Vec<f64> = candles
        .windows(2)
        .map(|w| {
            let (_, _, prev_close) = w[0];
            let (high, low, _) = w[1];
            let hl = high - low;
            let hpc = (high - prev_close).abs();
            let lpc = (low - prev_close).abs();
            hl.max(hpc).max(lpc)
        })
        .collect();

    let window_size = period.min(true_ranges.len());
    let start = true_ranges.len() - window_size;
    true_ranges[start..].iter().sum::<f64>() / window_size as f64
}

// ── ADX (Average Directional Index, 14) ──────────────────────────────────────

/// Average Directional Index (ADX) measures trend strength regardless of direction.
/// `candles` is a slice of `(high, low, close)` tuples ordered oldest→newest.
/// Returns ADX value: 0-25 = weak/no trend, 25-50 = strong trend, 50+ = very strong.
/// Requires at least `period + 1` candles; returns 0.0 otherwise.
pub fn calculate_adx(candles: &[(f64, f64, f64)], period: usize) -> f64 {
    if candles.len() < period + 1 {
        return 0.0;
    }

    // Step 1: Calculate +DM, -DM, and TR for each pair of consecutive candles
    let mut plus_dm_list = Vec::with_capacity(candles.len() - 1);
    let mut minus_dm_list = Vec::with_capacity(candles.len() - 1);
    let mut tr_list = Vec::with_capacity(candles.len() - 1);

    for w in candles.windows(2) {
        let (prev_high, prev_low, prev_close) = w[0];
        let (high, low, _close) = w[1];

        let up_move = high - prev_high;
        let down_move = prev_low - low;

        let plus_dm = if up_move > down_move && up_move > 0.0 { up_move } else { 0.0 };
        let minus_dm = if down_move > up_move && down_move > 0.0 { down_move } else { 0.0 };

        let hl = high - low;
        let hpc = (high - prev_close).abs();
        let lpc = (low - prev_close).abs();
        let tr = hl.max(hpc).max(lpc);

        plus_dm_list.push(plus_dm);
        minus_dm_list.push(minus_dm);
        tr_list.push(tr);
    }

    if plus_dm_list.len() < period {
        return 0.0;
    }

    // Step 2: Smoothed +DM, -DM, TR using Wilder's smoothing (first = sum, then EMA-like)
    let mut smoothed_plus_dm: f64 = plus_dm_list[..period].iter().sum();
    let mut smoothed_minus_dm: f64 = minus_dm_list[..period].iter().sum();
    let mut smoothed_tr: f64 = tr_list[..period].iter().sum();

    let mut dx_values = Vec::with_capacity(plus_dm_list.len() - period + 1);

    // First DX
    let plus_di = if smoothed_tr > 0.0 { smoothed_plus_dm / smoothed_tr * 100.0 } else { 0.0 };
    let minus_di = if smoothed_tr > 0.0 { smoothed_minus_dm / smoothed_tr * 100.0 } else { 0.0 };
    let di_sum = plus_di + minus_di;
    let dx = if di_sum > 0.0 { (plus_di - minus_di).abs() / di_sum * 100.0 } else { 0.0 };
    dx_values.push(dx);

    // Continue Wilder's smoothing for remaining data
    let p = period as f64;
    for i in period..plus_dm_list.len() {
        smoothed_plus_dm = smoothed_plus_dm - smoothed_plus_dm / p + plus_dm_list[i];
        smoothed_minus_dm = smoothed_minus_dm - smoothed_minus_dm / p + minus_dm_list[i];
        smoothed_tr = smoothed_tr - smoothed_tr / p + tr_list[i];

        let plus_di = if smoothed_tr > 0.0 { smoothed_plus_dm / smoothed_tr * 100.0 } else { 0.0 };
        let minus_di = if smoothed_tr > 0.0 { smoothed_minus_dm / smoothed_tr * 100.0 } else { 0.0 };
        let di_sum = plus_di + minus_di;
        let dx = if di_sum > 0.0 { (plus_di - minus_di).abs() / di_sum * 100.0 } else { 0.0 };
        dx_values.push(dx);
    }

    if dx_values.len() < period {
        return dx_values.last().copied().unwrap_or(0.0);
    }

    // Step 3: ADX = Wilder-smoothed DX
    let mut adx: f64 = dx_values[..period].iter().sum::<f64>() / p;
    for dx in &dx_values[period..] {
        adx = (adx * (p - 1.0) + dx) / p;
    }

    adx
}

// ── VWAP (session-based) ──────────────────────────────────────────────────────

/// Session-based VWAP calculator.
/// Call `reset()` at the start of each UTC trading day.
/// Call `update(price, volume)` for every incoming tick.
#[derive(Debug, Clone)]
pub struct VwapCalculator {
    cumulative_pv: f64,
    cumulative_volume: f64,
}

impl VwapCalculator {
    pub fn new() -> Self {
        Self {
            cumulative_pv: 0.0,
            cumulative_volume: 0.0,
        }
    }

    /// Feed a new tick into the session accumulator.
    pub fn update(&mut self, price: f64, volume: f64) {
        self.cumulative_pv += price * volume;
        self.cumulative_volume += volume;
    }

    /// Current VWAP value; returns 0.0 if no data yet.
    pub fn vwap(&self) -> f64 {
        if self.cumulative_volume > 0.0 {
            self.cumulative_pv / self.cumulative_volume
        } else {
            0.0
        }
    }

    /// Reset at the start of a new session (00:00 UTC).
    pub fn reset(&mut self) {
        self.cumulative_pv = 0.0;
        self.cumulative_volume = 0.0;
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_prices(n: usize, start: f64, step: f64) -> Vec<f64> {
        (0..n).map(|i| start + i as f64 * step).collect()
    }

    // ── MACD ──────────────────────────────────────────────────────────────────

    #[test]
    fn macd_returns_zeros_for_insufficient_data() {
        let prices = sample_prices(30, 100.0, 1.0);
        let result = calculate_macd(&prices);
        assert_eq!(result.value, 0.0);
        assert_eq!(result.signal, 0.0);
        assert_eq!(result.histogram, 0.0);
    }

    #[test]
    fn macd_histogram_equals_value_minus_signal() {
        let prices = sample_prices(60, 100.0, 0.5);
        let result = calculate_macd(&prices);
        let diff = (result.histogram - (result.value - result.signal)).abs();
        assert!(diff < 1e-10, "histogram != value - signal: {diff}");
    }

    #[test]
    fn macd_bullish_crossover_on_rising_prices() {
        // Sharply rising prices → fast EMA > slow EMA → positive MACD
        let prices = sample_prices(80, 100.0, 2.0);
        let result = calculate_macd(&prices);
        assert!(result.value > 0.0, "expected positive MACD on rising prices, got {}", result.value);
    }

    // ── Bollinger Bands ────────────────────────────────────────────────────────

    #[test]
    fn bollinger_middle_equals_sma20() {
        let prices = sample_prices(30, 50.0, 1.0);
        let result = calculate_bollinger_bands(&prices, 20, 2.0);
        let sma20 = prices[10..].iter().sum::<f64>() / 20.0;
        let diff = (result.middle - sma20).abs();
        assert!(diff < 1e-10, "middle != SMA20: {diff}");
    }

    #[test]
    fn bollinger_upper_greater_than_lower() {
        let prices: Vec<f64> = (0..40).map(|i| 100.0 + (i as f64 * 0.7).sin() * 5.0).collect();
        let result = calculate_bollinger_bands(&prices, 20, 2.0);
        assert!(result.upper > result.lower, "upper should be > lower");
    }

    #[test]
    fn bollinger_position_within_0_100() {
        let prices: Vec<f64> = (0..40).map(|i| 100.0 + (i as f64).sin() * 3.0).collect();
        let result = calculate_bollinger_bands(&prices, 20, 2.0);
        assert!(result.position_pct >= 0.0 && result.position_pct <= 100.0);
    }

    #[test]
    fn bollinger_returns_neutral_for_insufficient_data() {
        let prices = vec![100.0, 101.0];
        let result = calculate_bollinger_bands(&prices, 20, 2.0);
        assert_eq!(result.upper, result.lower); // flat band with no data
    }

    // ── ATR ───────────────────────────────────────────────────────────────────

    fn sample_candles(n: usize) -> Vec<(f64, f64, f64)> {
        (0..n)
            .map(|i| {
                let close = 100.0 + i as f64;
                (close + 2.0, close - 2.0, close) // 4-unit range each candle
            })
            .collect()
    }

    #[test]
    fn atr_returns_zero_for_insufficient_candles() {
        let result = calculate_atr(&[(100.0, 95.0, 98.0)], 14);
        assert_eq!(result, 0.0);
    }

    #[test]
    fn atr_correct_for_constant_range() {
        // Every candle has H-L = 4, and consecutive closes differ by 1
        // True range ≈ 4 (since H-L dominates small close-to-close moves)
        let candles = sample_candles(20);
        let atr = calculate_atr(&candles, 14);
        assert!(atr > 0.0, "ATR should be positive");
        assert!(atr >= 3.0 && atr <= 6.0, "Expected ATR near 4.0, got {atr}");
    }

    #[test]
    fn atr_period_clamped_to_available_data() {
        let candles = sample_candles(5); // only 4 true-range values
        let atr = calculate_atr(&candles, 14); // period > data length
        assert!(atr > 0.0);
    }

    // ── ADX ───────────────────────────────────────────────────────────────────

    #[test]
    fn adx_returns_zero_for_insufficient_data() {
        let candles = sample_candles(5);
        let adx = calculate_adx(&candles, 14);
        assert_eq!(adx, 0.0);
    }

    #[test]
    fn adx_high_on_strong_trend() {
        // Strongly trending up: each candle moves higher
        let candles: Vec<(f64, f64, f64)> = (0..40)
            .map(|i| {
                let base = 100.0 + i as f64 * 3.0;
                (base + 2.0, base - 1.0, base + 1.0)
            })
            .collect();
        let adx = calculate_adx(&candles, 14);
        assert!(adx > 25.0, "Expected strong ADX on trending data, got {adx}");
    }

    #[test]
    fn adx_low_on_ranging_market() {
        // Oscillating sideways: price goes up and down around 100
        let candles: Vec<(f64, f64, f64)> = (0..40)
            .map(|i| {
                let offset = if i % 2 == 0 { 1.0 } else { -1.0 };
                let close = 100.0 + offset;
                (close + 1.0, close - 1.0, close)
            })
            .collect();
        let adx = calculate_adx(&candles, 14);
        assert!(adx < 30.0, "Expected low ADX on ranging data, got {adx}");
    }

    // ── VWAP ──────────────────────────────────────────────────────────────────

    #[test]
    fn vwap_returns_zero_before_any_ticks() {
        let calc = VwapCalculator::new();
        assert_eq!(calc.vwap(), 0.0);
    }

    #[test]
    fn vwap_single_tick_equals_price() {
        let mut calc = VwapCalculator::new();
        calc.update(50000.0, 1.0);
        assert!((calc.vwap() - 50000.0).abs() < 1e-9);
    }

    #[test]
    fn vwap_volume_weighted() {
        let mut calc = VwapCalculator::new();
        calc.update(100.0, 1.0); // small volume at low price
        calc.update(200.0, 9.0); // large volume at high price
        // Expected: (100*1 + 200*9) / (1+9) = 1900/10 = 190
        assert!((calc.vwap() - 190.0).abs() < 1e-9);
    }

    #[test]
    fn vwap_resets_correctly() {
        let mut calc = VwapCalculator::new();
        calc.update(50000.0, 2.0);
        calc.reset();
        assert_eq!(calc.vwap(), 0.0);
        calc.update(60000.0, 1.0);
        assert!((calc.vwap() - 60000.0).abs() < 1e-9);
    }
}
